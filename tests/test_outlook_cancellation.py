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

    def test_auto_baseline_falls_back_to_graph_when_imap_has_no_folders(self):
        account = outlook_mail.OutlookAccount(
            "u@example.com", "pw", "client", "refresh", "auto"
        )

        class FakeImap:
            def logout(self):
                return None

        with patch.object(outlook_mail, "refresh_outlook_imap_token", return_value="imap"), \
             patch.object(outlook_mail, "_connect_imap", return_value=FakeImap()), \
             patch.object(outlook_mail, "_discover_folders", return_value=[]), \
             patch.object(outlook_mail, "refresh_outlook_graph_token", return_value="graph"), \
             patch.object(outlook_mail, "_graph_inbox_count", return_value=7):
            self.assertEqual(
                outlook_mail.load_folder_counts(account),
                {outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: 7},
            )


if __name__ == "__main__":
    unittest.main()
