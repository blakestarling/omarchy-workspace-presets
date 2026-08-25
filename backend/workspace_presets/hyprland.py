"""Small, explicit adapter around Hyprland 0.56's JSON and Lua IPC."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from .errors import HyprlandError, UnsupportedError


STABLE_ID = re.compile(r"^[0-9]+$")
ADDRESS = re.compile(r"^0x[0-9a-fA-F]+$")
TAG = re.compile(r"^[A-Za-z0-9_-]+$")


class Hyprland:
    def __init__(self, *, timeout: float = 8.0):
        self.timeout = timeout

    def _run(self, args: list[str], *, json_result: bool = False, check: bool = True) -> object:
        if not shutil.which(args[0]):
            raise UnsupportedError(f"Required command {args[0]!r} is not installed")
        try:
            result = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HyprlandError(f"Cannot run {' '.join(args[:2])}: {exc}") from exc
        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown IPC error"
            raise HyprlandError(f"{' '.join(args[:2])} failed: {message}")
        if not json_result:
            return result.stdout.strip()
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HyprlandError(f"Hyprland returned invalid JSON: {exc}") from exc

    def query(self, name: str) -> object:
        return self._run(["hyprctl", name, "-j"], json_result=True)

    def clients(self) -> list[dict]:
        value = self.query("clients")
        if not isinstance(value, list):
            raise HyprlandError("hyprctl clients returned an unexpected value")
        return value

    def active_workspace(self) -> dict:
        value = self.query("activeworkspace")
        if not isinstance(value, dict) or "id" not in value:
            raise HyprlandError("No active Hyprland workspace")
        return value

    def monitors(self) -> list[dict]:
        value = self.query("monitors")
        if not isinstance(value, list):
            raise HyprlandError("hyprctl monitors returned an unexpected value")
        return value

    def version(self) -> dict:
        value = self.query("version")
        return value if isinstance(value, dict) else {}

    def active_context(self) -> dict:
        workspace = self.active_workspace()
        if int(workspace.get("id", 0)) < 0 or str(workspace.get("name", "")).startswith("special:"):
            raise UnsupportedError("Special workspaces are not supported in version 1")
        monitors = self.monitors()
        monitor = next(
            (
                item
                for item in monitors
                if item.get("id") == workspace.get("monitorID")
                or item.get("name") == workspace.get("monitor")
            ),
            None,
        )
        if monitor is None:
            monitor = next((item for item in monitors if item.get("focused")), None)
        if monitor is None:
            raise HyprlandError("Cannot resolve the active workspace monitor")
        reserved = monitor.get("reserved", [0, 0, 0, 0])
        left, top, right, bottom = [float(value) for value in reserved]
        scale = max(float(monitor.get("scale", 1)), 0.001)
        logical_width = float(monitor.get("width", 0)) / scale
        logical_height = float(monitor.get("height", 0)) / scale
        transform = int(monitor.get("transform", 0))
        if transform % 2 == 1:
            logical_width, logical_height = logical_height, logical_width
        workarea = {
            "x": round(float(monitor.get("x", 0)) + left),
            "y": round(float(monitor.get("y", 0)) + top),
            "width": round(logical_width - left - right),
            "height": round(logical_height - top - bottom),
            "scale": scale,
            "transform": transform,
            "monitor": str(monitor.get("name", "")),
        }
        return {"workspace": workspace, "monitor": monitor, "workarea": workarea}

    def workspace_clients(self, workspace_id: int) -> list[dict]:
        return [
            item
            for item in self.clients()
            if item.get("mapped", True)
            and int(item.get("workspace", {}).get("id", -999999)) == int(workspace_id)
        ]

    def option(self, name: str, fallback: object = None) -> object:
        value = self._run(["hyprctl", "getoption", name, "-j"], json_result=True, check=False)
        if not isinstance(value, dict):
            return fallback
        for key in ("str", "custom", "float", "int"):
            candidate = value.get(key)
            if candidate not in (None, ""):
                return candidate
        return fallback

    def repl(self, code: str, *, check: bool = True) -> str:
        return str(self._run(["hyprctl", "repl", code], check=check))

    @staticmethod
    def selector(window: dict | str) -> str:
        if isinstance(window, dict):
            stable = str(window.get("stableId", ""))
            address = str(window.get("address", ""))
        else:
            stable, address = str(window), ""
        if STABLE_ID.fullmatch(stable):
            return f"stableid:{stable}"
        if ADDRESS.fullmatch(address):
            return f"address:{address}"
        raise HyprlandError("Window has no safe Hyprland selector")

    def dispatch(self, dispatcher: str, argument: str = "", *, check: bool = True) -> str:
        args = ["hyprctl", "dispatch", dispatcher]
        if argument:
            args.append(argument)
        return str(self._run(args, check=check))

    def focus(self, window: dict | str) -> None:
        self.dispatch("focuswindow", self.selector(window))

    def close(self, window: dict | str) -> None:
        self.dispatch("closewindow", self.selector(window))

    def set_floating(self, window: dict | str, floating: bool) -> None:
        self.dispatch("setfloating" if floating else "settiled", self.selector(window))

    def move_to_workspace(self, window: dict | str, workspace_name: str) -> None:
        if "," in workspace_name or not workspace_name:
            raise HyprlandError("Unsafe workspace name")
        self.dispatch("movetoworkspacesilent", f"{workspace_name},{self.selector(window)}")

    def move_resize(self, window: dict | str, geometry: dict) -> None:
        selector = self.selector(window)
        self.dispatch(
            "resizewindowpixel",
            f"exact {int(geometry['width'])} {int(geometry['height'])},{selector}",
        )
        self.dispatch(
            "movewindowpixel",
            f"exact {int(geometry['x'])} {int(geometry['y'])},{selector}",
        )

    def layout_message(self, message: str) -> None:
        self.dispatch("layoutmsg", message)

    def set_workspace_layout(self, workspace_name: str, layout: dict) -> None:
        if "," in workspace_name or not workspace_name:
            raise HyprlandError("Unsafe workspace name")
        parts = [f"name:{workspace_name}", f"layout:{layout['name']}"]
        if layout["name"] == "master":
            orientation = str(layout.get("orientation", "left"))
            if orientation not in {"left", "right", "top", "bottom", "center"}:
                orientation = "left"
            parts.append(f"layoutopt:orientation:{orientation}")
        elif layout["name"] == "scrolling":
            direction = str(layout.get("direction", "right"))
            if direction not in {"left", "right", "up", "down"}:
                direction = "right"
            parts.append(f"layoutopt:direction:{direction}")
        self._run(["hyprctl", "keyword", "workspace", ", ".join(parts)])

    def apply_pseudo(self, window: dict | str, enabled: bool) -> None:
        selector = self.selector(window)
        action = "enable" if enabled else "disable"
        code = f"hl.dispatch(hl.dsp.window.pseudo({{action='{action}',window='{selector}'}}))"
        self.repl(code)

    def probe_pseudo(self, window: dict) -> bool:
        if window.get("floating"):
            return False
        stable_id = str(window.get("stableId", ""))
        if not STABLE_ID.fullmatch(stable_id):
            return False
        original = (tuple(window.get("at", [])), tuple(window.get("size", [])))
        self.apply_pseudo(window, True)
        time.sleep(0.035)
        enabled = self.find_window(stable_id)
        self.apply_pseudo(window, False)
        time.sleep(0.035)
        disabled = self.find_window(stable_id)
        enabled_geometry = (tuple(enabled.get("at", [])), tuple(enabled.get("size", []))) if enabled else original
        disabled_geometry = (tuple(disabled.get("at", [])), tuple(disabled.get("size", []))) if disabled else original
        was_enabled = original == enabled_geometry and enabled_geometry != disabled_geometry
        self.apply_pseudo(window, was_enabled)
        return was_enabled

    def apply_window_state(self, window: dict, state: dict) -> None:
        self.apply_pseudo(window, bool(state.get("pseudo", False)))
        current = self.find_window(str(window.get("stableId", ""))) or window
        desired_pin = bool(state.get("pinned", False))
        if bool(current.get("pinned", False)) != desired_pin:
            self.dispatch("pin", self.selector(window))
        for tag in state.get("tags", []):
            if isinstance(tag, str) and TAG.fullmatch(tag) and not tag.endswith("*"):
                self.dispatch("tagwindow", f"+{tag} {self.selector(window)}", check=False)
        internal = int(state.get("fullscreen", 0))
        client = int(state.get("fullscreenClient", 0))
        if internal or client:
            self.focus(window)
            self.dispatch("fullscreenstate", f"{internal} {client}")

    def find_window(self, stable_id: str) -> dict | None:
        return next((item for item in self.clients() if str(item.get("stableId")) == stable_id), None)

    def layout_metadata(self, windows: list[dict]) -> dict[str, dict]:
        ids = [str(item.get("stableId", "")) for item in windows]
        ids = [value for value in ids if STABLE_ID.fullmatch(value)]
        if not ids:
            return {}
        lua_ids = "{" + ",".join(f"'{value}'" for value in ids) + "}"
        code = (
            f"local ids={lua_ids}; "
            "for _,s in ipairs(ids) do "
            "local w=hl.get_window('stableid:'..s); "
            "if w then local l=w.layout; local n=''; local im=''; local pm=''; local ps=''; local ci=''; local cw=''; local iw=''; "
            "if l then n=tostring(l.name or ''); im=tostring(l.is_master or ''); pm=tostring(l.perc_master or ''); ps=tostring(l.perc_size or ''); "
            "if l.column then ci=tostring(l.column.index or ''); cw=tostring(l.column.width or ''); iw=tostring(l.index_in_column or '') end end; "
            "local gm=''; local gi=''; local gl=''; if w.group then local a={}; for _,m in ipairs(w.group.members) do table.insert(a,tostring(m.stable_id)) end; gm=table.concat(a,','); gi=tostring(w.group.current_index or ''); gl=tostring(w.group.locked or false) end; "
            "print('WSPMETA|'..s..'|'..n..'|'..im..'|'..pm..'|'..ps..'|'..ci..'|'..cw..'|'..iw..'|'..gm..'|'..gi..'|'..gl) end end"
        )
        output = self.repl(code)
        result: dict[str, dict] = {}
        for line in output.splitlines():
            if "WSPMETA|" not in line:
                continue
            fields = line[line.index("WSPMETA|") :].split("|", 11)
            if len(fields) < 12:
                continue
            _, stable, name, is_master, perc_master, perc_size, col_index, col_width, index_in_col, members, current_index, locked = fields
            value: dict = {"name": name}
            if is_master:
                value["isMaster"] = is_master == "true"
            for key, raw, cast in (
                ("percMaster", perc_master, float),
                ("percSize", perc_size, float),
                ("columnIndex", col_index, int),
                ("columnWidth", col_width, float),
                ("indexInColumn", index_in_col, int),
                ("groupCurrentIndex", current_index, int),
            ):
                if raw:
                    try:
                        value[key] = cast(float(raw)) if cast is int else cast(raw)
                    except ValueError:
                        pass
            value["groupMembers"] = [member for member in members.split(",") if member]
            value["groupLocked"] = locked == "true"
            result[stable] = value
        return result

    def create_group(self, representative: dict, members: list[dict], *, current_index: int = 1, locked: bool = False) -> None:
        if len(members) < 2:
            return
        self.focus(representative)
        self.dispatch("togglegroup")
        rep_selector = self.selector(representative)
        for member in members:
            if self.selector(member) == rep_selector:
                continue
            code = (
                f"local a=hl.get_window('{rep_selector}'); local b=hl.get_window('{self.selector(member)}'); "
                "if a and b and a.group then a.group:add(b) end"
            )
            self.repl(code)
        self.focus(representative)
        if current_index > 1:
            self.dispatch("changegroupactive", str(current_index), check=False)
        if locked:
            self.dispatch("lockactivegroup", "lock", check=False)

    def wait_until_closed(self, stable_ids: set[str], timeout: float) -> set[str]:
        deadline = time.monotonic() + timeout
        remaining = set(stable_ids)
        while remaining and time.monotonic() < deadline:
            present = {str(item.get("stableId")) for item in self.clients()}
            remaining &= present
            if remaining:
                time.sleep(0.15)
        return remaining


def window_match_score(candidate: dict, match: dict) -> int:
    score = 0
    candidate_class = str(candidate.get("class", "")).casefold()
    candidate_initial = str(candidate.get("initialClass", "")).casefold()
    saved_class = str(match.get("class", "")).casefold()
    saved_initial = str(match.get("initialClass", "")).casefold()
    if saved_initial and candidate_initial == saved_initial:
        score += 120
    if saved_class and candidate_class == saved_class:
        score += 100
    if saved_initial and candidate_class == saved_initial:
        score += 50
    if saved_class and candidate_initial == saved_class:
        score += 50
    return score


def wait_for_new_window(
    hypr: Hyprland,
    before: set[str],
    match: dict,
    *,
    timeout: float = 12.0,
) -> dict | None:
    deadline = time.monotonic() + timeout
    best: tuple[int, dict] | None = None
    while time.monotonic() < deadline:
        for candidate in hypr.clients():
            stable = str(candidate.get("stableId", candidate.get("address", "")))
            if stable in before:
                continue
            score = window_match_score(candidate, match)
            if score >= 100 and (best is None or score > best[0]):
                best = (score, candidate)
        if best:
            return best[1]
        time.sleep(0.12)
    return None
