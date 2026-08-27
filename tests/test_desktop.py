import os
import tempfile
import threading
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
        tty: int, tpgid: int, argv: list[str], cwd: str = "/tmp",
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
            # _process_cwd resolves strictly, so this has to point at a
            # directory that exists wherever the suite runs.
            working_directory = str(Path(directory).resolve())
            os.symlink(working_directory, process / "cwd")

            result = terminal_process_launcher(42, "foot", proc_root=Path(directory))

            self.assertEqual(result, ({
                "kind": "command",
                "argv": [
                    "foot", "--app-id=TUI.tile", "-e", "omarchy-launch-docker-tui",
                ],
                "cwd": working_directory,
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


class ScannedFileTests(unittest.TestCase):
    """Both scanned trees include a user-writable directory, so a name that
    matched the glob is not necessarily a file the service can safely read."""

    def _scan(self, directory: Path) -> tuple[dict, dict]:
        """Run both scans on a worker so a blocking read fails instead of hanging."""
        outcome: list[dict] = []

        def scan() -> None:
            with patch(
                "workspace_presets.desktop._data_dirs", return_value=[directory]
            ):
                outcome.append(scan_desktop_entries())
            outcome.append(scan_omarchy_panel_plugins([directory / "plugins"]))

        worker = threading.Thread(target=scan, daemon=True)
        worker.start()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "a scanned file blocked the caller")
        self.assertEqual(len(outcome), 2)
        return outcome[0], outcome[1]

    @staticmethod
    def _populate(directory: Path) -> None:
        applications = directory / "applications"
        applications.mkdir()
        (applications / "editor.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Editor\nExec=editor %F\n"
        )
        plugin = directory / "plugins" / "panel"
        plugin.mkdir(parents=True)
        (plugin / "manifest.json").write_text(
            '{"id":"panel","name":"Panel","kinds":["panel"]}'
        )

    def test_a_planted_fifo_is_skipped_instead_of_blocking_a_scan(self):
        # Nothing opens the write end, so a read without O_NONBLOCK would wait
        # here for as long as the service ran, on a file the user never
        # installed and neither scan needs.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._populate(root)
            os.mkfifo(root / "applications" / "trap.desktop")
            trap = root / "plugins" / "trap"
            trap.mkdir()
            os.mkfifo(trap / "manifest.json")

            entries, panels = self._scan(root)

            # The legitimate neighbours are still found: one unreadable file
            # must not hide the rest of the directory.
            self.assertEqual(set(entries), {"editor.desktop"})
            self.assertEqual(set(panels), {"panel"})

    def test_an_oversized_manifest_or_entry_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._populate(root)
            bloated = root / "plugins" / "bloated"
            bloated.mkdir()
            (bloated / "manifest.json").write_text(
                '{"id":"bloated","name":"Bloated","kinds":["panel"],"pad":"'
                + "x" * 4096 + '"}'
            )
            (root / "applications" / "bloated.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Bloated\nExec=bloated\n"
                + "#" * 4096
            )
            with patch("workspace_presets.desktop.MAX_SCANNED_FILE_BYTES", 1024):
                entries, panels = self._scan(root)

            self.assertEqual(set(entries), {"editor.desktop"})
            self.assertEqual(set(panels), {"panel"})

    def test_a_symlinked_entry_is_still_read(self):
        # Packaged and dotfile-managed entries are routinely links, so the
        # scans follow them on purpose and refusing one would hide a real
        # application.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._populate(root)
            packaged = root / "packaged"
            packaged.mkdir()
            (packaged / "viewer.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Viewer\nExec=viewer\n"
            )
            (root / "applications" / "viewer.desktop").symlink_to(
                packaged / "viewer.desktop"
            )

            entries, _ = self._scan(root)

            self.assertEqual(entries["viewer.desktop"].name, "Viewer")


if __name__ == "__main__":
    unittest.main()
