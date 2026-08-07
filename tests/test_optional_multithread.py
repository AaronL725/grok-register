import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import app_config
import account_outputs
from registration_parallel import load_isolated_module, split_worker_counts


class OptionalMultithreadTests(unittest.TestCase):
    def test_defaults_keep_parallel_disabled_with_four_workers_configured(self):
        cfg = app_config.validate_config_structure({})
        self.assertFalse(cfg["multi_thread_enabled"])
        self.assertEqual(cfg["multi_thread_workers"], 4)

    def test_worker_count_validation(self):
        for workers in (1, 4, 8):
            cfg = app_config.validate_config_structure({"multi_thread_workers": workers})
            self.assertEqual(cfg["multi_thread_workers"], workers)
        for workers in (0, 9):
            with self.assertRaises(app_config.ConfigError):
                app_config.validate_config_structure({"multi_thread_workers": workers})

    def test_static_distribution_never_exceeds_target(self):
        self.assertEqual(split_worker_counts(10, 4), [3, 3, 2, 2])
        self.assertEqual(split_worker_counts(2, 4), [1, 1])
        self.assertEqual(split_worker_counts(1, 4), [1])
        self.assertEqual(sum(split_worker_counts(37, 8)), 37)

    def test_isolated_module_instances_do_not_share_globals(self):
        with tempfile.TemporaryDirectory() as directory:
            module_path = Path(directory) / "sample_runtime.py"
            module_path.write_text("value = None\n", encoding="utf-8")
            first = load_isolated_module(module_path, "_parallel_test_first")
            second = load_isolated_module(module_path, "_parallel_test_second")
            first.value = "worker-1"
            second.value = "worker-2"
            self.assertEqual(first.value, "worker-1")
            self.assertEqual(second.value, "worker-2")

    def test_concurrent_account_appends_remain_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "accounts.txt")
            def write_one(index):
                account_outputs.append_account_line(
                    output,
                    "user%s@example.com" % index,
                    "password-%s" % index,
                    "sso-%s" % index,
                )
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(write_one, range(40)))
            lines = Path(output).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 40)
            self.assertEqual(len(set(lines)), 40)
            for line in lines:
                self.assertEqual(len(line.split("----", 2)), 3)

    def test_concurrent_pending_appends_are_valid_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "accounts.txt")
            def write_one(index):
                account_outputs.queue_unsaved_account(
                    output,
                    {"email": "user%s@example.com" % index, "password": "p", "sso": "sso-%s" % index},
                    "test",
                )
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(write_one, range(30)))
            pending = Path(output + ".pending.jsonl")
            rows = [json.loads(line) for line in pending.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 30)
            self.assertEqual(len({row["email"] for row in rows}), 30)


if __name__ == "__main__":
    unittest.main()
