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
)
from .errors import LaunchError, RestoreError, UnsupportedError, ValidationError
from .hyprland import Hyprland, wait_for_new_window, window_match_score
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

    def capabilities(self) -> dict:
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

    @staticmethod
    def _at_least(value: str, major: int, minor: int) -> bool:
        match = VERSION_RE.search(value)
        return bool(match and (int(match.group(1)), int(match.group(2))) >= (major, minor))

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
            launcher, candidates = resolve_launcher(resolution_input, entries, panel_plugins)
            geometry = rect_for(client)
            self.progress(
                "capture",
                f"Capturing {match['class'] or match['title'] or f'window {index}'}",
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
                        "pseudo": self.hypr.probe_pseudo(client),
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
        context = self.hypr.active_context()
        current = self.hypr.workspace_clients(int(context["workspace"]["id"]))
        entries = scan_desktop_entries()
        for slot in preset["snapshot"]["windows"]:
            self._validate_runtime_launcher(slot["launcher"], entries)
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
        return {
            "preset": summary,
            "workspace": {
                "id": context["workspace"]["id"],
                "name": context["workspace"]["name"],
                "layout": context["workspace"].get("tiledLayout", ""),
            },
            "windowsToClose": [
                {"stableId": str(item.get("stableId", "")), "class": item.get("class", ""), "title": item.get("title", "")}
                for item in current
            ],
            "conflicts": conflicts,
            # Existing matches on other workspaces are informational only for
            # the default launch-new policy. Confirmation is needed solely
            # when replacing this workspace would close a window.
            "requiresConfirmation": bool(current),
        }

    def resolve_unresolved_launchers(self) -> dict:
        entries = scan_desktop_entries()
        panel_plugins = scan_omarchy_panel_plugins()
        resolved = 0
        normalized = 0
        changed_presets = 0
        for summary in self.store.list_summaries():
            preset = self.store.get(summary["id"])
            changed = False
            for slot in preset.get("snapshot", {}).get("windows", []):
                launcher, _ = resolve_launcher(slot.get("match", {}), entries, panel_plugins)
                current = slot.get("launcher")
                if not current and launcher:
                    self.store.set_launcher(preset["id"], slot["id"], launcher)
                    resolved += 1
                    changed = True
                elif (
                    launcher and launcher.get("kind") == "omarchy-plugin"
                    and current and current.get("kind") == "command"
                    and current.get("argv") == [
                        "omarchy-shell", "shell", "summon", launcher["pluginId"], "{}"
                    ]
                ):
                    self.store.set_launcher(preset["id"], slot["id"], launcher)
                    normalized += 1
                    changed = True
            if changed:
                changed_presets += 1
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
        all_clients = self.hypr.clients()
        targets = []
        preset_fingerprints = []
        for assignment in sorted(group["assignments"], key=lambda item: item["workspace"]):
            preset = self.store.get(assignment["presetId"])
            preset_fingerprints.append([
                preset["id"],
                hashlib.sha256(
                    json.dumps(preset, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            ])
            for slot in preset["snapshot"]["windows"]:
                self._validate_runtime_launcher(slot["launcher"], entries)
            workspace_id = int(assignment["workspace"])
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
                "workspace": {"id": workspace_id, "name": str(workspace_id)},
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
        original_workspace_id = int(self.hypr.active_workspace().get("id", 0))
        results = []
        try:
            for target in check["targets"]:
                workspace_id = int(target["workspace"]["id"])
                self.progress(
                    "group", f"Loading {target['preset']['name']} on workspace {workspace_id}",
                    {"current": len(results) + 1, "total": len(check["targets"])},
                )
                self.hypr.focus_workspace(workspace_id)
                context = self.hypr.active_context()
                if int(context["workspace"]["id"]) != workspace_id:
                    raise RestoreError(f"Could not activate workspace {workspace_id}")
                results.append(self.load(
                    target["preset"]["id"], expected_workspace_id=workspace_id,
                    conflict_policy=conflict_policy, close_timeout=close_timeout,
                    launch_timeout=launch_timeout,
                ))
        finally:
            if original_workspace_id >= 0:
                self.hypr.focus_workspace(original_workspace_id)
        result = {
            "groupId": group_id, "name": check["group"]["name"],
            "workspaceCount": len(results), "results": results,
        }
        self.store.record_group_use(group_id)
        self.progress("complete", f"Loaded group {check['group']['name']}", result)
        return result

    @staticmethod
    def _validate_runtime_launcher(launcher: dict, entries: dict) -> None:
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
        else:
            plugin_id = launcher["pluginId"]
            if not shutil.which("omarchy-shell"):
                raise ValidationError("Required command 'omarchy-shell' is not on PATH")
            if plugin_id not in scan_omarchy_panel_plugins():
                raise ValidationError(f"Omarchy plugin {plugin_id!r} is no longer installed")

    def load(
        self,
        preset_id: str,
        *,
        expected_workspace_id: int,
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
        current = self.hypr.workspace_clients(int(context["workspace"]["id"]))
        self.progress("close", f"Closing {len(current)} current workspace window(s)", None)
        for window in current:
            self.hypr.close(window)
        remaining = self.hypr.wait_until_closed(
            {str(item.get("stableId")) for item in current}, close_timeout
        )
        if remaining:
            raise RestoreError(
                "One or more applications did not close; restore was stopped without force-killing them",
                details={"remainingStableIds": sorted(remaining)},
            )

        self.hypr.set_workspace_layout(workspace_name, snapshot["layout"])
        conflict_by_slot = {item["slotId"]: item for item in preflight["conflicts"]}
        slot_windows: dict[str, dict] = {}
        launched: list[str] = []
        slots = snapshot["windows"]
        for index, slot in enumerate(slots, start=1):
            label = slot.get("match", {}).get("class") or slot.get("match", {}).get("title") or f"window {index}"
            self.progress("launch", f"Opening {label}", {"current": index, "total": len(slots)})
            conflict = conflict_by_slot.get(slot["id"])
            window = None
            if conflict_policy == "move-existing" and conflict:
                window = self.hypr.find_window(conflict["stableId"])
                if window:
                    self.hypr.move_to_workspace(window, workspace_name)
            if window is None:
                before = {str(item.get("stableId", item.get("address", ""))) for item in self.hypr.clients()}
                self._launch(slot["launcher"])
                window = wait_for_new_window(
                    self.hypr,
                    before,
                    slot["match"],
                    timeout=launch_timeout,
                )
                if window is None:
                    details = {"slotId": slot["id"], "launcher": slot["launcher"], "launchedSlots": launched}
                    if conflict:
                        details["existingConflict"] = conflict
                    raise LaunchError(
                        f"{label} did not create a matching window before the timeout",
                        details=details,
                    )
            self.hypr.move_to_workspace(window, workspace_name)
            self.hypr.set_floating(window, True)
            refreshed = self.hypr.find_window(str(window.get("stableId", ""))) or window
            slot_windows[slot["id"]] = refreshed
            launched.append(slot["id"])

        self.progress("layout", "Rebuilding groups and tiling topology", None)
        for group in snapshot.get("groups", []):
            members = [slot_windows[slot_id] for slot_id in group["members"]]
            representative = slot_windows[group["representativeSlotId"]]
            self.hypr.create_group(
                representative,
                members,
                current_index=int(group.get("currentIndex", 1)),
                locked=bool(group.get("locked", False)),
            )

        self._restore_tiling(snapshot["layout"], slot_windows)
        source_workarea = snapshot.get("source", {}).get("workarea", {})
        for slot in slots:
            window = slot_windows[slot["id"]]
            if slot.get("state", {}).get("floating", False):
                self.hypr.set_floating(window, True)
                geometry = denormalized_geometry(slot["geometry"], source_workarea, context["workarea"])
                self.hypr.move_resize(window, geometry)

        self.progress("state", "Restoring window state and focus", None)
        # Pin/fullscreen changes can hide or redirect focus, so apply them last.
        for slot in slots:
            self.hypr.apply_window_state(slot_windows[slot["id"]], slot.get("state", {}))
        focus_id = snapshot.get("finalFocusSlotId")
        if focus_id in slot_windows:
            self.hypr.focus(slot_windows[focus_id])
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
    def _launch(launcher: dict) -> None:
        if launcher["kind"] == "desktop":
            command = ["uwsm-app", "--", "gtk-launch", launcher["desktopId"]]
        elif launcher["kind"] == "omarchy-plugin":
            command = [
                "uwsm-app", "--", "omarchy-shell", "shell", "summon",
                launcher["pluginId"], "{}",
            ]
        else:
            command = ["uwsm-app", "--", *launcher["argv"]]
        try:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise LaunchError(f"Could not start {command[-1]!r}: {exc}") from exc

    def _restore_tiling(self, layout: dict, windows: dict[str, dict]) -> None:
        name = layout["name"]
        if name == "dwindle":
            for operation in dwindle_replay(layout.get("tree")):
                window = windows[operation["slotId"]]
                if operation["op"] == "add":
                    self.hypr.set_floating(window, False)
                    continue
                self.hypr.focus(windows[operation["anchor"]])
                self.hypr.layout_message(f"preselect {operation['direction']}")
                self.hypr.set_floating(window, False)
                self.hypr.focus(window)
                self.hypr.layout_message(f"splitratio {float(operation['ratio']):.6f} exact")
            return
        order = target_order(layout)
        for slot_id in order:
            self.hypr.set_floating(windows[slot_id], False)
        if name == "master":
            orientation = str(layout.get("orientation", "left"))
            if orientation in {"left", "right", "top", "bottom", "center"} and order:
                self.hypr.focus(windows[order[0]])
                self.hypr.layout_message(f"orientation{orientation}")
            masters = list(layout.get("masters", []))
            for slot_id in masters[1:]:
                self.hypr.focus(windows[slot_id])
                self.hypr.layout_message("addmaster")
            if order:
                self.hypr.focus(windows[masters[0] if masters else order[0]])
                self.hypr.layout_message(f"mfact {float(layout.get('masterFactor', 0.55)):.6f} exact")
            return
        if name == "scrolling":
            for column in layout.get("columns", []):
                members = list(column.get("slots", []))
                if not members:
                    continue
                self.hypr.focus(windows[members[0]])
                for _ in members[1:]:
                    self.hypr.layout_message("consume")
                self.hypr.focus(windows[members[0]])
                self.hypr.layout_message(f"colresize {float(column.get('width', 0.5)):.6f}")
            offset = float(layout.get("tapeOffset", 0))
            if order and abs(offset) >= 1:
                self.hypr.focus(windows[order[0]])
                self.hypr.layout_message(f"move {offset:.2f}")
            return
        if name == "monocle":
            return
        raise ValidationError(f"Unsupported layout {name!r}")
