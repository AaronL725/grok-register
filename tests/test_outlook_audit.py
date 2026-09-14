import tempfile
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
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)
        self.ok = 200 <= status_code < 300
        self.headers = headers or {}

    def json(self):
        return self._payload


def graph_message(message_id, received, subject="", body="", sender="no-reply@x.ai"):
    return {
        "id": message_id,
        "receivedDateTime": received,
        "subject": subject,
        "bodyPreview": body,
        "body": {"content": body},
        "from": {"emailAddress": {"address": sender}},
    }


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

    def test_verification_code_rejects_untrusted_ticket_identifier(self):
        self.assertIsNone(
            outlook_mail.extract_verification_code(
                "Support ticket ABC-123",
                "Your support ticket ABC-123 has been created.",
                sender="support@example.com",
            )
        )
        self.assertEqual(
            outlook_mail._normalize_code(
                outlook_mail.extract_verification_code(
                    "Verification code",
                    "Your verification code is ABC-123",
                    sender="support@example.com",
                )
            ),
            "ABC123",
        )

    def test_verification_code_accepts_trusted_xai_and_grok_domains(self):
        for sender in ("xAI <no-reply@x.ai>", "Grok <login@accounts.grok.com>"):
            with self.subTest(sender=sender):
                code = outlook_mail.extract_verification_code("ABC-123", "", sender=sender)
                self.assertEqual(outlook_mail._normalize_code(code), "ABC123")

    def test_modified_utf7_round_trip_and_special_use_folder_detection(self):
        original = "垃圾邮件 & Archive"
        encoded = outlook_mail._encode_modified_utf7(original)
        self.assertEqual(outlook_mail._decode_modified_utf7(encoded), original)
        folder = outlook_mail._parse_imap_list_line(
            b'(\\HasNoChildren \\Junk) "/" "Junk Email"'
        )
        self.assertEqual(folder.name, "Junk Email")
        self.assertIn("\\junk", folder.attributes)
        self.assertTrue(outlook_mail._looks_like_verification_folder(folder))

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

    def test_imap_uid_cursor_ignores_mailbox_count_changes(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "imap")
        folder = outlook_mail.ImapFolderRef("INBOX", "INBOX")
        cursor = outlook_mail.ImapFolderCursor(folder, "42", 100)
        client = Mock()
        state = outlook_mail.OutlookMailboxState(
            imap={"INBOX": cursor}, imap_token="prepared-token", imap_client=client
        )
        with patch.object(outlook_mail, "_select_folder_uidvalidity", return_value="42"), \
             patch.object(outlook_mail, "_search_uids_after", return_value=[]), \
             patch.object(outlook_mail, "_fetch_message_content_by_uid") as fetch, \
             patch.object(outlook_mail, "_select_folder_count") as count:
            self.assertIsNone(outlook_mail._scan_imap_once(account, state))
        fetch.assert_not_called()
        count.assert_not_called()
        self.assertEqual(cursor.last_uid, 100)

    def test_imap_uid_cursor_fetches_only_new_uid_and_caches_folder_list(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "imap")
        folder = outlook_mail.ImapFolderRef("INBOX", "INBOX")
        cursor = outlook_mail.ImapFolderCursor(folder, "42", 100)
        client = Mock()
        state = outlook_mail.OutlookMailboxState(
            imap={"INBOX": cursor}, imap_token="prepared-token", imap_client=client
        )
        with patch.object(outlook_mail, "_select_folder_uidvalidity", return_value="42"), \
             patch.object(outlook_mail, "_search_uids_after", return_value=[101]), \
             patch.object(
                 outlook_mail,
                 "_fetch_message_content_by_uid",
                 return_value=("Verification code ABC-123", "", "", "no-reply@x.ai"),
             ) as fetch, \
             patch.object(outlook_mail, "_discover_folders") as discover:
            self.assertEqual(outlook_mail._scan_imap_once(account, state), "ABC123")
        fetch.assert_called_once_with(client, 101)
        discover.assert_not_called()
        self.assertEqual(cursor.last_uid, 101)

    def test_imap_uidvalidity_change_rebaselines_without_reading_old_mail(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "imap")
        folder = outlook_mail.ImapFolderRef("INBOX", "INBOX")
        cursor = outlook_mail.ImapFolderCursor(folder, "old", 100)
        client = Mock()
        state = outlook_mail.OutlookMailboxState(
            imap={"INBOX": cursor}, imap_token="token", imap_client=client
        )
        with patch.object(outlook_mail, "_select_folder_uidvalidity", return_value="new"), \
             patch.object(outlook_mail, "_search_all_uids", return_value=[1, 2, 3]), \
             patch.object(outlook_mail, "_fetch_message_content_by_uid") as fetch:
            self.assertIsNone(outlook_mail._scan_imap_once(account, state))
        fetch.assert_not_called()
        self.assertEqual(cursor.uidvalidity, "new")
        self.assertEqual(cursor.last_uid, 3)

    def test_graph_fetch_is_capped_to_scan_depth(self):
        with patch.object(outlook_mail, "_graph_get", return_value={"value": []}) as get:
            self.assertEqual(outlook_mail._graph_messages("token", "inbox"), [])
        params = get.call_args.args[2]
        self.assertEqual(params["$top"], str(outlook_mail.OUTLOOK_GRAPH_SCAN_DEPTH))
        self.assertIn("id", params["$select"])
        self.assertIn("receivedDateTime", params["$select"])

    def test_graph_poll_fetches_body_only_after_new_frontier_item(self):
        cursor = outlook_mail.GraphFolderCursor(
            "inbox", outlook_mail.OUTLOOK_GRAPH_INBOX_KEY,
            newest_received="2026-09-14T10:00:00Z",
            seen_ids={"baseline"},
        )
        state = outlook_mail.OutlookMailboxState(
            graph={cursor.key: cursor}, graph_token="token"
        )
        unchanged = [graph_message("baseline", "2026-09-14T10:00:00Z")]
        with patch.object(outlook_mail, "_graph_messages", return_value=unchanged), \
             patch.object(outlook_mail, "_graph_message_detail") as detail:
            self.assertIsNone(outlook_mail._scan_graph_once("token", state))
        detail.assert_not_called()

        metadata = {"id": "new", "receivedDateTime": "2026-09-14T10:01:00Z"}
        detail_message = graph_message(
            "new", "2026-09-14T10:01:00Z",
            "Verification code ABC-123", "ABC-123", "no-reply@x.ai",
        )
        with patch.object(outlook_mail, "_graph_messages", return_value=[metadata]), \
             patch.object(outlook_mail, "_graph_message_detail", return_value=detail_message) as detail:
            self.assertEqual(outlook_mail._scan_graph_once("token", state), "ABC123")
        detail.assert_called_once_with("token", "new", cancel_callback=None)

    def test_graph_detail_failure_does_not_advance_cursor(self):
        cursor = outlook_mail.GraphFolderCursor(
            "inbox", outlook_mail.OUTLOOK_GRAPH_INBOX_KEY,
            newest_received="2026-09-14T10:00:00Z",
            seen_ids={"baseline"},
        )
        state = outlook_mail.OutlookMailboxState(
            graph={cursor.key: cursor}, graph_token="token"
        )
        metadata = {"id": "retry-me", "receivedDateTime": "2026-09-14T10:01:00Z"}
        with patch.object(outlook_mail, "_graph_messages", return_value=[metadata]), \
             patch.object(outlook_mail, "_graph_message_detail", side_effect=RuntimeError("temporary 503")):
            with self.assertRaisesRegex(RuntimeError, "temporary 503"):
                outlook_mail._scan_graph_once("token", state)
        self.assertNotIn("retry-me", cursor.seen_ids)
        self.assertNotIn("retry-me", state.graph_seen_ids)
        self.assertEqual(cursor.newest_received, "2026-09-14T10:00:00Z")

    def test_graph_seen_ids_are_shared_across_inbox_and_junk(self):
        inbox = outlook_mail.GraphFolderCursor(
            "inbox", outlook_mail.OUTLOOK_GRAPH_INBOX_KEY,
            newest_received="2026-09-14T10:00:00Z",
            seen_ids={"baseline"},
        )
        junk = outlook_mail.GraphFolderCursor(
            "junkemail", outlook_mail.OUTLOOK_GRAPH_JUNK_KEY,
            newest_received="2026-09-14T10:00:00Z",
            seen_ids=set(),
        )
        state = outlook_mail.OutlookMailboxState(
            graph={inbox.key: inbox, junk.key: junk},
            graph_token="token",
            graph_seen_ids={"baseline"},
        )
        moved = graph_message(
            "cross-folder", "2026-09-14T10:01:00Z",
            "Verification code ABC-123", "ABC-123", "no-reply@x.ai",
        )
        with patch.object(outlook_mail, "_graph_messages", side_effect=[[moved], [moved]]):
            self.assertEqual(outlook_mail._scan_graph_once("token", state), "ABC123")
        self.assertIn("cross-folder", state.graph_seen_ids)
        self.assertFalse(outlook_mail._graph_message_is_new(moved, junk, state.graph_seen_ids))

    def test_graph_cursor_ignores_old_unseen_message_after_deletion_or_move(self):
        cursor = outlook_mail.GraphFolderCursor(
            "inbox", outlook_mail.OUTLOOK_GRAPH_INBOX_KEY,
            newest_received="2026-09-14T10:00:00Z",
            seen_ids={"baseline"},
        )
        state = outlook_mail.OutlookMailboxState(
            graph={outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: cursor}, graph_token="token"
        )
        messages = [
            graph_message("old-moved", "2026-09-14T09:00:00Z", "Verification ABC-123", "ABC-123"),
            graph_message("baseline", "2026-09-14T10:00:00Z"),
        ]
        with patch.object(outlook_mail, "_graph_messages", return_value=messages):
            self.assertIsNone(outlook_mail._scan_graph_once("token", state))

    def test_graph_scans_inbox_and_junkemail_and_finds_new_junk_code(self):
        inbox = outlook_mail.GraphFolderCursor(
            "inbox", outlook_mail.OUTLOOK_GRAPH_INBOX_KEY,
            newest_received="2026-09-14T10:00:00Z",
            seen_ids={"inbox-base"},
        )
        junk = outlook_mail.GraphFolderCursor(
            "junkemail", outlook_mail.OUTLOOK_GRAPH_JUNK_KEY,
            newest_received="2026-09-14T10:00:00Z",
            seen_ids={"junk-base"},
        )
        state = outlook_mail.OutlookMailboxState(
            graph={inbox.key: inbox, junk.key: junk}, graph_token="token"
        )
        junk_code = graph_message(
            "junk-new", "2026-09-14T10:01:00Z",
            "Verification code ABC-123", "ABC-123", "no-reply@x.ai",
        )
        with patch.object(outlook_mail, "_graph_messages", side_effect=[[], [junk_code]]) as messages:
            self.assertEqual(outlook_mail._scan_graph_once("token", state), "ABC123")
        self.assertEqual([call.args[1] for call in messages.call_args_list], ["inbox", "junkemail"])

    def test_graph_oauth_tries_compatibility_endpoint_after_first_terminal_error(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "graph")
        responses = [
            Response(400, {"error": "invalid_grant", "error_description": "AADSTS7000012 wrong tenant"}),
            Response(200, {"access_token": "graph-access"}),
        ]
        with patch.object(outlook_mail, "_request_with_backoff", side_effect=responses) as request:
            self.assertEqual(outlook_mail.refresh_outlook_graph_token(account), "graph-access")
        self.assertEqual(request.call_count, 2)

    def test_microsoft_http_retry_honors_retry_after(self):
        responses = [
            Response(429, {}, headers={"Retry-After": "7"}),
            Response(200, {"ok": True}),
        ]
        with patch.object(outlook_mail.requests, "request", side_effect=responses) as request, \
             patch.object(outlook_mail, "_sleep_interruptibly") as sleep:
            result = outlook_mail._request_with_backoff("GET", "https://example.invalid", timeout=1)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(7.0, None)

    def test_auto_prepare_records_independent_imap_and_graph_baselines(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "auto")
        client = Mock()

        def prepare_imap(_account, state, cancel_callback=None):
            state.imap_token = "imap-token"
            state.imap_client = client
            folder = outlook_mail.ImapFolderRef("INBOX", "INBOX")
            state.imap["INBOX"] = outlook_mail.ImapFolderCursor(folder, "42", 10)

        def prepare_graph(_account, state, cancel_callback=None):
            state.graph_token = "graph-token"
            state.graph[outlook_mail.OUTLOOK_GRAPH_INBOX_KEY] = outlook_mail.GraphFolderCursor(
                "inbox", outlook_mail.OUTLOOK_GRAPH_INBOX_KEY, "2026-09-14T10:00:00Z", {"base"}
            )

        with patch.object(outlook_mail, "_prepare_imap_state", side_effect=prepare_imap) as imap, \
             patch.object(outlook_mail, "_prepare_graph_state", side_effect=prepare_graph) as graph:
            state = outlook_mail.prepare_outlook_state(account)
        self.assertTrue(state.has_imap)
        self.assertTrue(state.has_graph)
        imap.assert_called_once()
        graph.assert_called_once()
        state.close()

    def test_auto_wait_never_enables_channel_without_pre_send_baseline(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "auto")
        state = outlook_mail.OutlookMailboxState(
            graph={
                outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: outlook_mail.GraphFolderCursor(
                    "inbox", outlook_mail.OUTLOOK_GRAPH_INBOX_KEY
                )
            },
            graph_token="prepared-graph-token",
        )
        with patch.object(outlook_mail, "refresh_outlook_imap_token") as imap_refresh, \
             patch.object(outlook_mail, "refresh_outlook_graph_token") as graph_refresh, \
             patch.object(outlook_mail, "_scan_graph_once", return_value="ABC123"):
            code = outlook_mail.wait_for_outlook_code(account, state, timeout=2, interval=0)
        self.assertEqual(code, "ABC123")
        imap_refresh.assert_not_called()
        graph_refresh.assert_not_called()

    def test_prepare_tokens_are_reused_during_first_poll(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "auto")
        state = outlook_mail.OutlookMailboxState(
            graph={
                outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: outlook_mail.GraphFolderCursor(
                    "inbox", outlook_mail.OUTLOOK_GRAPH_INBOX_KEY
                )
            },
            graph_token="prepared-token",
        )
        with patch.object(outlook_mail, "refresh_outlook_graph_token") as refresh, \
             patch.object(outlook_mail, "_scan_graph_once", return_value="ABC123") as scan:
            self.assertEqual(outlook_mail.wait_for_outlook_code(account, state, timeout=2, interval=0), "ABC123")
        refresh.assert_not_called()
        self.assertEqual(scan.call_args.args[0], "prepared-token")

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
