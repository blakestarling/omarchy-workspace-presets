import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from workspace_presets import cli
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


if __name__ == "__main__":
    unittest.main()
