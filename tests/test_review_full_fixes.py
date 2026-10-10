"""Targeted regressions for the engineering reliability review."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import account_outputs
import app_config
import outlook_mail
import outlook_mailbox_pool
import registration_browser
import registration_flow
from tests.test_registration_flow import FakeOps


class ReviewFixTests(unittest.TestCase):
    def callbacks(self):
        return registration_flow.RegistrationCallbacks(log=lambda message: None, cancelled=lambda: False)

    def test_mail_creation_failure_is_retryable_before_submit(self):
        with patch.object(registration_browser, "get_email_and_token", side_effect=TimeoutError("temporary mail timeout"), create=True), \
             patch.object(registration_browser, "raise_if_cancelled", create=True):
            with self.assertRaises(registration_flow.RecoverablePreSubmitError):
                registration_browser.fill_email_and_submit()

    def test_transient_mail_failure_does_not_abort_batch(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.internal_stage_markers = True
        calls = {"count": 0}

        def create_mail():
            calls["count"] += 1
            if calls["count"] == 1:
                raise registration_flow.RecoverablePreSubmitError("mail API timed out")
            return "user@example.com", "token"

        ops.fill_email_and_submit = create_mail
        with patch.object(registration_flow, "begin_registration_slot", return_value=None), \
             patch.object(registration_flow, "end_registration_slot", return_value=None):
            result = registration_flow.run_batch(1, self.callbacks(), lambda *_: None, ops)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.fail_count, 0)
        self.assertEqual(calls["count"], 2)

    def test_unrecoverable_account_write_skips_downstream_exports(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.persist_account_line = Mock(side_effect=OSError("disk full"))
        ops.queue_unsaved_result = Mock(return_value=False)
        ops.add_tokens = Mock()
        ops.export_cpa = Mock()
        account = registration_flow.RegistrationResult(
            ok=True, email="u@example.com", password="pw", sso="sso",
        )
        output = registration_flow.persist_account_result(account, self.callbacks(), ops)
        self.assertFalse(output.saved)
        self.assertFalse(output.pending_saved)
        ops.add_tokens.assert_not_called()
        ops.export_cpa.assert_not_called()

    def test_account_and_mail_outputs_use_regular_file_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            account_path = str(Path(directory) / "accounts_1.txt")
            account_outputs.append_account_line(account_path, "u@example.com", "pw", "sso")
            account_outputs.save_mail_credential(directory, "u@example.com", "mailsecret")
            self.assertIn("u@example.com----pw----sso", Path(account_path).read_text(encoding="utf-8"))
            self.assertIn("u@example.com\tmailsecret", (Path(directory) / "mail_credentials.txt").read_text(encoding="utf-8"))

    def test_refresh_rotation_updates_in_memory_account(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "old")
        rotations = []
        account.on_refresh = lambda old, new: rotations.append((old, new))
        response = SimpleNamespace(
            status_code=200,
            json=lambda: {"access_token": "access", "refresh_token": "rotated"},
        )
        with patch.object(outlook_mail, "_request_with_backoff", return_value=response):
            self.assertEqual(outlook_mail.refresh_outlook_graph_token(account), "access")
        self.assertEqual(account.refresh_token, "rotated")
        self.assertEqual(rotations, [("old", "rotated")])

    def test_refresh_rotation_persists_to_mailbox_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "pool.txt")
            outlook_mailbox_pool.save_outlook_mailbox_pool(
                path, "u@example.com----pw----client----old----graph\n",
            )
            runtime = outlook_mailbox_pool.create_outlook_task_runtime(path)
            account = runtime._pool._accounts[0]
            response = SimpleNamespace(
                status_code=200,
                json=lambda: {"access_token": "access", "refresh_token": "new"},
            )
            with patch.object(outlook_mail, "_request_with_backoff", return_value=response):
                self.assertEqual(outlook_mail.refresh_outlook_graph_token(account), "access")
            self.assertIn("----new----graph", outlook_mailbox_pool.load_outlook_mailbox_pool(path)["data"])
            runtime.close()

    def test_imap_scans_all_new_uids_without_skipping_older_code(self):
        total = outlook_mail.OUTLOOK_SCAN_DEPTH + 3
        folder = outlook_mail.ImapFolderRef("INBOX", "INBOX")
        cursor = outlook_mail.ImapFolderCursor(folder, "42", 0)
        state = outlook_mail.OutlookMailboxState(imap={"INBOX": cursor})
        with patch.object(outlook_mail, "_ensure_imap_client", return_value=Mock()), \
             patch.object(outlook_mail, "_select_folder_uidvalidity", return_value="42"), \
             patch.object(outlook_mail, "_search_uids_after", return_value=list(range(1, total + 1))), \
             patch.object(outlook_mail, "_fetch_message_content_by_uid",
                          side_effect=lambda client, uid: ("code" if uid == 1 else "", "", "", "no-reply@x.ai")), \
             patch.object(outlook_mail, "extract_verification_code",
                          side_effect=lambda subject, *args: "ABC123" if subject else None) as extract:
            self.assertEqual(outlook_mail._scan_imap_once(outlook_mail.OutlookAccount("u", "p", "c", "r"), state), "ABC123")
        self.assertEqual(extract.call_count, total)
        self.assertEqual(cursor.last_uid, total)

    def test_graph_follows_newer_pages_until_baseline(self):
        first = [{"id": "latest", "receivedDateTime": "2026-10-10T11:00:00Z"}]
        older = [{"id": "old", "receivedDateTime": "2026-10-10T09:00:00Z"}]
        next_link = outlook_mail.OUTLOOK_GRAPH_BASE_URL + "/me/mailFolders/inbox/messages?$skiptoken=next"
        responses = [
            {"value": first, "@odata.nextLink": next_link},
            {"value": older},
        ]
        with patch.object(outlook_mail, "_graph_get", side_effect=responses) as getter:
            items = outlook_mail._graph_messages(
                "token", "inbox", after_epoch=outlook_mail._received_epoch("2026-10-10T10:00:00Z"),
            )
        self.assertEqual(items, first + older)
        self.assertEqual(getter.call_count, 2)
        self.assertIn("$skiptoken=next", getter.call_args.args[1])

    def test_mail_logs_include_codes(self):
        self.assertIn("验证码: {code}", Path("mail_service.py").read_text(encoding="utf-8"))
        source = Path("registration_browser.py").read_text(encoding="utf-8")
        self.assertIn("等待资料页确认: {code}", source)
        self.assertIn("等待自动提交确认: {code}", source)

    def test_cpa_is_enabled_by_default(self):
        self.assertTrue(app_config.DEFAULT_CONFIG["cpa_export_enabled"])

    def test_cloudflare_debug_original_output(self):
        source = Path("cf_mail_debug.py").read_text(encoding="utf-8")
        self.assertIn('print("[NEW] credential(jwt)=%s" % credential)', source)
        self.assertIn('print("[FOUND] %s" % code)', source)


class ReviewWebFixTests(unittest.TestCase):
    def test_outlook_pool_invalid_data_does_not_switch_path(self):
        from fastapi.testclient import TestClient
        from web import server
        with tempfile.TemporaryDirectory() as directory:
            original = str(Path(directory) / "original.txt")
            candidate = str(Path(directory) / "new.txt")
            config = {**server.engine.DEFAULT_CONFIG, "outlook_accounts_file": original}

            def load():
                server.engine.config.clear()
                server.engine.config.update(config)
                return server.engine.config

            with server._job_lock:
                server._job_state["running"] = False
                server._maintenance_state = None
            with patch.object(server.engine, "load_config", side_effect=load), \
                 patch.object(server.engine, "save_config") as save:
                result = TestClient(server.app).put(
                    "/api/mailboxes/outlook",
                    json={"path": candidate, "data": "invalid"},
                )
            self.assertEqual(result.status_code, 400)
            self.assertEqual(server.engine.config["outlook_accounts_file"], original)
            self.assertFalse(Path(candidate).exists())
            save.assert_not_called()

    def test_outlook_pool_single_request_updates_file_and_config(self):
        from fastapi.testclient import TestClient
        from web import server
        with tempfile.TemporaryDirectory() as directory:
            original = str(Path(directory) / "original.txt")
            candidate = str(Path(directory) / "new.txt")
            config = {**server.engine.DEFAULT_CONFIG, "outlook_accounts_file": original}

            def load():
                server.engine.config.clear()
                server.engine.config.update(config)
                return server.engine.config

            with server._job_lock:
                server._job_state["running"] = False
                server._maintenance_state = None
            with patch.object(server.engine, "load_config", side_effect=load), \
                 patch.object(server.engine, "save_config") as save:
                result = TestClient(server.app).put(
                    "/api/mailboxes/outlook",
                    json={"path": candidate, "data": "u@example.com----pw----client----refresh----auto\n"},
                )
            self.assertEqual(result.status_code, 200)
            self.assertEqual(server.engine.config["outlook_accounts_file"], candidate)
            self.assertTrue(Path(candidate).exists())
            save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
