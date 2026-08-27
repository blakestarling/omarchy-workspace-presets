"""Versioned, atomic preset persistence."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import SCHEMA_VERSION
from .errors import ValidationError


PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Mirrors the limits _process_argv already applies when capturing a command
# line, so a legitimately captured launcher can never fail validation.
MAX_ARGV_ARGUMENTS = 256
MAX_ARGV_CHARACTERS = 65536
# A store the UI can produce stays in the low kilobytes, and even a pathological
# one -- a hundred presets each holding twenty windows with a maximum-length
# launcher -- would have to grow more than an order of magnitude to reach this.
# The ceiling exists so a planted file cannot make the service read until it
# runs out of memory, not to constrain anything a user can legitimately save.
MAX_STATE_BYTES = 8 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "omarchy-workspace-presets" / "presets.json"


class PresetStore:
    """Owns the on-disk schema and serializes independent helper processes."""

    def __init__(self, path: Path | None = None):
        self.path = path or state_path()
        self.lock_path = self.path.with_suffix(".lock")
        # Identity of the file the cached document was parsed from. Every write
        # lands through os.replace, so a change always produces a new inode.
        self._cache_key: tuple | None = None
        self._cached: dict | None = None

    @staticmethod
    def empty() -> dict:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "presets": [],
            "presetGroups": [],
            "startupGroupId": None,
            "confirmStartupLaunch": False,
        }

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[None]:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir applies its mode only to the final component and never repairs
        # a directory that already exists. Restricting the data directory is
        # hardening, not the protection itself, so a failure is not fatal.
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        # Opening the lock by path would follow a symlink planted by anything
        # able to write into the data directory, and the permission fix would
        # then land on that symlink's target instead of the lock.
        try:
            descriptor = os.open(
                self.lock_path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
            )
        except OSError as exc:
            raise ValidationError(
                f"Cannot open the preset lock file {self.lock_path}: {exc}"
            ) from exc
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _open_state(self) -> int | None:
        """Open the state file for reading, or return None when it is absent.

        The data directory is user-writable, so presets.json is whatever was
        last left at that pathname -- anything running as the user can swap it
        between one read and the next. O_NOFOLLOW refuses a symlink planted in
        its place rather than reading through to the target, and O_NONBLOCK
        keeps a FIFO or a device node from parking the long-lived service
        inside open() with no writer on the other end.
        """
        try:
            return os.open(
                self.path,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            # A planted symlink arrives here as ELOOP. A FIFO or device opens
            # successfully under O_NONBLOCK instead of failing, which is the
            # point -- the refusal is the regular-file check below, reached
            # rather than waited for.
            raise ValidationError(f"Cannot read preset data: {exc}") from exc

    def _state_identity(self, descriptor: int) -> tuple:
        """Return the cache key for an opened state file, rejecting what cannot be one.

        The check runs against the descriptor, not the pathname, so what is
        verified here is exactly what the read below returns. Reading a
        character device or a directory would misbehave in ways json never
        sees, so only a regular file gets that far.
        """
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise ValidationError(f"Preset data {self.path} is not a regular file")
        if status.st_size > MAX_STATE_BYTES:
            raise ValidationError(
                f"Preset data {self.path} exceeds the {MAX_STATE_BYTES} byte limit"
            )
        return (status.st_ino, status.st_size, status.st_mtime_ns)

    def _state_text(self, descriptor: int) -> str:
        """Read an opened state file, stopping one byte past the ceiling.

        The size fstat reported is a hint, not a promise: another process can
        still be appending to the inode this descriptor holds. So the ceiling
        is enforced against what is actually read, and the extra byte is what
        distinguishes a file that just fits from one that does not.
        """
        chunks: list[bytes] = []
        remaining = MAX_STATE_BYTES + 1
        try:
            while remaining > 0:
                chunk = os.read(descriptor, min(remaining, 1 << 16))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        except OSError as exc:
            raise ValidationError(f"Cannot read preset data: {exc}") from exc
        raw = b"".join(chunks)
        if len(raw) > MAX_STATE_BYTES:
            raise ValidationError(
                f"Preset data {self.path} exceeds the {MAX_STATE_BYTES} byte limit"
            )
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"Cannot read preset data: {exc}") from exc

    def _shared_unlocked(self) -> dict:
        """Return the validated document, reusing the parse while the file is unchanged.

        The caller must not mutate the result. Re-reading, re-parsing and
        re-validating the whole store for each of the several reads a single
        command makes was most of what loading a preset group spent on disk;
        an fstat is free by comparison and still notices another process's write.

        The pathname is resolved once per call and everything after that goes
        through the descriptor, so the identity that decides the cache hit
        describes the same bytes the read returns.
        """
        descriptor = self._open_state()
        if descriptor is None:
            self._cache_key, self._cached = None, None
            return self.empty()
        try:
            key = self._state_identity(descriptor)
            if self._cached is not None and self._cache_key == key:
                return self._cached
            text = self._state_text(descriptor)
        finally:
            os.close(descriptor)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Cannot read preset data: {exc}") from exc
        # _validate_root also upgrades older documents in place, so cache what
        # it returns rather than what was parsed.
        validated = self._validate_root(data)
        self._cache_key, self._cached = key, validated
        return validated

    def _read_unlocked(self) -> dict:
        """Return a document the caller is free to mutate and write back."""
        return copy.deepcopy(self._shared_unlocked())

    def load(self) -> dict:
        with self._locked(exclusive=False):
            return self._read_unlocked()

    def _write_unlocked(self, data: dict) -> None:
        data = self._validate_root(data)
        encoded = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
        fd, temporary = tempfile.mkstemp(
            prefix=".presets-", suffix=".json", dir=self.path.parent
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _validate_root(data: object) -> dict:
        if not isinstance(data, dict):
            raise ValidationError("Preset data must be a JSON object")
        version = data.get("schemaVersion")
        if version != SCHEMA_VERSION:
            raise ValidationError(
                f"Unsupported preset schema {version!r}; expected {SCHEMA_VERSION}"
            )
        presets = data.get("presets")
        if not isinstance(presets, list):
            raise ValidationError("Preset data is missing its presets array")
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for preset in presets:
            if not isinstance(preset, dict):
                raise ValidationError("Every preset must be an object")
            preset_id = preset.get("id")
            name = preset.get("name")
            if not isinstance(preset_id, str) or not preset_id:
                raise ValidationError("Every preset must have an id")
            normalized = PresetStore.normalize_name(name)
            if preset_id in seen_ids or normalized in seen_names:
                raise ValidationError("Preset ids and names must be unique")
            seen_ids.add(preset_id)
            seen_names.add(normalized)
            PresetStore._validate_usage(preset, "Preset")
            PresetStore._validate_snapshot(preset)
        # These fields were added without bumping the schema so existing v1
        # installations remain readable and are upgraded on their next write.
        groups = data.setdefault("presetGroups", [])
        data.setdefault("startupGroupId", None)
        confirm_startup = data.setdefault("confirmStartupLaunch", False)
        if not isinstance(confirm_startup, bool):
            raise ValidationError("confirmStartupLaunch must be true or false")
        if not isinstance(groups, list):
            raise ValidationError("Preset data has an invalid presetGroups array")
        preset_ids = seen_ids
        group_ids: set[str] = set()
        group_names: set[str] = set()
        for group in groups:
            if not isinstance(group, dict):
                raise ValidationError("Every preset group must be an object")
            group_id = group.get("id")
            name = group.get("name")
            if not isinstance(group_id, str) or not group_id:
                raise ValidationError("Every preset group must have an id")
            normalized = PresetStore.normalize_name(name)
            if group_id in group_ids or normalized in group_names:
                raise ValidationError("Preset group ids and names must be unique")
            assignments = group.get("assignments")
            if not isinstance(assignments, list):
                raise ValidationError("Every preset group must have assignments")
            workspaces: set[int] = set()
            assigned_presets: set[str] = set()
            for assignment in assignments:
                if not isinstance(assignment, dict):
                    raise ValidationError("Preset group assignments must be objects")
                preset_id = assignment.get("presetId")
                workspace = assignment.get("workspace")
                if preset_id not in preset_ids:
                    raise ValidationError("Preset group references a missing preset")
                if (
                    not isinstance(workspace, int)
                    or isinstance(workspace, bool)
                    or not 0 <= workspace <= 9
                ):
                    raise ValidationError("Group workspaces must be numbered from 0 to 9")
                if workspace in workspaces:
                    raise ValidationError("A group can only assign one preset to each workspace")
                if preset_id in assigned_presets:
                    raise ValidationError("A preset can only appear once in a group")
                workspaces.add(workspace)
                assigned_presets.add(preset_id)
            group_ids.add(group_id)
            group_names.add(normalized)
            PresetStore._validate_usage(group, "Preset group")
        startup_group_id = data.get("startupGroupId")
        if startup_group_id is not None and startup_group_id not in group_ids:
            raise ValidationError("The startup preset group does not exist")
        return data

    @staticmethod
    def _validate_snapshot(preset: dict) -> None:
        """Check only what every stored snapshot must structurally satisfy.

        Deeper checks live in the engine preflight instead: rejecting the whole
        file here would make one damaged preset hide every other preset, while
        preflight refuses a single bad preset before anything is closed.
        """
        snapshot = preset.get("snapshot")
        if snapshot is None:
            return
        if not isinstance(snapshot, dict):
            raise ValidationError("Preset snapshots must be objects")
        windows = snapshot.get("windows", [])
        if not isinstance(windows, list):
            raise ValidationError("Preset snapshots must have a windows array")
        seen_slots: set[str] = set()
        for slot in windows:
            if not isinstance(slot, dict):
                raise ValidationError("Every saved window must be an object")
            slot_id = slot.get("id")
            if not isinstance(slot_id, str) or not slot_id:
                raise ValidationError("Every saved window must have an id")
            if slot_id in seen_slots:
                raise ValidationError("Saved window ids must be unique")
            seen_slots.add(slot_id)

    @staticmethod
    def _validate_usage(item: dict, label: str) -> None:
        # Usage metadata is optional so existing schema-v1 files remain valid.
        count = item.setdefault("useCount", 0)
        last_used = item.setdefault("lastUsedAt", "")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValidationError(f"{label} useCount must be a non-negative number")
        if not isinstance(last_used, str):
            raise ValidationError(f"{label} lastUsedAt must be a string")

    @staticmethod
    def normalize_name(name: object) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("Preset names cannot be empty")
        return name.strip().casefold()

    @staticmethod
    def public_summary(preset: dict) -> dict:
        snapshot = preset.get("snapshot", {})
        windows = snapshot.get("windows", [])
        unresolved = sum(1 for slot in windows if not slot.get("launcher"))
        return {
            "id": preset["id"],
            "name": preset["name"],
            "createdAt": preset.get("createdAt", ""),
            "updatedAt": preset.get("updatedAt", ""),
            "lastUsedAt": preset.get("lastUsedAt", ""),
            "useCount": preset.get("useCount", 0),
            "layout": snapshot.get("layout", {}).get("name", "unknown"),
            "windowCount": len(windows),
            "unresolvedCount": unresolved,
            "loadable": unresolved == 0 and bool(windows),
            "windows": [
                {
                    "id": slot.get("id", ""),
                    "class": slot.get("match", {}).get("class", ""),
                    "title": slot.get("match", {}).get("title", ""),
                    "program": slot.get("match", {}).get("terminalProgram", ""),
                    # Detached: the caller may hold this after the store has
                    # moved on, and in a long-lived process the source dict is
                    # the cached document itself.
                    "launcher": copy.deepcopy(slot.get("launcher")),
                }
                for slot in windows
            ],
        }

    def _shared(self) -> dict:
        with self._locked(exclusive=False):
            return self._shared_unlocked()

    def list_summaries(self) -> list[dict]:
        data = self._shared()
        presets = sorted(
            data["presets"], key=lambda item: item.get("updatedAt", ""), reverse=True
        )
        return [self.public_summary(item) for item in presets]

    def list_group_summaries(self) -> list[dict]:
        data = self._shared()
        presets = {item["id"]: self.public_summary(item) for item in data["presets"]}
        groups = sorted(
            data["presetGroups"], key=lambda item: item.get("updatedAt", ""), reverse=True
        )
        return [
            self.public_group_summary(
                item,
                presets,
                data.get("startupGroupId"),
                data.get("confirmStartupLaunch", False),
            )
            for item in groups
        ]

    @staticmethod
    def public_group_summary(
        group: dict,
        presets: dict[str, dict],
        startup_group_id: str | None,
        confirm_startup_launch: bool = False,
    ) -> dict:
        assignments = []
        for item in sorted(group.get("assignments", []), key=lambda value: value["workspace"]):
            preset = presets.get(item["presetId"])
            assignments.append({
                "presetId": item["presetId"],
                "presetName": preset["name"] if preset else "Missing preset",
                "workspace": item["workspace"],
                "loadable": bool(preset and preset["loadable"]),
            })
        return {
            "id": group["id"],
            "name": group["name"],
            "createdAt": group.get("createdAt", ""),
            "updatedAt": group.get("updatedAt", ""),
            "lastUsedAt": group.get("lastUsedAt", ""),
            "useCount": group.get("useCount", 0),
            "assignments": assignments,
            "assignmentCount": len(assignments),
            "loadable": bool(assignments) and all(item["loadable"] for item in assignments),
            "launchOnStartup": group["id"] == startup_group_id,
            "confirmOnStartup": (
                group["id"] == startup_group_id and confirm_startup_launch
            ),
        }

    def get_group(self, group_id: str) -> dict:
        data = self._shared()
        for group in data["presetGroups"]:
            if group["id"] == group_id:
                return copy.deepcopy(group)
        raise ValidationError(f"Preset group {group_id!r} does not exist")

    def startup_group_id(self) -> str | None:
        return self._shared().get("startupGroupId")

    def startup_settings(self) -> dict:
        data = self._shared()
        return {
            "startupGroupId": data.get("startupGroupId"),
            "confirmStartupLaunch": data.get("confirmStartupLaunch", False),
        }

    def save_group(self, name: str, assignments: list[dict], *, group_id: str | None = None) -> dict:
        normalized = self.normalize_name(name)
        with self._locked(exclusive=True):
            data = self._read_unlocked()
            now = utc_now()
            if group_id:
                target = next((g for g in data["presetGroups"] if g["id"] == group_id), None)
                if target is None:
                    raise ValidationError(f"Preset group {group_id!r} does not exist")
                if any(g["id"] != group_id and self.normalize_name(g["name"]) == normalized for g in data["presetGroups"]):
                    raise ValidationError(f"A preset group named {name!r} already exists")
                target["name"] = name.strip()
                target["assignments"] = copy.deepcopy(assignments)
                target["updatedAt"] = now
            else:
                if any(self.normalize_name(g["name"]) == normalized for g in data["presetGroups"]):
                    raise ValidationError(f"A preset group named {name!r} already exists")
                target = {
                    "id": str(uuid.uuid4()), "name": name.strip(),
                    "createdAt": now, "updatedAt": now,
                    "lastUsedAt": "", "useCount": 0,
                    "assignments": copy.deepcopy(assignments),
                }
                data["presetGroups"].append(target)
            self._write_unlocked(data)
            return copy.deepcopy(target)

    def delete_group(self, group_id: str) -> dict:
        with self._locked(exclusive=True):
            data = self._read_unlocked()
            target = next((g for g in data["presetGroups"] if g["id"] == group_id), None)
            if target is None:
                raise ValidationError(f"Preset group {group_id!r} does not exist")
            data["presetGroups"] = [g for g in data["presetGroups"] if g["id"] != group_id]
            if data.get("startupGroupId") == group_id:
                data["startupGroupId"] = None
                data["confirmStartupLaunch"] = False
            self._write_unlocked(data)
            return copy.deepcopy(target)

    def set_startup_group(self, group_id: str | None) -> dict | None:
        with self._locked(exclusive=True):
            data = self._read_unlocked()
            target = next((g for g in data["presetGroups"] if g["id"] == group_id), None)
            if group_id is not None and target is None:
                raise ValidationError(f"Preset group {group_id!r} does not exist")
            if target is not None:
                presets = {item["id"]: self.public_summary(item) for item in data["presets"]}
                summary = self.public_group_summary(target, presets, group_id)
                if not summary["loadable"]:
                    raise ValidationError("Only a complete, loadable preset group can launch on startup")
            data["startupGroupId"] = group_id
            if group_id is None:
                data["confirmStartupLaunch"] = False
            self._write_unlocked(data)
            return copy.deepcopy(target)

    def set_startup_confirmation(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            raise ValidationError("Startup confirmation must be true or false")
        with self._locked(exclusive=True):
            data = self._read_unlocked()
            if enabled and data.get("startupGroupId") is None:
                raise ValidationError(
                    "Select a preset group for startup before enabling confirmation"
                )
            data["confirmStartupLaunch"] = enabled
            self._write_unlocked(data)
            return enabled

    def get(self, preset_id: str) -> dict:
        data = self._shared()
        for preset in data["presets"]:
            if preset["id"] == preset_id:
                return copy.deepcopy(preset)
        raise ValidationError(f"Preset {preset_id!r} does not exist")

    def save_snapshot(
        self, name: str, snapshot: dict, *, overwrite_id: str | None = None
    ) -> dict:
        normalized = self.normalize_name(name)
        with self._locked(exclusive=True):
            data = self._read_unlocked()
            now = utc_now()
            if overwrite_id:
                target = next(
                    (p for p in data["presets"] if p["id"] == overwrite_id), None
                )
                if target is None:
                    raise ValidationError(f"Preset {overwrite_id!r} does not exist")
                for candidate in data["presets"]:
                    if candidate["id"] != overwrite_id and self.normalize_name(
                        candidate["name"]
                    ) == normalized:
                        raise ValidationError(f"A preset named {name!r} already exists")
                target["name"] = name.strip()
                target["updatedAt"] = now
                target["snapshot"] = snapshot
                result = target
            else:
                if any(
                    self.normalize_name(candidate["name"]) == normalized
                    for candidate in data["presets"]
                ):
                    raise ValidationError(f"A preset named {name!r} already exists")
                result = {
                    "id": str(uuid.uuid4()),
                    "name": name.strip(),
                    "createdAt": now,
                    "updatedAt": now,
                    "lastUsedAt": "",
                    "useCount": 0,
                    "snapshot": snapshot,
                }
                data["presets"].append(result)
            self._write_unlocked(data)
            return copy.deepcopy(result)

    def record_preset_use(self, preset_id: str) -> dict:
        return self._record_use("presets", preset_id, "Preset")

    def record_group_use(self, group_id: str) -> dict:
        return self._record_use("presetGroups", group_id, "Preset group")

    def _record_use(self, collection: str, item_id: str, label: str) -> dict:
        with self._locked(exclusive=True):
            data = self._read_unlocked()
            target = next((item for item in data[collection] if item["id"] == item_id), None)
            if target is None:
                raise ValidationError(f"{label} {item_id!r} does not exist")
            target["useCount"] = int(target.get("useCount", 0)) + 1
            target["lastUsedAt"] = utc_now()
            self._write_unlocked(data)
            return copy.deepcopy(target)

    def rename(self, preset_id: str, name: str) -> dict:
        normalized = self.normalize_name(name)
        with self._locked(exclusive=True):
            data = self._read_unlocked()
            target = next((p for p in data["presets"] if p["id"] == preset_id), None)
            if target is None:
                raise ValidationError(f"Preset {preset_id!r} does not exist")
            if any(
                candidate["id"] != preset_id
                and self.normalize_name(candidate["name"]) == normalized
                for candidate in data["presets"]
            ):
                raise ValidationError(f"A preset named {name!r} already exists")
            target["name"] = name.strip()
            target["updatedAt"] = utc_now()
            self._write_unlocked(data)
            return copy.deepcopy(target)

    def delete(self, preset_id: str) -> dict:
        with self._locked(exclusive=True):
            data = self._read_unlocked()
            target = next((p for p in data["presets"] if p["id"] == preset_id), None)
            if target is None:
                raise ValidationError(f"Preset {preset_id!r} does not exist")
            used_by = [g["name"] for g in data["presetGroups"] if any(a["presetId"] == preset_id for a in g["assignments"])]
            if used_by:
                raise ValidationError(
                    f"Preset {target['name']!r} is used by group(s): {', '.join(used_by)}"
                )
            data["presets"] = [p for p in data["presets"] if p["id"] != preset_id]
            self._write_unlocked(data)
            return copy.deepcopy(target)

    def set_launcher(self, preset_id: str, slot_id: str, launcher: dict) -> dict:
        return self.set_launchers([(preset_id, slot_id, launcher)])[preset_id]

    def set_launchers(
        self, updates: list[tuple[str, str, dict]]
    ) -> dict[str, dict]:
        """Apply every launcher assignment inside one lock and one write.

        Repairing drafts at service start wrote and fsynced the whole store
        once per slot, so a preset needing six repairs rewrote it six times.
        """
        for _preset_id, _slot_id, launcher in updates:
            self._validate_launcher(launcher)
        if not updates:
            return {}
        with self._locked(exclusive=True):
            data = self._read_unlocked()
            presets = {preset["id"]: preset for preset in data["presets"]}
            now = utc_now()
            touched: dict[str, dict] = {}
            for preset_id, slot_id, launcher in updates:
                target = presets.get(preset_id)
                if target is None:
                    raise ValidationError(f"Preset {preset_id!r} does not exist")
                slot = next(
                    (
                        item
                        for item in target.get("snapshot", {}).get("windows", [])
                        if item.get("id") == slot_id
                    ),
                    None,
                )
                if slot is None:
                    raise ValidationError(f"Window slot {slot_id!r} does not exist")
                slot["launcher"] = launcher
                target["updatedAt"] = now
                touched[preset_id] = target
            self._write_unlocked(data)
            return {
                preset_id: copy.deepcopy(target)
                for preset_id, target in touched.items()
            }

    @staticmethod
    def _validate_launcher(launcher: object) -> None:
        if not isinstance(launcher, dict):
            raise ValidationError("Launcher must be an object")
        kind = launcher.get("kind")
        if kind == "desktop":
            desktop_id = launcher.get("desktopId")
            if not isinstance(desktop_id, str) or not desktop_id.endswith(".desktop"):
                raise ValidationError("Desktop launchers require a .desktop id")
            return
        if kind == "command":
            argv = launcher.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(value, str) and value for value in argv)
            ):
                raise ValidationError("Custom launchers require a non-empty argv array")
            # Match the bounds the /proc capture path already enforces so no
            # captured command can fail this check, and reject the null bytes
            # that neither exec nor a Lua literal can carry.
            if len(argv) > MAX_ARGV_ARGUMENTS:
                raise ValidationError(
                    f"Custom launchers are limited to {MAX_ARGV_ARGUMENTS} arguments"
                )
            if sum(len(value) for value in argv) > MAX_ARGV_CHARACTERS:
                raise ValidationError(
                    f"Custom launchers are limited to {MAX_ARGV_CHARACTERS} characters"
                )
            if any("\0" in value for value in argv):
                raise ValidationError("Custom launcher arguments cannot contain null bytes")
            cwd = launcher.get("cwd")
            if cwd is not None and (
                not isinstance(cwd, str)
                or not cwd
                or not Path(cwd).is_absolute()
                or "\0" in cwd
            ):
                raise ValidationError("Custom launcher working directories must be absolute paths")
            return
        if kind == "omarchy-plugin":
            plugin_id = launcher.get("pluginId")
            if not isinstance(plugin_id, str) or not PLUGIN_ID.fullmatch(plugin_id):
                raise ValidationError("Omarchy plugin launchers require a valid plugin id")
            return
        raise ValidationError("Launcher kind must be desktop, command, or omarchy-plugin")
