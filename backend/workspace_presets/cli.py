"""JSON-lines command interface consumed by Service.qml and useful for diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .engine import Progress

from . import VERSION
from .errors import WorkspacePresetsError
from .storage import PresetStore


# Set while a persistent worker is handling one request so its progress and
# result events can be matched to it.
_request_id: object = None


def emit(kind: str, **values: Any) -> None:
    event = {"type": kind, **values}
    if _request_id is not None:
        event["requestId"] = _request_id
    print(json.dumps(event, ensure_ascii=False), flush=True)


def progress(stage: str, message: str, details: dict | None = None) -> None:
    emit("progress", stage=stage, message=message, details=details or {})


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="workspace-presets-backend")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    commands.add_parser("groups")
    commands.add_parser("state")
    commands.add_parser("capabilities")
    commands.add_parser("desktop-entries")
    commands.add_parser("resolve-launchers")

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

    startup_confirmation = commands.add_parser("group-startup-confirmation")
    confirmation_choice = startup_confirmation.add_mutually_exclusive_group(required=True)
    confirmation_choice.add_argument("--enable", action="store_true")
    confirmation_choice.add_argument("--disable", action="store_true")

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
    load.add_argument("--expected-token", required=True)
    load.add_argument(
        "--conflict-policy",
        choices=("launch-new", "move-existing"),
        default="launch-new",
    )
    load.add_argument("--confirmed", action="store_true")
    load.add_argument("--close-timeout", type=float, default=8.0)
    load.add_argument("--launch-timeout", type=float, default=12.0)

    serve_command = commands.add_parser("serve")
    serve_command.add_argument("--idle-timeout", type=float, default=120.0)
    return root


def serve(idle_timeout: float) -> int:
    """Answer commands on stdin until the caller closes it or goes quiet.

    Starting an interpreter and importing this package cost about 72 ms of the
    ~90 ms a `list` took, so the panel's two-command refresh spent almost all
    of its time on process setup. One worker pays that once. The idle timeout
    keeps a shell that is finished with presets from holding an interpreter
    resident all session.
    """
    global _request_id
    import select

    store = PresetStore()
    engine = _LazyEngine(store, progress)
    command_parser = parser()
    emit("ready", version=VERSION)
    while True:
        if idle_timeout > 0:
            readable, _, _ = select.select([sys.stdin], [], [], idle_timeout)
            if not readable:
                return 0
        line = sys.stdin.readline()
        if line == "":
            return 0
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            argv = [str(value) for value in request["args"]]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            emit("error", code="bad-request", message=f"Unreadable request: {exc}", details={})
            continue
        _request_id = request.get("id")
        try:
            # parse_args exits the process on a bad argument, which a worker
            # must survive.
            try:
                args = command_parser.parse_args(argv)
            except SystemExit:
                emit(
                    "error",
                    code="bad-request",
                    message=f"Unsupported command {' '.join(argv[:1]) or '(none)'}",
                    details={},
                )
                continue
            if args.command == "serve":
                emit("error", code="bad-request", message="Already serving", details={})
                continue
            run(args, store, engine)
        finally:
            _request_id = None


class _LazyEngine:
    """Build the engine only once a command actually reaches it.

    Importing ``engine`` pulls in ``desktop``, ``subprocess`` and ``shutil``,
    which together cost more than the work of the read-only commands the panel
    calls most often. ``engine`` is resolved from the module at call time so
    the import can still be patched in tests.
    """

    __slots__ = ("_store", "_progress", "_engine")

    def __init__(self, store: PresetStore, progress: "Progress"):
        self._store = store
        self._progress = progress
        self._engine = None

    def __getattr__(self, name: str):
        if self._engine is None:
            from . import engine as engine_module

            self._engine = engine_module.WorkspaceEngine(
                store=self._store, progress=self._progress
            )
        return getattr(self._engine, name)


def dispatch(args: argparse.Namespace, store: PresetStore, engine: Any) -> object:
    """Run one parsed command and return its result payload."""
    if args.command == "list":
        result = store.list_summaries()
    elif args.command == "groups":
        result = store.list_group_summaries()
    elif args.command == "state":
        # The panel refreshes both lists together, and both come from one
        # read of the store.
        result = {
            "presets": store.list_summaries(),
            "groups": store.list_group_summaries(),
        }
    elif args.command == "capabilities":
        # The panel asks for this explicitly to recheck a system it just
        # reported as unsupported, so never answer it from a cache.
        result = engine.capabilities(refresh=True)
    elif args.command == "desktop-entries":
        from .desktop import list_entries

        result = list_entries()
    elif args.command == "resolve-launchers":
        result = engine.resolve_unresolved_launchers()
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
        if not 0 <= args.workspace <= 9:
            raise WorkspacePresetsError("Workspace must be numbered from 0 to 9")
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
        result = store.startup_settings()
    elif args.command == "group-startup-confirmation":
        store.set_startup_confirmation(args.enable)
        result = store.startup_settings()
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
        # The once-per-session guard must live in the private per-user
        # runtime directory. Falling back to /tmp put it in a world-writable
        # directory under a name any local user could predict and
        # pre-create, silently suppressing the user's startup group.
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if not runtime or not signature:
            return {"launched": False, "reason": "no-session-runtime-directory"}
        marker_dir = Path(runtime) / "omarchy-workspace-presets"
        marker_name = hashlib.sha256(signature.encode()).hexdigest()[:20]
        marker = marker_dir / f"{marker_name}.startup"
        try:
            marker_dir.mkdir(mode=0o700, exist_ok=True)
            os.chmod(marker_dir, 0o700)
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            result = {"launched": False, "reason": "already-attempted-this-session"}
        except OSError as exc:
            raise WorkspacePresetsError(
                f"Cannot record the startup marker in {marker_dir}: {exc}"
            ) from exc
        else:
            os.close(descriptor)
            settings = store.startup_settings()
            group_id = settings["startupGroupId"]
            if group_id is None:
                result = {"launched": False, "reason": "no-startup-group"}
            elif settings["confirmStartupLaunch"]:
                check = engine.preflight_group(group_id)
                check["startupConfirmation"] = True
                result = {
                    "launched": False,
                    "reason": "confirmation-required",
                    "confirmationRequired": True,
                    "preflight": check,
                }
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
            expected_token=args.expected_token,
            conflict_policy=args.conflict_policy,
            close_timeout=max(1.0, args.close_timeout),
            launch_timeout=max(1.0, args.launch_timeout),
        )
    else:
        raise WorkspacePresetsError(f"Unknown command {args.command!r}")
    return result


def run(args: argparse.Namespace, store: PresetStore, engine: Any) -> int:
    """Dispatch one command and emit exactly one terminal event for it."""
    try:
        result = dispatch(args, store, engine)
    except WorkspacePresetsError as exc:
        emit("error", code=exc.code, message=str(exc), details=exc.details)
        return 2
    except Exception as exc:  # Never leave the shell with an opaque helper failure.
        emit("error", code="unexpected-error", message=str(exc), details={})
        return 3
    emit("result", operation=args.command, data=result)
    return 0


def main(argv: list[str] | None = None) -> int:
    parsed = parser().parse_args(argv)
    if parsed.command == "serve":
        return serve(parsed.idle_timeout)
    store = PresetStore()
    return run(parsed, store, _LazyEngine(store, progress))
