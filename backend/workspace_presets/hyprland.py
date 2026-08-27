"""Small, explicit adapter around Hyprland 0.56's JSON and Lua IPC."""

from __future__ import annotations

import json
import math
import os
import re
import select
import shlex
import socket
import time
from pathlib import Path

from . import SUPPORTED_LAYOUTS
from .errors import HyprlandError, UnsupportedError, ValidationError


# Hyprland serializes stable IDs as hexadecimal without a 0x prefix. IDs can
# look numeric early in a session and later contain a-f, so accepting decimal
# only made layout capture fail intermittently as windows were opened.
STABLE_ID = re.compile(r"^[0-9a-fA-F]+$")
ADDRESS = re.compile(r"^0x[0-9a-fA-F]+$")
TAG = re.compile(r"^[A-Za-z0-9_-]+$")

# hyprctl is a thin client over this socket. It reports a Lua or dispatcher
# failure by exiting 7, and exactly those responses begin with "error:".
ERROR_PREFIX = "error:"

# An event line is a kind, an address, a workspace, a class and a title. The
# title is the one part an application chooses, and a window can set one of any
# length, so the buffer holding an unfinished line needs an end. Real lines are
# well under a kilobyte.
MAX_EVENT_LINE_BYTES = 64 * 1024


class EventStream:
    """A subscription to Hyprland's event socket, used to stop guessing.

    Every wait in a restore used to sleep a fixed interval and re-ask what the
    windows were doing, which both burned queries and added up to half that
    interval of latency per window. Hyprland announces these exact transitions
    on ``.socket2.sock``: an ``openwindow`` arrives about 65 ms after a launch,
    where the old loop would not have looked again for another 120 ms.

    The events are only a wake-up. They carry addresses, not the stable IDs the
    matching works in, so the caller still re-reads the client list - it just
    does so when something has actually changed. Waiting is capped at the
    interval the loop used to sleep, so a missed or unparsed event degrades to
    exactly the old polling behaviour rather than hanging.
    """

    def __init__(self, path: str, kinds: set[str]):
        self._kinds = kinds
        self._buffer = b""
        # Set while the remainder of an over-long line is being discarded, so
        # the events after it are still read as events.
        self._skipping = False
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._socket.connect(path)
            self._socket.setblocking(False)
        except OSError:
            self._socket.close()
            raise

    def __enter__(self) -> "EventStream":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._socket.close()
        except OSError:
            pass

    def wait(self, timeout: float) -> bool:
        """Block until a subscribed event arrives or ``timeout`` elapses."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                ready, _, _ = select.select([self._socket], [], [], remaining)
            except (OSError, ValueError):
                return False
            if not ready:
                return False
            try:
                block = self._socket.recv(65536)
            except BlockingIOError:
                continue
            except OSError:
                return False
            if not block:
                return False
            self._buffer += block
            matched = False
            while b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                if self._skipping:
                    # The tail of a line already given up on; its newline is
                    # the resynchronisation point.
                    self._skipping = False
                    continue
                kind = line.split(b">>", 1)[0].decode("utf-8", errors="replace")
                if kind in self._kinds:
                    matched = True
            if len(self._buffer) > MAX_EVENT_LINE_BYTES:
                # Hyprland does not announce a line this long, and an event is
                # only ever a wake-up here: the caller re-reads the client list
                # either way, so dropping one costs at most the 120 ms poll
                # this class exists to avoid.
                self._buffer = b""
                self._skipping = True
            if matched:
                return True


class Hyprland:
    def __init__(self, *, timeout: float = 8.0):
        self.timeout = timeout
        self._socket_path: str | None = None

    def events(self, kinds: set[str]) -> EventStream | None:
        """Subscribe to window lifecycle events, or None if unavailable.

        Callers must subscribe before the action they are waiting on, so a
        transition cannot slip between the two.
        """
        try:
            path = self.socket_path().replace(".socket.sock", ".socket2.sock")
            return EventStream(path, kinds)
        except (OSError, UnsupportedError):
            return None

    def socket_path(self) -> str:
        """Return this session's Hyprland IPC socket, or raise if there is none.

        Spawning hyprctl per call cost a fork, an exec and a dynamic link to
        send a few bytes to this same socket - about 9 ms against 0.1 ms here,
        which dominated every restore. hyprctl's own request grammar is used
        verbatim so responses stay byte-identical.
        """
        if self._socket_path is None:
            signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
            runtime = os.environ.get("XDG_RUNTIME_DIR", "")
            if not signature or not runtime:
                raise UnsupportedError(
                    "No Hyprland session is available on this display"
                )
            self._socket_path = f"{runtime}/hypr/{signature}/.socket.sock"
        return self._socket_path

    @staticmethod
    def _request(args: list[str]) -> str:
        """Translate an argv hyprctl would have been given into its wire form."""
        rest = args[1:]
        flags = ""
        if rest and rest[-1] == "-j":
            rest = rest[:-1]
            flags = "j/"
        return flags + " ".join(rest)

    def _run(self, args: list[str], *, json_result: bool = False, check: bool = True) -> object:
        request = self._request(args)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
                stream.settimeout(self.timeout)
                stream.connect(self.socket_path())
                stream.sendall(request.encode())
                blocks = []
                while True:
                    block = stream.recv(65536)
                    if not block:
                        break
                    blocks.append(block)
        except OSError as exc:
            raise HyprlandError(f"Cannot run {' '.join(args[:2])}: {exc}") from exc
        output = b"".join(blocks).decode("utf-8", errors="replace")
        if check and output.startswith(ERROR_PREFIX):
            raise HyprlandError(f"{' '.join(args[:2])} failed: {output.strip()}")
        if not json_result:
            return output.strip()
        try:
            return json.loads(output)
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

    def active_window(self) -> dict | None:
        value = self.query("activewindow")
        if not isinstance(value, dict) or not value.get("address"):
            return None
        return value

    def monitors(self) -> list[dict]:
        value = self.query("monitors")
        if not isinstance(value, list):
            raise HyprlandError("hyprctl monitors returned an unexpected value")
        return value

    def version(self) -> dict:
        value = self.query("version")
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _monitor_geometry(monitors: list[dict]) -> list[tuple]:
        return sorted(
            (
                str(monitor.get("name", "")),
                tuple(monitor.get("reserved", [0, 0, 0, 0])),
                monitor.get("width"),
                monitor.get("height"),
                monitor.get("scale"),
                monitor.get("transform"),
            )
            for monitor in monitors
        )

    def await_stable_monitors(
        self, *, timeout: float = 5.0, settle: float = 0.2
    ) -> bool:
        """Wait until two consecutive reads report the same monitor geometry.

        Saved geometry is normalized against the work area, which is the
        monitor minus whatever the bar has reserved. This plugin is hosted by
        that bar, so at login its own reservation may not have landed when the
        startup group runs, and every floating window would then be placed
        against a work area that never existed. Stability is the test rather
        than a non-zero reservation, because a session with no bar at all is
        legitimate and must not be made to wait for the timeout.
        """
        deadline = time.monotonic() + timeout
        previous = self._monitor_geometry(self.monitors())
        while time.monotonic() < deadline:
            time.sleep(settle)
            current = self._monitor_geometry(self.monitors())
            if current == previous:
                return True
            previous = current
        return False

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

    def eval(self, code: str, *, check: bool = True) -> str:
        return str(self._run(["hyprctl", "eval", code], check=check))

    @staticmethod
    def lua_string(value: object) -> str:
        """Return a safely quoted UTF-8 Lua string literal.

        JSON quoting cannot be reused here. Lua has no ``\\uXXXX`` escape, so a
        control character would produce a chunk Hyprland refuses to compile.
        Control bytes use Lua's three-digit decimal form, which stays
        unambiguous when the following character is itself a digit.
        """
        out = ['"']
        for char in str(value):
            code = ord(char)
            if char in '"\\':
                out.append("\\" + char)
            elif code < 0x20 or code == 0x7F:
                out.append(f"\\{code:03d}")
            else:
                out.append(char)
        out.append('"')
        return "".join(out)

    @staticmethod
    def lua_number(value: object) -> str:
        """Return a Lua numeric literal, refusing values Lua cannot express.

        Preset geometry is interpolated into Lua as bare numbers. ``inf`` and
        ``nan`` render as bare identifiers that Lua resolves to nil, producing
        an arithmetic error deep inside a replay transaction.
        """
        number = float(value)
        if not math.isfinite(number):
            raise ValidationError("Saved layout geometry must be a finite number")
        return repr(number)

    def lua_dispatch(self, expression: str, *, check: bool = True) -> str:
        """Run a Hyprland 0.56 typed dispatcher and surface result-table errors."""
        code = (
            f"local r=hl.dispatch({expression}); "
            "if type(r)=='table' and r.ok==false then "
            "error('Workspace Presets dispatch failed ['..tostring(r.code)..']: '..tostring(r.error)) "
            "end"
        )
        return self.repl(code, check=check)

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

    def focus(self, window: dict | str) -> None:
        selector = self.lua_string(self.selector(window))
        self.lua_dispatch(f"hl.dsp.focus({{window={selector}}})")

    def focus_for_layout(self, window: dict | str, *, timeout: float = 1.0) -> None:
        """Focus a window and wait until layout dispatchers can observe it.

        Hyprland publishes focus changes asynchronously to layout algorithms.
        A following layout command can otherwise operate on the previously
        active column or node even though the focus dispatcher succeeded.
        """
        selector = self.selector(window)
        # Subscribed before the dispatch so the announcement cannot arrive in
        # the gap between focusing and starting to watch.
        stream = self.events({"activewindowv2", "activewindow"})
        try:
            self.focus(window)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                active = self.active_window()
                if active is not None:
                    if selector.startswith("stableid:") and str(
                        active.get("stableId", "")
                    ) == selector.removeprefix("stableid:"):
                        return
                    if selector.startswith("address:") and str(
                        active.get("address", "")
                    ) == selector.removeprefix("address:"):
                        return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if stream is None:
                    time.sleep(min(0.005, remaining))
                elif not stream.wait(min(0.005, remaining)):
                    continue
        finally:
            if stream is not None:
                stream.close()
        raise HyprlandError(
            f"Could not focus {selector} before applying its saved layout"
        )

    @staticmethod
    def workspace_selector(workspace: str | int) -> str:
        """Return an unambiguous Hyprland workspace selector.

        Hyprland parses a bare selector, so only a positive integer id is
        unambiguous: a workspace a user named ``+2``, ``empty`` or ``previous``
        would otherwise be read as a relative or special target and the window
        would land somewhere else entirely.
        """
        value = str(workspace)
        if not value.isdigit() or int(value) < 1:
            raise HyprlandError("Unsafe workspace number")
        return value

    def focus_workspace(self, workspace: str | int) -> None:
        value = self.workspace_selector(workspace)
        self.lua_dispatch(f"hl.dsp.focus({{workspace={self.lua_string(value)}}})")

    def close(self, window: dict | str) -> None:
        selector = self.lua_string(self.selector(window))
        self.lua_dispatch(f"hl.dsp.window.close({{window={selector}}})")

    def set_floating(self, window: dict | str, floating: bool) -> None:
        selector = self.lua_string(self.selector(window))
        action = "set" if floating else "unset"
        self.lua_dispatch(
            f"hl.dsp.window.float({{action={self.lua_string(action)},window={selector}}})"
        )

    def move_to_workspace(self, window: dict | str, workspace: str | int) -> None:
        selector = self.lua_string(self.selector(window))
        target = self.lua_string(self.workspace_selector(workspace))
        self.lua_dispatch(
            f"hl.dsp.window.move({{workspace={target},follow=false,window={selector}}})"
        )

    def exec_on_workspace(
        self, command: list[str], workspace: str | int, *, cwd: str | None = None
    ) -> None:
        """Launch silently on a workspace without forcing persistent float state."""
        workspace_rule_target = self.workspace_selector(workspace)
        launch_command = list(command)
        if cwd:
            launch_command = ["env", f"--chdir={Path(cwd).expanduser().resolve()}", *launch_command]
        command_text = shlex.join(launch_command)
        workspace_rule = f"{workspace_rule_target} silent"
        self.repl(
            "hl.exec_cmd("
            f"{self.lua_string(command_text)},"
            f"{{workspace={self.lua_string(workspace_rule)},"
            "no_initial_focus=true})"
        )

    def move_resize(self, window: dict | str, geometry: dict) -> None:
        selector = self.lua_string(self.selector(window))
        self.lua_dispatch(
            "hl.dsp.window.resize({"
            f"x={int(geometry['width'])},y={int(geometry['height'])},"
            f"relative=false,window={selector}}})"
        )
        self.lua_dispatch(
            "hl.dsp.window.move({"
            f"x={int(geometry['x'])},y={int(geometry['y'])},"
            f"relative=false,window={selector}}})"
        )

    def layout_message(self, message: str) -> None:
        self.lua_dispatch(f"hl.dsp.layout({self.lua_string(message)})")

    def restore_scrolling_layout(
        self, layout: dict, windows: dict[str, dict], workarea: dict
    ) -> None:
        """Replay Scrolling topology as one compositor-side transaction.

        Scrolling layout messages resolve their target from Hyprland's single
        global focused window. Keeping ordering, grouping, and sizing in one
        REPL request prevents concurrent preset-group workspaces from stealing
        that focus between commands.
        """
        columns = [
            {
                "width": float(column.get("width", 0.5)),
                "slots": list(column.get("slots", [])),
                "sizes": dict(column.get("sizes", {})),
            }
            for column in layout.get("columns", [])
            if column.get("slots")
        ]
        order = [slot for column in columns for slot in column["slots"]]
        selectors = [self.selector(windows[slot]) for slot in order]
        if not selectors:
            return

        expected_columns: list[int] = []
        expected_rows: list[int] = []
        for column_index, column in enumerate(columns):
            for row_index, _slot in enumerate(column["slots"]):
                expected_columns.append(column_index)
                expected_rows.append(row_index)

        lua_selectors = "{" + ",".join(self.lua_string(value) for value in selectors) + "}"
        lua_columns = "{" + ",".join(str(value) for value in expected_columns) + "}"
        lua_rows = "{" + ",".join(str(value) for value in expected_rows) + "}"
        operations: list[str] = []
        cursor = 0
        direction = str(layout.get("direction", "right"))
        horizontal = direction in {"left", "right"}
        secondary_extent = float(
            workarea.get("height" if horizontal else "width", 0)
        )
        for column in columns:
            anchor = self.lua_string(selectors[cursor])
            width = column["width"]
            if not math.isfinite(width):
                raise ValidationError("Saved column widths must be finite numbers")
            width_command = self.lua_string(f"colresize {width:.6f}")
            operations.append(f"run(hl.dsp.focus({{window={anchor}}}))")
            operations.extend(
                "run(hl.dsp.layout('consume'))"
                for _ in column["slots"][1:]
            )
            operations.append(f"run(hl.dsp.layout({width_command}))")
            if secondary_extent > 0:
                for slot_offset, slot_id in enumerate(column["slots"][:-1]):
                    fraction = column["sizes"].get(slot_id)
                    if fraction is None:
                        continue
                    selector = self.lua_string(selectors[cursor + slot_offset])
                    scaled = float(fraction) * secondary_extent
                    if not math.isfinite(scaled):
                        raise ValidationError("Saved column sizes must be finite numbers")
                    desired = max(1, round(scaled))
                    if horizontal:
                        size = f"x=sized.size.x,y={desired}"
                    else:
                        size = f"x={desired},y=sized.size.y"
                    operations.append(
                        "do local sized=window("
                        f"{selector}); run(hl.dsp.window.resize({{{size},relative=false,window={selector}}})) end"
                    )
            cursor += len(column["slots"])

        body = "; ".join(operations)
        code = (
            "local old=hl.get_active_window(); local oldsel=old and string.format('stableid:%x',old.stable_id) or nil; "
            "local cursor=hl.get_cursor_pos(); "
            "local function run(d) local r=hl.dispatch(d); if type(r)=='table' and r.ok==false then error(tostring(r.error)) end end; "
            f"local sels={lua_selectors}; local expectedcols={lua_columns}; local expectedrows={lua_rows}; "
            "local function window(sel) local w=hl.get_window(sel); if not w then error('saved window disappeared during Scrolling replay') end; return w end; "
            "local ok,err=pcall(function() "
            "for i,sel in ipairs(sels) do local desired=window(sel); local col=desired.layout and desired.layout.column; "
            "if not col then error('saved window is not in the Scrolling layout') end; "
            "if col.index~=i-1 then local other=nil; for _,candidate in ipairs(sels) do local w=window(candidate); "
            "if w.layout and w.layout.column and w.layout.column.index==i-1 then other=candidate; break end end; "
            "if not other then error('could not locate Scrolling column during replay') end; "
            "run(hl.dsp.window.swap({window=sel,target=other})) end end; "
            f"{body}; "
            "for i,sel in ipairs(sels) do local w=window(sel); local l=w.layout; local c=l and l.column; "
            "if not c or c.index~=expectedcols[i] or l.index_in_column~=expectedrows[i] then "
            "error('Scrolling replay did not produce the saved topology') end end end); "
            "if oldsel and hl.get_window(oldsel) then hl.dispatch(hl.dsp.focus({window=oldsel})) end; "
            "if cursor then hl.dispatch(hl.dsp.cursor.move({x=cursor.x,y=cursor.y})) end; "
            "if not ok then error(err) end"
        )
        self.repl(code)

    def restore_scrolling_view(
        self,
        layout: dict,
        windows: dict[str, dict],
        focus_window: dict | None,
        workarea: dict,
    ) -> None:
        columns = [column for column in layout.get("columns", []) if column.get("slots")]
        order = [slot for column in columns for slot in column["slots"]]
        selectors = [self.selector(windows[slot]) for slot in order]
        if not selectors:
            return
        target = self.selector(focus_window or windows[order[0]])
        direction = str(layout.get("direction", "right"))
        axis = "x" if direction in {"left", "right"} else "y"
        primary_extent = float(workarea.get("width" if axis == "x" else "height", 0))
        if "tapeOffsetNormalized" in layout and primary_extent > 0:
            desired = float(layout["tapeOffsetNormalized"]) * primary_extent
        else:
            desired = float(layout.get("tapeOffset", 0))
        origin = float(workarea.get(axis, 0))
        lua_selectors = "{" + ",".join(self.lua_string(value) for value in selectors) + "}"
        code = (
            "local cursor=hl.get_cursor_pos(); "
            "local function run(d) local r=hl.dispatch(d); if type(r)=='table' and r.ok==false then error(tostring(r.error)) end end; "
            f"local sels={lua_selectors}; run(hl.dsp.focus({{window={self.lua_string(target)}}})); "
            f"local leading=nil; for _,sel in ipairs(sels) do local w=hl.get_window(sel); local at=w and w.at; local value=at and at.{axis}; "
            "if value and (not leading or value<leading) then leading=value end end; "
            f"if leading then local delta={self.lua_number(desired + origin)}-leading; if math.abs(delta)>=0.5 then run(hl.dsp.layout('move '..tostring(delta))) end end; "
            "if cursor then hl.dispatch(hl.dsp.cursor.move({x=cursor.x,y=cursor.y})) end"
        )
        self.repl(code)

    def set_workspace_layout(self, workspace_name: str, layout: dict) -> None:
        if not workspace_name:
            raise HyprlandError("Unsafe workspace name")
        layout_name = str(layout["name"])
        # Preset files are the only source of this value and nothing upstream
        # constrains it, so refuse anything outside the supported set before it
        # reaches a Lua literal.
        if layout_name not in SUPPORTED_LAYOUTS:
            raise ValidationError(f"Unsupported saved layout {layout_name!r}")
        options: dict[str, str] = {}
        if layout["name"] == "master":
            orientation = str(layout.get("orientation", "left"))
            if orientation not in {"left", "right", "top", "bottom", "center"}:
                orientation = "left"
            options["orientation"] = orientation
        elif layout["name"] == "scrolling":
            direction = str(layout.get("direction", "right"))
            if direction not in {"left", "right", "up", "down"}:
                direction = "right"
            options["direction"] = direction
        spec = (
            "{workspace="
            f"{self.lua_string(f'name:{workspace_name}')},layout={self.lua_string(layout_name)}"
        )
        if options:
            values = ",".join(
                f"{key}={self.lua_string(value)}" for key, value in options.items()
            )
            spec += f",layout_opts={{{values}}}"
        spec += "}"
        self.eval(f"hl.workspace_rule({spec})")

    def apply_pseudo(self, window: dict | str, enabled: bool) -> None:
        selector = self.lua_string(self.selector(window))
        action = "enable" if enabled else "disable"
        self.lua_dispatch(
            f"hl.dsp.window.pseudo({{action={self.lua_string(action)},window={selector}}})"
        )

    def apply_window_state(self, window: dict, state: dict) -> None:
        self.apply_pseudo(window, bool(state.get("pseudo", False)))
        current = self.find_window(str(window.get("stableId", ""))) or window
        desired_pin = bool(state.get("pinned", False))
        if bool(current.get("pinned", False)) != desired_pin:
            selector = self.lua_string(self.selector(window))
            action = "set" if desired_pin else "unset"
            self.lua_dispatch(
                f"hl.dsp.window.pin({{action={self.lua_string(action)},window={selector}}})"
            )
        for tag in state.get("tags", []):
            if isinstance(tag, str) and TAG.fullmatch(tag) and not tag.endswith("*"):
                selector = self.lua_string(self.selector(window))
                self.lua_dispatch(
                    f"hl.dsp.window.tag({{tag={self.lua_string(f'+{tag}')},window={selector}}})",
                    check=False,
                )
        internal = int(state.get("fullscreen", 0))
        client = int(state.get("fullscreenClient", 0))
        if internal or client:
            selector = self.lua_string(self.selector(window))
            self.lua_dispatch(
                "hl.dsp.window.fullscreen_state({"
                f"internal={internal},client={client},action='set',window={selector}}})"
            )

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
            "local gm=''; local gi=''; local gl=''; if w.group then local a={}; for _,m in ipairs(w.group.members) do table.insert(a,string.format('%x',m.stable_id)) end; gm=table.concat(a,','); gi=tostring(w.group.current_index or ''); gl=tostring(w.group.locked or false) end; "
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
        rep_selector = self.selector(representative)
        self.lua_dispatch(
            f"hl.dsp.group.toggle({{window={self.lua_string(rep_selector)}}})"
        )
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
            self.lua_dispatch(
                "hl.dsp.group.active({"
                f"index={current_index},window={self.lua_string(rep_selector)}}})",
                check=False,
            )
        if locked:
            self.lua_dispatch("hl.dsp.group.lock_active({action='set'})", check=False)

    def wait_until_closed(self, stable_ids: set[str], timeout: float) -> set[str]:
        deadline = time.monotonic() + timeout
        remaining = set(stable_ids)
        stream = self.events({"closewindow"})
        try:
            while remaining and time.monotonic() < deadline:
                present = {str(item.get("stableId")) for item in self.clients()}
                remaining &= present
                if not remaining:
                    break
                # Capped at the interval this loop used to sleep, so a missed
                # event is no worse than the old poll.
                left = min(0.15, deadline - time.monotonic())
                if left <= 0:
                    break
                if stream is None:
                    time.sleep(left)
                else:
                    stream.wait(left)
        finally:
            if stream is not None:
                stream.close()
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
    stream = hypr.events({"openwindow"})
    try:
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
            left = min(0.12, deadline - time.monotonic())
            if left <= 0:
                break
            if stream is None:
                time.sleep(left)
            else:
                stream.wait(left)
    finally:
        if stream is not None:
            stream.close()
    return None
