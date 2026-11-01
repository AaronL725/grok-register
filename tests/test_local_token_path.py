"""Regression tests for the default local Grok2API token-file location."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import account_outputs
import preflight
from app_config import DEFAULT_CONFIG


class LocalTokenPathTests(unittest.TestCase):
    def test_default_token_path_is_under_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            module_file = Path(directory) / "account_outputs.py"
            config = {"grok2api_local_token_file": ""}
            with patch.object(account_outputs, "__file__", str(module_file)), \
                 patch.object(account_outputs, "config", config, create=True):
                path = Path(account_outputs.resolve_grok2api_local_token_file())
            self.assertEqual(path, Path(directory) / "data" / "token.json")

    def test_default_writer_creates_token_and_backup_under_data(self):
        with tempfile.TemporaryDirectory() as directory:
            module_file = Path(directory) / "account_outputs.py"
            config = {"grok2api_local_token_file": "", "grok2api_pool_name": "ssoBasic"}
            with patch.object(account_outputs, "__file__", str(module_file)), \
                 patch.object(account_outputs, "config", config, create=True):
                account_outputs.add_token_to_grok2api_local_pool("sso=first-token")
                account_outputs.add_token_to_grok2api_local_pool("sso=second-token")

            token_file = Path(directory) / "data" / "token.json"
            backup_file = Path(str(token_file) + ".bak")
            self.assertTrue(token_file.is_file())
            self.assertTrue(backup_file.is_file())
            payload = json.loads(token_file.read_text(encoding="utf-8"))
            backup = json.loads(backup_file.read_text(encoding="utf-8"))
            self.assertEqual([item["token"] for item in payload["ssoBasic"]], [
                "first-token", "second-token",
            ])
            self.assertEqual([item["token"] for item in backup["ssoBasic"]], [
                "first-token",
            ])

    def test_legacy_root_token_file_is_migrated_on_first_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_file = root / "token.json"
            legacy_file.write_text(json.dumps({
                "ssoBasic": [{"token": "legacy-token", "tags": [], "note": ""}],
            }), encoding="utf-8")
            config = {"grok2api_local_token_file": "", "grok2api_pool_name": "ssoBasic"}
            with patch.object(account_outputs, "__file__", str(root / "account_outputs.py")), \
                 patch.object(account_outputs, "config", config, create=True):
                account_outputs.add_token_to_grok2api_local_pool("new-token")

            token_file = root / "data" / "token.json"
            payload = json.loads(token_file.read_text(encoding="utf-8"))
            self.assertFalse(legacy_file.exists())
            self.assertEqual(
                [item["token"] for item in payload["ssoBasic"]],
                ["legacy-token", "new-token"],
            )

    def test_preflight_validates_the_same_default_path(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "data" / "token.json"
            token_file.parent.mkdir()
            token_file.write_text("{broken", encoding="utf-8")
            with patch.object(account_outputs, "__file__", str(Path(directory) / "account_outputs.py")):
                result = preflight._check_grok2api_config(dict(
                    DEFAULT_CONFIG,
                    grok2api_auto_add_local=True,
                    grok2api_local_token_file="",
                ))
            self.assertEqual(result["status"], "WARN")
            self.assertIn("本地 token 文件不可用", result["message"])

    @unittest.skipUnless(shutil.which("git"), "git is required to verify ignore rules")
    def test_token_files_and_temporary_files_are_gitignored(self):
        repository_root = Path(__file__).resolve().parents[1]
        for relative_path in (
            "token.json",
            "token.json.bak",
            "token.json.lock",
            ".token-test.tmp",
            "data/token.json",
            "data/token.json.bak",
        ):
            with self.subTest(path=relative_path):
                result = subprocess.run(
                    ["git", "check-ignore", "--no-index", "-q", relative_path],
                    cwd=repository_root,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, relative_path)


if __name__ == "__main__":
    unittest.main()
