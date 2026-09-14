import os
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import account_outputs
import mail_service
import outlook_mail
import outlook_mailbox_pool as pool
from registration_flow import VerificationCodeUnavailable


ACCOUNT_ROWS = "\n".join(
    "user%s@example.com----pw%s----client%s----refresh-token-%s----auto" % (i, i, i, i)
    for i in range(1, 9)
)


class Response:
    status_code = 200
    text = ""
    headers = {}
    ok = True

    def json(self):
        return {"access_token": "access"}


class OutlookMailboxPoolTests(unittest.TestCase):
    def test_parser_supports_dash_pipe_and_modes(self):
        data = (
            "a@example.com----pw----client----refresh-token----imap\n"
            "b@example.com|pw|client2|refresh-token-2|graph\n"
            "c@example.com----pw----client3----refresh-token-3\n"
        )
        accounts = pool.parse_outlook_accounts(data)
        self.assertEqual([a.mode for a in accounts], ["imap", "graph", "auto"])
        self.assertEqual(accounts[0].refresh_token, "refresh-token")

    def test_invalid_mode_and_duplicate_are_reported_without_secrets(self):
        secret = "super-secret-refresh-token"
        data = (
            "a@example.com----pw----client----%s----badmode\n" % secret
            + "b@example.com----pw----client----token----auto\n"
            + "B@example.com----pw----client2----token2----imap\n"
        )
        summary = pool.inspect_outlook_mailbox_pool(data)
        self.assertEqual(summary["invalid"], 1)
        self.assertEqual(summary["duplicates"], ["b@example.com"])
        self.assertNotIn(secret, repr(summary["accounts"]))

    def test_private_atomic_save_and_safe_load_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pool.txt"
            saved = pool.save_outlook_mailbox_pool(path, ACCOUNT_ROWS)
            self.assertEqual(saved["count"], 8)
            loaded = pool.load_outlook_mailbox_pool(path)
            self.assertEqual(loaded["count"], 8)
            self.assertEqual(len(loaded["accounts"]), 8)
            self.assertNotIn("refresh-token", repr(loaded["accounts"]))
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_task_runtime_is_shared_thread_safe_and_non_reusing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            pool.OutlookMailbox, "prepare", return_value=None
        ):
            path = Path(tmp) / "pool.txt"
            pool.save_outlook_mailbox_pool(path, ACCOUNT_ROWS)
            runtime = pool.create_outlook_task_runtime(path)
            with ThreadPoolExecutor(max_workers=8) as executor:
                pairs = list(executor.map(lambda _i: runtime.acquire(), range(8)))
            self.assertEqual(len({email for email, _handle in pairs}), 8)
            self.assertEqual(len({handle for _email, handle in pairs}), 8)
            self.assertTrue(all(handle.startswith("outlook:") for _email, handle in pairs))
            with self.assertRaises(RuntimeError):
                runtime.acquire()
            runtime.close()

    def test_new_task_gets_fresh_allocator(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            pool.OutlookMailbox, "prepare", return_value=None
        ):
            path = Path(tmp) / "pool.txt"
            pool.save_outlook_mailbox_pool(path, ACCOUNT_ROWS)
            first = pool.create_outlook_task_runtime(path)
            second = pool.create_outlook_task_runtime(path)
            self.assertEqual(first.acquire()[0], "user1@example.com")
            self.assertEqual(second.acquire()[0], "user1@example.com")

    def test_runtime_handle_is_one_shot_and_timeout_is_typed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            pool.OutlookMailbox, "prepare", return_value=None
        ):
            path = Path(tmp) / "pool.txt"
            pool.save_outlook_mailbox_pool(path, ACCOUNT_ROWS)
            runtime = pool.create_outlook_task_runtime(path)
            email, handle = runtime.acquire()
            with patch.object(pool.OutlookMailbox, "wait_for_code", return_value="ABC123"):
                self.assertEqual(runtime.wait_for_code(handle, email), "ABC123")
            with self.assertRaisesRegex(RuntimeError, "已使用|已失效|不存在"):
                runtime.wait_for_code(handle, email)

            email2, handle2 = runtime.acquire()
            with patch.object(pool.OutlookMailbox, "wait_for_code", return_value=None):
                with self.assertRaises(VerificationCodeUnavailable):
                    runtime.wait_for_code(handle2, email2, timeout=1)
            with self.assertRaisesRegex(RuntimeError, "已使用|已失效|不存在"):
                runtime.wait_for_code(handle2, email2)

    def test_wrong_email_does_not_consume_handle(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            pool.OutlookMailbox, "prepare", return_value=None
        ):
            path = Path(tmp) / "pool.txt"
            pool.save_outlook_mailbox_pool(path, ACCOUNT_ROWS)
            runtime = pool.create_outlook_task_runtime(path)
            email, handle = runtime.acquire()
            with self.assertRaisesRegex(RuntimeError, "不匹配"):
                runtime.wait_for_code(handle, "wrong@example.com")
            with patch.object(pool.OutlookMailbox, "wait_for_code", return_value="ABC123"):
                self.assertEqual(runtime.wait_for_code(handle, email), "ABC123")

    def test_mail_service_dispatches_to_injected_runtime(self):
        calls = []

        class FakeRuntime:
            def acquire(self):
                calls.append("acquire")
                return "u@example.com", "outlook:opaque"

            def wait_for_code(self, *args, **kwargs):
                calls.append((args, kwargs))
                return "ABC123"

        previous_config = mail_service.config
        previous_runtime = getattr(mail_service, "outlook_runtime", None)
        had_runtime = hasattr(mail_service, "outlook_runtime")
        try:
            mail_service.bind_runtime({
                "config": {"email_provider": "outlook"},
                "outlook_runtime": FakeRuntime(),
            })
            self.assertEqual(mail_service.get_email_and_token(), ("u@example.com", "outlook:opaque"))
            self.assertEqual(
                mail_service.get_oai_code("outlook:opaque", "u@example.com", timeout=7),
                "ABC123",
            )
            self.assertEqual(calls[0], "acquire")
            self.assertEqual(calls[1][0][:2], ("outlook:opaque", "u@example.com"))
        finally:
            mail_service.config = previous_config
            if had_runtime:
                mail_service.outlook_runtime = previous_runtime
            elif hasattr(mail_service, "outlook_runtime"):
                delattr(mail_service, "outlook_runtime")

    def test_outlook_handle_is_never_persisted_as_mail_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(account_outputs.save_mail_credential(tmp, "u@example.com", "outlook:opaque"))
            self.assertFalse((Path(tmp) / "mail_credentials.txt").exists())

    def test_prepare_requires_pre_send_baseline(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh")
        mailbox = outlook_mail.OutlookMailbox(account)
        with patch.object(outlook_mail, "prepare_outlook_state", side_effect=RuntimeError("network")):
            with self.assertRaises(RuntimeError):
                mailbox.prepare()

    def test_token_refresh_uses_expected_scopes(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh")
        with patch.object(outlook_mail, "_request_with_backoff", return_value=Response()) as request:
            self.assertEqual(outlook_mail.refresh_outlook_imap_token(account), "access")
            imap_scope = request.call_args.kwargs["data"]["scope"]
            self.assertIn("IMAP.AccessAsUser.All", imap_scope)
        with patch.object(outlook_mail, "_request_with_backoff", return_value=Response()) as request:
            self.assertEqual(outlook_mail.refresh_outlook_graph_token(account), "access")
            graph_scope = request.call_args.kwargs["data"]["scope"]
            self.assertIn("Mail.Read", graph_scope)

    def test_auto_mode_can_use_graph_when_imap_was_not_baselined(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "auto")
        state = outlook_mail.OutlookMailboxState(
            graph={
                outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: outlook_mail.GraphFolderCursor(
                    "inbox", outlook_mail.OUTLOOK_GRAPH_INBOX_KEY
                )
            },
            graph_token="graph-token",
        )
        with patch.object(outlook_mail, "refresh_outlook_imap_token") as imap_refresh, patch.object(
            outlook_mail, "_scan_graph_once", return_value="ABC123"
        ):
            self.assertEqual(
                outlook_mail.wait_for_outlook_code(account, state, timeout=2, interval=0),
                "ABC123",
            )
        imap_refresh.assert_not_called()

    def test_double_terminal_oauth_error_fails_fast(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "auto")
        folder = outlook_mail.ImapFolderRef("INBOX", "INBOX")
        state = outlook_mail.OutlookMailboxState(
            imap={"INBOX": outlook_mail.ImapFolderCursor(folder, "42", 1)},
            graph={
                outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: outlook_mail.GraphFolderCursor(
                    "inbox", outlook_mail.OUTLOOK_GRAPH_INBOX_KEY
                )
            },
        )
        with patch.object(
            outlook_mail, "refresh_outlook_imap_token", side_effect=RuntimeError("invalid_grant")
        ), patch.object(
            outlook_mail, "refresh_outlook_graph_token", side_effect=RuntimeError("invalid_grant")
        ), patch.object(outlook_mail, "_connect_imap", side_effect=RuntimeError("invalid_grant")), patch.object(
            outlook_mail, "_sleep_interruptibly"
        ) as sleep:
            with self.assertRaises(RuntimeError):
                outlook_mail.wait_for_outlook_code(account, state, timeout=30, interval=3)
            sleep.assert_not_called()

    def test_pool_health_summary_contains_no_credentials(self):
        data = (
            "a@example.com----password----client-a----super-secret-token----auto\n"
            "b@example.com----password----client-b----another-secret-token----graph\n"
        )
        safe_results = [
            {
                "email": "a@example.com", "mode": "auto", "usable": True,
                "imap": {"ok": True, "folders": ["INBOX"], "error": ""},
                "graph": {"ok": True, "folders": ["inbox"], "error": ""},
            },
            {
                "email": "b@example.com", "mode": "graph", "usable": False,
                "imap": {"ok": False, "folders": [], "error": ""},
                "graph": {"ok": False, "folders": [], "error": "invalid_grant"},
            },
        ]
        with patch.object(pool, "probe_outlook_account", side_effect=safe_results):
            summary = pool.probe_outlook_mailbox_pool_data(data, max_workers=1)
        self.assertEqual(summary["healthy"], 1)
        self.assertEqual(summary["unhealthy"], 1)
        self.assertEqual(summary["imap"], 1)
        self.assertEqual(summary["graph"], 1)
        rendered = repr(summary)
        self.assertNotIn("super-secret-token", rendered)
        self.assertNotIn("another-secret-token", rendered)
        self.assertNotIn("password", rendered)


if __name__ == "__main__":
    unittest.main()
