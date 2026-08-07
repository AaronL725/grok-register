import re
import unittest
from pathlib import Path

from app_config import DEFAULT_CONFIG


class WebUIStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")

    def test_all_config_keys_are_exposed_by_web_form(self):
        missing = [key for key in DEFAULT_CONFIG if ("'" + key + "'") not in self.html]
        self.assertEqual(missing, [], "WebUI missing config fields: %s" % missing)

    def test_zh_en_switch_and_persistence_exist(self):
        for marker in (
            'id="langZh"',
            'id="langEn"',
            "grok_register_lang",
            "zh:{console:'控制台'",
            "en:{console:'Console'",
        ):
            self.assertIn(marker, self.html)

    def test_reference_dashboard_structure_is_present(self):
        self.assertEqual(len(re.findall(r'class="stat"', self.html)), 4)
        for marker in (
            'class="topbar"',
            'id="saveBtn"',
            'id="startBtn"',
            'id="stopBtn"',
            'id="tabs"',
            'id="terminal"',
            'id="copyBtn"',
            'id="clearBtn"',
            '[hidden]{display:none!important}',
        ):
            self.assertIn(marker, self.html)

    def test_responsive_breakpoints_exist(self):
        self.assertIn('@media(max-width:1050px)', self.html)
        self.assertIn('@media(max-width:760px)', self.html)
        self.assertIn('@media(max-width:430px)', self.html)


if __name__ == "__main__":
    unittest.main()
