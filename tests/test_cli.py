import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from workspace_presets import cli
from workspace_presets.errors import LaunchError, UnsupportedError
from workspace_presets.hyprland import Hyprland
from workspace_presets.storage import PresetStore


class StartupGroupCliTests(unittest.TestCase):
    def test_confirmed_startup_returns_preflight_without_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_home = root / "config"
            runtime = root / "runtime"
            runtime.mkdir()
            store = PresetStore(
                config_home / "omarchy-workspace-presets" / "presets.json"
            )
            snapshot = {
                "layout": {"name": "monocle"},
                "windows": [{
                    "id": "slot-1",
                    "match": {"class": "foot", "title": "Terminal"},
                    "launcher": {"kind": "desktop", "desktopId": "foot.desktop"},
                }],
            }
            preset = store.save_snapshot("Ready", snapshot)
            group = store.save_group(
                "Workday", [{"presetId": preset["id"], "workspace": 1}]
            )
            store.set_startup_group(group["id"])
            store.set_startup_confirmation(True)

            engine = Mock()
            engine.await_stable_workarea.return_value = True
            engine.preflight_group.return_value = {
                "kind": "group",
                "group": {"id": group["id"], "name": "Workday"},
                "token": "safe-token",
                "targets": [],
                "windowCountToClose": 0,
            }
            environment = {
                "XDG_CONFIG_HOME": str(config_home),
                "XDG_RUNTIME_DIR": str(runtime),
                "HYPRLAND_INSTANCE_SIGNATURE": "startup-confirmation-test",
            }
            output = io.StringIO()
            with (
                patch.dict(os.environ, environment),
                patch("workspace_presets.engine.WorkspaceEngine", return_value=engine),
                redirect_stdout(output),
            ):
                self.assertEqual(cli.main(["startup-group"]), 0)

            event = json.loads(output.getvalue())
            self.assertTrue(event["data"]["confirmationRequired"])
            self.assertTrue(event["data"]["preflight"]["startupConfirmation"])
            engine.preflight_group.assert_called_once_with(group["id"])
            engine.load_group.assert_not_called()

    def _session(self, root: Path):
        """Build a store with a loadable startup group and the session env."""
        config_home = root / "config"
        runtime = root / "runtime"
        runtime.mkdir()
        store = PresetStore(config_home / "omarchy-workspace-presets" / "presets.json")
        snapshot = {
            "layout": {"name": "monocle"},
            "windows": [{
                "id": "slot-1",
                "match": {"class": "foot", "title": "Terminal"},
                "launcher": {"kind": "desktop", "desktopId": "foot.desktop"},
            }],
        }
        preset = store.save_snapshot("Ready", snapshot)
        group = store.save_group(
            "Workday", [{"presetId": preset["id"], "workspace": 1}]
        )
        store.set_startup_group(group["id"])
        environment = {
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_RUNTIME_DIR": str(runtime),
            "HYPRLAND_INSTANCE_SIGNATURE": "startup-marker-test",
        }
        marker_dir = runtime / "omarchy-workspace-presets"
        return store, group, environment, marker_dir

    def _run_startup(self, environment, engine):
        output = io.StringIO()
        with (
            patch.dict(os.environ, environment),
            patch("workspace_presets.engine.WorkspaceEngine", return_value=engine),
            redirect_stdout(output),
        ):
            code = cli.main(["startup-group"])
        return code, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_a_readiness_failure_releases_the_once_per_session_marker(self):
        """A session that was not ready yet must stay retryable.

        The marker is taken before the attempt so two services racing at login
        cannot both launch, but preflight closes and launches nothing, so a
        failure inside it must not suppress the startup group for the session.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _store, _group, environment, marker_dir = self._session(root)

            failing = Mock()
            failing.await_stable_workarea.return_value = True
            failing.preflight_group.side_effect = UnsupportedError(
                "This system does not meet the plugin requirements"
            )
            code, events = self._run_startup(environment, failing)

            self.assertEqual(code, 2)
            self.assertEqual(events[-1]["type"], "error")
            self.assertEqual(list(marker_dir.glob("*.startup")), [])

            # The retry finds the marker gone and gets a real attempt.
            ready = Mock()
            ready.await_stable_workarea.return_value = True
            ready.load_group.return_value = {"name": "Workday", "workspaceCount": 1}
            code, events = self._run_startup(environment, ready)

            self.assertEqual(code, 0)
            self.assertTrue(events[-1]["data"]["launched"])
            ready.load_group.assert_called_once()
            self.assertEqual(len(list(marker_dir.glob("*.startup"))), 1)

    def test_a_failed_load_keeps_the_marker_so_nothing_relaunches(self):
        """Once windows may have been closed or launched, do not retry."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _store, _group, environment, marker_dir = self._session(root)

            engine = Mock()
            engine.await_stable_workarea.return_value = True
            engine.preflight_group.return_value = {"kind": "group", "token": "t"}
            engine.load_group.side_effect = LaunchError("foot did not appear")
            code, events = self._run_startup(environment, engine)

            self.assertEqual(code, 2)
            self.assertEqual(events[-1]["type"], "error")
            self.assertEqual(len(list(marker_dir.glob("*.startup"))), 1)

            second = Mock()
            second.await_stable_workarea.return_value = True
            _code, events = self._run_startup(environment, second)
            self.assertEqual(
                events[-1]["data"]["reason"], "already-attempted-this-session"
            )
            second.load_group.assert_not_called()

    def test_the_startup_launch_waits_for_monitor_geometry_to_settle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _store, _group, environment, _marker_dir = self._session(root)

            engine = Mock()
            engine.await_stable_workarea.return_value = False
            engine.preflight_group.return_value = {"kind": "group", "token": "t"}
            engine.load_group.return_value = {"name": "Workday", "workspaceCount": 1}
            _code, events = self._run_startup(environment, engine)

            engine.await_stable_workarea.assert_called_once_with()
            # The launch still proceeds; the caller is told it did not settle.
            self.assertFalse(events[-1]["data"]["workareaSettled"])
            self.assertTrue(
                engine.await_stable_workarea.call_count == 1
                and engine.load_group.called
            )

    def test_the_boot_path_allows_longer_for_applications_to_appear(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _store, _group, environment, _marker_dir = self._session(root)

            engine = Mock()
            engine.await_stable_workarea.return_value = True
            engine.preflight_group.return_value = {"kind": "group", "token": "t"}
            engine.load_group.return_value = {"name": "Workday", "workspaceCount": 1}
            self._run_startup(environment, engine)

            _args, kwargs = engine.load_group.call_args
            self.assertGreater(kwargs["launch_timeout"], 12.0)


class StableWorkareaTests(unittest.TestCase):
    def test_two_matching_reads_settle_and_a_changing_one_times_out(self):
        readings = [
            [{"name": "eDP-1", "reserved": [0, 0, 0, 0], "width": 1920}],
            [{"name": "eDP-1", "reserved": [0, 22, 0, 0], "width": 1920}],
            [{"name": "eDP-1", "reserved": [0, 22, 0, 0], "width": 1920}],
        ]

        class SettlingHyprland(Hyprland):
            def monitors(self):
                return readings.pop(0) if len(readings) > 1 else readings[0]

        self.assertTrue(
            SettlingHyprland().await_stable_monitors(timeout=2.0, settle=0.01)
        )

        class ChurningHyprland(Hyprland):
            def __init__(self):
                super().__init__()
                self.width = 0

            def monitors(self):
                self.width += 1
                return [{"name": "eDP-1", "reserved": [0, 22, 0, 0], "width": self.width}]

        self.assertFalse(
            ChurningHyprland().await_stable_monitors(timeout=0.1, settle=0.01)
        )


class MultiMonitorCliTests(unittest.TestCase):
    def test_capture_forwards_the_multi_monitor_flag(self):
        engine = Mock()
        engine.capture.return_value = {"id": "preset"}
        with (
            patch("workspace_presets.cli.PresetStore"),
            patch("workspace_presets.engine.WorkspaceEngine", return_value=engine),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.main(["capture", "--name", "Desk", "--multi-monitor"]), 0)
        engine.capture.assert_called_once_with(
            "Desk", overwrite_id=None, multi_monitor=True
        )

    def test_capture_defaults_to_single_monitor(self):
        engine = Mock()
        engine.capture.return_value = {"id": "preset"}
        with (
            patch("workspace_presets.cli.PresetStore"),
            patch("workspace_presets.engine.WorkspaceEngine", return_value=engine),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.main(["capture", "--name", "Desk"]), 0)
        engine.capture.assert_called_once_with(
            "Desk", overwrite_id=None, multi_monitor=False
        )

    def test_load_forwards_confirmed_multi_monitor_workspaces(self):
        engine = Mock()
        engine.load.return_value = {"monitorCount": 2}
        with (
            patch("workspace_presets.cli.PresetStore"),
            patch("workspace_presets.engine.WorkspaceEngine", return_value=engine),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                cli.main([
                    "load", "--id", "preset",
                    "--expected-workspace-ids", "1,5",
                    "--expected-token", "token",
                    "--confirmed",
                ]),
                0,
            )
        engine.load.assert_called_once_with(
            "preset",
            expected_workspace_id=0,
            expected_workspace_ids=[1, 5],
            expected_token="token",
            conflict_policy="launch-new",
            close_timeout=8.0,
            launch_timeout=12.0,
        )

    def test_load_without_confirmed_workspaces_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(Path(directory) / "presets.json")
            store.save_snapshot(
                "Single",
                {
                    "layout": {"name": "monocle"},
                    "windows": [{
                        "id": "slot-1",
                        "match": {"class": "foot", "title": "Terminal"},
                        "launcher": {"kind": "desktop", "desktopId": "foot.desktop"},
                    }],
                },
            )
            output = io.StringIO()
            with (
                patch("workspace_presets.cli.PresetStore", return_value=store),
                redirect_stdout(output),
            ):
                code = cli.main([
                    "load", "--id", store.list_summaries()[0]["id"],
                    "--expected-token", "token",
                    "--confirmed",
                ])
            self.assertEqual(code, 2)
            event = json.loads(output.getvalue())
            self.assertEqual(event["type"], "error")
            self.assertIn("workspace id", event["message"])


if __name__ == "__main__":
    unittest.main()
