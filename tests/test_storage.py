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
        self.assertEqual(parsed["presetGroups"], [])
        self.assertIsNone(parsed["startupGroupId"])
        self.assertFalse(parsed["confirmStartupLaunch"])
        self.assertEqual(parsed["presets"][0]["useCount"], 0)
        self.assertEqual(parsed["presets"][0]["lastUsedAt"], "")

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
        updated = self.store.set_launcher(
            saved["id"], "slot-1",
            {"kind": "omarchy-plugin", "pluginId": "quickshell.spotify"},
        )
        self.assertTrue(self.store.public_summary(updated)["loadable"])
        updated = self.store.set_launcher(
            saved["id"], "slot-1",
            {
                "kind": "command",
                "argv": ["foot", "-e", "herdr"],
                "cwd": "/home/blake",
            },
        )
        self.assertEqual(
            updated["snapshot"]["windows"][0]["launcher"]["cwd"],
            "/home/blake",
        )
        with self.assertRaises(ValidationError):
            self.store.set_launcher(
                saved["id"], "slot-1",
                {"kind": "command", "argv": ["foot"], "cwd": "relative/path"},
            )
        with self.assertRaises(ValidationError):
            self.store.set_launcher(
                saved["id"], "slot-1",
                {"kind": "omarchy-plugin", "pluginId": "bad plugin id"},
            )

    def test_group_management_and_startup_selection(self):
        preset = self.store.save_snapshot("Ready", self.snapshot)
        self.store.set_launcher(
            preset["id"], "slot-1", {"kind": "desktop", "desktopId": "foot.desktop"}
        )
        group = self.store.save_group(
            "Workday", [{"presetId": preset["id"], "workspace": 2}]
        )
        self.store.set_startup_group(group["id"])
        self.store.set_startup_confirmation(True)
        summary = self.store.list_group_summaries()[0]
        self.assertTrue(summary["loadable"])
        self.assertTrue(summary["launchOnStartup"])
        self.assertTrue(summary["confirmOnStartup"])
        self.assertTrue(self.store.startup_settings()["confirmStartupLaunch"])
        self.assertEqual(summary["assignments"][0]["presetName"], "Ready")
        with self.assertRaisesRegex(ValidationError, "used by group"):
            self.store.delete(preset["id"])
        self.store.delete_group(group["id"])
        self.assertIsNone(self.store.startup_group_id())
        self.assertFalse(self.store.startup_settings()["confirmStartupLaunch"])

    def test_startup_confirmation_requires_a_selected_group(self):
        with self.assertRaisesRegex(ValidationError, "Select a preset group"):
            self.store.set_startup_confirmation(True)

    def test_group_rejects_duplicate_workspace_and_incomplete_startup(self):
        one = self.store.save_snapshot("One", self.snapshot)
        two = self.store.save_snapshot("Two", self.snapshot)
        with self.assertRaisesRegex(ValidationError, "one preset to each workspace"):
            self.store.save_group("Broken", [
                {"presetId": one["id"], "workspace": 3},
                {"presetId": two["id"], "workspace": 3},
            ])
        draft = self.store.save_group("Draft group", [])
        with self.assertRaisesRegex(ValidationError, "complete, loadable"):
            self.store.set_startup_group(draft["id"])

    def test_group_workspaces_are_limited_to_zero_through_nine(self):
        one = self.store.save_snapshot("One", self.snapshot)
        zero = self.store.save_group(
            "Zero", [{"presetId": one["id"], "workspace": 0}]
        )
        self.assertEqual(zero["assignments"][0]["workspace"], 0)
        for workspace in (-1, 10):
            with self.subTest(workspace=workspace):
                with self.assertRaisesRegex(ValidationError, "0 to 9"):
                    self.store.save_group(
                        f"Invalid {workspace}",
                        [{"presetId": one["id"], "workspace": workspace}],
                    )

    def test_usage_metadata_is_backward_compatible_and_recorded(self):
        preset = self.store.save_snapshot("Used preset", self.snapshot)
        group = self.store.save_group("Used group", [])
        preset_summary = self.store.public_summary(self.store.record_preset_use(preset["id"]))
        group_summary = self.store.public_group_summary(
            self.store.record_group_use(group["id"]), {}, None
        )
        self.assertEqual(preset_summary["useCount"], 1)
        self.assertTrue(preset_summary["lastUsedAt"])
        self.assertEqual(group_summary["useCount"], 1)
        self.assertTrue(group_summary["lastUsedAt"])


if __name__ == "__main__":
    unittest.main()
