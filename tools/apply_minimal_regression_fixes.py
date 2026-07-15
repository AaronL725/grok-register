#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def write(path, text):
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text, old, new, label):
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


# 1. CPA hotload copy failures remain local-success, but become partial warnings.
path = "cpa_export.py"
text = read(path)
old = '''        except Exception as exc:
            result["cpa_copy_error"] = str(exc)
            log("[cpa] hotload copy failed: %s" % exc)
'''
new = '''        except Exception as exc:
            result["cpa_copy_error"] = str(exc)
            result["warning"] = True
            result["partial"] = True
            log("[cpa] hotload copy failed: %s" % exc)
'''
text = replace_once(text, old, new, "CPA copy warning flags")
write(path, text)


# 2. Public flow isolates optional NSFW exceptions and counts partial CPA warnings.
path = "registration_flow.py"
text = read(path)
old = '''    if enable_nsfw:
        callbacks.log("[*] 6. 开启 NSFW")
        nsfw_ok, nsfw_msg = ops.enable_nsfw(sso)
        if nsfw_ok:
            callbacks.log(f"[+] NSFW 开启成功: {nsfw_msg}")
        else:
            callbacks.log(f"[!] NSFW 未开启，继续保存账号: {nsfw_msg}")
'''
new = '''    if enable_nsfw:
        callbacks.log("[*] 6. 开启 NSFW")
        try:
            nsfw_ok, nsfw_msg = ops.enable_nsfw(sso)
            if nsfw_ok:
                callbacks.log(f"[+] NSFW 开启成功: {nsfw_msg}")
            else:
                callbacks.log(f"[!] NSFW 未开启，继续保存账号: {nsfw_msg}")
        except Exception as exc:
            callbacks.log(f"[!] NSFW 开启异常，继续保存账号: {exc}")
'''
text = replace_once(text, old, new, "NSFW exception isolation")
old = '''                cpa_warning = bool(output.cpa and not output.cpa.get("ok") and not output.cpa.get("skipped"))
'''
new = '''                cpa_warning = bool(
                    output.cpa
                    and not output.cpa.get("skipped")
                    and (
                        not output.cpa.get("ok")
                        or output.cpa.get("warning")
                        or output.cpa.get("cpa_copy_error")
                    )
                )
'''
text = replace_once(text, old, new, "CPA partial warning statistics")
write(path, text)


# 3. DuckMail and YYDS retry the same message up to five times.
path = "mail_service.py"
text = read(path)
old = '''    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
'''
new = '''    deadline = time.time() + timeout
    seen_attempts = {}
    while time.time() < deadline:
'''
# The same prelude exists in exactly DuckMail and YYDS.
if "seen_ids = set()" in text:
    if text.count(old) != 2:
        raise RuntimeError(f"mail retry preludes: expected two matches, got {text.count(old)}")
    text = text.replace(old, new, 2)

old = '''            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if email.lower() not in recipients:
                continue
            try:
'''
new = '''            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id:
                continue
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if email.lower() not in recipients:
                continue
            attempt = int(seen_attempts.get(msg_id, 0))
            if attempt >= 5:
                continue
            seen_attempts[msg_id] = attempt + 1
            try:
'''
text = replace_once(text, old, new, "DuckMail message retries")
old = '''            msg_id = msg.get("id")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            to_addrs = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if address.lower() not in to_addrs:
                continue
            try:
'''
new = '''            msg_id = msg.get("id")
            if not msg_id:
                continue
            to_addrs = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if address.lower() not in to_addrs:
                continue
            attempt = int(seen_attempts.get(msg_id, 0))
            if attempt >= 5:
                continue
            seen_attempts[msg_id] = attempt + 1
            try:
'''
text = replace_once(text, old, new, "YYDS message retries")
write(path, text)


# 4. Pending recovery locks both pending and target paths in deterministic order.
path = "account_outputs.py"
text = read(path)
text = replace_once(
    text,
    "import time\nfrom datetime import datetime, timezone\n",
    "import time\nfrom contextlib import ExitStack\nfrom datetime import datetime, timezone\n",
    "ExitStack import",
)
old = '''    lock_path = pending_path + ".lock"
    with FileLock(lock_path, timeout=30):
        if not os.path.isfile(pending_path):
'''
new = '''    lock_paths = sorted(
        {pending_path + ".lock", target_path + ".lock"},
        key=lambda value: os.path.normcase(os.path.abspath(value)),
    )
    with ExitStack() as stack:
        for lock_path in lock_paths:
            stack.enter_context(FileLock(lock_path, timeout=30))
        if not os.path.isfile(pending_path):
'''
text = replace_once(text, old, new, "pending and target locks")
write(path, text)


# 5. Preserve main-module constant read/write compatibility without broadening proxies.
path = "grok_register_ttk.py"
text = read(path)
text = replace_once(
    text,
    '''from app_config import (
    CONFIG_FILE, DEFAULT_CONFIG, ConfigError, config, load_config, save_config,
''',
    '''from app_config import (
    DEFAULT_CONFIG, ConfigError, config, load_config, save_config,
''',
    "remove copied CONFIG_FILE",
)
old = '''def __getattr__(name):
    if name in {"browser", "page", "browser_proxy_bridge", "browser_started_with_proxy", "cf_clearance"}:
        return getattr(_registration_browser, name)
'''
new = '''def __getattr__(name):
    if name == "CONFIG_FILE":
        return _app_config.CONFIG_FILE
    if name == "SIGNUP_URL":
        return _registration_browser.SIGNUP_URL
    if name in {"browser", "page", "browser_proxy_bridge", "browser_started_with_proxy", "cf_clearance"}:
        return getattr(_registration_browser, name)
'''
text = replace_once(text, old, new, "constant read compatibility")
old = '''    def __setattr__(self, name, value):
        if name == "config":
'''
new = '''    def __setattr__(self, name, value):
        if name == "CONFIG_FILE":
            _app_config.CONFIG_FILE = str(value)
            self.__dict__.pop(name, None)
            return
        if name == "SIGNUP_URL":
            _registration_browser.SIGNUP_URL = str(value)
            self.__dict__.pop(name, None)
            return
        if name == "config":
'''
text = replace_once(text, old, new, "constant write compatibility")
old = '''    if result.get("ok"):
        exported_path = result.get("hotload_path") or result.get("path") or ""
        suffix = f": {exported_path}" if exported_path else ""
        logger(f"[+] CPA OIDC 导出成功{suffix}")
    elif not result.get("skipped"):
'''
new = '''    if result.get("ok"):
        exported_path = result.get("hotload_path") or result.get("path") or ""
        suffix = f": {exported_path}" if exported_path else ""
        if result.get("warning") or result.get("partial") or result.get("cpa_copy_error"):
            detail = result.get("cpa_copy_error") or "后处理未完整完成"
            logger(f"[!] CPA OIDC 凭证已生成，但存在后处理警告{suffix}: {detail}")
        else:
            logger(f"[+] CPA OIDC 导出成功{suffix}")
    elif not result.get("skipped"):
'''
text = replace_once(text, old, new, "CPA partial success logging")
write(path, text)


# Focused regression tests only for the changed boundaries.
tests = r'''import json
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
'''
write("tests/test_minimal_boundary_regressions.py", tests)

print("minimal regression fixes applied")
