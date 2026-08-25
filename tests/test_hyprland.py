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


if __name__ == "__main__":
    unittest.main()
