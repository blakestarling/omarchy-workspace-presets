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
    @staticmethod
    def _proc_process(
        root: Path, pid: int, *, ppid: int, pgrp: int, session: int,
        tty: int, tpgid: int, argv: list[str], cwd: str = "/home/blake",
    ) -> Path:
        process = root / str(pid)
        process.mkdir()
        (process / "cmdline").write_bytes(
            b"\0".join(part.encode() for part in argv) + b"\0"
        )
        (process / "stat").write_text(
            f"{pid} ({Path(argv[0]).name}) S {ppid} {pgrp} {session} {tty} {tpgid} 0 0 0\n"
        )
        os.symlink(cwd, process / "cwd")
        return process

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

    def test_manually_started_foreground_program_becomes_launch_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._proc_process(
                root, 7, ppid=1, pgrp=7, session=7, tty=0, tpgid=-1,
                argv=["foot", "--app-id=TUI.tile"],
            )
            self._proc_process(
                root, 8, ppid=7, pgrp=8, session=8, tty=34816, tpgid=9,
                argv=["/usr/bin/zsh"],
            )
            self._proc_process(
                root, 9, ppid=8, pgrp=9, session=8, tty=34816, tpgid=9,
                argv=["herdr", "--theme", "dark"], cwd="/tmp",
            )

            result = terminal_process_launcher(7, "foot", proc_root=root)

            self.assertEqual(result, ({
                "kind": "command",
                "argv": [
                    "foot", "--app-id=TUI.tile", "-e", "herdr", "--theme", "dark",
                ],
                "cwd": "/tmp",
            }, "herdr"))

    def test_idle_shell_is_not_mistaken_for_a_foreground_program(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._proc_process(
                root, 7, ppid=1, pgrp=7, session=7, tty=0, tpgid=-1,
                argv=["foot"],
            )
            self._proc_process(
                root, 8, ppid=7, pgrp=8, session=8, tty=34816, tpgid=8,
                argv=["-zsh"], cwd="/tmp",
            )

            self.assertIsNone(terminal_process_launcher(7, "foot", proc_root=root))

    def test_pipeline_is_left_unresolved_instead_of_partially_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._proc_process(
                root, 7, ppid=1, pgrp=7, session=7, tty=0, tpgid=-1,
                argv=["foot"],
            )
            self._proc_process(
                root, 8, ppid=7, pgrp=8, session=8, tty=34816, tpgid=9,
                argv=["zsh"],
            )
            self._proc_process(
                root, 9, ppid=8, pgrp=9, session=8, tty=34816, tpgid=9,
                argv=["journalctl", "-f"],
            )
            self._proc_process(
                root, 10, ppid=8, pgrp=9, session=8, tty=34816, tpgid=9,
                argv=["less"],
            )

            self.assertIsNone(terminal_process_launcher(7, "foot", proc_root=root))

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

    def test_omarchy_webapps_with_spaced_desktop_ids_resolve_from_url_class(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = Path(directory) / "applications"
            applications.mkdir()
            fixtures = {
                "WhatsApp.desktop": (
                    "WhatsApp", "https://web.whatsapp.com/",
                    {
                        "class": "chrome-web.whatsapp.com__-Default",
                        "initialClass": "chrome-web.whatsapp.com__-Default",
                        "title": "web.whatsapp.com",
                        "initialTitle": "web.whatsapp.com_/",
                        "executable": "chrome",
                    },
                ),
                "Google Messages.desktop": (
                    "Google Messages", "https://messages.google.com/web/conversations",
                    {
                        "class": "chrome-messages.google.com__web_conversations-Default",
                        "initialClass": "chrome-messages.google.com__web_conversations-Default",
                        "title": "Google Messages for web: Conversations",
                        "initialTitle": "messages.google.com_/web/conversations",
                        "executable": "chrome",
                    },
                ),
            }
            for filename, (name, url, _window) in fixtures.items():
                (applications / filename).write_text(
                    "[Desktop Entry]\nType=Application\n"
                    f"Name={name}\nExec=omarchy-launch-webapp {url}\n"
                )
            with patch("workspace_presets.desktop._data_dirs", return_value=[Path(directory)]):
                entries = scan_desktop_entries()

            self.assertEqual(set(entries), set(fixtures))
            for filename, (_name, url, window) in fixtures.items():
                with self.subTest(filename=filename):
                    launcher, candidates = resolve_launcher(window, entries)
                    self.assertEqual(launcher, {
                        "kind": "command",
                        "argv": ["omarchy-launch-webapp", url],
                    })
                    self.assertEqual(candidates[0]["desktopId"], filename)
                    self.assertIn("web app hostname", candidates[0]["reasons"])

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
