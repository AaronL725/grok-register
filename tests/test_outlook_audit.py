import tempfile
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
    def test_load_rejects_oversized_pool_before_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outlook.txt"
            path.write_bytes(b"x" * 1_000_001)
            with self.assertRaisesRegex(ValueError, "过大"):
                pool.load_outlook_mailbox_pool(path)

    def test_capacity_path_uses_same_bounded_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outlook.txt"
            path.write_bytes(b"x" * 1_000_001)
            with self.assertRaisesRegex(ValueError, "过大"):
                pool.get_outlook_mailbox_pool_capacity(path)

    def test_imap_folder_with_spaces_is_quoted(self):
        client = Mock()
        client.select.return_value = ("OK", [b"5"])
        self.assertEqual(outlook_mail._select_folder_count(client, "Junk Email"), 5)
        client.select.assert_called_once_with('"Junk Email"', readonly=True)

    def test_unencodable_localized_fallback_does_not_break_other_folders(self):
        client = Mock()
        client.select.side_effect = UnicodeEncodeError(
            "ascii", "垃圾邮件", 0, 1, "ordinal not in range"
        )
        self.assertIsNone(outlook_mail._select_folder_count(client, "垃圾邮件"))

    def test_graph_fetch_is_capped_to_scan_depth(self):
        counts = {outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: 1}
        with patch.object(outlook_mail, "_graph_inbox_count", return_value=100), \
             patch.object(outlook_mail, "_graph_get", return_value={"value": []}) as get:
            self.assertIsNone(outlook_mail._scan_graph_once("token", counts))
        self.assertEqual(get.call_args.args[2]["$top"], str(outlook_mail.OUTLOOK_GRAPH_SCAN_DEPTH))

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
