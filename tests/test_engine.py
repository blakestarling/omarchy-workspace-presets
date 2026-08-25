import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_presets.engine import WorkspaceEngine
from workspace_presets.storage import PresetStore


class FakeHyprland:
    def __init__(self):
        self.current = {
            "address": "0x1",
            "stableId": "1",
            "mapped": True,
            "workspace": {"id": 1, "name": "1"},
            "class": "old",
            "initialClass": "old",
            "title": "Old window",
            "floating": False,
        }
        self.spawned = []
        self.actions = []

    def version(self): return {"version": "0.56.2"}
    def active_context(self):
        return {
            "workspace": {"id": 1, "name": "1", "tiledLayout": "monocle"},
            "monitor": {"name": "test"},
            "workarea": {"x": 0, "y": 0, "width": 1000, "height": 800, "scale": 1},
        }
    def workspace_clients(self, workspace_id):
        return [item for item in self.clients() if item["workspace"]["id"] == workspace_id]
    def clients(self): return ([self.current] if self.current else []) + self.spawned
    def close(self, window): self.current = None; self.actions.append(("close", window["stableId"]))
    def wait_until_closed(self, stable_ids, timeout): return set()
    def set_workspace_layout(self, name, layout): self.actions.append(("layout", name, layout["name"]))
    def move_to_workspace(self, window, name): window["workspace"] = {"id": 1, "name": name}
    def set_floating(self, window, value): window["floating"] = value; self.actions.append(("float", window["stableId"], value))
    def find_window(self, stable_id): return next((item for item in self.clients() if item["stableId"] == stable_id), None)
    def create_group(self, *args, **kwargs): self.actions.append(("group",))
    def apply_window_state(self, window, state): self.actions.append(("state", window["stableId"]))
    def focus(self, window): self.actions.append(("focus", window["stableId"]))


class EngineRestoreTests(unittest.TestCase):
    def test_cold_restore_launches_a_missing_window(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(Path(directory) / "presets.json")
            snapshot = {
                "source": {"workarea": {"x": 0, "y": 0, "width": 1000, "height": 800, "scale": 1}},
                "layout": {"name": "monocle", "order": ["slot"]},
                "windows": [{
                    "id": "slot",
                    "match": {"class": "foot", "initialClass": "foot", "title": "Terminal"},
                    "launcher": {"kind": "command", "argv": ["true"]},
                    "geometry": {
                        "pixels": {"x": 0, "y": 0, "width": 1000, "height": 800},
                        "normalized": {"x": 0, "y": 0, "width": 1, "height": 1},
                    },
                    "state": {"floating": False},
                }],
                "groups": [],
                "finalFocusSlotId": "slot",
            }
            preset = store.save_snapshot("Cold", snapshot)
            fake = FakeHyprland()
            engine = WorkspaceEngine(store=store, hyprland=fake)
            engine.capabilities = lambda: {"ready": True}

            def spawn(_launcher):
                fake.spawned.append({
                    "address": "0x2",
                    "stableId": "2",
                    "mapped": True,
                    "workspace": {"id": 1, "name": "1"},
                    "class": "foot",
                    "initialClass": "foot",
                    "title": "Terminal",
                    "floating": False,
                })

            with patch.object(engine, "_launch", side_effect=spawn):
                result = engine.load(preset["id"], launch_timeout=0.2)
            self.assertEqual(result["windowCount"], 1)
            self.assertIn(("close", "1"), fake.actions)
            self.assertIn(("float", "2", True), fake.actions)
            self.assertIn(("float", "2", False), fake.actions)


if __name__ == "__main__":
    unittest.main()
