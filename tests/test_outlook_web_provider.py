import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app_config
import mail_service


class FakeReaderProcess:
    def __init__(self, stdout, returncode=0):
        self._stdout = stdout
        self.returncode = returncode
        self.stdout = None
        self.stderr = None

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        return self._stdout, ""

    def terminate(self):
        self.returncode = 1

    def kill(self):
        self.returncode = 1


class OutlookWebProviderTests(unittest.TestCase):
    def setUp(self):
        self.old_config = mail_service.config

    def tearDown(self):
        mail_service.config = self.old_config

    def make_pool(self, directory):
        base = Path(directory)
        results = base / "Results"
        results.mkdir(parents=True)
        session_file = results / "session.json"
        session_file.write_text("{}", encoding="utf-8")
        record = {
            "id": "record-1",
            "email": "outlook-user@example.com",
            "password": "secret-value",
            "session_file": str(session_file),
        }
        (results / "outlook_web_pool.jsonl").write_text(
            json.dumps(record) + "\n", encoding="utf-8"
        )
        return base

    def test_provider_reserves_unused_record_without_exposing_password(self):
        with tempfile.TemporaryDirectory() as directory:
            base = self.make_pool(directory)
            mail_service.config = {"outlook_register_dir": str(base)}
            email, credential = mail_service.outlook_web_get_email_and_token()
            self.assertEqual(email, "outlook-user@example.com")
            self.assertEqual(credential, "outlook-web:record-1")
            self.assertNotIn("secret-value", credential)

            state = json.loads(
                (base / "Results" / "outlook_web_pool_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["records"]["record-1"]["status"], "in_progress")

    def test_consumed_record_is_not_selected_again(self):
        with tempfile.TemporaryDirectory() as directory:
            base = self.make_pool(directory)
            mail_service.config = {"outlook_register_dir": str(base)}
            mail_service.outlook_web_update_status("record-1", "consumed")
            with self.assertRaisesRegex(RuntimeError, "邮箱池已用尽"):
                mail_service.outlook_web_get_email_and_token()

    def test_reader_result_updates_status_and_returns_code(self):
        with tempfile.TemporaryDirectory() as directory:
            base = self.make_pool(directory)
            mail_service.config = {
                "outlook_register_dir": str(base),
                "outlook_web_headless": False,
            }
            output = 'OUTLOOK_WEB_RESULT={"status":"ok","code":"123456"}\n'
            statuses = []
            with patch.object(
                mail_service.subprocess,
                "Popen",
                return_value=FakeReaderProcess(output),
            ), patch.object(
                mail_service,
                "outlook_web_update_status",
                side_effect=lambda record_id, status, error="": statuses.append((record_id, status)),
            ), patch.object(
                mail_service, "raise_if_cancelled", return_value=None, create=True
            ):
                code = mail_service.outlook_web_get_oai_code(
                    "outlook-web:record-1",
                    "outlook-user@example.com",
                    timeout=1,
                    poll_interval=0.1,
                )
            self.assertEqual(code, "123456")
            self.assertIn(("record-1", "consumed"), statuses)

    def test_runtime_validation_requires_exported_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = app_config.DEFAULT_CONFIG.copy()
            cfg.update(
                {
                    "email_provider": "outlook_web",
                    "outlook_register_dir": directory,
                }
            )
            with self.assertRaisesRegex(app_config.ConfigError, "邮箱池"):
                app_config.validate_run_requirements(cfg)


if __name__ == "__main__":
    unittest.main()
