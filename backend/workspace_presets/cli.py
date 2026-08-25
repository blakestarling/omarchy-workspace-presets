"""JSON-lines command interface consumed by Service.qml and useful for diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from .desktop import list_entries
from .engine import WorkspaceEngine
from .errors import WorkspacePresetsError
from .storage import PresetStore


def emit(kind: str, **values: Any) -> None:
    print(json.dumps({"type": kind, **values}, ensure_ascii=False), flush=True)


def progress(stage: str, message: str, details: dict | None = None) -> None:
    emit("progress", stage=stage, message=message, details=details or {})


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="workspace-presets-backend")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    commands.add_parser("groups")
    commands.add_parser("capabilities")
    commands.add_parser("desktop-entries")

    details = commands.add_parser("details")
    details.add_argument("--id", required=True)

    capture = commands.add_parser("capture")
    capture.add_argument("--name", required=True)
    capture.add_argument("--overwrite-id")

    rename = commands.add_parser("rename")
    rename.add_argument("--id", required=True)
    rename.add_argument("--name", required=True)

    delete = commands.add_parser("delete")
    delete.add_argument("--id", required=True)

    group_create = commands.add_parser("group-create")
    group_create.add_argument("--name", required=True)

    group_rename = commands.add_parser("group-rename")
    group_rename.add_argument("--id", required=True)
    group_rename.add_argument("--name", required=True)

    group_assign = commands.add_parser("group-assign")
    group_assign.add_argument("--id", required=True)
    group_assign.add_argument("--preset-id", required=True)
    group_assign.add_argument("--workspace", required=True, type=int)

    group_unassign = commands.add_parser("group-unassign")
    group_unassign.add_argument("--id", required=True)
    group_unassign.add_argument("--preset-id", required=True)

    group_delete = commands.add_parser("group-delete")
    group_delete.add_argument("--id", required=True)

    group_startup = commands.add_parser("group-startup")
    startup_choice = group_startup.add_mutually_exclusive_group(required=True)
    startup_choice.add_argument("--id")
    startup_choice.add_argument("--disable", action="store_true")

    group_preflight = commands.add_parser("group-preflight")
    group_preflight.add_argument("--id", required=True)

    group_load = commands.add_parser("group-load")
    group_load.add_argument("--id", required=True)
    group_load.add_argument("--expected-token")
    group_load.add_argument("--confirmed", action="store_true")
    group_load.add_argument("--close-timeout", type=float, default=8.0)
    group_load.add_argument("--launch-timeout", type=float, default=12.0)

    commands.add_parser("startup-group")

    launcher = commands.add_parser("set-launcher")
    launcher.add_argument("--id", required=True)
    launcher.add_argument("--slot-id", required=True)
    launcher_kind = launcher.add_mutually_exclusive_group(required=True)
    launcher_kind.add_argument("--desktop-id")
    launcher_kind.add_argument("--argv-json")

    preflight = commands.add_parser("preflight")
    preflight.add_argument("--id", required=True)

    load = commands.add_parser("load")
    load.add_argument("--id", required=True)
    load.add_argument("--expected-workspace-id", required=True, type=int)
    load.add_argument(
        "--conflict-policy",
        choices=("launch-new", "move-existing"),
        default="launch-new",
    )
    load.add_argument("--confirmed", action="store_true")
    load.add_argument("--close-timeout", type=float, default=8.0)
    load.add_argument("--launch-timeout", type=float, default=12.0)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = PresetStore()
    engine = WorkspaceEngine(store=store, progress=progress)
    try:
        if args.command == "list":
            result = store.list_summaries()
        elif args.command == "groups":
            result = store.list_group_summaries()
        elif args.command == "capabilities":
            result = engine.capabilities()
        elif args.command == "desktop-entries":
            result = list_entries()
        elif args.command == "details":
            result = store.get(args.id)
        elif args.command == "capture":
            result = engine.capture(args.name, overwrite_id=args.overwrite_id)
        elif args.command == "rename":
            result = store.public_summary(store.rename(args.id, args.name))
        elif args.command == "delete":
            result = store.public_summary(store.delete(args.id))
        elif args.command == "group-create":
            group = store.save_group(args.name, [])
            result = PresetStore.public_group_summary(
                group, {item["id"]: item for item in store.list_summaries()}, store.startup_group_id()
            )
        elif args.command == "group-rename":
            group = store.get_group(args.id)
            updated = store.save_group(args.name, group["assignments"], group_id=args.id)
            result = PresetStore.public_group_summary(
                updated, {item["id"]: item for item in store.list_summaries()}, store.startup_group_id()
            )
        elif args.command == "group-assign":
            if args.workspace < 1:
                raise WorkspacePresetsError("Workspace must be a positive number")
            group = store.get_group(args.id)
            assignments = [
                item for item in group["assignments"]
                if item["presetId"] != args.preset_id and item["workspace"] != args.workspace
            ]
            assignments.append({"presetId": args.preset_id, "workspace": args.workspace})
            updated = store.save_group(group["name"], assignments, group_id=args.id)
            result = PresetStore.public_group_summary(
                updated, {item["id"]: item for item in store.list_summaries()}, store.startup_group_id()
            )
        elif args.command == "group-unassign":
            group = store.get_group(args.id)
            assignments = [item for item in group["assignments"] if item["presetId"] != args.preset_id]
            updated = store.save_group(group["name"], assignments, group_id=args.id)
            result = PresetStore.public_group_summary(
                updated, {item["id"]: item for item in store.list_summaries()}, store.startup_group_id()
            )
        elif args.command == "group-delete":
            result = store.delete_group(args.id)
        elif args.command == "group-startup":
            store.set_startup_group(None if args.disable else args.id)
            result = {"startupGroupId": store.startup_group_id()}
        elif args.command == "group-preflight":
            result = engine.preflight_group(args.id)
        elif args.command == "group-load":
            if not args.confirmed:
                raise WorkspacePresetsError("Refusing to replace group workspaces without --confirmed")
            result = engine.load_group(
                args.id, expected_token=args.expected_token,
                close_timeout=max(1.0, args.close_timeout),
                launch_timeout=max(1.0, args.launch_timeout),
            )
        elif args.command == "startup-group":
            runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
            signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "unknown")
            marker_name = hashlib.sha256(signature.encode()).hexdigest()[:20]
            marker = runtime / f"omarchy-workspace-presets-{marker_name}.startup"
            try:
                descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                result = {"launched": False, "reason": "already-attempted-this-session"}
            else:
                os.close(descriptor)
                group_id = store.startup_group_id()
                if group_id is None:
                    result = {"launched": False, "reason": "no-startup-group"}
                else:
                    loaded = engine.load_group(group_id)
                    result = {"launched": True, "group": loaded}
        elif args.command == "set-launcher":
            if args.desktop_id:
                launcher = {"kind": "desktop", "desktopId": args.desktop_id}
            else:
                try:
                    parsed = json.loads(args.argv_json)
                except json.JSONDecodeError as exc:
                    raise WorkspacePresetsError(f"Custom command is not valid JSON: {exc}") from exc
                launcher = {"kind": "command", "argv": parsed}
            result = store.public_summary(store.set_launcher(args.id, args.slot_id, launcher))
        elif args.command == "preflight":
            result = engine.preflight(args.id)
        elif args.command == "load":
            if not args.confirmed:
                raise WorkspacePresetsError("Refusing to replace a workspace without --confirmed")
            result = engine.load(
                args.id,
                expected_workspace_id=args.expected_workspace_id,
                conflict_policy=args.conflict_policy,
                close_timeout=max(1.0, args.close_timeout),
                launch_timeout=max(1.0, args.launch_timeout),
            )
        else:
            raise WorkspacePresetsError(f"Unknown command {args.command!r}")
        emit("result", operation=args.command, data=result)
        return 0
    except WorkspacePresetsError as exc:
        emit("error", code=exc.code, message=str(exc), details=exc.details)
        return 2
    except Exception as exc:  # Never leave the shell with an opaque helper failure.
        emit("error", code="unexpected-error", message=str(exc), details={})
        return 3
