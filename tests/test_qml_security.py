import re
import unittest
from pathlib import Path


class QmlSecurityTests(unittest.TestCase):
    def test_every_text_element_forces_plain_text(self):
        project_root = Path(__file__).resolve().parents[1]
        for path in project_root.glob("*.qml"):
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


if __name__ == "__main__":
    unittest.main()
