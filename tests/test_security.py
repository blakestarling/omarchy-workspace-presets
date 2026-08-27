"""Regression coverage for the trust boundaries the plugin has to hold.

Everything here runs as the user, so these tests are not about privilege.
They pin the three places where data the user did not write becomes something
that executes or renders: window titles set by arbitrary applications, command
lines captured from /proc, and preset files another process can edit.
"""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from workspace_presets import SUPPORTED_LAYOUTS, storage
from workspace_presets.engine import WorkspaceEngine, safe_label
from workspace_presets.errors import HyprlandError, ValidationError
from workspace_presets.hyprland import Hyprland
from workspace_presets.storage import PresetStore


LUA = shutil.which("lua5.4") or shutil.which("lua")


class LuaQuotingTests(unittest.TestCase):
    """`hyprctl repl` evaluates arbitrary Lua inside the compositor and
    `hl.exec_cmd` runs through /bin/sh, so lua_string is the single barrier
    between preset data and code execution."""

    @unittest.skipIf(LUA is None, "lua interpreter not installed")
    def test_every_codepoint_round_trips_through_a_real_lua_parser(self):
        probes = [chr(code) for code in range(0, 0x300)]
        probes += ["\x7f", "\U0001f600", '"', "\\"]
        for probe in probes:
            # The trailing digit would expose an under-padded decimal escape.
            sample = "A" + probe + "9"
            literal = Hyprland.lua_string(sample)
            with self.subTest(codepoint=hex(ord(probe))):
                result = subprocess.run(
                    [
                        LUA,
                        "-e",
                        f"local x={literal}; "
                        "io.write(table.concat({string.byte(x,1,#x)},','))",
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                decoded = (
                    bytes(int(value) for value in result.stdout.split(","))
                    if result.stdout
                    else b""
                )
                self.assertEqual(decoded, sample.encode())

    def test_control_characters_use_lua_decimal_escapes_not_json(self):
        # JSON quoting would emit a \\u escape here, which Lua cannot compile.
        self.assertEqual(Hyprland.lua_string("a\x01b"), '"a\\001b"')
        self.assertEqual(Hyprland.lua_string('say "hi"'), '"say \\"hi\\""')
        self.assertEqual(Hyprland.lua_string("back\\slash"), '"back\\\\slash"')

    def test_non_finite_geometry_is_refused_before_reaching_lua(self):
        self.assertEqual(Hyprland.lua_number(0.5), "0.5")
        for value in ("1e400", float("inf"), float("-inf"), float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    Hyprland.lua_number(value)

    def test_saved_layout_name_is_whitelisted_before_becoming_lua(self):
        hostile = "'..os.execute('id')..'"
        self.assertNotIn(hostile, SUPPORTED_LAYOUTS)
        with self.assertRaises(ValidationError):
            Hyprland().set_workspace_layout("3", {"name": hostile})


class ShellQuotingTests(unittest.TestCase):
    """Hyprland's exec_cmd takes a single string and runs it under /bin/sh, so
    argv has to survive both a shell and a Lua literal intact."""

    def test_shell_metacharacters_in_a_launcher_stay_inert(self):
        hostile = ["foot", "-e", "sh", "-c", "x; touch /tmp/PWNED #"]
        command = WorkspaceEngine._launcher_command(
            {"kind": "command", "argv": hostile}
        )
        joined = shlex.join(command)

        self.assertEqual(shlex.split(joined), command)
        self.assertIn("'x; touch /tmp/PWNED #'", joined)

    @unittest.skipIf(LUA is None, "lua interpreter not installed")
    def test_the_lua_literal_decodes_back_to_the_exact_shell_string(self):
        joined = shlex.join(["foot", "-e", "sh", "-c", 'a"b; rm -rf ~ #'])
        result = subprocess.run(
            [LUA, "-e", f"io.write({Hyprland.lua_string(joined)})"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, joined)


class WorkspaceSelectorTests(unittest.TestCase):
    def test_only_a_positive_integer_id_is_accepted_as_a_workspace(self):
        for unsafe in ("+2", "-1", "0", "empty", "previous", "name:3", "special:x", ""):
            with self.subTest(workspace=unsafe):
                with self.assertRaises(HyprlandError):
                    Hyprland.workspace_selector(unsafe)
        self.assertEqual(Hyprland.workspace_selector(7), "7")


class LabelSanitizationTests(unittest.TestCase):
    """Window titles are written by the application that owns the window - a
    browser title is a remote page's own title element - and these messages
    reach shell chrome that renders auto-detected rich text."""

    def test_markup_never_survives_into_a_user_visible_message(self):
        evil = '<img src="http://attacker.example/beacon.png">'
        self.assertNotIn("<", safe_label(evil))
        self.assertNotIn(">", safe_label(evil))
        self.assertNotIn("&", safe_label("Tom &amp; Jerry"))

    def test_control_characters_and_runaway_length_are_contained(self):
        self.assertEqual(safe_label("a\x00\x1bb"), "a b")
        self.assertLessEqual(len(safe_label("x" * 500)), 61)
        self.assertEqual(safe_label(""), "window")
        self.assertEqual(safe_label(None, fallback="window 3"), "window 3")


class LauncherValidationTests(unittest.TestCase):
    def test_argv_rejects_null_bytes_and_unbounded_input(self):
        PresetStore._validate_launcher({"kind": "command", "argv": ["ok"]})
        for argv in (["fo\0ot"], ["x"] * 257, ["y" * 65537]):
            with self.subTest(argv=str(argv)[:32]):
                with self.assertRaises(ValidationError):
                    PresetStore._validate_launcher({"kind": "command", "argv": argv})

    def test_a_captured_command_line_can_never_fail_its_own_bounds(self):
        # _process_argv caps captures at 256 arguments and 65536 bytes, so the
        # storage bounds must sit at or above that or a capture could not load.
        PresetStore._validate_launcher(
            {"kind": "command", "argv": ["foot"] + ["a"] * 255}
        )


class PresetFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_the_lock_file_never_follows_a_planted_symlink(self):
        victim = self.root / "victim.conf"
        victim.write_text("stays readable\n")
        os.chmod(victim, 0o644)
        data = self.root / "cfg"
        data.mkdir()
        (data / "presets.lock").symlink_to(victim)

        with self.assertRaises(ValidationError):
            PresetStore(data / "presets.json").load()

        self.assertEqual(oct(os.stat(victim).st_mode & 0o777), "0o644")
        self.assertEqual(victim.read_text(), "stays readable\n")

    def test_an_ordinary_lock_is_created_private(self):
        store = PresetStore(self.root / "cfg" / "presets.json")
        store.load()
        self.assertEqual(oct(os.stat(store.lock_path).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(store.path.parent).st_mode & 0o777), "0o700")

    def test_the_state_file_never_follows_a_planted_symlink(self):
        # The link points at a document that would validate, so a read that
        # followed it would succeed silently instead of failing loudly.
        victim = self.root / "victim.json"
        victim.write_text(json.dumps(PresetStore.empty()))
        data = self.root / "cfg"
        data.mkdir()
        (data / "presets.json").symlink_to(victim)

        with self.assertRaises(ValidationError):
            PresetStore(data / "presets.json").load()

    def test_a_planted_fifo_is_refused_instead_of_blocking_the_reader(self):
        # Nothing ever opens the write end, so an open() without O_NONBLOCK
        # would wait here for as long as the service kept running.
        path = self.root / "presets.json"
        os.mkfifo(path)
        outcome: list[BaseException | None] = []

        def read() -> None:
            try:
                PresetStore(path).load()
                outcome.append(None)
            except BaseException as exc:  # noqa: BLE001 - handed to the assertion
                outcome.append(exc)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(timeout=10)
        self.assertFalse(reader.is_alive(), "reading a FIFO blocked the caller")
        self.assertIsInstance(outcome[0], ValidationError)

    def test_a_directory_left_at_the_state_path_is_refused(self):
        path = self.root / "presets.json"
        path.mkdir()
        with self.assertRaises(ValidationError):
            PresetStore(path).load()

    def test_an_oversized_state_file_is_refused_before_it_is_parsed(self):
        path = self.root / "presets.json"
        with mock.patch.object(storage, "MAX_STATE_BYTES", 4096):
            path.write_text(json.dumps(PresetStore.empty()) + " " * 4096)
            with self.assertRaises(ValidationError):
                PresetStore(path).load()
            # What fits still loads, so the ceiling only rejects a file no
            # legitimate store would have produced.
            path.write_text(json.dumps(PresetStore.empty()))
            self.assertEqual(PresetStore(path).load()["presets"], [])

    def test_one_damaged_preset_does_not_hide_the_rest_on_read(self):
        path = self.root / "presets.json"
        path.write_text(json.dumps({
            "schemaVersion": 1,
            "presets": [
                {"id": "a", "name": "Good", "snapshot": {
                    "layout": {"name": "monocle", "order": ["s1"]},
                    "windows": [{"id": "s1"}],
                }},
                # References a slot it does not define: readable, not loadable.
                {"id": "b", "name": "Broken", "snapshot": {
                    "layout": {"name": "monocle", "order": ["missing"]},
                    "windows": [{"id": "s2"}],
                }},
            ],
            "presetGroups": [],
            "startupGroupId": None,
            "confirmStartupLaunch": False,
        }))
        summaries = PresetStore(path).list_summaries()
        self.assertEqual({item["name"] for item in summaries}, {"Good", "Broken"})

    def test_duplicate_slot_ids_are_refused_on_read(self):
        path = self.root / "presets.json"
        path.write_text(json.dumps({
            "schemaVersion": 1,
            "presets": [{"id": "a", "name": "Dup", "snapshot": {
                "windows": [{"id": "same"}, {"id": "same"}],
            }}],
            "presetGroups": [],
            "startupGroupId": None,
            "confirmStartupLaunch": False,
        }))
        with self.assertRaises(ValidationError):
            PresetStore(path).list_summaries()


class SnapshotIntegrityTests(unittest.TestCase):
    """Restore closes the target windows before it launches anything, so an
    inconsistent snapshot has to be refused while those windows still exist."""

    @staticmethod
    def _preset(layout, windows, **extra):
        return {
            "name": "Broken",
            "snapshot": {"layout": layout, "windows": windows, **extra},
        }

    def test_a_layout_referencing_an_unknown_slot_is_refused(self):
        preset = self._preset({"name": "monocle", "order": ["ghost"]}, [{"id": "s1"}])
        with self.assertRaises(ValidationError) as caught:
            WorkspaceEngine._validate_snapshot_integrity(preset)
        self.assertEqual(caught.exception.details["missingSlotIds"], ["ghost"])

    def test_a_group_or_focus_referencing_an_unknown_slot_is_refused(self):
        for label, extra in (
            ("group", {"groups": [
                {"id": "g", "members": ["s1", "ghost"], "representativeSlotId": "s1"}
            ]}),
            ("focus", {"finalFocusSlotId": "ghost"}),
        ):
            with self.subTest(reference=label):
                preset = self._preset(
                    {"name": "monocle", "order": ["s1"]}, [{"id": "s1"}], **extra
                )
                with self.assertRaises(ValidationError):
                    WorkspaceEngine._validate_snapshot_integrity(preset)

    def test_an_unsupported_or_damaged_layout_is_refused(self):
        for layout in (
            {"name": "floating"},
            {"name": "dwindle", "tree": {"kind": "split", "first": {"kind": "leaf"}}},
            "not-an-object",
        ):
            with self.subTest(layout=str(layout)[:24]):
                with self.assertRaises(ValidationError):
                    WorkspaceEngine._validate_snapshot_integrity(
                        self._preset(layout, [{"id": "s1"}])
                    )

    def test_an_empty_or_missing_snapshot_is_refused(self):
        for preset in (
            {"name": "Empty", "snapshot": {"layout": {"name": "monocle"}, "windows": []}},
            {"name": "None"},
        ):
            with self.subTest(preset=preset["name"]):
                with self.assertRaises(ValidationError):
                    WorkspaceEngine._validate_snapshot_integrity(preset)

    def test_a_consistent_snapshot_passes(self):
        WorkspaceEngine._validate_snapshot_integrity(self._preset(
            {"name": "monocle", "order": ["s1", "s2"]},
            [{"id": "s1"}, {"id": "s2"}],
            groups=[{
                "id": "g",
                "members": ["s1", "s2"],
                "representativeSlotId": "s1",
                "activeSlotId": "s2",
            }],
            finalFocusSlotId="s2",
        ))


if __name__ == "__main__":
    unittest.main()
