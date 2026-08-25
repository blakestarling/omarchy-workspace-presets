"""JSON-lines command interface consumed by Service.qml and useful for diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
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
