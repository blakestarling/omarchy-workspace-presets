import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_presets.desktop import (
    OmarchyPanelPlugin,
    resolve_launcher,
    scan_desktop_entries,
    scan_omarchy_panel_plugins,
    terminal_process_launcher,
)


class DesktopEntryTests(unittest.TestCase):
    def test_terminal_program_and_working_directory_become_launch_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Path(directory) / "42"
            process.mkdir()
            (process / "cmdline").write_bytes(
                b"foot\0--app-id=TUI.tile\0-e\0omarchy-launch-docker-tui\0"
            )
            os.symlink("/home/blake", process / "cwd")

            result = terminal_process_launcher(42, "foot", proc_root=Path(directory))

            self.assertEqual(result, ({
                "kind": "command",
                "argv": [
                    "foot", "--app-id=TUI.tile", "-e", "omarchy-launch-docker-tui",
                ],
                "cwd": "/home/blake",
            }, "omarchy-launch-docker-tui"))

    def test_plain_shell_terminal_keeps_normal_desktop_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Path(directory) / "7"
            process.mkdir()
            (process / "cmdline").write_bytes(b"foot\0--working-directory=/tmp\0")
            os.symlink("/tmp", process / "cwd")

            self.assertIsNone(
                terminal_process_launcher(7, "foot", proc_root=Path(directory))
            )

    def test_exact_startup_class_is_resolved(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = Path(directory) / "applications"
            applications.mkdir()
            (applications / "editor.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Editor\nExec=editor %F\nStartupWMClass=Code\n"
            )
            with patch("workspace_presets.desktop._data_dirs", return_value=[Path(directory)]):
                entries = scan_desktop_entries()
            launcher, candidates = resolve_launcher(
                {"class": "code", "initialClass": "Code", "executable": "editor"}, entries
            )
            self.assertEqual(launcher, {"kind": "desktop", "desktopId": "editor.desktop"})
            self.assertGreaterEqual(candidates[0]["score"], 120)

    def test_ambiguous_matches_remain_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = Path(directory) / "applications"
            applications.mkdir()
            for name in ("first", "second"):
                (applications / f"{name}.desktop").write_text(
                    f"[Desktop Entry]\nType=Application\nName={name}\nExec=shared\nStartupWMClass=Shared\n"
                )
            with patch("workspace_presets.desktop._data_dirs", return_value=[Path(directory)]):
                entries = scan_desktop_entries()
            launcher, candidates = resolve_launcher(
                {"class": "Shared", "initialClass": "Shared", "executable": "shared"}, entries
            )
            self.assertIsNone(launcher)
            self.assertEqual(len(candidates), 2)

    def test_omarchy_panel_manifest_resolves_quickshell_window(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = Path(directory) / "quickshell.spotify"
            plugin.mkdir()
            (plugin / "manifest.json").write_text(
                '{"id":"quickshell.spotify","name":"Omarchy Spotify",'
                '"kinds":["service","bar-widget","panel"]}'
            )
            panels = scan_omarchy_panel_plugins([Path(directory)])
            launcher, candidates = resolve_launcher(
                {
                    "class": "org.quickshell", "initialClass": "org.quickshell",
                    "title": "Omarchy Spotify", "initialTitle": "Omarchy Spotify",
                },
                {}, panels,
            )
            self.assertEqual(launcher, {
                "kind": "omarchy-plugin", "pluginId": "quickshell.spotify"
            })
            self.assertEqual(candidates, [])

    def test_quickshell_window_without_unique_panel_name_stays_unresolved(self):
        panels = {
            "one": OmarchyPanelPlugin("one", "Shared", "/one"),
            "two": OmarchyPanelPlugin("two", "Shared", "/two"),
        }
        launcher, _ = resolve_launcher(
            {"class": "org.quickshell", "title": "Shared"}, {}, panels
        )
        self.assertIsNone(launcher)


if __name__ == "__main__":
    unittest.main()
