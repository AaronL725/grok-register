import tempfile
import unittest
from pathlib import Path

import app_config
from outlook_mailbox_pool import save_outlook_mailbox_pool


class OutlookConfigValidationTests(unittest.TestCase):
    def _config(self, path):
        cfg = dict(app_config.DEFAULT_CONFIG)
        cfg["email_provider"] = "outlook"
        cfg["outlook_accounts_file"] = str(path)
        return cfg

    def test_valid_pool_passes_full_run_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outlook.txt"
            save_outlook_mailbox_pool(
                path,
                "u@example.com----pw----client----refresh-token----auto\n",
            )
            validated = app_config.validate_run_requirements(self._config(path))
            self.assertEqual(validated["email_provider"], "outlook")

    def test_oversized_pool_is_reported_as_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outlook.txt"
            path.write_text(
                "u@example.com----pw----client----" + ("x" * 1_000_001) + "----auto\n",
                encoding="utf-8",
            )
            with self.assertRaises(app_config.ConfigError):
                app_config.validate_run_requirements(self._config(path))

    def test_duplicate_pool_is_reported_as_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outlook.txt"
            path.write_text(
                "u@example.com----pw----client----token1----auto\n"
                "U@example.com----pw----client2----token2----graph\n",
                encoding="utf-8",
            )
            with self.assertRaises(app_config.ConfigError):
                app_config.validate_run_requirements(self._config(path))


if __name__ == "__main__":
    unittest.main()
