import unittest

from workspace_presets.layouts import (
    Rect,
    capture_layout,
    denormalized_geometry,
    dwindle_replay,
    infer_dwindle,
    normalized_geometry,
)


class LayoutTests(unittest.TestCase):
    def test_dwindle_tree_and_replay_keep_every_leaf(self):
        items = [
            {"slotId": "a", "rect": Rect(10, 10, 480, 980)},
            {"slotId": "b", "rect": Rect(510, 10, 480, 480)},
            {"slotId": "c", "rect": Rect(510, 510, 480, 480)},
        ]
        tree = infer_dwindle(items)
        self.assertEqual(tree["axis"], "x")
        operations = dwindle_replay(tree)
        self.assertEqual({item["slotId"] for item in operations}, {"a", "b", "c"})
        self.assertEqual(operations[1]["direction"], "r")

    def test_floating_geometry_uses_exact_pixels_on_same_workarea(self):
        source = {"x": 0, "y": 30, "width": 1900, "height": 1050, "scale": 1.0}
        rect = Rect(100, 130, 800, 600)
        geometry = {"pixels": rect.public(), "normalized": normalized_geometry(rect, source)}
        self.assertEqual(denormalized_geometry(geometry, source, source), rect.public())

    def test_floating_geometry_normalizes_and_clamps_on_new_monitor(self):
        source = {"x": 0, "y": 0, "width": 1000, "height": 1000, "scale": 1.0}
        target = {"x": 1920, "y": 20, "width": 500, "height": 400, "scale": 2.0}
        rect = Rect(500, 500, 750, 750)
        geometry = {"pixels": rect.public(), "normalized": normalized_geometry(rect, source)}
        restored = denormalized_geometry(geometry, source, target)
        self.assertGreaterEqual(restored["x"], target["x"])
        self.assertLessEqual(restored["x"] + restored["width"], target["x"] + target["width"])

    def test_master_and_scrolling_metadata_are_preserved(self):
        targets = [
            {"slotId": "a", "stableId": "1", "rect": Rect(0, 0, 500, 1000)},
            {"slotId": "b", "stableId": "2", "rect": Rect(500, 0, 500, 500)},
            {"slotId": "c", "stableId": "3", "rect": Rect(500, 500, 500, 500)},
        ]
        master = capture_layout(
            "master",
            targets,
            {
                "1": {"isMaster": True, "percMaster": 0.6, "percSize": 1.0},
                "2": {"isMaster": False, "percSize": 0.5},
                "3": {"isMaster": False, "percSize": 0.5},
            },
            options={"orientation": "left"},
        )
        self.assertEqual(master["masters"], ["a"])
        self.assertEqual(master["stack"], ["b", "c"])
        scrolling = capture_layout(
            "scrolling",
            targets,
            {
                "1": {"columnIndex": 0, "columnWidth": 0.4, "indexInColumn": 0},
                "2": {"columnIndex": 1, "columnWidth": 0.6, "indexInColumn": 0},
                "3": {"columnIndex": 1, "columnWidth": 0.6, "indexInColumn": 1},
            },
            options={
                "direction": "right",
                "primaryExtent": 1000,
                "secondaryExtent": 1000,
                "tapeOffset": -250,
            },
        )
        self.assertEqual(scrolling["columns"][1]["slots"], ["b", "c"])
        self.assertEqual(scrolling["columns"][1]["sizes"], {"b": 0.5, "c": 0.5})
        self.assertEqual(scrolling["tapeOffsetNormalized"], -0.25)


if __name__ == "__main__":
    unittest.main()
