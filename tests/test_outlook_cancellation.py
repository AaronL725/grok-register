import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import outlook_mail
import outlook_mailbox_pool as pool


ROW = "u@example.com----pw----client----refresh-token----auto\n"


class OutlookCancellationTests(unittest.TestCase):
    def _runtime(self, tmp, cancelled_exception=None):
        path = Path(tmp) / "outlook.txt"
        pool.save_outlook_mailbox_pool(path, ROW)
        return pool.create_outlook_task_runtime(
            path, cancelled_exception=cancelled_exception
        )

    def _acquire_without_network(self, runtime):
        with patch.object(pool.OutlookMailbox, "prepare", return_value=None):
            return runtime.acquire()

    def test_immediate_cancel_uses_registration_cancel_exception(self):
        class Cancelled(Exception):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp, cancelled_exception=Cancelled)
            email, handle = self._acquire_without_network(runtime)
            with patch.object(pool.OutlookMailbox, "wait_for_code") as wait:
                with self.assertRaises(Cancelled):
                    runtime.wait_for_code(
                        handle, email, cancel_callback=lambda: True
                    )
                wait.assert_not_called()

    def test_immediate_cancel_closes_claimed_mailbox(self):
        class Cancelled(Exception):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp, cancelled_exception=Cancelled)
            email, handle = self._acquire_without_network(runtime)
            with patch.object(pool.OutlookMailbox, "close") as close:
                with self.assertRaises(Cancelled):
                    runtime.wait_for_code(handle, email, cancel_callback=lambda: True)
            close.assert_called_once_with()

    def test_low_level_stop_is_converted_to_registration_cancel_exception(self):
        class Cancelled(Exception):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp, cancelled_exception=Cancelled)
            email, handle = self._acquire_without_network(runtime)
            with patch.object(
                pool.OutlookMailbox,
                "wait_for_code",
                side_effect=RuntimeError("任务已停止"),
            ):
                with self.assertRaises(Cancelled):
                    runtime.wait_for_code(
                        handle, email, cancel_callback=lambda: True
                    )

    def test_non_cancel_error_is_not_reclassified(self):
        class Cancelled(Exception):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp, cancelled_exception=Cancelled)
            email, handle = self._acquire_without_network(runtime)
            with patch.object(
                pool.OutlookMailbox,
                "wait_for_code",
                side_effect=RuntimeError("mailbox network failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "mailbox network failure"):
                    runtime.wait_for_code(
                        handle, email, cancel_callback=lambda: False
                    )

    def test_auto_baseline_falls_back_to_graph_when_imap_has_no_safe_baseline(self):
        account = outlook_mail.OutlookAccount(
            "u@example.com", "pw", "client", "refresh", "auto"
        )

        def fail_imap(_account, _state, cancel_callback=None):
            raise RuntimeError("未发现可建立 UID 基线的 Outlook IMAP 文件夹")

        def prepare_graph(_account, state, cancel_callback=None):
            state.graph_token = "graph"
            state.graph[outlook_mail.OUTLOOK_GRAPH_INBOX_KEY] = outlook_mail.GraphFolderCursor(
                "inbox",
                outlook_mail.OUTLOOK_GRAPH_INBOX_KEY,
                "2026-09-14T10:00:00Z",
                {"baseline"},
            )

        with patch.object(outlook_mail, "_prepare_imap_state", side_effect=fail_imap), \
             patch.object(outlook_mail, "_prepare_graph_state", side_effect=prepare_graph):
            state = outlook_mail.prepare_outlook_state(account)
        self.assertFalse(state.has_imap)
        self.assertTrue(state.has_graph)
        self.assertIn("imap", state.errors)
        state.close()


if __name__ == "__main__":
    unittest.main()
