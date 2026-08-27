"""Capture, preflight, and cold-restore orchestration."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable

from . import SUPPORTED_LAYOUTS
from .desktop import (
    process_executable,
    resolve_launcher,
    scan_desktop_entries,
    scan_omarchy_panel_plugins,
    scan_process_table,
    terminal_process_launcher,
)
from .errors import LaunchError, RestoreError, UnsupportedError, ValidationError
from .hyprland import Hyprland, window_match_score
from .layouts import (
    capture_layout,
    denormalized_geometry,
    dwindle_replay,
    normalized_geometry,
    rect_for,
    target_order,
)
from .storage import PresetStore, utc_now


Progress = Callable[[str, str, dict | None], None]
VERSION_RE = re.compile(r"^(\d+)\.(\d+)")
UNSAFE_LABEL = re.compile(r"[\x00-\x1f\x7f<>&]")
WINDOW_SETTLE_SECONDS = 1.0
WINDOW_STABILIZE_SECONDS = 5.0
TRANSIENT_SURFACE_TITLE = re.compile(
    r"\b(?:checking for updates?|updat(?:e|er|ing)|splash|loading|starting)\b",
    re.IGNORECASE,
)
WINDOW_LIFECYCLE_EVENTS = {
    "openwindow", "closewindow", "windowtitle", "windowtitlev2",
    "changefloatingmode", "fullscreen",
}


def safe_label(value: object, *, fallback: str = "window", limit: int = 60) -> str:
    """Return a window label that is safe to place in a user-visible message.

    Window classes and titles are written by whatever application owns the
    window - a browser title is a remote page's own <title> - and these
    messages reach shell chrome whose Text elements leave textFormat at
    Text.AutoText. Markup characters must never survive into one.
    """
    text = " ".join(UNSAFE_LABEL.sub(" ", str(value or "")).split())
    if not text:
        return fallback
    return (text[:limit].rstrip() + "\u2026") if len(text) > limit else text


def _noop_progress(stage: str, message: str, details: dict | None = None) -> None:
    del stage, message, details


class WorkspaceEngine:
    def __init__(
        self,
        *,
        store: PresetStore | None = None,
        hyprland: Hyprland | None = None,
        progress: Progress | None = None,
    ):
        self.store = store or PresetStore()
        self.hypr = hyprland or Hyprland()
        self.progress = progress or _noop_progress
        self._capabilities: dict | None = None

    def capabilities(self, *, refresh: bool = False) -> dict:
        """Probe the installed compositor and Omarchy versions.

        Every preflight opens with this check, and `omarchy version` is a shell
        script costing about 80 ms - the bulk of a restore's fixed overhead, to
        re-answer a question that cannot change while the session is running.
        The panel's explicit recheck passes refresh=True.
        """
        if self._capabilities is not None and not refresh:
            return self._capabilities
        result = self._probe_capabilities()
        self._capabilities = result
        return result

    def _probe_capabilities(self) -> dict:
        missing = [
            name for name in ("hyprctl", "uwsm-app", "gtk-launch", "omarchy-shell")
            if not shutil.which(name)
        ]
        hypr_version = self.hypr.version().get("version", "") if not missing or "hyprctl" not in missing else ""
        omarchy_version = ""
        if shutil.which("omarchy"):
            try:
                omarchy_version = subprocess.run(
                    ["omarchy", "version"], capture_output=True, text=True, timeout=5, check=False
                ).stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
        supported_hyprland = self._at_least(str(hypr_version), 0, 56)
        supported_omarchy = self._at_least(str(omarchy_version), 4, 0)
        return {
            "ready": not missing and supported_hyprland and supported_omarchy,
            "missingCommands": missing,
            "hyprlandVersion": hypr_version,
            "omarchyVersion": omarchy_version,
            "supportedHyprland": supported_hyprland,
            "supportedOmarchy": supported_omarchy,
            "supportedLayouts": list(SUPPORTED_LAYOUTS),
            "dataPath": str(self.store.path),
        }

    def await_stable_workarea(
        self, *, timeout: float = 5.0, settle: float = 0.2
    ) -> bool:
        """Hold the startup launch until monitor geometry stops changing."""
        self.progress("startup", "Waiting for the desktop to settle", None)
        return self.hypr.await_stable_monitors(timeout=timeout, settle=settle)

    @staticmethod
    def _at_least(value: str, major: int, minor: int) -> bool:
        match = VERSION_RE.search(value)
        return bool(match and (int(match.group(1)), int(match.group(2))) >= (major, minor))

    @staticmethod
    def group_workspace_id(workspace_slot: int) -> int:
        """Map Omarchy's number-row label to its Hyprland workspace ID."""
        slot = int(workspace_slot)
        return 10 if slot == 0 else slot

    def capture(self, name: str, *, overwrite_id: str | None = None) -> dict:
        self.progress("capture", "Reading the active workspace", None)
        context = self.hypr.active_context()
        workspace = context["workspace"]
        layout_name = str(workspace.get("tiledLayout", ""))
        if layout_name not in SUPPORTED_LAYOUTS:
            raise UnsupportedError(
                f"Workspace layout {layout_name or 'unknown'!r} is not supported",
                details={"supportedLayouts": list(SUPPORTED_LAYOUTS)},
            )
        clients = self.hypr.workspace_clients(int(workspace["id"]))
        if not clients:
            raise ValidationError("The active workspace has no application windows")
        metadata = self.hypr.layout_metadata(clients)
        entries = scan_desktop_entries()
        panel_plugins = scan_omarchy_panel_plugins()
        # Read the process table once for the whole workspace rather than once
        # per terminal window.
        process_table = scan_process_table()
        stable_to_slot: dict[str, str] = {
            str(client.get("stableId")): str(uuid.uuid4()) for client in clients
        }

        self.progress("capture", f"Resolving {len(clients)} application launcher(s)", None)
        windows: list[dict] = []
        for index, client in enumerate(clients, start=1):
            stable = str(client.get("stableId", ""))
            executable = process_executable(client.get("pid"))
            match = {
                "class": str(client.get("class", "")),
                "initialClass": str(client.get("initialClass", "")),
                "title": str(client.get("title", "")),
                "initialTitle": str(client.get("initialTitle", "")),
                "executable": executable,
                "xwayland": bool(client.get("xwayland", False)),
            }
            resolution_input = {**client, "executable": executable}
            terminal_launch = terminal_process_launcher(
                client.get("pid"), executable, records=process_table
            )
            if terminal_launch:
                launcher, terminal_program = terminal_launch
                match["terminalProgram"] = terminal_program
                candidates = []
            else:
                launcher, candidates = resolve_launcher(
                    resolution_input, entries, panel_plugins
                )
            geometry = rect_for(client)
            self.progress(
                "capture",
                f"Capturing {safe_label(match['class'] or match['title'], fallback=f'window {index}')}",
                {"current": index, "total": len(clients)},
            )
            windows.append(
                {
                    "id": stable_to_slot[stable],
                    "captureStableId": stable,
                    "match": match,
                    "launcher": launcher,
                    "launcherCandidates": candidates,
                    "geometry": {
                        "pixels": geometry.public(),
                        "normalized": normalized_geometry(geometry, context["workarea"]),
                    },
                    "state": {
                        "floating": bool(client.get("floating", False)),
                        "pinned": bool(client.get("pinned", False)),
                        "pinFullscreened": bool(client.get("pinFullscreened", False)),
                        "fullscreen": int(client.get("fullscreen", 0)),
                        "fullscreenClient": int(client.get("fullscreenClient", 0)),
                        "tags": list(client.get("tags", [])),
                        # Hyprland 0.56 does not expose target pseudo state to
                        # read-only IPC. Never toggle live state during capture.
                        "pseudo": False,
                    },
                    "focusHistoryID": int(client.get("focusHistoryID", 0)),
                }
            )

        groups, stable_to_group = self._capture_groups(metadata, stable_to_slot)
        for slot in windows:
            group_id = stable_to_group.get(slot["captureStableId"])
            if group_id:
                slot["groupId"] = group_id

        window_by_stable = {slot["captureStableId"]: slot for slot in windows}
        group_by_member = {
            member: group for group in groups for member in group.get("members", [])
        }
        targets: list[dict] = []
        emitted_groups: set[str] = set()
        for client in clients:
            stable = str(client.get("stableId", ""))
            slot = window_by_stable[stable]
            group = group_by_member.get(slot["id"])
            if group:
                if group["id"] in emitted_groups:
                    continue
                emitted_groups.add(group["id"])
                representative_slot = group["representativeSlotId"]
                representative = next(item for item in windows if item["id"] == representative_slot)
                representative_client = next(
                    item for item in clients if str(item.get("stableId")) == representative["captureStableId"]
                )
                targets.append(
                    {
                        "slotId": representative_slot,
                        "stableId": representative["captureStableId"],
                        "rect": rect_for(representative_client),
                        "focusHistoryID": representative["focusHistoryID"],
                    }
                )
            else:
                targets.append(
                    {
                        "slotId": slot["id"],
                        "stableId": stable,
                        "rect": rect_for(client),
                        "focusHistoryID": slot["focusHistoryID"],
                    }
                )

        options: dict = {}
        if layout_name == "master":
            options["orientation"] = str(self.hypr.option("master:orientation", "left"))
        elif layout_name == "scrolling":
            options["direction"] = str(self.hypr.option("scrolling:direction", "right"))
            primary_axis = "x" if options["direction"] in {"left", "right"} else "y"
            if targets:
                leading = min(
                    target["rect"].x if primary_axis == "x" else target["rect"].y
                    for target in targets
                )
                origin = context["workarea"][primary_axis]
                options["tapeOffset"] = leading - origin
            options["primaryExtent"] = context["workarea"][
                "width" if primary_axis == "x" else "height"
            ]
            options["secondaryExtent"] = context["workarea"][
                "height" if primary_axis == "x" else "width"
            ]
        layout = capture_layout(layout_name, targets, metadata, options=options)
        focused = min(windows, key=lambda item: item["focusHistoryID"])
        snapshot = {
            "schemaVersion": 1,
            "capturedAt": utc_now(),
            "source": {
                "workspaceName": str(workspace.get("name", workspace.get("id"))),
                "monitor": str(context["monitor"].get("name", "")),
                "workarea": context["workarea"],
                "hyprlandVersion": self.hypr.version().get("version", ""),
            },
            "layout": layout,
            "windows": windows,
            "groups": groups,
            "finalFocusSlotId": focused["id"],
        }
        if overwrite_id:
            old = self.store.get(overwrite_id)
            self._carry_launchers(old.get("snapshot", {}), snapshot)
        saved = self.store.save_snapshot(name, snapshot, overwrite_id=overwrite_id)
        summary = self.store.public_summary(saved)
        self.progress(
            "complete",
            "Preset captured" if summary["loadable"] else "Preset saved; launcher setup is required",
            summary,
        )
        return summary

    @staticmethod
    def _capture_groups(metadata: dict[str, dict], stable_to_slot: dict[str, str]) -> tuple[list[dict], dict[str, str]]:
        seen: set[tuple[str, ...]] = set()
        groups: list[dict] = []
        membership: dict[str, str] = {}
        for stable, meta in metadata.items():
            members = tuple(value for value in meta.get("groupMembers", []) if value in stable_to_slot)
            if len(members) < 2 or members in seen:
                continue
            seen.add(members)
            group_id = str(uuid.uuid4())
            current_index = max(1, min(int(meta.get("groupCurrentIndex", 1)), len(members)))
            slot_members = [stable_to_slot[value] for value in members]
            group = {
                "id": group_id,
                "members": slot_members,
                "activeSlotId": slot_members[current_index - 1],
                "representativeSlotId": slot_members[current_index - 1],
                "currentIndex": current_index,
                "locked": bool(meta.get("groupLocked", False)),
            }
            groups.append(group)
            membership.update({value: group_id for value in members})
        return groups, membership

    @staticmethod
    def _carry_launchers(old_snapshot: dict, new_snapshot: dict) -> None:
        buckets: dict[tuple[str, str], list[dict]] = {}
        for slot in old_snapshot.get("windows", []):
            match = slot.get("match", {})
            key = (str(match.get("class", "")).casefold(), str(match.get("initialClass", "")).casefold())
            buckets.setdefault(key, []).append(slot)
        for slot in new_snapshot.get("windows", []):
            if slot.get("launcher"):
                continue
            match = slot.get("match", {})
            key = (str(match.get("class", "")).casefold(), str(match.get("initialClass", "")).casefold())
            candidates = buckets.get(key, [])
            if candidates:
                old = candidates.pop(0)
                if old.get("launcher"):
                    slot["launcher"] = copy.deepcopy(old["launcher"])

    @staticmethod
    def _validate_snapshot_integrity(preset: dict) -> None:
        """Refuse a preset whose parts disagree, before anything is closed.

        Restore closes the target windows first and then indexes the launched
        windows by slot id. A snapshot referencing a slot it does not define
        would raise part-way through, after the user's windows are already gone
        and with no way to put them back.
        """
        name = preset.get("name", "")
        snapshot = preset.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValidationError(f"Preset {name!r} has no saved snapshot")
        windows = snapshot.get("windows")
        if not isinstance(windows, list) or not windows:
            raise ValidationError(f"Preset {name!r} has no saved windows")
        slot_ids = {str(slot.get("id", "")) for slot in windows}
        layout = snapshot.get("layout")
        if not isinstance(layout, dict) or layout.get("name") not in SUPPORTED_LAYOUTS:
            raise ValidationError(
                f"Preset {name!r} was saved with an unsupported layout",
                details={"supportedLayouts": list(SUPPORTED_LAYOUTS)},
            )
        try:
            referenced = set(target_order(layout))
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ValidationError(
                f"Preset {name!r} has a damaged saved layout: {exc}"
            ) from exc
        for group in snapshot.get("groups") or []:
            if not isinstance(group, dict):
                raise ValidationError(f"Preset {name!r} has a damaged window group")
            referenced.update(str(member) for member in group.get("members", []))
            referenced.add(str(group.get("representativeSlotId", "")))
            referenced.add(str(group.get("activeSlotId", "")))
        focus_id = snapshot.get("finalFocusSlotId")
        if isinstance(focus_id, str):
            referenced.add(focus_id)
        referenced.discard("")
        missing = sorted(referenced - slot_ids)
        if missing:
            raise ValidationError(
                f"Preset {name!r} references {len(missing)} saved window(s) it no longer contains",
                details={"missingSlotIds": missing},
            )

    def preflight(self, preset_id: str) -> dict:
        capability = self.capabilities()
        if not capability["ready"]:
            raise UnsupportedError("This system does not meet the plugin requirements", details=capability)
        preset = self.store.get(preset_id)
        summary = self.store.public_summary(preset)
        if not summary["loadable"]:
            raise ValidationError(
                f"Preset {preset['name']!r} has {summary['unresolvedCount']} unresolved launcher(s)"
            )
        self._validate_snapshot_integrity(preset)
        context = self.hypr.active_context()
        current = self.hypr.workspace_clients(int(context["workspace"]["id"]))
        entries = scan_desktop_entries()
        panel_plugins = scan_omarchy_panel_plugins()
        for slot in preset["snapshot"]["windows"]:
            self._validate_runtime_launcher(slot["launcher"], entries, panel_plugins)
        other_clients = [
            item
            for item in self.hypr.clients()
            if int(item.get("workspace", {}).get("id", -999999)) != int(context["workspace"]["id"])
        ]
        conflicts = []
        seen_addresses: set[str] = set()
        for slot in preset["snapshot"]["windows"]:
            matches = sorted(
                (
                    (window_match_score(item, slot["match"]), item)
                    for item in other_clients
                    if str(item.get("address", "")) not in seen_addresses
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            if matches and matches[0][0] >= 100:
                candidate = matches[0][1]
                seen_addresses.add(str(candidate.get("address", "")))
                conflicts.append(
                    {
                        "slotId": slot["id"],
                        "class": candidate.get("class", ""),
                        "title": candidate.get("title", ""),
                        "workspace": candidate.get("workspace", {}).get("name", ""),
                        "stableId": str(candidate.get("stableId", "")),
                    }
                )
        windows_to_close = [
            {
                "stableId": str(item.get("stableId", "")),
                "class": item.get("class", ""),
                "title": item.get("title", ""),
            }
            for item in current
        ]
        token_input = {
            "presetId": preset["id"],
            "preset": hashlib.sha256(
                json.dumps(preset, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "workspaceId": int(context["workspace"]["id"]),
            "windowsToClose": sorted(
                item["stableId"] for item in windows_to_close
            ),
        }
        token = hashlib.sha256(
            json.dumps(token_input, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "preset": summary,
            "workspace": {
                "id": context["workspace"]["id"],
                "name": context["workspace"]["name"],
                "layout": context["workspace"].get("tiledLayout", ""),
            },
            "windowsToClose": windows_to_close,
            "conflicts": conflicts,
            # Existing matches on other workspaces are informational only for
            # the default launch-new policy. Confirmation is needed solely
            # when replacing this workspace would close a window.
            "requiresConfirmation": bool(current),
            "token": token,
        }

    def resolve_unresolved_launchers(self) -> dict:
        entries = scan_desktop_entries()
        panel_plugins = scan_omarchy_panel_plugins()
        resolved = 0
        normalized = 0
        changed_ids: set[str] = set()
        # Collected first so the whole repair pass is one write and one fsync
        # rather than one per slot.
        updates: list[tuple[str, str, dict]] = []
        for summary in self.store.list_summaries():
            preset = self.store.get(summary["id"])
            for slot in preset.get("snapshot", {}).get("windows", []):
                launcher, _ = resolve_launcher(slot.get("match", {}), entries, panel_plugins)
                current = slot.get("launcher")
                if not current and launcher:
                    updates.append((preset["id"], slot["id"], launcher))
                    resolved += 1
                    changed_ids.add(preset["id"])
                elif (
                    launcher and launcher.get("kind") == "omarchy-plugin"
                    and current and current.get("kind") == "command"
                    and current.get("argv") == [
                        "omarchy-shell", "shell", "summon", launcher["pluginId"], "{}"
                    ]
                ):
                    updates.append((preset["id"], slot["id"], launcher))
                    normalized += 1
                    changed_ids.add(preset["id"])
        self.store.set_launchers(updates)
        changed_presets = len(changed_ids)
        return {
            "resolvedWindowCount": resolved,
            "normalizedLauncherCount": normalized,
            "changedPresetCount": changed_presets,
        }

    def preflight_group(self, group_id: str) -> dict:
        capability = self.capabilities()
        if not capability["ready"]:
            raise UnsupportedError("This system does not meet the plugin requirements", details=capability)
        group = self.store.get_group(group_id)
        summaries = {item["id"]: item for item in self.store.list_summaries()}
        summary = PresetStore.public_group_summary(
            group, summaries, self.store.startup_group_id()
        )
        if not group.get("assignments"):
            raise ValidationError(f"Preset group {group['name']!r} has no workspace assignments")
        if not summary["loadable"]:
            raise ValidationError(
                f"Preset group {group['name']!r} contains a preset that is not loadable"
            )

        entries = scan_desktop_entries()
        panel_plugins = scan_omarchy_panel_plugins()
        all_clients = self.hypr.clients()
        targets = []
        preset_fingerprints = []
        for assignment in sorted(
            group["assignments"],
            key=lambda item: self.group_workspace_id(item["workspace"]),
        ):
            preset = self.store.get(assignment["presetId"])
            self._validate_snapshot_integrity(preset)
            preset_fingerprints.append([
                preset["id"],
                hashlib.sha256(
                    json.dumps(preset, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            ])
            for slot in preset["snapshot"]["windows"]:
                self._validate_runtime_launcher(
                    slot["launcher"], entries, panel_plugins
                )
            workspace_slot = int(assignment["workspace"])
            workspace_id = self.group_workspace_id(workspace_slot)
            current = [
                item for item in all_clients
                if item.get("mapped", True)
                and int(item.get("workspace", {}).get("id", -999999)) == workspace_id
            ]
            other_clients = [
                item for item in all_clients
                if int(item.get("workspace", {}).get("id", -999999)) != workspace_id
            ]
            conflicts = []
            seen_addresses: set[str] = set()
            for slot in preset["snapshot"]["windows"]:
                matches = sorted(
                    (
                        (window_match_score(item, slot["match"]), item)
                        for item in other_clients
                        if str(item.get("address", "")) not in seen_addresses
                    ),
                    key=lambda item: item[0], reverse=True,
                )
                if matches and matches[0][0] >= 100:
                    candidate = matches[0][1]
                    seen_addresses.add(str(candidate.get("address", "")))
                    conflicts.append({
                        "slotId": slot["id"], "class": candidate.get("class", ""),
                        "title": candidate.get("title", ""),
                        "workspace": candidate.get("workspace", {}).get("name", ""),
                        "stableId": str(candidate.get("stableId", "")),
                    })
            targets.append({
                "preset": summaries[preset["id"]],
                "workspace": {
                    "id": workspace_id,
                    "name": str(workspace_id),
                    "slot": workspace_slot,
                },
                "windowsToClose": [
                    {"stableId": str(item.get("stableId", "")), "class": item.get("class", ""), "title": item.get("title", "")}
                    for item in current
                ],
                "conflicts": conflicts,
            })
        token_input = {
            "groupId": group["id"], "updatedAt": group.get("updatedAt", ""),
            "assignments": group["assignments"],
            "presets": preset_fingerprints,
            "targets": [
                [item["workspace"]["id"], sorted(window["stableId"] for window in item["windowsToClose"])]
                for item in targets
            ],
        }
        token = hashlib.sha256(
            json.dumps(token_input, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "kind": "group", "group": summary, "targets": targets,
            "windowCountToClose": sum(len(item["windowsToClose"]) for item in targets),
            "conflictCount": sum(len(item["conflicts"]) for item in targets),
            "requiresConfirmation": any(item["windowsToClose"] for item in targets),
            "token": token,
        }

    def load_group(
        self, group_id: str, *, expected_token: str | None = None,
        conflict_policy: str = "launch-new", close_timeout: float = 8.0,
        launch_timeout: float = 12.0,
    ) -> dict:
        check = self.preflight_group(group_id)
        if expected_token is not None and check["token"] != expected_token:
            raise RestoreError(
                "The group or one of its target workspaces changed after confirmation; nothing was closed"
            )
        if conflict_policy not in {"launch-new", "move-existing"}:
            raise ValidationError("Conflict policy must be launch-new or move-existing")
        original_workspace_id = int(self.hypr.active_workspace().get("id", 0))
        active_window = getattr(self.hypr, "active_window", lambda: None)()
        original_window_id = str((active_window or {}).get("stableId", ""))
        targets = []
        closing_ids: set[str] = set()
        for index, target in enumerate(check["targets"]):
            preset = self.store.get(target["preset"]["id"])
            targets.append({
                "index": index,
                "preset": preset,
                "snapshot": preset["snapshot"],
                "workspace": target["workspace"],
                "conflicts": {item["slotId"]: item for item in target["conflicts"]},
                "slotWindows": {},
            })
            closing_ids.update(str(item["stableId"]) for item in target["windowsToClose"])

        self.progress(
            "close", f"Closing {len(closing_ids)} window(s) across target workspaces", None
        )
        for stable_id in closing_ids:
            window = self.hypr.find_window(stable_id)
            if window:
                self.hypr.close(window)
        remaining = self.hypr.wait_until_closed(closing_ids, close_timeout)
        if remaining:
            raise RestoreError(
                "One or more applications did not close; group restore was stopped without force-killing them",
                details={"remainingStableIds": sorted(remaining)},
            )

        tasks = []
        for target in targets:
            workspace_name = str(target["workspace"]["name"])
            workspace_id = int(target["workspace"]["id"])
            self.hypr.set_workspace_layout(workspace_name, target["snapshot"]["layout"])
            for slot in target["snapshot"]["windows"]:
                tasks.append({
                    "key": f"{target['index']}:{slot['id']}",
                    "slot": slot,
                    "workspaceId": workspace_id,
                    "conflict": target["conflicts"].get(slot["id"]),
                    "targetIndex": target["index"],
                })
        self.progress("launch", f"Opening {len(tasks)} window(s) across all workspaces", None)
        try:
            materialized = self._materialize_slots(
                tasks,
                conflict_policy=conflict_policy,
                launch_timeout=launch_timeout,
                preserve_workspace_id=original_workspace_id,
                preserve_window_id=original_window_id,
            )
        except Exception:
            if original_workspace_id >= 1:
                self.hypr.focus_workspace(original_workspace_id)
                if original_window_id:
                    original_window = self.hypr.find_window(original_window_id)
                    if original_window:
                        self.hypr.focus(original_window)
            raise
        for task in tasks:
            targets[task["targetIndex"]]["slotWindows"][task["slot"]["id"]] = materialized[task["key"]]

        results = []
        finalize_failures: list[Exception] = []
        try:
            for finalize_index, target in enumerate(targets, start=1):
                workspace_id = int(target["workspace"]["id"])
                workspace_slot = int(target["workspace"].get("slot", workspace_id))
                self.progress(
                    "layout", f"Finalizing {target['preset']['name']} on workspace {workspace_slot}",
                    {"current": finalize_index, "total": len(check["targets"])},
                )
                order = target_order(target["snapshot"]["layout"])
                anchor = target["slotWindows"][order[0]]
                try:
                    context = self._activate_workspace_for_layout(
                        workspace_id, anchor
                    )
                    self._finalize_snapshot(
                        target["snapshot"], target["slotWindows"], context,
                        focus_ready=True,
                    )
                except Exception as exc:
                    # Every target was launched before finalization begins. A
                    # failure in one workspace must not prevent independent
                    # targets from rebuilding their saved layouts. Restore
                    # this target's saved floating modes even if activation
                    # failed before _finalize_snapshot installed its own
                    # cleanup guard.
                    self._restore_saved_floating_modes(
                        target["snapshot"], target["slotWindows"]
                    )
                    finalize_failures.append(exc)
                    continue
                result = {
                    "presetId": target["preset"]["id"],
                    "name": target["preset"]["name"],
                    "workspace": str(target["workspace"]["name"]),
                    "windowCount": len(target["slotWindows"]),
                }
                results.append(result)
                self.store.record_preset_use(target["preset"]["id"])
        finally:
            if original_workspace_id >= 1:
                self.hypr.focus_workspace(original_workspace_id)
                if original_window_id:
                    original_window = self.hypr.find_window(original_window_id)
                    if original_window:
                        self.hypr.focus(original_window)
        if finalize_failures:
            raise finalize_failures[0]
        result = {
            "groupId": group_id, "name": check["group"]["name"],
            "workspaceCount": len(results), "results": results,
        }
        self.store.record_group_use(group_id)
        self.progress("complete", f"Loaded group {check['group']['name']}", result)
        return result

    @staticmethod
    def _validate_runtime_launcher(
        launcher: dict, entries: dict, panel_plugins: dict | None = None
    ) -> None:
        PresetStore._validate_launcher(launcher)
        if launcher["kind"] == "desktop":
            if launcher["desktopId"] not in entries:
                raise ValidationError(f"Desktop entry {launcher['desktopId']!r} no longer exists")
        elif launcher["kind"] == "command":
            executable = launcher["argv"][0]
            if "/" in executable:
                if not Path(executable).expanduser().is_file():
                    raise ValidationError(f"Custom command {executable!r} no longer exists")
            elif not shutil.which(executable):
                raise ValidationError(f"Custom command {executable!r} is not on PATH")
            cwd = launcher.get("cwd")
            if cwd is not None and not Path(cwd).is_dir():
                raise ValidationError(f"Command working directory {cwd!r} no longer exists")
        else:
            plugin_id = launcher["pluginId"]
            if not shutil.which("omarchy-shell"):
                raise ValidationError("Required command 'omarchy-shell' is not on PATH")
            # Callers validating a whole preset pass one scan in; rescanning
            # every plugin root per window cost 7 ms a slot.
            if panel_plugins is None:
                panel_plugins = scan_omarchy_panel_plugins()
            if plugin_id not in panel_plugins:
                raise ValidationError(f"Omarchy plugin {plugin_id!r} is no longer installed")

    def load(
        self,
        preset_id: str,
        *,
        expected_workspace_id: int,
        expected_token: str,
        conflict_policy: str = "launch-new",
        close_timeout: float = 8.0,
        launch_timeout: float = 12.0,
    ) -> dict:
        if conflict_policy not in {"launch-new", "move-existing"}:
            raise ValidationError("Conflict policy must be launch-new or move-existing")
        preflight = self.preflight(preset_id)
        preflight_workspace_id = int(preflight["workspace"]["id"])
        if preflight_workspace_id != int(expected_workspace_id):
            raise RestoreError(
                "The active workspace changed after load confirmation; nothing was closed",
                details={
                    "expectedWorkspaceId": int(expected_workspace_id),
                    "activeWorkspaceId": preflight_workspace_id,
                },
            )
        if preflight["token"] != expected_token:
            raise RestoreError(
                "The preset or workspace windows changed after preflight; nothing was closed"
            )
        preset = self.store.get(preset_id)
        snapshot = preset["snapshot"]
        context = self.hypr.active_context()
        active_workspace_id = int(context["workspace"]["id"])
        if active_workspace_id != int(expected_workspace_id):
            raise RestoreError(
                "The active workspace changed before replacement began; nothing was closed",
                details={
                    "expectedWorkspaceId": int(expected_workspace_id),
                    "activeWorkspaceId": active_workspace_id,
                },
            )
        workspace_name = str(context["workspace"]["name"])
        # Windows are routed by workspace ID, so prove the ID is usable while
        # the workspace is still intact rather than after it has been cleared.
        Hyprland.workspace_selector(active_workspace_id)
        # Close only the exact stable IDs covered by the preflight token. A
        # window that appears after this check must never be swept into the
        # replacement without warning and consent.
        closing_ids = {
            str(item["stableId"])
            for item in preflight["windowsToClose"]
            if str(item.get("stableId", ""))
        }
        self.progress("close", f"Closing {len(closing_ids)} current workspace window(s)", None)
        for stable_id in closing_ids:
            window = self.hypr.find_window(stable_id)
            if (
                window
                and int(window.get("workspace", {}).get("id", -999999))
                == active_workspace_id
            ):
                self.hypr.close(window)
        remaining = self.hypr.wait_until_closed(
            closing_ids, close_timeout
        )
        if remaining:
            raise RestoreError(
                "One or more applications did not close; restore was stopped without force-killing them",
                details={"remainingStableIds": sorted(remaining)},
            )

        self.hypr.set_workspace_layout(workspace_name, snapshot["layout"])
        conflict_by_slot = {item["slotId"]: item for item in preflight["conflicts"]}
        slots = snapshot["windows"]
        slot_windows = self._materialize_slots([{
            "key": slot["id"],
            "slot": slot,
            "workspaceId": active_workspace_id,
            "conflict": conflict_by_slot.get(slot["id"]),
        } for slot in slots], conflict_policy=conflict_policy, launch_timeout=launch_timeout)
        self._finalize_snapshot(snapshot, slot_windows, context)
        result = {
            "presetId": preset_id,
            "name": preset["name"],
            "workspace": workspace_name,
            "windowCount": len(slot_windows),
        }
        self.store.record_preset_use(preset_id)
        self.progress("complete", f"Loaded {preset['name']}", result)
        return result

    @staticmethod
    def _match_identity(slot: dict) -> set[str]:
        match = slot.get("match", {})
        return {
            str(match.get(name, "")).casefold()
            for name in ("class", "initialClass")
            if str(match.get(name, "")).strip()
        }

    @classmethod
    def _launch_waves(cls, tasks: list[dict]) -> list[list[dict]]:
        """Serialize ambiguous matches while launching unrelated applications together."""
        components: list[list[dict]] = []
        identities_by_component: list[set[str]] = []
        for task in tasks:
            identities = cls._match_identity(task["slot"])
            overlaps = [
                index for index, existing in enumerate(identities_by_component)
                if identities and existing.intersection(identities)
            ]
            if not overlaps:
                components.append([task])
                identities_by_component.append(set(identities))
                continue
            first = overlaps[0]
            components[first].append(task)
            identities_by_component[first].update(identities)
            for index in reversed(overlaps[1:]):
                components[first].extend(components.pop(index))
                identities_by_component[first].update(identities_by_component.pop(index))
        return [
            [component[index] for component in components if index < len(component)]
            for index in range(max((len(component) for component in components), default=0))
        ]

    def _materialize_slots(
        self,
        tasks: list[dict],
        *,
        conflict_policy: str,
        launch_timeout: float,
        preserve_workspace_id: int | None = None,
        preserve_window_id: str = "",
        settle_timeout: float = WINDOW_SETTLE_SECONDS,
    ) -> dict[str, dict]:
        def surface_signature(window: dict) -> tuple:
            return (
                str(window.get("class", "")),
                str(window.get("initialClass", "")),
                str(window.get("title", "")),
                str(window.get("initialTitle", "")),
                bool(window.get("mapped", True)),
                bool(window.get("floating", False)),
                int(window.get("fullscreen", 0)),
            )

        def surface_is_provisional(window: dict, slot: dict) -> bool:
            """Identify launch-time updater/splash surfaces that share an app class.

            Class identity is sufficient to claim a newly mapped window, but
            it is not proof that the application reached its saved surface.
            Electron applications commonly map a quiet, long-lived splash
            whose class is identical to the eventual main window. An obvious
            transient title stays provisional. So does a title that is merely
            the application class when the captured surface had a distinct
            title. The real window may have dynamic content, so an exact saved
            title match is deliberately not required.
            """
            title = str(window.get("title", "")).strip()
            if not title or TRANSIENT_SURFACE_TITLE.search(title):
                return True
            match = slot.get("match", {})
            saved_title = str(match.get("title", "")).strip().casefold()
            identity_titles = {
                str(value).strip().casefold()
                for value in (
                    window.get("class", ""),
                    window.get("initialClass", ""),
                    match.get("class", ""),
                    match.get("initialClass", ""),
                )
                if str(value).strip()
            }
            folded_title = title.casefold()
            return bool(
                saved_title
                and folded_title in identity_titles
                and folded_title != saved_title
            )

        settle_timeout = max(0.0, float(settle_timeout))
        windows: dict[str, dict] = {}
        pending = []
        used_existing: set[str] = set()
        for task in tasks:
            conflict = task.get("conflict")
            window = None
            if conflict_policy == "move-existing" and conflict:
                stable_id = str(conflict.get("stableId", ""))
                if stable_id not in used_existing:
                    window = self.hypr.find_window(stable_id)
            if window:
                stable_id = str(window.get("stableId", ""))
                used_existing.add(stable_id)
                self.hypr.move_to_workspace(window, task["workspaceId"])
                windows[task["key"]] = self.hypr.find_window(stable_id) or window
            else:
                pending.append(task)

        launched: list[str] = []
        waves = self._launch_waves(pending)
        total = len(pending)
        for wave_index, wave in enumerate(waves, start=1):
            before = {
                str(item.get("stableId", item.get("address", "")))
                for item in self.hypr.clients()
            }
            labels = [
                safe_label(
                    task["slot"].get("match", {}).get("class")
                    or task["slot"].get("match", {}).get("title")
                )
                for task in wave
            ]
            self.progress(
                "launch",
                f"Opening {', '.join(labels)}",
                {"wave": wave_index, "waves": len(waves), "current": len(launched), "total": total},
            )
            # Subscribed before anything is launched so a window that maps or
            # is replaced immediately cannot be missed. Some applications map
            # an updater or splash with the same class as their real window.
            events = self.hypr.events(WINDOW_LIFECYCLE_EVENTS)
            unresolved = {task["key"]: task for task in wave}
            task_by_key = dict(unresolved)
            assigned_ids: set[str] = set()
            signatures: dict[str, tuple] = {}
            provisional: set[str] = set()
            launch_deadline = time.monotonic() + launch_timeout
            settled_since: float | None = None
            settle_deadline: float | None = None
            try:
                for task in wave:
                    self._launch_on_workspace(
                        task["slot"]["launcher"], task["workspaceId"]
                    )

                while True:
                    now = time.monotonic()
                    if unresolved or provisional:
                        if now >= launch_deadline:
                            break
                    elif settle_deadline is not None and now >= settle_deadline:
                        break

                    changed = False
                    candidates = []
                    current_clients = self.hypr.clients()
                    current_by_id = {
                        str(item.get("stableId", item.get("address", ""))): item
                        for item in current_clients
                        if item.get("mapped", True)
                    }
                    for key, task in task_by_key.items():
                        assigned = windows.get(key)
                        if assigned is None:
                            continue
                        stable_id = str(
                            assigned.get("stableId", assigned.get("address", ""))
                        )
                        current = current_by_id.get(stable_id)
                        if (
                            current is None
                            or window_match_score(current, task["slot"]["match"]) < 100
                        ):
                            windows.pop(key, None)
                            assigned_ids.discard(stable_id)
                            signatures.pop(key, None)
                            provisional.discard(key)
                            unresolved[key] = task
                            changed = True
                        else:
                            windows[key] = current
                            signature = surface_signature(current)
                            if signatures.get(key) != signature:
                                signatures[key] = signature
                                changed = True

                            was_provisional = key in provisional
                            is_provisional = surface_is_provisional(
                                current, task["slot"]
                            )
                            if is_provisional:
                                provisional.add(key)
                            else:
                                provisional.discard(key)
                            if was_provisional != is_provisional:
                                changed = True

                    matchable = dict(unresolved)
                    matchable.update({
                        key: task_by_key[key] for key in provisional
                    })
                    for candidate in current_clients:
                        stable_id = str(candidate.get("stableId", candidate.get("address", "")))
                        if stable_id in before or stable_id in assigned_ids:
                            continue
                        for key, task in matchable.items():
                            score = window_match_score(candidate, task["slot"]["match"])
                            if score >= 100:
                                ready = not surface_is_provisional(
                                    candidate, task["slot"]
                                )
                                candidates.append((ready, score, stable_id, key, candidate))
                    for _ready, _score, stable_id, key, candidate in sorted(
                        candidates,
                        key=lambda item: (item[0], item[1], item[2], item[3]),
                        reverse=True,
                    ):
                        if (
                            key not in unresolved and key not in provisional
                        ) or stable_id in assigned_ids:
                            continue
                        task = unresolved.pop(key, task_by_key[key])
                        previous = windows.get(key)
                        if previous is not None:
                            previous_id = str(
                                previous.get("stableId", previous.get("address", ""))
                            )
                            assigned_ids.discard(previous_id)
                        assigned_ids.add(stable_id)
                        self.hypr.move_to_workspace(candidate, task["workspaceId"])
                        windows[key] = self.hypr.find_window(stable_id) or candidate
                        signatures[key] = surface_signature(windows[key])
                        if surface_is_provisional(windows[key], task["slot"]):
                            provisional.add(key)
                        else:
                            provisional.discard(key)
                        if key not in launched:
                            launched.append(key)
                        changed = True

                    now = time.monotonic()
                    if unresolved or provisional:
                        settled_since = None
                        settle_deadline = None
                        if now >= launch_deadline:
                            break
                        wait_deadline = launch_deadline
                    else:
                        if settle_deadline is None:
                            # Most windows complete after one quiet second. A
                            # changing Electron/Chromium surface gets a bounded
                            # grace period without consuming the whole
                            # application-launch deadline.
                            settle_deadline = min(
                                launch_deadline + settle_timeout,
                                now + WINDOW_STABILIZE_SECONDS,
                            )
                        if changed or settled_since is None:
                            settled_since = now
                        if now - settled_since >= settle_timeout:
                            break
                        wait_deadline = min(
                            settled_since + settle_timeout, settle_deadline
                        )
                    left = min(0.12, wait_deadline - now)
                    if left <= 0:
                        break
                    if events is None:
                        time.sleep(left)
                    else:
                        events.wait(left)
            finally:
                if events is not None:
                    events.close()

            incomplete = dict(unresolved)
            incomplete.update({key: task_by_key[key] for key in provisional})
            if incomplete:
                task = next(iter(incomplete.values()))
                slot = task["slot"]
                label = safe_label(
                    slot.get("match", {}).get("class") or slot.get("match", {}).get("title")
                )
                details = {
                    "slotId": slot["id"],
                    "launcher": slot["launcher"],
                    "launchedSlots": launched,
                    "missingSlotIds": [
                        item["slot"]["id"] for item in incomplete.values()
                    ],
                }
                if task.get("conflict"):
                    details["existingConflict"] = task["conflict"]
                raise LaunchError(
                    f"{label} did not create a settled matching window before the timeout",
                    details=details,
                )
            if preserve_workspace_id and preserve_workspace_id >= 1:
                self.hypr.focus_workspace(preserve_workspace_id)
                if preserve_window_id:
                    preserved = self.hypr.find_window(preserve_window_id)
                    if preserved:
                        self.hypr.focus(preserved)
        return windows

    def _activate_workspace_for_layout(
        self, workspace_id: int, anchor: dict
    ) -> dict:
        """Activate a group target through one of its exact saved windows.

        Hyprland can acknowledge a workspace dispatcher one frame before its
        active workspace and layout focus are published. Late application
        windows can widen that gap or steal focus in between. The exact-window
        barrier is therefore part of activation, before reading target context.
        """
        failure: Exception = RestoreError(
            f"Could not activate workspace {workspace_id}"
        )
        for _attempt in range(2):
            try:
                self.hypr.focus_workspace(workspace_id)
                self.hypr.focus_for_layout(anchor)
                context = self.hypr.active_context()
            except Exception as exc:
                failure = exc
                continue
            if int(context["workspace"]["id"]) == int(workspace_id):
                return context
            failure = RestoreError(f"Could not activate workspace {workspace_id}")
        raise failure

    def _finalize_snapshot(
        self, snapshot: dict, slot_windows: dict[str, dict], context: dict,
        *, focus_ready: bool = False,
    ) -> None:
        slots = snapshot["windows"]
        self.progress("layout", "Rebuilding groups and tiling topology", None)

        # Workspace focus publication and the active layout dispatcher can be
        # a frame apart, particularly while the session is still starting.
        # Synchronize through an exact window on this target workspace before
        # any grouping, float->tile transition, or layout message. Checking
        # only the active workspace ID is not a sufficient compositor barrier.
        order = target_order(snapshot["layout"])
        if order and not focus_ready:
            self.hypr.focus_for_layout(slot_windows[order[0]])

        try:
            # Keep launch-time windows in their compositor-native tiled state.
            # Detach only this target, after its exact focus barrier, so a slow
            # or failed application cannot strand every other group workspace
            # in the temporary floating state used for deterministic replay.
            for slot in slots:
                self.hypr.set_floating(slot_windows[slot["id"]], True)

            for group in snapshot.get("groups", []):
                members = [slot_windows[slot_id] for slot_id in group["members"]]
                representative = slot_windows[group["representativeSlotId"]]
                self.hypr.create_group(
                    representative,
                    members,
                    current_index=int(group.get("currentIndex", 1)),
                    locked=bool(group.get("locked", False)),
                )

            self._restore_tiling(
                snapshot["layout"], slot_windows, context.get("workarea", {})
            )
            source_workarea = snapshot.get("source", {}).get("workarea", {})
            for slot in slots:
                window = slot_windows[slot["id"]]
                if slot.get("state", {}).get("floating", False):
                    self.hypr.set_floating(window, True)
                    geometry = denormalized_geometry(
                        slot["geometry"], source_workarea, context["workarea"]
                    )
                    self.hypr.move_resize(window, geometry)

            self.progress("state", "Restoring window state and focus", None)
            # Pin/fullscreen changes can hide or redirect focus, so apply them last.
            for slot in slots:
                self.hypr.apply_window_state(
                    slot_windows[slot["id"]], slot.get("state", {})
                )
            focus_id = snapshot.get("finalFocusSlotId")
            if snapshot["layout"].get("name") == "scrolling":
                focus_window = slot_windows.get(focus_id)
                self.hypr.restore_scrolling_view(
                    snapshot["layout"], slot_windows, focus_window,
                    context.get("workarea", {}),
                )
            elif focus_id in slot_windows:
                self.hypr.focus(slot_windows[focus_id])
        except Exception:
            # Restoring the exact topology may fail if a client disappears.
            # Never leave the temporary replay state behind while surfacing the
            # original error.
            self._restore_saved_floating_modes(snapshot, slot_windows)
            raise

    def _restore_saved_floating_modes(
        self, snapshot: dict, slot_windows: dict[str, dict]
    ) -> None:
        """Best-effort cleanup for a failed target restore.

        Each lookup and mutation is isolated: one disappeared or invalid
        client must not prevent every remaining window from leaving the
        temporary replay state.
        """
        for slot in snapshot["windows"]:
            window = slot_windows.get(slot["id"])
            if window is None:
                continue
            try:
                stable_id = str(window.get("stableId", ""))
                current = self.hypr.find_window(stable_id) if stable_id else None
                self.hypr.set_floating(
                    current or window,
                    bool(slot.get("state", {}).get("floating", False)),
                )
            except Exception:
                pass

    @staticmethod
    def _launcher_command(launcher: dict) -> list[str]:
        if launcher["kind"] == "desktop":
            return ["uwsm-app", "--", "gtk-launch", launcher["desktopId"]]
        elif launcher["kind"] == "omarchy-plugin":
            return [
                "uwsm-app", "--", "omarchy-shell", "shell", "summon",
                launcher["pluginId"], "{}",
            ]
        return ["uwsm-app", "--", *launcher["argv"]]

    @staticmethod
    def _launch(launcher: dict) -> None:
        command = WorkspaceEngine._launcher_command(launcher)
        try:
            subprocess.Popen(
                command,
                cwd=launcher.get("cwd"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise LaunchError(f"Could not start {command[-1]!r}: {exc}") from exc

    def _launch_on_workspace(self, launcher: dict, workspace: str | int) -> None:
        launch = getattr(self.hypr, "exec_on_workspace", None)
        if launch is None:
            self._launch(launcher)
            return
        try:
            launch(
                self._launcher_command(launcher),
                workspace,
                cwd=launcher.get("cwd"),
            )
        except OSError as exc:
            raise LaunchError(f"Could not start {self._launcher_command(launcher)[-1]!r}: {exc}") from exc

    def _restore_tiling(
        self,
        layout: dict,
        windows: dict[str, dict],
        workarea: dict | None = None,
    ) -> None:
        name = layout["name"]
        if name == "dwindle":
            for operation in dwindle_replay(layout.get("tree")):
                window = windows[operation["slotId"]]
                if operation["op"] == "add":
                    self.hypr.set_floating(window, False)
                    continue
                self.hypr.focus_for_layout(windows[operation["anchor"]])
                self.hypr.layout_message(f"preselect {operation['direction']}")
                self.hypr.set_floating(window, False)
                self.hypr.focus_for_layout(window)
                self.hypr.layout_message(f"splitratio {float(operation['ratio']):.6f} exact")
            return
        order = target_order(layout)
        # Launch waves finish in compositor arrival order. Anchor every tiling
        # insertion to the previously restored target so arrival timing cannot
        # leak into the final layout order.
        previous = None
        for slot_id in order:
            if previous is not None and name != "scrolling":
                self.hypr.focus_for_layout(previous)
            window = windows[slot_id]
            self.hypr.set_floating(window, False)
            if name != "scrolling":
                self.hypr.focus(window)
            previous = window
        if name == "master":
            orientation = str(layout.get("orientation", "left"))
            if orientation in {"left", "right", "top", "bottom", "center"} and order:
                self.hypr.focus_for_layout(windows[order[0]])
                self.hypr.layout_message(f"orientation{orientation}")
            masters = list(layout.get("masters", []))
            for slot_id in masters[1:]:
                self.hypr.focus_for_layout(windows[slot_id])
                self.hypr.layout_message("addmaster")
            if order:
                self.hypr.focus_for_layout(windows[masters[0] if masters else order[0]])
                self.hypr.layout_message(f"mfact {float(layout.get('masterFactor', 0.55)):.6f} exact")
            return
        if name == "scrolling":
            self.hypr.restore_scrolling_layout(layout, windows, workarea or {})
            return
        if name == "monocle":
            return
        raise ValidationError(f"Unsupported layout {name!r}")
