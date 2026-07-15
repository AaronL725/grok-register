"""验证后处理警告、邮件重试、目标锁和常量兼容边界。"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import account_outputs
import app_config
import cpa_export
import grok_register_ttk as app
import mail_service
import registration_browser
from registration_flow import RegistrationCallbacks, RegistrationOperations, run_batch


class Cancelled(Exception):
    pass


class Retryable(Exception):
    pass


def make_ops(enable_nsfw=None, export_cpa=None):
    return RegistrationOperations(
        start_browser=lambda: None,
        restart_browser=lambda: None,
        browser_missing=lambda: False,
        open_signup_page=lambda: None,
        fill_email_and_submit=lambda: ("user@example.com", "mail-token"),
        save_mail_credential=lambda email, token: True,
        fill_code_and_submit=lambda email, token: "ABC-123",
        fill_profile_and_submit=lambda: {"given_name": "A", "family_name": "B", "password": "pw"},
        wait_for_sso_cookie=lambda: "sso-token",
        enable_nsfw=enable_nsfw or (lambda sso: (True, "ok")),
        persist_account_line=lambda email, password, sso: None,
        queue_unsaved_result=lambda payload, error: True,
        add_tokens=lambda sso, email: {
            "local": {"enabled": False, "ok": None, "error": None},
            "remote": {"enabled": False, "ok": None, "error": None},
        },
        export_cpa=export_cpa or (lambda email, password, sso: {"ok": False, "skipped": True}),
        cleanup=lambda reason: None,
        sleep=lambda seconds: None,
        cancelled_exception=Cancelled,
        retry_exception=Retryable,
    )


class MinimalBoundaryRegressionTests(unittest.TestCase):
    def callbacks(self, logs=None):
        logs = logs if logs is not None else []
        return RegistrationCallbacks(log=logs.append, cancelled=lambda: False)

    def test_cpa_copy_failure_is_partial_warning_and_batch_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "cpa_export_enabled": True,
                "cpa_auth_dir": directory,
                "cpa_copy_to_hotload": True,
                "cpa_hotload_dir": str(Path(directory) / "hotload"),
            }
            mint = lambda **kwargs: {"ok": True, "path": str(Path(directory) / "xai-test.json")}
            with patch.object(cpa_export, "_load_mint_and_export", return_value=mint), \
                 patch.object(cpa_export.shutil, "copy2", side_effect=OSError("copy failed")):
                result = cpa_export.export_cpa_xai_for_account("user@example.com", "pw", config=config)
            self.assertTrue(result["ok"])
            self.assertTrue(result["warning"])
            self.assertTrue(result["partial"])
            self.assertIn("copy failed", result["cpa_copy_error"])

        batch = run_batch(
            1,
            self.callbacks(),
            lambda *args: None,
            make_ops(export_cpa=lambda *args: {
                "ok": True,
                "warning": True,
                "partial": True,
                "cpa_copy_error": "copy failed",
            }),
        )
        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.postprocess_warning_count, 1)

    def test_duckmail_retries_same_message_after_detail_failure(self):
        message = {"id": "m1", "to": [{"address": "user@example.com"}]}
        with patch.object(mail_service, "get_messages", return_value=[message]), \
             patch.object(mail_service, "get_message_detail", side_effect=[RuntimeError("temporary"), {"subject": "ABC-123 xAI"}]), \
             patch.object(mail_service, "sleep_with_cancel", return_value=None), \
             patch.object(mail_service.time, "time", side_effect=[0, 0, 0]):
            code = mail_service.duckmail_get_oai_code("token", "user@example.com", timeout=1, poll_interval=0)
        self.assertEqual(code, "ABC-123")

    def test_yyds_retries_same_message_after_detail_failure(self):
        message = {"id": "m1", "to": [{"address": "user@example.com"}]}
        with patch.object(mail_service, "yyds_get_messages", return_value=[message]), \
             patch.object(mail_service, "yyds_get_message_detail", side_effect=[RuntimeError("temporary"), {"subject": "ABC-123 xAI"}]), \
             patch.object(mail_service, "sleep_with_cancel", return_value=None), \
             patch.object(mail_service.time, "time", side_effect=[0, 0, 0]):
            code = mail_service.yyds_get_oai_code("token", "user@example.com", timeout=1, poll_interval=0)
        self.assertEqual(code, "ABC-123")

    def test_pending_recovery_acquires_pending_and_target_locks_in_fixed_order(self):
        acquired = []

        class FakeLock:
            def __init__(self, path, timeout=30):
                self.path = path
            def __enter__(self):
                acquired.append(self.path)
                return self
            def __exit__(self, exc_type, exc, tb):
                return False

        with tempfile.TemporaryDirectory() as directory:
            pending = Path(directory) / "one.pending.jsonl"
            target = Path(directory) / "accounts.txt"
            pending.write_text(json.dumps({"email": "u@example.com", "password": "pw", "sso": "sso"}) + "\n", encoding="utf-8")
            with patch.object(account_outputs, "FileLock", FakeLock):
                account_outputs.retry_pending_file(str(pending), output_path=str(target))
            expected = sorted(
                [str(pending.resolve()) + ".lock", str(target.resolve()) + ".lock"],
                key=lambda value: __import__("os").path.normcase(__import__("os").path.abspath(value)),
            )
            self.assertEqual(acquired, expected)

    def test_nsfw_exception_does_not_discard_registered_account(self):
        logs = []
        batch = run_batch(
            1,
            self.callbacks(logs),
            lambda *args: None,
            make_ops(enable_nsfw=lambda sso: (_ for _ in ()).throw(RuntimeError("nsfw down"))),
        )
        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.fail_count, 0)
        self.assertTrue(any("NSFW 开启异常" in line for line in logs))

    def test_main_constant_assignments_forward_to_owner_modules(self):
        old_config_file = app_config.CONFIG_FILE
        old_signup_url = registration_browser.SIGNUP_URL
        try:
            app.CONFIG_FILE = "other.json"
            app.SIGNUP_URL = "https://example.test/signup"
            self.assertEqual(app_config.CONFIG_FILE, "other.json")
            self.assertEqual(app.CONFIG_FILE, "other.json")
            self.assertEqual(registration_browser.SIGNUP_URL, "https://example.test/signup")
            self.assertEqual(app.SIGNUP_URL, "https://example.test/signup")
        finally:
            app.CONFIG_FILE = old_config_file
            app.SIGNUP_URL = old_signup_url


if __name__ == "__main__":
    unittest.main()
