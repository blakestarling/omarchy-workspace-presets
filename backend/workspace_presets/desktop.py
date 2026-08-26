"""Freedesktop desktop-entry discovery and conservative window matching."""

from __future__ import annotations

import configparser
import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


FIELD_CODE = re.compile(r"^%[fFuUdDnNickvm]$")
SAFE_DESKTOP_ID = re.compile(r"^[A-Za-z0-9_.+ -]+\.desktop$")
TERMINAL_EXECUTABLES = {
    "alacritty",
    "foot",
    "footclient",
    "ghostty",
    "kitty",
    "wezterm-gui",
}
SHELL_EXECUTABLES = {
    "bash", "dash", "elvish", "fish", "ksh", "nu", "sh", "xonsh", "zsh",
}
SESSION_EXECUTABLES = {
    "mosh-client", "ssh", "tmux", "zellij",
}


@dataclass(frozen=True)
class DesktopEntry:
    desktop_id: str
    name: str
    startup_class: str
    executable: str
    terminal: bool
    no_display: bool
    path: str
    webapp_url: str = ""

    def public(self) -> dict:
        return {
            "desktopId": self.desktop_id,
            "name": self.name,
            "startupClass": self.startup_class,
            "executable": self.executable,
            "terminal": self.terminal,
            "noDisplay": self.no_display,
        }


@dataclass(frozen=True)
class OmarchyPanelPlugin:
    plugin_id: str
    name: str
    path: str


def _omarchy_plugin_roots() -> list[Path]:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return [config_home / "omarchy/plugins", Path("/usr/share/omarchy/shell/plugins")]


def scan_omarchy_panel_plugins(roots: list[Path] | None = None) -> dict[str, OmarchyPanelPlugin]:
    """Discover panel plugins from manifests, with user plugins taking precedence."""
    plugins: dict[str, OmarchyPanelPlugin] = {}
    plugin_names: set[str] = set()
    for root in roots or _omarchy_plugin_roots():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("manifest.json")):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            plugin_id = manifest.get("id")
            name = manifest.get("name")
            kinds = manifest.get("kinds", [])
            if (
                not isinstance(plugin_id, str) or not plugin_id
                or not isinstance(name, str) or not name.strip()
                or not isinstance(kinds, list) or "panel" not in kinds
                or plugin_id in plugins
                or _norm(name) in plugin_names
            ):
                continue
            plugins[plugin_id] = OmarchyPanelPlugin(plugin_id, name.strip(), str(path))
            plugin_names.add(_norm(name))
    return plugins


def _data_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    system = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    return [home, *(Path(part) for part in system.split(":") if part)]


def _executable_from_exec(value: str) -> str:
    try:
        argv = shlex.split(value)
    except ValueError:
        return ""
    while argv and ("=" in argv[0] and not argv[0].startswith("/")):
        key = argv[0].split("=", 1)[0]
        if not key.replace("_", "").isalnum():
            break
        argv.pop(0)
    wrappers = {"env", "uwsm-app", "setsid", "systemd-run"}
    for token in argv:
        if FIELD_CODE.match(token) or token.startswith("-"):
            continue
        base = Path(token).name
        if base in wrappers:
            continue
        return base
    return ""


def _webapp_url_from_exec(value: str) -> str:
    try:
        argv = shlex.split(value)
    except ValueError:
        return ""
    for index, token in enumerate(argv[:-1]):
        if Path(token).name == "omarchy-launch-webapp":
            candidate = argv[index + 1]
            parsed = urlsplit(candidate)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                return candidate
    return ""


def scan_desktop_entries() -> dict[str, DesktopEntry]:
    """Return entries using the same user-over-system precedence as launchers."""
    entries: dict[str, DesktopEntry] = {}
    for data_dir in _data_dirs():
        applications = data_dir / "applications"
        if not applications.is_dir():
            continue
        for path in sorted(applications.rglob("*.desktop")):
            relative = path.relative_to(applications)
            desktop_id = "-".join(relative.parts)
            if desktop_id in entries or not SAFE_DESKTOP_ID.fullmatch(desktop_id):
                continue
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            try:
                parser.read(path, encoding="utf-8")
                section = parser["Desktop Entry"]
            except (OSError, KeyError, configparser.Error):
                continue
            if section.get("Type", "Application") != "Application":
                continue
            if section.getboolean("Hidden", fallback=False):
                continue
            exec_value = section.get("Exec", "").strip()
            if not exec_value:
                continue
            entries[desktop_id] = DesktopEntry(
                desktop_id=desktop_id,
                name=section.get("Name", path.stem).strip(),
                startup_class=section.get("StartupWMClass", "").strip(),
                executable=_executable_from_exec(exec_value),
                terminal=section.getboolean("Terminal", fallback=False),
                no_display=section.getboolean("NoDisplay", fallback=False),
                path=str(path),
                webapp_url=_webapp_url_from_exec(exec_value),
            )
    return entries


def process_executable(pid: object) -> str:
    try:
        return Path(f"/proc/{int(pid)}/exe").resolve().name
    except (OSError, TypeError, ValueError):
        return ""


def _process_argv(process: Path) -> list[str] | None:
    try:
        raw = (process / "cmdline").read_bytes()
    except OSError:
        return None
    if not raw or len(raw) > 65536:
        return None
    argv = [
        part.decode("utf-8", errors="replace")
        for part in raw.split(b"\0")
        if part
    ]
    return argv if 0 < len(argv) <= 256 else None


def _process_cwd(process: Path) -> str | None:
    try:
        cwd = str((process / "cwd").resolve(strict=True))
    except OSError:
        return None
    return cwd if Path(cwd).is_absolute() else None


def _process_stat(process: Path) -> dict | None:
    """Read only the process-tree and controlling-TTY fields from proc stat."""
    try:
        raw = (process / "stat").read_text(encoding="utf-8")
        pid = int(process.name)
        end = raw.rfind(")")
        if end < 0:
            return None
        fields = raw[end + 2:].split()
        if len(fields) < 6:
            return None
        return {
            "pid": pid,
            "ppid": int(fields[1]),
            "pgrp": int(fields[2]),
            "session": int(fields[3]),
            "tty": int(fields[4]),
            "tpgid": int(fields[5]),
        }
    except (OSError, UnicodeError, ValueError):
        return None


def _foreground_terminal_process(terminal_pid: int, proc_root: Path) -> Path | None:
    """Find one unambiguous foreground job below a terminal emulator process."""
    records: dict[int, dict] = {}
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return None
    for process in entries:
        if process.name.isdigit():
            stat = _process_stat(process)
            if stat:
                records[stat["pid"]] = stat

    def descendant_of(pid: int, ancestor: int) -> bool:
        seen: set[int] = set()
        while pid in records and pid not in seen:
            if pid == ancestor:
                return True
            seen.add(pid)
            pid = records[pid]["ppid"]
        return False

    candidates: list[Path] = []
    seen_groups: set[tuple[int, int]] = set()
    for record in records.values():
        if (
            record["tty"] == 0
            or record["tpgid"] <= 0
            or not descendant_of(record["pid"], terminal_pid)
        ):
            continue
        group = (record["tty"], record["tpgid"])
        if group in seen_groups:
            continue
        seen_groups.add(group)
        leader = records.get(record["tpgid"])
        if not leader or not descendant_of(leader["pid"], terminal_pid):
            continue
        leader_path = proc_root / str(leader["pid"])
        argv = _process_argv(leader_path)
        if not argv:
            continue
        executable = Path(argv[0]).name.casefold().lstrip("-")
        shell_has_command = len(argv) > 1 and (
            argv[1] == "-c" or not argv[1].startswith("-")
        )
        if (
            executable in SESSION_EXECUTABLES
            or (executable in SHELL_EXECUTABLES and not shell_has_command)
        ):
            continue
        # Multiple foreground siblings normally indicate a shell pipeline. Saving
        # only its process-group leader would silently change the command.
        if any(
            other["pid"] != leader["pid"]
            and other["pgrp"] == leader["pgrp"]
            and other["ppid"] == leader["ppid"]
            for other in records.values()
        ):
            continue
        candidates.append(leader_path)
    return candidates[0] if len(candidates) == 1 else None


def terminal_process_launcher(
    pid: object,
    executable: str,
    *,
    proc_root: Path = Path("/proc"),
) -> tuple[dict, str] | None:
    """Capture an explicit or currently foreground terminal command.

    Omarchy's xdg-terminal-exec integration uses ``-e`` before the program.
    Replaying the terminal's own argv preserves terminal-specific app IDs,
    titles, and wrappers such as omarchy-launch-docker-tui. When the terminal
    started as a shell, its controlling TTY identifies a manually launched
    foreground program without relying on shell history.
    """
    if Path(executable).name.casefold() not in TERMINAL_EXECUTABLES:
        return None
    try:
        terminal_pid = int(pid)
        process = proc_root / str(terminal_pid)
        argv = _process_argv(process)
        if not argv:
            return None
        if "-e" in argv:
            marker = argv.index("-e")
            command = argv[marker + 1:]
            cwd = _process_cwd(process)
        else:
            foreground = _foreground_terminal_process(terminal_pid, proc_root)
            if foreground is None:
                return None
            command = _process_argv(foreground)
            cwd = _process_cwd(foreground)
            argv = [*argv, "-e", *(command or [])]
        if not command or not cwd:
            return None
    except (OSError, TypeError, ValueError):
        return None
    return {"kind": "command", "argv": argv, "cwd": cwd}, Path(command[0]).name


def _norm(value: object) -> str:
    return str(value or "").strip().casefold()


def score_entry(entry: DesktopEntry, window: dict) -> tuple[int, list[str]]:
    window_class = _norm(window.get("class"))
    initial_class = _norm(window.get("initialClass"))
    executable = _norm(window.get("executable"))
    startup = _norm(entry.startup_class)
    stem = _norm(entry.desktop_id.removesuffix(".desktop"))
    entry_exec = _norm(entry.executable)
    score = 0
    reasons: list[str] = []
    if startup and startup in {window_class, initial_class}:
        score += 120
        reasons.append("StartupWMClass")
    if stem and stem in {window_class, initial_class}:
        score += 100
        reasons.append("desktop id")
    if entry_exec and executable and entry_exec == executable:
        score += 90
        reasons.append("process executable")
    if entry_exec and entry_exec in {window_class, initial_class}:
        score += 70
        reasons.append("Exec/class")
    if entry.webapp_url:
        parsed = urlsplit(entry.webapp_url)
        hostname = _norm(parsed.hostname)
        class_text = " ".join((window_class, initial_class))
        titles = " ".join((_norm(window.get("title")), _norm(window.get("initialTitle"))))
        if hostname and (hostname in class_text or hostname in titles):
            score += 140
            reasons.append("web app hostname")
            path_tokens = [
                token.casefold()
                for token in re.findall(r"[A-Za-z0-9]+", unquote(parsed.path))
                if len(token) > 2
            ]
            searchable = class_text.replace("_", " ") + " " + titles
            if path_tokens and all(token in searchable for token in path_tokens):
                score += 40
                reasons.append("web app path")
            if _norm(entry.name) and _norm(entry.name) in titles:
                score += 30
                reasons.append("web app name")
    return score, reasons


def resolve_launcher(
    window: dict,
    entries: dict[str, DesktopEntry],
    panel_plugins: dict[str, OmarchyPanelPlugin] | None = None,
) -> tuple[dict | None, list[dict]]:
    window_classes = {_norm(window.get("class")), _norm(window.get("initialClass"))}
    if "org.quickshell" in window_classes:
        titles = {_norm(window.get("title")), _norm(window.get("initialTitle"))}
        matching_panels = [
            plugin for plugin in (panel_plugins or {}).values()
            if _norm(plugin.name) in titles
        ]
        if len(matching_panels) == 1:
            return {
                "kind": "omarchy-plugin",
                "pluginId": matching_panels[0].plugin_id,
            }, []
    scored: list[tuple[int, DesktopEntry, list[str]]] = []
    for entry in entries.values():
        score, reasons = score_entry(entry, window)
        if score:
            scored.append((score, entry, reasons))
    scored.sort(key=lambda item: (-item[0], item[1].name.casefold(), item[1].desktop_id))
    candidates = [
        {**entry.public(), "score": score, "reasons": reasons}
        for score, entry, reasons in scored[:8]
    ]
    if not scored or scored[0][0] < 90:
        return None, candidates
    top_score = scored[0][0]
    tied = [item for item in scored if item[0] == top_score]
    if len(tied) != 1:
        return None, candidates
    selected = scored[0][1]
    if selected.webapp_url:
        return {
            "kind": "command",
            "argv": ["omarchy-launch-webapp", selected.webapp_url],
        }, candidates
    return {"kind": "desktop", "desktopId": selected.desktop_id}, candidates


def list_entries() -> list[dict]:
    return [
        entry.public()
        for entry in sorted(
            scan_desktop_entries().values(),
            key=lambda item: (item.no_display, item.name.casefold(), item.desktop_id),
        )
    ]
