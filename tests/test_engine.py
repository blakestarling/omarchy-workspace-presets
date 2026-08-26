import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_presets.desktop import OmarchyPanelPlugin
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
    def active_workspace(self): return self.active_context()["workspace"]
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
    def focus_for_layout(self, window): self.actions.append(("layout-focus", window["stableId"]))
    def layout_message(self, command): self.actions.append(("layout-message", command))
    def focus_workspace(self, workspace): self.actions.append(("focus-workspace", str(workspace)))


class EngineRestoreTests(unittest.TestCase):
    @staticmethod
    def _single_window_snapshot():
        return {
            "source": {"workarea": {}},
            "layout": {"name": "monocle", "order": ["slot"]},
            "windows": [{
                "id": "slot",
                "match": {"class": "foot", "initialClass": "foot", "title": "Terminal"},
                "launcher": {"kind": "command", "argv": ["true"]},
                "geometry": {"pixels": {}, "normalized": {}},
                "state": {"floating": False},
            }],
            "groups": [],
            "finalFocusSlotId": "slot",
        }

    def test_preflight_only_requires_confirmation_for_current_workspace_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(Path(directory) / "presets.json")
            preset = store.save_snapshot("Safe", self._single_window_snapshot())
            fake = FakeHyprland()
            engine = WorkspaceEngine(store=store, hyprland=fake)
            engine.capabilities = lambda: {"ready": True}

            self.assertTrue(engine.preflight(preset["id"])["requiresConfirmation"])
            fake.current = None
            fake.spawned.append({
                "address": "0x2",
                "stableId": "2",
                "mapped": True,
                "workspace": {"id": 8, "name": "8"},
                "class": "foot",
                "initialClass": "foot",
                "title": "Terminal",
                "floating": False,
            })
            check = engine.preflight(preset["id"])
            self.assertEqual(len(check["conflicts"]), 1)
            self.assertFalse(check["requiresConfirmation"])

    def test_group_capture_maps_lua_hex_stable_ids_to_slots(self):
        metadata = {
            "1800000a": {
                "groupMembers": ["1800000a", "1800000b"],
                "groupCurrentIndex": 2,
                "groupLocked": True,
            },
            "1800000b": {
                "groupMembers": ["1800000a", "1800000b"],
                "groupCurrentIndex": 2,
                "groupLocked": True,
            },
        }
        groups, membership = WorkspaceEngine._capture_groups(
            metadata,
            {"1800000a": "slot-a", "1800000b": "slot-b"},
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["members"], ["slot-a", "slot-b"])
        self.assertEqual(groups[0]["activeSlotId"], "slot-b")
        self.assertTrue(groups[0]["locked"])
        self.assertEqual(membership["1800000a"], groups[0]["id"])

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
                result = engine.load(
                    preset["id"], expected_workspace_id=1, launch_timeout=0.2
                )
            self.assertEqual(result["windowCount"], 1)
            self.assertIn(("close", "1"), fake.actions)
            self.assertIn(("float", "2", True), fake.actions)
            self.assertIn(("float", "2", False), fake.actions)

    def test_restore_aborts_before_close_if_workspace_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(Path(directory) / "presets.json")
            snapshot = {
                "source": {"workarea": {}},
                "layout": {"name": "monocle", "order": ["slot"]},
                "windows": [{
                    "id": "slot",
                    "match": {"class": "foot"},
                    "launcher": {"kind": "command", "argv": ["true"]},
                    "geometry": {"pixels": {}, "normalized": {}},
                    "state": {"floating": False},
                }],
                "groups": [],
                "finalFocusSlotId": "slot",
            }
            preset = store.save_snapshot("Guarded", snapshot)
            fake = FakeHyprland()
            engine = WorkspaceEngine(store=store, hyprland=fake)
            engine.capabilities = lambda: {"ready": True}

            with self.assertRaisesRegex(Exception, "active workspace changed"):
                engine.load(preset["id"], expected_workspace_id=9)
            self.assertNotIn(("close", "1"), fake.actions)

    def test_group_preflight_validates_all_targets_and_uses_stale_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(Path(directory) / "presets.json")
            first = store.save_snapshot("First", self._single_window_snapshot())
            second = store.save_snapshot("Second", self._single_window_snapshot())
            group = store.save_group("Workday", [
                {"presetId": first["id"], "workspace": 2},
                {"presetId": second["id"], "workspace": 4},
            ])
            fake = FakeHyprland()
            fake.current["workspace"] = {"id": 2, "name": "2"}
            engine = WorkspaceEngine(store=store, hyprland=fake)
            engine.capabilities = lambda: {"ready": True}

            check = engine.preflight_group(group["id"])
            self.assertEqual([item["workspace"]["id"] for item in check["targets"]], [2, 4])
            self.assertEqual(check["windowCountToClose"], 1)
            self.assertTrue(check["requiresConfirmation"])
            self.assertEqual(len(check["token"]), 64)

            with self.assertRaisesRegex(Exception, "changed after confirmation"):
                engine.load_group(group["id"], expected_token="stale")
            self.assertEqual(fake.actions, [])

    def test_group_workspace_zero_targets_omarchy_workspace_ten(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(Path(directory) / "presets.json")
            preset = store.save_snapshot("Zero key", self._single_window_snapshot())
            group = store.save_group("Number row", [
                {"presetId": preset["id"], "workspace": 0},
            ])
            fake = FakeHyprland()
            fake.current = None
            engine = WorkspaceEngine(store=store, hyprland=fake)
            engine.capabilities = lambda: {"ready": True}

            check = engine.preflight_group(group["id"])

            self.assertEqual(check["targets"][0]["workspace"], {
                "id": 10, "name": "10", "slot": 0,
            })

    def test_group_load_activates_workspace_ten_for_zero_key(self):
        class SwitchingHyprland(FakeHyprland):
            def __init__(self):
                super().__init__()
                self.active_id = 1

            def active_workspace(self):
                return {"id": self.active_id, "name": str(self.active_id)}

            def active_context(self):
                return {
                    "workspace": {
                        "id": self.active_id,
                        "name": str(self.active_id),
                        "tiledLayout": "monocle",
                    },
                    "monitor": {"name": "test"},
                    "workarea": {
                        "x": 0, "y": 0, "width": 1000, "height": 800, "scale": 1,
                    },
                }

            def focus_workspace(self, workspace):
                self.active_id = int(workspace)
                super().focus_workspace(workspace)

        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(Path(directory) / "presets.json")
            preset = store.save_snapshot("Zero key", self._single_window_snapshot())
            group = store.save_group("Number row", [
                {"presetId": preset["id"], "workspace": 0},
            ])
            fake = SwitchingHyprland()
            fake.current = None
            engine = WorkspaceEngine(store=store, hyprland=fake)
            engine.capabilities = lambda: {"ready": True}

            def spawn(_launcher):
                fake.spawned.append({
                    "address": "0x2", "stableId": "2", "mapped": True,
                    "workspace": {"id": 1, "name": "1"},
                    "class": "foot", "initialClass": "foot",
                    "title": "Terminal", "floating": False,
                })

            with patch.object(engine, "_launch", side_effect=spawn):
                engine.load_group(group["id"])

            focus_actions = [item for item in fake.actions if item[0] == "focus-workspace"]
            self.assertEqual(focus_actions, [
                ("focus-workspace", "1"),
                ("focus-workspace", "10"),
                ("focus-workspace", "1"),
            ])

    def test_unrelated_window_classes_launch_in_the_same_wave(self):
        fake = FakeHyprland()
        fake.current = None
        engine = WorkspaceEngine(hyprland=fake)
        tasks = []
        for index, window_class in enumerate(("firefox", "code"), start=1):
            tasks.append({
                "key": str(index), "workspaceName": str(index),
                "slot": {
                    "id": str(index), "match": {"class": window_class},
                    "launcher": {"kind": "command", "argv": ["true"]},
                },
            })
        launches = []

        def spawn(_launcher):
            launches.append(len(fake.spawned) + 1)
            index = len(fake.spawned)
            window_class = ("firefox", "code")[index]
            fake.spawned.append({
                "address": f"0x{index + 2}", "stableId": str(index + 2),
                "mapped": True, "workspace": {"id": 1, "name": "1"},
                "class": window_class, "initialClass": window_class,
                "title": window_class, "floating": False,
            })

        with patch.object(engine, "_launch", side_effect=spawn):
            result = engine._materialize_slots(
                tasks, conflict_policy="launch-new", launch_timeout=0.2
            )

        self.assertEqual(launches, [1, 2])
        self.assertEqual(set(result), {"1", "2"})
        self.assertEqual(len(engine._launch_waves(tasks)), 1)

    def test_duplicate_window_classes_launch_in_separate_waves(self):
        engine = WorkspaceEngine(hyprland=FakeHyprland())
        tasks = [{
            "key": str(index), "workspaceName": str(index),
            "slot": {
                "id": str(index),
                "match": {"class": "foot", "initialClass": "foot"},
                "launcher": {"kind": "command", "argv": ["true"]},
            },
        } for index in (1, 2)]

        self.assertEqual([[task["key"] for task in wave] for wave in engine._launch_waves(tasks)], [
            ["1"], ["2"],
        ])

    def test_tiling_replay_anchors_each_window_in_saved_order(self):
        fake = FakeHyprland()
        engine = WorkspaceEngine(hyprland=fake)
        windows = {
            slot: {
                "stableId": stable, "address": f"0x{stable}", "floating": True,
            }
            for slot, stable in (("first", "11"), ("second", "12"), ("third", "13"))
        }

        engine._restore_tiling(
            {"name": "monocle", "order": ["first", "second", "third"]},
            windows,
        )

        self.assertEqual(fake.actions, [
            ("float", "11", False),
            ("focus", "11"),
            ("layout-focus", "11"),
            ("float", "12", False),
            ("focus", "12"),
            ("layout-focus", "12"),
            ("float", "13", False),
            ("focus", "13"),
        ])

    def test_scrolling_columns_use_synchronized_focus_before_consume(self):
        fake = FakeHyprland()
        engine = WorkspaceEngine(hyprland=fake)
        windows = {
            slot: {"stableId": stable, "address": f"0x{stable}"}
            for slot, stable in (("first", "11"), ("second", "12"))
        }

        engine._restore_tiling(
            {
                "name": "scrolling",
                "order": ["first", "second"],
                "columns": [{
                    "slots": ["first", "second"],
                    "width": 0.5,
                }],
            },
            windows,
        )

        consume_index = fake.actions.index(("layout-message", "consume"))
        self.assertEqual(fake.actions[consume_index - 1], ("layout-focus", "11"))

    def test_omarchy_panel_launcher_uses_shell_summon_ipc(self):
        with patch("workspace_presets.engine.subprocess.Popen") as popen:
            WorkspaceEngine._launch({
                "kind": "omarchy-plugin", "pluginId": "quickshell.spotify"
            })
        command = popen.call_args.args[0]
        self.assertEqual(command, [
            "uwsm-app", "--", "omarchy-shell", "shell", "summon",
            "quickshell.spotify", "{}",
        ])

    def test_terminal_command_launcher_restores_working_directory(self):
        with patch("workspace_presets.engine.subprocess.Popen") as popen:
            WorkspaceEngine._launch({
                "kind": "command",
                "argv": ["foot", "-e", "herdr"],
                "cwd": "/home/blake",
            })

        self.assertEqual(
            popen.call_args.args[0],
            ["uwsm-app", "--", "foot", "-e", "herdr"],
        )
        self.assertEqual(popen.call_args.kwargs["cwd"], "/home/blake")

    def test_existing_draft_gets_automatic_panel_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(Path(directory) / "presets.json")
            snapshot = self._single_window_snapshot()
            snapshot["windows"][0]["match"] = {
                "class": "org.quickshell", "initialClass": "org.quickshell",
                "title": "Omarchy Spotify", "initialTitle": "Omarchy Spotify",
            }
            snapshot["windows"][0]["launcher"] = None
            preset = store.save_snapshot("Spotify", snapshot)
            engine = WorkspaceEngine(store=store, hyprland=FakeHyprland())
            panels = {
                "quickshell.spotify": OmarchyPanelPlugin(
                    "quickshell.spotify", "Omarchy Spotify", "/manifest.json"
                )
            }
            with (
                patch("workspace_presets.engine.scan_desktop_entries", return_value={}),
                patch("workspace_presets.engine.scan_omarchy_panel_plugins", return_value=panels),
            ):
                result = engine.resolve_unresolved_launchers()
            self.assertEqual(result["resolvedWindowCount"], 1)
            self.assertEqual(
                store.get(preset["id"])["snapshot"]["windows"][0]["launcher"],
                {"kind": "omarchy-plugin", "pluginId": "quickshell.spotify"},
            )

    def test_manual_panel_summon_is_normalized_to_typed_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(Path(directory) / "presets.json")
            snapshot = self._single_window_snapshot()
            snapshot["windows"][0]["match"] = {
                "class": "org.quickshell", "title": "Omarchy Spotify",
            }
            snapshot["windows"][0]["launcher"] = {
                "kind": "command",
                "argv": ["omarchy-shell", "shell", "summon", "quickshell.spotify", "{}"],
            }
            preset = store.save_snapshot("Spotify", snapshot)
            engine = WorkspaceEngine(store=store, hyprland=FakeHyprland())
            panels = {
                "quickshell.spotify": OmarchyPanelPlugin(
                    "quickshell.spotify", "Omarchy Spotify", "/manifest.json"
                )
            }
            with (
                patch("workspace_presets.engine.scan_desktop_entries", return_value={}),
                patch("workspace_presets.engine.scan_omarchy_panel_plugins", return_value=panels),
            ):
                result = engine.resolve_unresolved_launchers()
            self.assertEqual(result["normalizedLauncherCount"], 1)
            self.assertEqual(
                store.get(preset["id"])["snapshot"]["windows"][0]["launcher"],
                {"kind": "omarchy-plugin", "pluginId": "quickshell.spotify"},
            )


if __name__ == "__main__":
    unittest.main()
