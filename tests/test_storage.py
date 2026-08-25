import json
import os
import tempfile
import unittest
from pathlib import Path

from workspace_presets.errors import ValidationError
from workspace_presets.storage import PresetStore


class PresetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "presets.json"
        self.store = PresetStore(self.path)
        self.snapshot = {
            "layout": {"name": "monocle"},
            "windows": [
                {
                    "id": "slot-1",
                    "match": {"class": "foot", "title": "Terminal"},
                    "launcher": None,
                }
            ],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_and_atomic_permissions(self):
        saved = self.store.save_snapshot("Coding", self.snapshot)
        self.assertEqual(self.store.get(saved["id"])["name"], "Coding")
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")
        parsed = json.loads(self.path.read_text())
        self.assertEqual(parsed["schemaVersion"], 1)

    def test_names_are_case_insensitively_unique(self):
        self.store.save_snapshot("Coding", self.snapshot)
        with self.assertRaises(ValidationError):
            self.store.save_snapshot(" coding ", self.snapshot)

    def test_rename_overwrite_and_delete_preserve_identity(self):
        saved = self.store.save_snapshot("One", self.snapshot)
        renamed = self.store.rename(saved["id"], "Two")
        replacement = {"layout": {"name": "dwindle"}, "windows": []}
        overwritten = self.store.save_snapshot("Two", replacement, overwrite_id=saved["id"])
        self.assertEqual(overwritten["id"], saved["id"])
        self.assertEqual(overwritten["createdAt"], saved["createdAt"])
        self.assertEqual(overwritten["snapshot"], replacement)
        self.assertEqual(self.store.delete(saved["id"])["name"], renamed["name"])
        self.assertEqual(self.store.list_summaries(), [])

    def test_launcher_validation_controls_loadability(self):
        saved = self.store.save_snapshot("Draft", self.snapshot)
        self.assertFalse(self.store.list_summaries()[0]["loadable"])
        updated = self.store.set_launcher(
            saved["id"], "slot-1", {"kind": "desktop", "desktopId": "foot.desktop"}
        )
        self.assertTrue(self.store.public_summary(updated)["loadable"])
        with self.assertRaises(ValidationError):
            self.store.set_launcher(saved["id"], "slot-1", {"kind": "command", "argv": []})


if __name__ == "__main__":
    unittest.main()
