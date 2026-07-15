"""验证 pending 账号恢复的去重、锁、原子更新和异常处理。"""

import json
import os
import tempfile
import unittest

from account_outputs import retry_pending_file


class PendingRecoveryTests(unittest.TestCase):
    def test_retry_is_idempotent_after_target_was_already_written(self):
        with tempfile.TemporaryDirectory() as directory:
            pending = os.path.join(directory, "accounts.txt.pending.jsonl")
            target = os.path.join(directory, "accounts.txt")
            record = {"email": "a@example.com", "password": "pw", "sso": "token"}
            with open(pending, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("a@example.com----pw----token\n")
            summary = retry_pending_file(pending)
            self.assertEqual(summary["restored"], 1)
            with open(target, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.readlines(), ["a@example.com----pw----token\n"])
            self.assertFalse(os.path.exists(pending))

    def test_rejects_same_input_and_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            pending = os.path.join(directory, "pending.jsonl")
            with open(pending, "w", encoding="utf-8") as handle:
                handle.write("{}\n")
            with self.assertRaises(ValueError):
                retry_pending_file(pending, output_path=pending)


if __name__ == "__main__":
    unittest.main()
