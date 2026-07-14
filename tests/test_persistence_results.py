import os
import tempfile
import unittest
from unittest.mock import patch

import grok_register_ttk as app
from registration_core import OutputContext, RegistrationCallbacks, RegistrationResult


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.original_config = app.config.copy()
        app.config = app.validate_config(app.DEFAULT_CONFIG.copy())
        self.callbacks = RegistrationCallbacks(log=lambda message: None, cancelled=lambda: False)
        self.registration = RegistrationResult(
            ok=True,
            registered=True,
            email="a@example.com",
            password="pw",
            sso="token",
            profile={"password": "pw"},
        )

    def tearDown(self):
        app.config = self.original_config

    def test_pool_wrapper_returns_structured_results(self):
        app.config["grok2api_auto_add_local"] = True
        app.config["grok2api_auto_add_remote"] = True
        with patch.object(app, "add_token_to_grok2api_local_pool", return_value=True), \
                patch.object(app, "add_token_to_grok2api_remote_pool", side_effect=RuntimeError("remote failed")):
            result = app.add_token_to_grok2api_pools("token", email="a@example.com")

        self.assertTrue(result["local"]["ok"])
        self.assertFalse(result["remote"]["ok"])
        self.assertIn("remote failed", result["remote"]["error"])

    def test_account_save_failure_is_pending_and_not_silently_successful(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = OutputContext(
                accounts_output_file=os.path.join(temp_dir, "accounts.txt"),
                pending_output_file=os.path.join(temp_dir, "pending.jsonl"),
            )
            real_append = app.append_line_durable

            def fake_append(path, line):
                if path == context.accounts_output_file:
                    raise OSError("disk full")
                return real_append(path, line)

            with patch.object(app, "append_line_durable", side_effect=fake_append), \
                    patch.object(app, "add_token_to_grok2api_pools", return_value={
                        "local": {"enabled": False, "ok": None, "skipped": True, "error": None},
                        "remote": {"enabled": False, "ok": None, "skipped": True, "error": None},
                    }), \
                    patch.object(app, "maybe_export_cpa_xai_after_success", return_value={
                        "ok": False,
                        "skipped": True,
                        "reason": "disabled",
                    }):
                result = app.persist_account_result(
                    self.registration,
                    context,
                    self.callbacks,
                )

            self.assertFalse(result.saved)
            self.assertTrue(result.pending_retry)
            self.assertIn("account_result", result.pending_actions)
            self.assertEqual(result.pending_file, os.path.abspath(context.pending_output_file))
            self.assertTrue(os.path.exists(context.pending_output_file))

    def test_successful_primary_save_is_marked_saved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = OutputContext(
                accounts_output_file=os.path.join(temp_dir, "accounts.txt"),
            )
            with patch.object(app, "add_token_to_grok2api_pools", return_value={
                "local": {"enabled": False, "ok": None, "skipped": True, "error": None},
                "remote": {"enabled": False, "ok": None, "skipped": True, "error": None},
            }), patch.object(app, "maybe_export_cpa_xai_after_success", return_value={
                "ok": False,
                "skipped": True,
                "reason": "disabled",
            }):
                result = app.persist_account_result(
                    self.registration,
                    context,
                    self.callbacks,
                )

            self.assertTrue(result.saved)
            with open(context.accounts_output_file, "r", encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("a@example.com----pw----token", content)


if __name__ == "__main__":
    unittest.main()
