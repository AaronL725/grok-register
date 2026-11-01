"""Offline tests for container-specific runtime paths and Chromium settings."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import browser_runtime
import grok_register_ttk
from cpa_xai import browser_confirm
from web import server


class FakeChromiumOptions:
    def __init__(self):
        self.arguments = []
        self.browser_path = ""
        self.headless_enabled = False

    def auto_port(self):
        return None

    def set_timeouts(self, **_kwargs):
        return None

    def set_browser_path(self, path):
        self.browser_path = path

    def headless(self, enabled):
        self.headless_enabled = enabled

    def set_argument(self, argument):
        self.arguments.append(argument)

    def add_extension(self, _path):
        return None


class DockerRuntimeTests(unittest.TestCase):
    def test_config_path_can_live_outside_the_application_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = str(Path(directory) / "config.json")
            environment = dict(os.environ, GROK_REGISTER_CONFIG_FILE=config_path)
            result = subprocess.check_output(
                [
                    sys.executable,
                    "-c",
                    "import app_config; print(app_config.CONFIG_FILE)",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                text=True,
            )
        self.assertEqual(result.strip(), config_path)

    def test_browser_options_apply_container_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            chromium = Path(directory) / "chromium"
            chromium.write_text("", encoding="utf-8")
            options = FakeChromiumOptions()
            environment = {
                "CHROME_PATH": str(chromium),
                "GROK_REGISTER_CHROMIUM_HEADLESS": "1",
                "GROK_REGISTER_CHROMIUM_NO_SANDBOX": "1",
                "GROK_REGISTER_CHROMIUM_DISABLE_DEV_SHM_USAGE": "1",
            }
            with patch.dict(os.environ, environment), patch.object(
                browser_runtime, "ChromiumOptions", return_value=options
            ):
                browser_runtime.create_browser_options()

        self.assertEqual(options.browser_path, str(chromium))
        self.assertTrue(options.headless_enabled)
        self.assertIn("--no-sandbox", options.arguments)
        self.assertIn("--disable-dev-shm-usage", options.arguments)

    def test_invalid_explicit_chromium_path_fails_clearly(self):
        with patch.dict(os.environ, {"CHROME_PATH": "/missing/chromium"}), patch.object(
            browser_runtime, "ChromiumOptions", return_value=FakeChromiumOptions()
        ):
            with self.assertRaisesRegex(FileNotFoundError, "CHROME_PATH"):
                browser_runtime.create_browser_options()

    def test_account_exports_use_the_persistent_output_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"GROK_REGISTER_OUTPUT_DIR": directory}
        ):
            account_path = Path(server._new_accounts_file())
            self.assertEqual(account_path.parent, Path(directory))
            self.assertTrue(account_path.parent.is_dir())

    def test_mail_credentials_and_cpa_screenshots_use_the_data_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"GROK_REGISTER_DATA_DIR": directory}
        ):
            self.assertTrue(
                grok_register_ttk._save_mail_credential("user@example.com", "mail-token")
            )
            screenshot_dir = browser_confirm._debug_shot_dir()

            self.assertTrue((Path(directory) / "mail_credentials.txt").is_file())
            self.assertEqual(screenshot_dir, Path(directory) / "screenshots")


if __name__ == "__main__":
    unittest.main()
