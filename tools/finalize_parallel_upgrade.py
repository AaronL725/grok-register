from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new, label):
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected one match, got {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Keep editable GUI worker count inside the existing validation/ValueError boundary.
replace_once(
    "grok_register_ttk.py",
    '        config["multi_thread_enabled"] = bool(self.multi_thread_var.get())\n        config["multi_thread_workers"] = int(self.multi_thread_workers_var.get())\n        raw_paths = [x.strip() for x in self.cloudflare_paths_var.get().split(",") if x.strip()]\n',
    '        config["multi_thread_enabled"] = bool(self.multi_thread_var.get())\n        raw_paths = [x.strip() for x in self.cloudflare_paths_var.get().split(",") if x.strip()]\n',
    "remove early GUI worker parse",
)
replace_once(
    "grok_register_ttk.py",
    '        try:\n            count = int(self.count_var.get())\n            config["register_count"] = count\n',
    '        try:\n            count = int(self.count_var.get())\n            config["register_count"] = count\n            config["multi_thread_workers"] = int(self.multi_thread_workers_var.get())\n',
    "parse GUI worker count inside validation block",
)

# Exercise the real coordinator with isolated fake worker modules, not only helper functions.
test_path = ROOT / "tests/test_optional_multithread.py"
text = test_path.read_text(encoding="utf-8")
text = text.replace("import threading\n", "import threading\nfrom unittest.mock import patch\n", 1)
text = text.replace(
    "from registration_parallel import load_isolated_module, split_worker_counts\n",
    "from registration_flow import RegistrationCallbacks\nfrom registration_parallel import load_isolated_module, run_parallel_batch, split_worker_counts\n",
    1,
)
marker = '''\n\nif __name__ == "__main__":\n    unittest.main()\n'''
if text.count(marker) != 1:
    raise RuntimeError("test insertion marker mismatch")
addition = r'''

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
'''
text = text.replace(marker, addition + marker, 1)
test_path.write_text(text, encoding="utf-8")

for relative in (
    "tools/finalize_parallel_upgrade.py",
    ".github/workflows/finalize-parallel-upgrade.yml",
):
    target = ROOT / relative
    if target.exists():
        target.unlink()

print("final parallel review fixes applied")
