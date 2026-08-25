import unittest

from workspace_presets.hyprland import Hyprland


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

    def test_dispatch_result_failures_are_promoted_to_hyprctl_errors(self):
        hypr = RecordingHyprland()
        hypr.close({"stableId": "7"})

        lua = hypr.calls[0][0][2]
        self.assertIn("r.ok==false", lua)
        self.assertIn("Workspace Presets dispatch failed", lua)


if __name__ == "__main__":
    unittest.main()
