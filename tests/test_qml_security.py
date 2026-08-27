import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class QmlSecurityTests(unittest.TestCase):
    def test_every_text_element_forces_plain_text(self):
        for path in PROJECT_ROOT.glob("*.qml"):
            source = path.read_text(encoding="utf-8")
            text_elements = len(re.findall(r"^\s*Text \{$", source, re.MULTILINE))
            plain_text_guards = len(
                re.findall(
                    r"^\s*textFormat: Text\.PlainText$", source, re.MULTILINE
                )
            )
            self.assertEqual(
                plain_text_guards,
                text_elements,
                f"Every Text element in {path.name} must disable rich-text auto-detection",
            )

    def test_tooltip_text_is_stripped_before_it_leaves_the_plugin(self):
        """Bar tooltips are rendered by shell chrome whose Text element leaves
        textFormat at Text.AutoText, so Qt parses any '<' as markup. Backend
        messages embed window classes and titles that applications control."""
        for path in PROJECT_ROOT.glob("*.qml"):
            source = path.read_text(encoding="utf-8")
            for binding in re.findall(r"^\s*tooltipText:(.*(?:\n\s{6,}.*)*)", source, re.MULTILINE):
                with self.subTest(file=path.name):
                    self.assertIn(
                        "plainTooltip(",
                        binding,
                        f"tooltipText in {path.name} must go through plainTooltip()",
                    )

    def test_the_tooltip_filter_removes_every_markup_character(self):
        source = (PROJECT_ROOT / "BarWidget.qml").read_text(encoding="utf-8")
        match = re.search(r"function plainTooltip\(value\) \{(.*?)\n  \}", source, re.DOTALL)
        self.assertIsNotNone(match, "BarWidget.qml must define plainTooltip()")
        for character in ("<", ">", "&"):
            self.assertIn(character, match.group(1))


if __name__ == "__main__":
    unittest.main()
