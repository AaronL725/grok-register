import json
import tempfile
import threading
from unittest.mock import patch
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import app_config
import account_outputs
from registration_flow import RegistrationCallbacks
from registration_parallel import load_isolated_module, run_parallel_batch, split_worker_counts


class OptionalMultithreadTests(unittest.TestCase):
    def test_defaults_keep_parallel_disabled_with_four_workers_configured(self):
        cfg = app_config.validate_config_structure({})
        self.assertFalse(cfg["multi_thread_enabled"])
        self.assertEqual(cfg["multi_thread_workers"], 4)

    def test_worker_count_validation(self):
        for workers in (1, 4, 8):
            cfg = app_config.validate_config_structure({"multi_thread_workers": workers})
            self.assertEqual(cfg["multi_thread_workers"], workers)
        for workers in (0, 9):
            with self.assertRaises(app_config.ConfigError):
                app_config.validate_config_structure({"multi_thread_workers": workers})

    def test_static_distribution_never_exceeds_target(self):
        self.assertEqual(split_worker_counts(10, 4), [3, 3, 2, 2])
        self.assertEqual(split_worker_counts(2, 4), [1, 1])
        self.assertEqual(split_worker_counts(1, 4), [1])
        self.assertEqual(sum(split_worker_counts(37, 8)), 37)

    def test_isolated_module_instances_do_not_share_globals(self):
        with tempfile.TemporaryDirectory() as directory:
            module_path = Path(directory) / "sample_runtime.py"
            module_path.write_text("value = None\n", encoding="utf-8")
            first = load_isolated_module(module_path, "_parallel_test_first")
            second = load_isolated_module(module_path, "_parallel_test_second")
            first.value = "worker-1"
            second.value = "worker-2"
            self.assertEqual(first.value, "worker-1")
            self.assertEqual(second.value, "worker-2")

    def test_concurrent_account_appends_remain_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "accounts.txt")
            def write_one(index):
                account_outputs.append_account_line(
                    output,
                    "user%s@example.com" % index,
                    "password-%s" % index,
                    "sso-%s" % index,
                )
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(write_one, range(40)))
            lines = Path(output).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 40)
            self.assertEqual(len(set(lines)), 40)
            for line in lines:
                self.assertEqual(len(line.split("----", 2)), 3)

    def test_concurrent_pending_appends_are_valid_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "accounts.txt")
            def write_one(index):
                account_outputs.queue_unsaved_account(
                    output,
                    {"email": "user%s@example.com" % index, "password": "p", "sso": "sso-%s" % index},
                    "test",
                )
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(write_one, range(30)))
            pending = Path(output + ".pending.jsonl")
            rows = [json.loads(line) for line in pending.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 30)
            self.assertEqual(len({row["email"] for row in rows}), 30)


    def test_parallel_coordinator_reuses_existing_batch_logic_with_isolated_workers(self):
        class Cancelled(Exception):
            pass

        class RetryNeeded(Exception):
            pass

        class FakeMail:
            _OWN_NAMES = set()
            def bind_runtime(self, _namespace):
                return None

        class FakeBrowser:
            def __init__(self, worker_number):
                self.worker_number = worker_number
                self.browser = None
                self.page = None
                self.next_account = 0

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
                self.next_account += 1
                return (
                    "worker%s-%s@example.com" % (self.worker_number, self.next_account),
                    "mail-token",
                )

            def fill_code_and_submit(self, _email, _token, **_kwargs):
                return "123456"

            def fill_profile_and_submit(self, **_kwargs):
                return {"given_name": "Test", "family_name": "User", "password": "pw"}

            def wait_for_sso_cookie(self, **_kwargs):
                return "sso-token"

            def enable_nsfw_for_token(self, _sso, **_kwargs):
                return True, "ok"

        module_lock = threading.Lock()
        browser_modules = []
        next_worker = {"value": 0}

        def fake_loader(path, _name):
            if Path(path).name == "mail_service.py":
                return FakeMail()
            with module_lock:
                next_worker["value"] += 1
                module = FakeBrowser(next_worker["value"])
                browser_modules.append(module)
                return module

        persisted = []
        persist_lock = threading.Lock()
        runtime_namespace = {
            "_save_mail_credential": lambda _email, _token, _log=None: True,
            "_append_account_line": lambda _path, email, _password, _sso: (
                persist_lock.acquire(), persisted.append(email), persist_lock.release()
            ),
            "_queue_unsaved_account": lambda *_args, **_kwargs: True,
            "add_token_to_grok2api_pools": lambda *_args, **_kwargs: {},
            "maybe_export_cpa_xai_after_success": lambda **_kwargs: {
                "ok": False, "skipped": True, "reason": "disabled"
            },
            "sleep_with_cancel": lambda _seconds, cancel: (
                (_ for _ in ()).throw(Cancelled()) if cancel() else None
            ),
            "RegistrationCancelled": Cancelled,
            "AccountRetryNeeded": RetryNeeded,
        }
        observed = []
        callbacks = RegistrationCallbacks(log=lambda _message: None, cancelled=lambda: False)

        with patch("registration_parallel.load_isolated_module", side_effect=fake_loader), patch(
            "cpa_xai.browser_confirm.shutdown_mint_browsers", return_value=None
        ):
            result = run_parallel_batch(
                count=5,
                callbacks=callbacks,
                observer=lambda batch, _account, _output: observed.append(batch.processed_count),
                runtime_namespace=runtime_namespace,
                accounts_output_file="unused.txt",
                workers=2,
                enable_nsfw=False,
                cleanup_interval=0,
            )

        self.assertEqual(result.processed_count, 5)
        self.assertEqual(result.success_count, 5)
        self.assertEqual(result.fail_count, 0)
        self.assertEqual(len(persisted), 5)
        self.assertEqual(len(set(persisted)), 5)
        self.assertEqual(len(browser_modules), 2)
        self.assertTrue(all(module.browser is None and module.page is None for module in browser_modules))
        self.assertTrue(observed)
        self.assertLessEqual(max(observed), 5)
        self.assertEqual(observed, sorted(observed))


if __name__ == "__main__":
    unittest.main()
