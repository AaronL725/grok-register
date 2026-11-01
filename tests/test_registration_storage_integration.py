"""Mocked integration tests for the registration engine's SQLite wiring."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import grok_register_ttk as engine
from app_config import ConfigError
from storage.database import Database


class RegistrationStorageIntegrationTests(unittest.TestCase):
    def test_serial_registration_persists_run_account_attempt_and_integrations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "accounts.sqlite3"
            output_path = root / "accounts-run.txt"
            config = dict(engine.DEFAULT_CONFIG)
            config.update({
                "database_path": str(database_path),
                "email_provider": "duckmail",
                "multi_thread_enabled": False,
                "enable_nsfw": False,
            })
            pools = {
                "local": {"enabled": True, "ok": True, "error": ""},
                "remote": {"enabled": False, "ok": None, "error": ""},
            }
            cpa = {"ok": True, "skipped": False}
            logs = []

            patches = [
                patch.object(engine, "config", config),
                patch.object(engine, "start_browser"),
                patch.object(engine, "restart_browser"),
                patch.object(engine, "open_signup_page"),
                patch.object(engine, "fill_email_and_submit", return_value=("user@example.com", "mail-token")),
                patch.object(engine, "_save_mail_credential", return_value=True),
                patch.object(engine, "fill_code_and_submit", return_value="123456"),
                patch.object(engine, "fill_profile_and_submit", return_value={
                    "given_name": "Test", "family_name": "User", "password": "account-password",
                }),
                patch.object(engine, "wait_for_sso_cookie", return_value="sso-secret"),
                patch.object(engine, "enable_nsfw_for_token", return_value=(True, "ok")),
                patch.object(engine, "_screen_registered_sso"),
                patch.object(engine, "add_token_to_grok2api_pools", return_value=pools),
                patch.object(engine, "maybe_export_cpa_xai_after_success", return_value=cpa),
                patch.object(engine, "cleanup_runtime_memory"),
                patch.object(engine, "sleep_with_cancel"),
                patch("registration_flow.begin_registration_slot"),
                patch("registration_flow.end_registration_slot"),
            ]

            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10], patches[11], patches[12], patches[13], patches[14], patches[15], patches[16]:
                batch = engine.run_registration_common(
                    1,
                    logs.append,
                    lambda: False,
                    str(output_path),
                    lambda *_args: None,
                )

            self.assertEqual(batch.success_count, 1)
            self.assertEqual(batch.processed_count, 1)
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "user@example.com----account-password----sso-secret\n",
            )

            database = Database(database_path)
            account = database.get_account(1)
            run = database.list_registration_runs()["items"][0]
            self.assertEqual(account["registration_status"], "registered")
            self.assertEqual(account["email_provider"], "duckmail")
            self.assertEqual(account["registration_run_id"], run["id"])
            self.assertEqual(account["cpa_status"], "succeeded")
            self.assertEqual(account["grok2api_local_status"], "succeeded")
            self.assertEqual(account["grok2api_remote_status"], "not_requested")
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["success_count"], 1)
            self.assertEqual(run["attempt_count"], 1)
            self.assertEqual(account["attempts"][0]["status"], "succeeded")
            self.assertEqual(account["attempts"][0]["account_id"], account["id"])


    def test_failed_serial_registration_saves_screenshot_and_traceback_before_cleanup(self):
        from registration_diagnostics import capture_failure_screenshot
        from storage.database import Database

        class FakePage:
            def get_screenshot(self, path, full_page=False):
                Path(path).write_bytes(b"fake-browser-screenshot")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "accounts.sqlite3"
            output_path = root / "accounts-run.txt"
            screenshot_dir = root / "diagnostics" / "registration"
            config = dict(engine.DEFAULT_CONFIG)
            config.update({
                "database_path": str(database_path),
                "email_provider": "duckmail",
                "multi_thread_enabled": False,
            })

            def capture(page, run_id, details, log_callback=None, screenshot_dir=None):
                return capture_failure_screenshot(
                    page, run_id, details, log_callback=log_callback,
                    screenshot_dir=screenshot_dir,
                )

            patches = [
                patch.object(engine, "config", config),
                patch.object(engine, "start_browser", side_effect=ConfigError("browser start failed")),
                patch.object(engine._registration_browser, "page", FakePage()),
                patch.object(engine, "cleanup_runtime_memory"),
                patch("registration_flow.begin_registration_slot"),
                patch("registration_flow.end_registration_slot"),
                patch("registration_diagnostics.capture_failure_screenshot", side_effect=capture),
            ]

            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                with self.assertRaisesRegex(ConfigError, "browser start failed"):
                    engine.run_registration_common(
                        1, lambda _message: None, lambda: False,
                        str(output_path), lambda *_args: None,
                    )

            database = Database(database_path)
            run = database.list_registration_runs()["items"][0]
            attempts = database.list_registration_attempts(run["id"])
            attempt = attempts["items"][0]

            self.assertEqual(run["status"], "failed")
            self.assertEqual(attempt["status"], "failed")
            self.assertEqual(attempt["failure_type"], "browser")
            self.assertEqual(attempt["error_type"], "ConfigError")
            self.assertIn("browser start failed", attempt["traceback"])
            self.assertTrue(Path(attempt["screenshot_path"]).is_file())
            self.assertEqual(Path(attempt["screenshot_path"]).parent, screenshot_dir)
            self.assertEqual(
                Path(attempt["screenshot_path"]).read_bytes(),
                b"fake-browser-screenshot",
            )


if __name__ == "__main__":
    unittest.main()
