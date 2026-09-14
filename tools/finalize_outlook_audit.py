from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new, label):
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit("%s: expected exactly one match, got %s" % (label, count))
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Always try every configured OAuth endpoint. A tenant-specific failure from
# one endpoint must not prevent the compatibility endpoint from succeeding.
replace_once(
    "outlook_mail.py",
    '''        if response.status_code != 200:\n            last_error = _microsoft_oauth_error_message(label, response.status_code, data)\n            if is_terminal_microsoft_token_error(last_error):\n                raise RuntimeError(last_error)\n            continue\n''',
    '''        if response.status_code != 200:\n            last_error = _microsoft_oauth_error_message(label, response.status_code, data)\n            continue\n''',
    "OAuth endpoint fallback",
)

# In auto mode establish baselines independently for IMAP and Graph and retain
# only channels that were successfully baselined before the verification mail
# can be sent. This prevents a recovered channel from scanning pre-existing mail.
replace_once(
    "outlook_mail.py",
    '''def load_folder_counts(account: OutlookAccount) -> dict[str, int]:\n    mode = normalize_outlook_mode(account.mode)\n    errors: list[str] = []\n    if mode in {"imap", "auto"}:\n        try:\n            token = refresh_outlook_imap_token(account)\n            client = _connect_imap(account, token)\n            try:\n                counts: dict[str, int] = {}\n                for folder in _discover_folders(client):\n                    total = _select_folder_count(client, folder)\n                    if total is not None:\n                        counts[folder] = total\n                if counts or mode == "imap":\n                    return counts\n                errors.append("IMAP: 未发现可读取的邮件文件夹")\n            finally:\n                try:\n                    client.logout()\n                except Exception:\n                    pass\n        except Exception as exc:\n            if mode == "imap":\n                raise\n            errors.append(f"IMAP: {exc}")\n    if mode in {"graph", "auto"}:\n        try:\n            token = refresh_outlook_graph_token(account)\n            return {OUTLOOK_GRAPH_INBOX_KEY: _graph_inbox_count(token)}\n        except Exception as exc:\n            if mode == "graph":\n                raise\n            errors.append(f"Graph: {exc}")\n    if errors:\n        raise RuntimeError("; ".join(errors))\n    return {}\n''',
    '''def load_folder_counts(account: OutlookAccount) -> dict[str, int]:\n    mode = normalize_outlook_mode(account.mode)\n    errors: list[str] = []\n    counts: dict[str, int] = {}\n\n    if mode in {"imap", "auto"}:\n        try:\n            token = refresh_outlook_imap_token(account)\n            client = _connect_imap(account, token)\n            try:\n                imap_counts: dict[str, int] = {}\n                for folder in _discover_folders(client):\n                    total = _select_folder_count(client, folder)\n                    if total is not None:\n                        imap_counts[folder] = total\n            finally:\n                try:\n                    client.logout()\n                except Exception:\n                    pass\n            if mode == "imap":\n                return imap_counts\n            if imap_counts:\n                counts.update(imap_counts)\n            else:\n                errors.append("IMAP: 未发现可读取的邮件文件夹")\n        except Exception as exc:\n            if mode == "imap":\n                raise\n            errors.append(f"IMAP: {exc}")\n\n    if mode in {"graph", "auto"}:\n        try:\n            token = refresh_outlook_graph_token(account)\n            graph_count = _graph_inbox_count(token)\n            if mode == "graph":\n                return {OUTLOOK_GRAPH_INBOX_KEY: graph_count}\n            counts[OUTLOOK_GRAPH_INBOX_KEY] = graph_count\n        except Exception as exc:\n            if mode == "graph":\n                raise\n            errors.append(f"Graph: {exc}")\n\n    if counts:\n        return counts\n    if errors:\n        raise RuntimeError("; ".join(errors))\n    return {}\n''',
    "per-channel pre-send baselines",
)

replace_once(
    "outlook_mail.py",
    '''def wait_for_outlook_code(\n    account: OutlookAccount,\n    before_counts: Optional[dict[str, int]],\n    timeout: int = 180,\n    interval: int = 3,\n    cancel_callback=None,\n    log_callback: LogCallback = None,\n) -> Optional[str]:\n    mode = normalize_outlook_mode(account.mode)\n    deadline = time.time() + max(int(timeout), 1)\n    counts = before_counts if before_counts is not None else {}\n    if not counts:\n        counts["INBOX"] = 0\n\n    imap_token: Optional[str] = None\n    graph_token: Optional[str] = None\n    imap_terminal = mode == "graph"\n    graph_terminal = mode == "imap"\n    terminal_errors: list[str] = []\n    attempt = 0\n\n    _log(log_callback, f"[*] Outlook 等待验证码: {account.email}（模式: {mode}）")\n''',
    '''def wait_for_outlook_code(\n    account: OutlookAccount,\n    before_counts: Optional[dict[str, int]],\n    timeout: int = 180,\n    interval: int = 3,\n    cancel_callback=None,\n    log_callback: LogCallback = None,\n    resend_callback=None,\n) -> Optional[str]:\n    mode = normalize_outlook_mode(account.mode)\n    deadline = time.time() + max(int(timeout), 1)\n    counts = before_counts if before_counts is not None else {}\n    if not counts:\n        if mode == "graph":\n            counts[OUTLOOK_GRAPH_INBOX_KEY] = 0\n        else:\n            counts["INBOX"] = 0\n\n    imap_baselined = any(key != OUTLOOK_GRAPH_INBOX_KEY for key in counts)\n    graph_baselined = OUTLOOK_GRAPH_INBOX_KEY in counts\n    imap_token: Optional[str] = None\n    graph_token: Optional[str] = None\n    imap_terminal = mode == "graph" or (mode == "auto" and not imap_baselined)\n    graph_terminal = mode == "imap" or (mode == "auto" and not graph_baselined)\n    terminal_errors: list[str] = []\n    attempt = 0\n    next_resend_at = time.time() + 35\n\n    _log(log_callback, f"[*] Outlook 等待验证码: {account.email}（模式: {mode}）")\n    if mode == "auto" and imap_terminal:\n        _log(log_callback, "[*] Outlook auto: IMAP 未建立发送前基线，本次禁用 IMAP 通道")\n    if mode == "auto" and graph_terminal:\n        _log(log_callback, "[*] Outlook auto: Graph 未建立发送前基线，本次禁用 Graph 通道")\n''',
    "safe auto channel gating",
)

replace_once(
    "outlook_mail.py",
    '''        attempt += 1\n        if attempt == 1 or attempt % 3 == 0:\n            _log(log_callback, f"[*] 仍在等待 Outlook 验证码，剩余约 {max(0, int(deadline-time.time()))}s")\n\n        if mode in {"imap", "auto"} and not imap_terminal:\n''',
    '''        attempt += 1\n        if attempt == 1 or attempt % 3 == 0:\n            _log(log_callback, f"[*] 仍在等待 Outlook 验证码，剩余约 {max(0, int(deadline-time.time()))}s")\n        if resend_callback and time.time() >= next_resend_at:\n            try:\n                resend_callback()\n                _log(log_callback, "[*] 已触发重新发送验证码")\n            except Exception as exc:\n                _log(log_callback, f"[Debug] 触发重发验证码失败: {exc}")\n            next_resend_at = time.time() + 35\n\n        if mode in {"imap", "auto"} and not imap_terminal:\n''',
    "Outlook resend compatibility",
)

replace_once(
    "outlook_mail.py",
    '''    def wait_for_code(self, timeout: int = 180, interval: int = 3, cancel_callback=None) -> Optional[str]:\n        return wait_for_outlook_code(\n            self.account, self._folder_counts, timeout=timeout, interval=interval,\n            cancel_callback=cancel_callback, log_callback=self._log_callback,\n        )\n''',
    '''    def wait_for_code(\n        self, timeout: int = 180, interval: int = 3, cancel_callback=None, resend_callback=None\n    ) -> Optional[str]:\n        return wait_for_outlook_code(\n            self.account, self._folder_counts, timeout=timeout, interval=interval,\n            cancel_callback=cancel_callback, log_callback=self._log_callback,\n            resend_callback=resend_callback,\n        )\n''',
    "mailbox resend forwarding",
)

replace_once(
    "outlook_mailbox_pool.py",
    '''    ) -> str:\n        del resend_callback\n        with self._lock:\n''',
    '''    ) -> str:\n        with self._lock:\n''',
    "retain resend callback",
)

replace_once(
    "outlook_mailbox_pool.py",
    '''            code = lease.mailbox.wait_for_code(\n                timeout=int(timeout),\n                interval=int(poll_interval),\n                cancel_callback=cancel_callback,\n            )\n''',
    '''            code = lease.mailbox.wait_for_code(\n                timeout=int(timeout),\n                interval=int(poll_interval),\n                cancel_callback=cancel_callback,\n                resend_callback=resend_callback,\n            )\n''',
    "runtime resend forwarding",
)

replace_once(
    "registration_flow.py",
    '''    callbacks.log(f"[*] 验证码: {code}")\n''',
    '''    callbacks.log("[*] 验证码已获取并提交（内容已隐藏）")\n''',
    "verification code log redaction",
)

# Existing auto-mode tests must declare both channels as pre-send-baselined.
replace_once(
    "tests/test_outlook_mailbox_pool.py",
    '''        counts = {"INBOX": 0}\n        with patch.object(\n            outlook_mail, "refresh_outlook_imap_token", side_effect=RuntimeError("temporary imap error")\n''',
    '''        counts = {"INBOX": 0, outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: 0}\n        with patch.object(\n            outlook_mail, "refresh_outlook_imap_token", side_effect=RuntimeError("temporary imap error")\n''',
    "auto fallback test baselines",
)

replace_once(
    "tests/test_outlook_mailbox_pool.py",
    '''                outlook_mail.wait_for_outlook_code(account, {"INBOX": 0}, timeout=30, interval=3)\n''',
    '''                outlook_mail.wait_for_outlook_code(\n                    account, {"INBOX": 0, outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: 0}, timeout=30, interval=3\n                )\n''',
    "double terminal test baselines",
)

replace_once(
    "README.md",
    '''- `auto`：优先尝试 IMAP，并在同一轮轮询中使用 Microsoft Graph 作为补充。\n''',
    '''- `auto`：提交邮箱前分别尝试为 IMAP 与 Microsoft Graph 建立基线；轮询时只启用已成功建立基线的通道。两者均可用时会在同一轮轮询中同时使用，避免通道恢复后误读旧邮件。\n''',
    "document safe auto semantics",
)

(ROOT / "tests/test_outlook_audit.py").write_text(
    r'''import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import Mock, patch

import outlook_mail
import outlook_mailbox_pool as pool
import registration_parallel
from registration_flow import RegistrationCallbacks, RegistrationOperations, run_batch


class Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class OutlookAuditTests(unittest.TestCase):
    def test_graph_oauth_tries_compatibility_endpoint_after_first_terminal_error(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "graph")
        responses = [
            Response(400, {"error": "invalid_grant", "error_description": "AADSTS7000012 wrong tenant"}),
            Response(200, {"access_token": "graph-access"}),
        ]
        with patch.object(outlook_mail.requests, "post", side_effect=responses) as post:
            self.assertEqual(outlook_mail.refresh_outlook_graph_token(account), "graph-access")
            self.assertEqual(post.call_count, 2)

    def test_auto_prepare_records_independent_imap_and_graph_baselines(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "auto")
        client = Mock()
        with patch.object(outlook_mail, "refresh_outlook_imap_token", return_value="imap"), \
             patch.object(outlook_mail, "_connect_imap", return_value=client), \
             patch.object(outlook_mail, "_discover_folders", return_value=["INBOX"]), \
             patch.object(outlook_mail, "_select_folder_count", return_value=4), \
             patch.object(outlook_mail, "refresh_outlook_graph_token", return_value="graph"), \
             patch.object(outlook_mail, "_graph_inbox_count", return_value=7):
            counts = outlook_mail.load_folder_counts(account)
        self.assertEqual(counts["INBOX"], 4)
        self.assertEqual(counts[outlook_mail.OUTLOOK_GRAPH_INBOX_KEY], 7)
        client.logout.assert_called_once_with()

    def test_auto_wait_never_enables_channel_without_pre_send_baseline(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "auto")
        with patch.object(outlook_mail, "refresh_outlook_imap_token") as imap_refresh, \
             patch.object(outlook_mail, "refresh_outlook_graph_token", return_value="graph"), \
             patch.object(outlook_mail, "_scan_graph_once", return_value="ABC123"):
            code = outlook_mail.wait_for_outlook_code(
                account,
                {outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: 3},
                timeout=2,
                interval=0,
            )
        self.assertEqual(code, "ABC123")
        imap_refresh.assert_not_called()

    def test_runtime_forwards_resend_callback(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "auto")
        runtime = pool.OutlookTaskRuntime([account])
        with patch.object(pool.OutlookMailbox, "prepare", return_value=None):
            email, handle = runtime.acquire()
        resend = Mock()
        with patch.object(pool.OutlookMailbox, "wait_for_code", return_value="ABC123") as wait:
            self.assertEqual(runtime.wait_for_code(handle, email, resend_callback=resend), "ABC123")
        self.assertIs(wait.call_args.kwargs["resend_callback"], resend)

    def test_xoauth2_format(self):
        self.assertEqual(
            outlook_mail._xoauth2_auth_string("u@example.com", "access"),
            b"user=u@example.com\x01auth=Bearer access\x01\x01",
        )

    def test_imap_unchanged_baseline_does_not_read_old_message(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "imap")
        client = Mock()
        counts = {"INBOX": 10}
        with patch.object(outlook_mail, "_connect_imap", return_value=client), \
             patch.object(outlook_mail, "_discover_folders", return_value=["INBOX"]), \
             patch.object(outlook_mail, "_select_folder_count", return_value=10), \
             patch.object(outlook_mail, "_fetch_message_content") as fetch:
            self.assertIsNone(outlook_mail._scan_imap_once(account, "token", counts))
        fetch.assert_not_called()
        client.logout.assert_called_once_with()

    def test_mime_parser_reads_subject_sender_text_and_html(self):
        message = EmailMessage()
        message["Subject"] = "ABC-123 verification"
        message["From"] = "xAI <no-reply@x.ai>"
        message.set_content("plain verification body")
        message.add_alternative("<b>html verification body</b>", subtype="html")
        client = Mock()
        client.fetch.return_value = ("OK", [(b"1 (BODY[] {1})", message.as_bytes())])
        subject, text, html, sender = outlook_mail._fetch_message_content(client, 1)
        self.assertIn("ABC-123", subject)
        self.assertIn("plain verification body", text)
        self.assertIn("html verification body", html)
        self.assertIn("no-reply@x.ai", sender)

    def test_isolated_mail_workers_share_one_outlook_allocator(self):
        rows = "\n".join(
            "u%s@example.com----pw----client%s----refresh%s----auto" % (i, i, i)
            for i in range(1, 5)
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outlook.txt"
            pool.save_outlook_mailbox_pool(path, rows)
            runtime = pool.create_outlook_task_runtime(path)
            modules = [
                registration_parallel.load_isolated_module(
                    Path(registration_parallel.__file__).resolve().parent / "mail_service.py",
                    "_outlook_audit_worker_%s" % i,
                )
                for i in range(4)
            ]
            for module in modules:
                module.bind_runtime({"config": {"email_provider": "outlook"}, "outlook_runtime": runtime})
            with patch.object(pool.OutlookMailbox, "prepare", return_value=None), \
                 ThreadPoolExecutor(max_workers=4) as executor:
                pairs = list(executor.map(lambda module: module.get_email_and_token(), modules))
        self.assertEqual(len({email for email, _handle in pairs}), 4)
        self.assertEqual(len({handle for _email, handle in pairs}), 4)

    def test_shared_registration_flow_does_not_log_verification_code(self):
        logs = []
        callbacks = RegistrationCallbacks(log=logs.append, cancelled=lambda: False)
        ops = RegistrationOperations(
            start_browser=lambda: None,
            restart_browser=lambda: None,
            browser_missing=lambda: False,
            open_signup_page=lambda: None,
            fill_email_and_submit=lambda: ("u@example.com", "mail-token"),
            save_mail_credential=lambda _email, _token: True,
            fill_code_and_submit=lambda _email, _token: "SECRET123",
            fill_profile_and_submit=lambda: {"given_name": "A", "family_name": "B", "password": "pw"},
            wait_for_sso_cookie=lambda: "sso",
            enable_nsfw=lambda _sso: (True, "ok"),
            persist_account_line=lambda _email, _password, _sso: None,
            queue_unsaved_result=lambda _payload, _error: True,
            add_tokens=lambda _sso, _email: {},
            export_cpa=lambda _email, _password, _sso: {"ok": False, "skipped": True},
            cleanup=lambda _reason: None,
            sleep=lambda _seconds: None,
            cancelled_exception=RuntimeError,
            retry_exception=ValueError,
        )
        batch = run_batch(1, callbacks, lambda *_args: None, ops, enable_nsfw=False)
        self.assertEqual(batch.success_count, 1)
        self.assertNotIn("SECRET123", "\n".join(logs))


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)

print("final Outlook audit hardening applied")
