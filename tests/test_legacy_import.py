"""Tests for importing the legacy account and pending file formats."""

import json
import tempfile
import unittest
from pathlib import Path

from storage.database import Database
from storage.legacy_import import import_legacy_accounts, import_legacy_pending


class LegacyImportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.database = Database(root / "accounts.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_imports_legacy_txt_and_preserves_delimiters_in_sso(self):
        path = Path(self.temp_dir.name) / "accounts.txt"
        original = (
            "user@example.com----pass----sso-token----suffix\n"
            "bad line\n"
            "\n"
        )
        path.write_text(original, encoding="utf-8")

        summary = import_legacy_accounts(self.database, path, "cloudflare")

        self.assertEqual((summary.total, summary.imported, summary.duplicates, summary.malformed), (2, 1, 0, 1))
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM accounts").fetchone()
            artifact = connection.execute("SELECT * FROM account_artifacts").fetchone()
        self.assertEqual(row["password"], "pass")
        self.assertEqual(row["sso"], "sso-token----suffix")
        self.assertEqual(row["email_provider"], "cloudflare")
        self.assertEqual(row["source"], "legacy_txt")
        self.assertEqual(artifact["format"], "txt")
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_import_is_idempotent(self):
        path = Path(self.temp_dir.name) / "accounts.txt"
        path.write_text("user@example.com----pw----token\n", encoding="utf-8")

        first = import_legacy_accounts(self.database, path)
        second = import_legacy_accounts(self.database, path)

        self.assertEqual(first.imported, 1)
        self.assertEqual(second.imported, 0)
        self.assertEqual(second.duplicates, 1)
        self.assertEqual(self.database.account_count(), 1)

    def test_imports_pending_records_without_consuming_pending_file(self):
        path = Path(self.temp_dir.name) / "accounts.txt.pending.jsonl"
        record = {
            "email": "pending@example.com",
            "password": "pw",
            "sso": "pending-token",
            "profile": {"given_name": "Test"},
            "queued_at": "2026-10-31T00:00:00+00:00",
        }
        original = json.dumps(record) + "\nnot-json\n"
        path.write_text(original, encoding="utf-8")

        summary = import_legacy_pending(self.database, path, "duckmail")

        self.assertEqual((summary.total, summary.imported, summary.malformed), (2, 1, 1))
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM accounts").fetchone()
        self.assertEqual(row["registration_status"], "pending_recovery")
        self.assertEqual(row["source"], "legacy_pending")
        self.assertEqual(json.loads(row["profile_json"]), {"given_name": "Test"})
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_txt_import_promotes_a_previously_imported_pending_account(self):
        txt = Path(self.temp_dir.name) / "accounts.txt"
        pending = Path(self.temp_dir.name) / "accounts.txt.pending.jsonl"
        record = {"email": "same@example.com", "password": "pw", "sso": "same-token"}
        pending.write_text(json.dumps(record) + "\n", encoding="utf-8")
        txt.write_text("same@example.com----pw----same-token\n", encoding="utf-8")

        import_legacy_pending(self.database, pending)
        summary = import_legacy_accounts(self.database, txt)

        self.assertEqual(summary.imported, 0)
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM accounts").fetchone()
        self.assertEqual(row["registration_status"], "registered")
        self.assertEqual(row["source"], "legacy_txt")

    def test_pending_import_does_not_duplicate_existing_txt_account(self):
        txt = Path(self.temp_dir.name) / "accounts.txt"
        pending = Path(self.temp_dir.name) / "accounts.txt.pending.jsonl"
        txt.write_text("same@example.com----pw----same-token\n", encoding="utf-8")
        pending.write_text(json.dumps({
            "email": "same@example.com", "password": "pw", "sso": "same-token"
        }) + "\n", encoding="utf-8")

        import_legacy_accounts(self.database, txt)
        summary = import_legacy_pending(self.database, pending)

        self.assertEqual(summary.imported, 0)
        self.assertEqual(summary.duplicates, 1)
        self.assertEqual(self.database.account_count(), 1)


if __name__ == "__main__":
    unittest.main()
