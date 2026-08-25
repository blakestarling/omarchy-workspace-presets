import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_presets.desktop import resolve_launcher, scan_desktop_entries


class DesktopEntryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
