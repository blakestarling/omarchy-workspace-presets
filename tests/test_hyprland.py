import unittest
from unittest.mock import patch

from workspace_presets.hyprland import Hyprland
from workspace_presets.errors import HyprlandError


class ContextHyprland(Hyprland):
    def active_workspace(self):
        return {"id": 1, "name": "1", "monitorID": 0, "monitor": "eDP-1"}

    def monitors(self):
        return [{
            "id": 0,
            "name": "eDP-1",
            "width": 1920,
            "height": 1200,
            "x": 0,
            "y": 0,
            "reserved": [0, 26, 0, 0],
            "scale": 1.25,
            "transform": 0,
            "focused": True,
        }]


class HyprlandContextTests(unittest.TestCase):
    def test_monitor_mode_pixels_become_logical_workarea(self):
        context = ContextHyprland().active_context()
        self.assertEqual(context["workarea"]["width"], 1536)
        self.assertEqual(context["workarea"]["height"], 934)
        self.assertEqual(context["workarea"]["y"], 26)


class RecordingHyprland(Hyprland):
    def __init__(self):
        super().__init__()
        self.calls = []

    def _run(self, args, *, json_result=False, check=True):
        self.calls.append((args, json_result, check))
        return "ok"


class HyprlandLuaDispatcherTests(unittest.TestCase):
    def test_layout_focus_waits_until_hyprland_reports_the_target(self):
        class DelayedFocusHyprland(Hyprland):
            def __init__(self):
                super().__init__()
                self.focused = None
                self.reads = 0

            def focus(self, window):
                self.focused = window

            def active_window(self):
                self.reads += 1
                return {"stableId": "old" if self.reads == 1 else "42"}

        hypr = DelayedFocusHyprland()
        with patch("workspace_presets.hyprland.time.sleep"):
            hypr.focus_for_layout({"stableId": "42"})

        self.assertEqual(hypr.focused, {"stableId": "42"})
        self.assertEqual(hypr.reads, 2)

    def test_only_positive_hyprland_workspace_ids_are_safe_focus_targets(self):
        hypr = RecordingHyprland()

        hypr.focus_workspace(10)

        self.assertIn('hl.dsp.focus({workspace="10"})', hypr.calls[0][0][2])
        for workspace in (0, -1):
            with self.subTest(workspace=workspace):
                with self.assertRaises(HyprlandError):
                    hypr.focus_workspace(workspace)

    def test_window_operations_use_typed_lua_dispatchers(self):
        hypr = RecordingHyprland()
        window = {"stableId": "42", "address": "0x123"}

        hypr.focus(window)
        hypr.close(window)
        hypr.set_floating(window, True)
        hypr.move_to_workspace(window, "dev's workspace")
        hypr.move_resize(window, {"x": 12, "y": 34, "width": 800, "height": 600})

        commands = "\n".join(call[0][2] for call in hypr.calls)
        self.assertIn("hl.dsp.focus({window=\"stableid:42\"})", commands)
        self.assertIn("hl.dsp.window.close({window=\"stableid:42\"})", commands)
        self.assertIn("hl.dsp.window.float({action=\"set\"", commands)
        self.assertIn("workspace=\"dev's workspace\"", commands)
        self.assertIn("hl.dsp.window.resize({x=800,y=600", commands)
        self.assertNotIn("hyprctl dispatch", commands)

    def test_workspace_layout_uses_eval_not_legacy_keyword(self):
        hypr = RecordingHyprland()
        hypr.set_workspace_layout("3", {"name": "master", "orientation": "right"})

        args = hypr.calls[0][0]
        self.assertEqual(args[:2], ["hyprctl", "eval"])
        self.assertIn('workspace="name:3"', args[2])
        self.assertIn('layout="master"', args[2])
        self.assertIn('orientation="right"', args[2])

    def test_exec_routes_new_windows_to_a_workspace_silently(self):
        hypr = RecordingHyprland()

        hypr.exec_on_workspace(
            ["uwsm-app", "--", "foot", "-e", "herdr"],
            "8",
            cwd="/home/blake/My Project",
        )

        lua = hypr.calls[0][0][2]
        self.assertIn("hl.exec_cmd(", lua)
        self.assertIn('workspace="8 silent"', lua)
        self.assertNotIn("float=", lua)
        self.assertIn("no_initial_focus=true", lua)
        self.assertIn("--chdir=/home/blake/My Project", lua)
        self.assertIn("foot -e herdr", lua)

    def test_dispatch_result_failures_are_promoted_to_hyprctl_errors(self):
        hypr = RecordingHyprland()
        hypr.close({"stableId": "7"})

        lua = hypr.calls[0][0][2]
        self.assertIn("r.ok==false", lua)
        self.assertIn("Workspace Presets dispatch failed", lua)


if __name__ == "__main__":
    unittest.main()
