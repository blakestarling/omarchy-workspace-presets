"""Versioned, atomic preset persistence."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import SCHEMA_VERSION
from .errors import ValidationError


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
        return {"schemaVersion": SCHEMA_VERSION, "presets": []}

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
        return data

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
                    "snapshot": snapshot,
                }
                data["presets"].append(result)
            self._write_unlocked(data)
            return copy.deepcopy(result)

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
        raise ValidationError("Launcher kind must be desktop or command")
