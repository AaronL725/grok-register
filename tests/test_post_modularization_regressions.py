"""验证模块化改造后的配置、浏览器、邮箱和兼容性回归。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app_config
import browser_runtime
import cpa_export
import grok_register_ttk as app
import mail_service
import registration_browser
from cpa_xai import browser_session


class PostModularizationRegressionTests(unittest.TestCase):
    def test_signup_url_preserves_redirect(self):
        self.assertEqual(
            registration_browser.SIGNUP_URL,
            "https://accounts.x.ai/sign-up?redirect=grok-com",
        )

    def test_config_identity_survives_load(self):
        original_path = app_config.CONFIG_FILE
        try:
            with tempfile.TemporaryDirectory() as directory:
                config_path = Path(directory) / "config.json"
                payload = dict(app_config.DEFAULT_CONFIG)
                payload["register_count"] = 3
                config_path.write_text(json.dumps(payload), encoding="utf-8")
                app_config.CONFIG_FILE = str(config_path)
                loaded = app.load_config()
                self.assertIs(loaded, app_config.config)
                self.assertIs(app.config, app_config.config)
                self.assertEqual(app.config["register_count"], 3)
        finally:
            app_config.CONFIG_FILE = original_path

    def test_legacy_runtime_state_assignments_are_forwarded(self):
        sentinel = object()
        original = registration_browser.page
        try:
            app.page = sentinel
            self.assertIs(registration_browser.page, sentinel)
            self.assertIs(app.page, sentinel)
        finally:
            app.page = original

    def test_gui_reset_clears_all_batch_counters(self):
        gui = app.GrokRegisterGUI.__new__(app.GrokRegisterGUI)
        gui.success_count = 1
        gui.fail_count = 2
        gui.registered_unsaved_count = 3
        gui.postprocess_warning_count = 4
        gui._reset_batch_counters()
        self.assertEqual(
            (gui.success_count, gui.fail_count, gui.registered_unsaved_count, gui.postprocess_warning_count),
            (0, 0, 0, 0),
        )

    def test_cpa_hotload_requirement_only_applies_when_export_enabled(self):
        cfg = dict(app_config.DEFAULT_CONFIG)
        cfg["cpa_copy_to_hotload"] = True
        cfg["cpa_export_enabled"] = False
        self.assertTrue(app_config.validate_run_requirements(cfg)["cpa_copy_to_hotload"])
        cfg["cpa_export_enabled"] = True
        with self.assertRaises(app_config.ConfigError):
            app_config.validate_run_requirements(cfg)

    def test_mail_body_normalizes_string_and_list_html(self):
        text = mail_service.normalize_mail_body(
            {"text": "plain", "html": "<b>one</b>"},
            {"html": ["<i>two</i>"]},
        )
        self.assertIn("plain", text)
        self.assertIn("one", text)
        self.assertIn("two", text)

    def test_cloudflare_skips_non_target_mail_without_logger(self):
        message = {
            "id": "1",
            "to": [{"address": "other@example.com"}],
            "subject": "ABC-123 xAI",
            "text": "ABC-123",
        }
        with patch.object(mail_service, "get_cloudflare_api_base", return_value="https://mail.example"),              patch.object(mail_service, "cloudflare_get_messages", return_value=[message]),              patch.object(mail_service, "cloudflare_get_message_detail") as detail,              patch.object(mail_service, "raise_if_cancelled", return_value=None),              patch.object(mail_service, "sleep_with_cancel", return_value=None),              patch.object(mail_service.time, "time", side_effect=[0, 0, 2, 2]):
            with self.assertRaises(Exception):
                mail_service.cloudflare_get_oai_code(
                    "token", "target@example.com", timeout=1, poll_interval=0, log_callback=None
                )
        detail.assert_not_called()

    def test_cpa_browser_session_does_not_import_main_module(self):
        source = Path(browser_session.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from grok_register_ttk", source)
        self.assertIn("from browser_runtime import create_browser_options", source)

    def test_browser_options_accept_explicit_extension_path(self):
        self.assertIn("extension_path", browser_runtime.create_browser_options.__code__.co_varnames)

    def test_cpa_export_annotations_are_python39_compatible(self):
        annotation = cpa_export.CpaExportSettings.__annotations__["hotload_dir"]
        self.assertNotIsInstance(annotation, str)


if __name__ == "__main__":
    unittest.main()
