"""Regression coverage for DrissionPage JS concurrency failures (issue #81)."""

import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import registration_browser
from registration_flow import RegistrationCallbacks
from registration_parallel import run_parallel_batch


class RetryNeeded(Exception):
    pass


class Cancelled(Exception):
    pass


class DrissionConcurrencyRegressionTests(unittest.TestCase):
    def test_pre_submit_timeout_becomes_safe_retry(self):
        class FakePage:
            def run_js(self, *_args):
                raise TimeoutError("执行js超时（等待30秒）")

        with patch.object(registration_browser, "page", FakePage()), patch.object(
            registration_browser, "AccountRetryNeeded", RetryNeeded, create=True
        ):
            with self.assertRaises(RetryNeeded):
                registration_browser._run_pre_submit_js("return true;")

    def test_pre_submit_parser_error_becomes_safe_retry(self):
        class FakePage:
            def run_js(self, *_args):
                raise RuntimeError("js结果解析错误。")

        with patch.object(registration_browser, "page", FakePage()), patch.object(
            registration_browser, "AccountRetryNeeded", RetryNeeded, create=True
        ):
            with self.assertRaises(RetryNeeded):
                registration_browser._run_pre_submit_js("return true;")

    def test_unexpected_runtime_error_is_not_hidden(self):
        class FakePage:
            def run_js(self, *_args):
                raise RuntimeError("unexpected application bug")

        with patch.object(registration_browser, "page", FakePage()), patch.object(
            registration_browser, "AccountRetryNeeded", RetryNeeded, create=True
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected application bug"):
                registration_browser._run_pre_submit_js("return true;")

    def test_email_and_turnstile_use_primitive_json_transport(self):
        source = Path(registration_browser.__file__).read_text(encoding="utf-8")
        email = source[source.index("def fill_email_and_submit("):source.index("def fill_code_and_submit(")]
        turnstile = source[source.index("def _read_turnstile_state("):source.index("def getTurnstileToken(")]

        self.assertIn("filled_raw = _run_pre_submit_js(", email)
        self.assertIn("filled = json.loads(filled_raw)", email)
        self.assertGreaterEqual(email.count("return JSON.stringify({"), 3)
        self.assertNotIn("return {\n        state:", email)
        self.assertIn("return JSON.stringify({", turnstile)
        self.assertIn("state = json.loads(state_raw)", turnstile)

    def test_submit_action_stays_outside_safe_retry_helper(self):
        source = Path(registration_browser.__file__).read_text(encoding="utf-8")
        email = source[source.index("def fill_email_and_submit("):source.index("def fill_code_and_submit(")]
        stage = email.index('_mark_registration_stage("email_submit")')
        submit = email.index("clicked = page.run_js(", stage)

        self.assertLess(stage, submit)
        self.assertNotIn("_run_pre_submit_js(", email[stage:])

    def test_turnstile_json_string_decodes_without_remote_object(self):
        class FakePage:
            def run_js(self, *_args):
                return json.dumps({
                    "state": "SOLVED",
                    "token": "abc",
                    "widget_present": True,
                    "iframe_present": False,
                    "visible": True,
                })

        with patch.object(registration_browser, "page", FakePage()):
            state = registration_browser._read_turnstile_state()

        self.assertEqual(state["state"], registration_browser.TURNSTILE_SOLVED)
        self.assertTrue(state["present"])
        self.assertEqual(state["token"], "abc")
        self.assertEqual(state["token_length"], 3)

    def test_eight_workers_recover_independent_pre_submit_retries(self):
        class FakeMail:
            _OWN_NAMES = set()

            def bind_runtime(self, _namespace):
                return None

            def get_email_provider(self):
                return "duckmail"

        class FakeBrowser:
            def __init__(self, worker_id):
                self.worker_id = worker_id
                self.browser = None
                self.page = None
                self.email_calls = 0

            def bind_runtime(self, _namespace):
                return None

            def start_browser(self, log_callback=None):
                self.browser = object()
                self.page = object()

            def restart_browser(self, log_callback=None, use_proxy=True):
                self.stop_browser()
                self.start_browser(log_callback=log_callback)

            def stop_browser(self):
                self.browser = None
                self.page = None

            def open_signup_page(self, **_kwargs):
                return None

            def fill_email_and_submit(self, **_kwargs):
                self.email_calls += 1
                if self.worker_id in (3, 6) and self.email_calls == 1:
                    raise RetryNeeded("transient pre-submit JS failure")
                return f"worker{self.worker_id}@example.com", "mail-token"

            def fill_code_and_submit(self, _email, _token, **_kwargs):
                return "123456"

            def fill_profile_and_submit(self, **_kwargs):
                return {"given_name": "Test", "family_name": "User", "password": "pw"}

            def wait_for_sso_cookie(self, **_kwargs):
                return f"sso-{self.worker_id}"

            def enable_nsfw_for_token(self, _sso, **_kwargs):
                return True, "ok"

        modules = {}
        module_lock = threading.Lock()

        def fake_loader(path, module_name):
            if Path(path).name == "mail_service.py":
                return FakeMail()
            worker_id = int(module_name.split("_worker_", 1)[1].split("_", 1)[0])
            with module_lock:
                module = FakeBrowser(worker_id)
                modules[worker_id] = module
            return module

        persisted = []
        persist_lock = threading.Lock()

        def append_line(_path, email, _password, _sso):
            with persist_lock:
                persisted.append(email)

        runtime_namespace = {
            "_save_mail_credential": lambda *_args, **_kwargs: True,
            "_append_account_line": append_line,
            "_queue_unsaved_account": lambda *_args, **_kwargs: True,
            "add_token_to_grok2api_pools": lambda *_args, **_kwargs: {},
            "maybe_export_cpa_xai_after_success": lambda **_kwargs: {
                "ok": False,
                "skipped": True,
                "reason": "disabled",
            },
            "sleep_with_cancel": lambda _seconds, cancel: (
                (_ for _ in ()).throw(Cancelled()) if cancel() else None
            ),
            "RegistrationCancelled": Cancelled,
            "AccountRetryNeeded": RetryNeeded,
            "_screen_registered_sso": lambda *_args, **_kwargs: None,
        }
        callbacks = RegistrationCallbacks(log=lambda _message: None, cancelled=lambda: False)

        with patch("registration_parallel.load_isolated_module", side_effect=fake_loader), patch(
            "cpa_xai.browser_confirm.shutdown_mint_browsers", return_value=None
        ):
            result = run_parallel_batch(
                count=8,
                callbacks=callbacks,
                observer=lambda *_args: None,
                runtime_namespace=runtime_namespace,
                accounts_output_file="unused.txt",
                workers=8,
                enable_nsfw=False,
                cleanup_interval=0,
                max_slot_retry=3,
            )

        self.assertEqual(result.processed_count, 8)
        self.assertEqual(result.success_count, 8)
        self.assertEqual(result.fail_count, 0)
        self.assertEqual(len(persisted), 8)
        self.assertEqual(modules[3].email_calls, 2)
        self.assertEqual(modules[6].email_calls, 2)


if __name__ == "__main__":
    unittest.main()
