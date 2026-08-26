"""Small, explicit adapter around Hyprland 0.56's JSON and Lua IPC."""

from __future__ import annotations

import json
import re
import shlex
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
        """Return a safely quoted UTF-8 Lua string literal."""
        return json.dumps(str(value), ensure_ascii=False)

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

    def focus_for_layout(self, window: dict | str, *, timeout: float = 0.25) -> None:
        """Focus a window and wait until layout dispatchers can observe it.

        Hyprland publishes focus changes asynchronously to layout algorithms.
        A following layout command can otherwise operate on the previously
        active column or node even though the focus dispatcher succeeded.
        """
        selector = self.selector(window)
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
            time.sleep(0.005)
        raise HyprlandError(
            f"Could not focus {selector} before applying its saved layout"
        )

    def focus_workspace(self, workspace: str | int) -> None:
        value = str(workspace)
        if not value.isdigit() or int(value) < 1:
            raise HyprlandError("Unsafe workspace number")
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

    def move_to_workspace(self, window: dict | str, workspace_name: str) -> None:
        if not workspace_name:
            raise HyprlandError("Unsafe workspace name")
        selector = self.lua_string(self.selector(window))
        workspace = self.lua_string(workspace_name)
        self.lua_dispatch(
            f"hl.dsp.window.move({{workspace={workspace},follow=false,window={selector}}})"
        )

    def exec_on_workspace(
        self, command: list[str], workspace_name: str, *, cwd: str | None = None
    ) -> None:
        """Launch silently on a workspace without forcing persistent float state."""
        if not workspace_name:
            raise HyprlandError("Unsafe workspace name")
        launch_command = list(command)
        if cwd:
            launch_command = ["env", f"--chdir={Path(cwd).expanduser().resolve()}", *launch_command]
        command_text = shlex.join(launch_command)
        workspace_rule = f"{workspace_name} silent"
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
            width_command = self.lua_string(
                f"colresize {column['width']:.6f}"
            )
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
                    desired = max(1, round(float(fraction) * secondary_extent))
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
            f"if leading then local delta={desired + origin}-leading; if math.abs(delta)>=0.5 then run(hl.dsp.layout('move '..tostring(delta))) end end; "
            "if cursor then hl.dispatch(hl.dsp.cursor.move({x=cursor.x,y=cursor.y})) end"
        )
        self.repl(code)

    def set_workspace_layout(self, workspace_name: str, layout: dict) -> None:
        if not workspace_name:
            raise HyprlandError("Unsafe workspace name")
        layout_name = str(layout["name"])
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
