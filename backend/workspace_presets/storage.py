"""Versioned, atomic preset persistence."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import SCHEMA_VERSION
from .errors import ValidationError


PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


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

    @staticmethod
    def empty() -> dict:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "presets": [],
            "presetGroups": [],
            "startupGroupId": None,
        }

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self) -> dict:
        if not self.path.exists():
            return self.empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"Cannot read preset data: {exc}") from exc
        return self._validate_root(data)

    def load(self) -> dict:
        with self._locked(exclusive=False):
            return copy.deepcopy(self._read_unlocked())

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
        # These fields were added without bumping the schema so existing v1
        # installations remain readable and are upgraded on their next write.
        groups = data.setdefault("presetGroups", [])
        data.setdefault("startupGroupId", None)
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
                    "launcher": slot.get("launcher"),
                }
                for slot in windows
            ],
        }

    def list_summaries(self) -> list[dict]:
        data = self.load()
        presets = sorted(
            data["presets"], key=lambda item: item.get("updatedAt", ""), reverse=True
        )
        return [self.public_summary(item) for item in presets]

    def list_group_summaries(self) -> list[dict]:
        data = self.load()
        presets = {item["id"]: self.public_summary(item) for item in data["presets"]}
        groups = sorted(
            data["presetGroups"], key=lambda item: item.get("updatedAt", ""), reverse=True
        )
        return [self.public_group_summary(item, presets, data.get("startupGroupId")) for item in groups]

    @staticmethod
    def public_group_summary(group: dict, presets: dict[str, dict], startup_group_id: str | None) -> dict:
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
        }

    def get_group(self, group_id: str) -> dict:
        data = self.load()
        for group in data["presetGroups"]:
            if group["id"] == group_id:
                return copy.deepcopy(group)
        raise ValidationError(f"Preset group {group_id!r} does not exist")

    def startup_group_id(self) -> str | None:
        return self.load().get("startupGroupId")

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
            self._write_unlocked(data)
            return copy.deepcopy(target)

    def get(self, preset_id: str) -> dict:
        data = self.load()
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
        self._validate_launcher(launcher)
        with self._locked(exclusive=True):
            data = self._read_unlocked()
            target = next((p for p in data["presets"] if p["id"] == preset_id), None)
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
            target["updatedAt"] = utc_now()
            self._write_unlocked(data)
            return copy.deepcopy(target)

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
            return
        if kind == "omarchy-plugin":
            plugin_id = launcher.get("pluginId")
            if not isinstance(plugin_id, str) or not PLUGIN_ID.fullmatch(plugin_id):
                raise ValidationError("Omarchy plugin launchers require a valid plugin id")
            return
        raise ValidationError("Launcher kind must be desktop, command, or omarchy-plugin")
