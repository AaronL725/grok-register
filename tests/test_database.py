"""Tests for the SQLite database schema and account persistence."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from storage.database import Database, SCHEMA_VERSION


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "data" / "accounts.sqlite3"
        self.database = Database(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initializes_versioned_schema_and_wal(self):
        self.assertEqual(self.database.schema_version(), SCHEMA_VERSION)
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue({
            "accounts", "registration_runs", "registration_attempts",
            "account_artifacts", "job_runs",
        }.issubset(tables))

    def test_initialization_is_idempotent(self):
        Database(self.database_path)
        Database(self.database_path)
        self.assertEqual(self.database.schema_version(), SCHEMA_VERSION)

    def test_insert_account_is_idempotent_and_email_is_case_insensitive(self):
        record = {"email": "User@example.com", "password": "pw", "sso": "token"}
        account_id, inserted = self.database.insert_account(record)
        duplicate_id, duplicate_inserted = self.database.insert_account(
            {**record, "email": "user@EXAMPLE.com"}
        )
        self.assertTrue(inserted)
        self.assertFalse(duplicate_inserted)
        self.assertEqual(account_id, duplicate_id)
        self.assertEqual(self.database.account_count(), 1)

    def test_insert_accounts_uses_one_transaction(self):
        rows = self.database.insert_accounts([
            {"email": "one@example.com", "sso": "one"},
            {"email": "two@example.com", "sso": "two"},
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.database.account_count(), 2)

    def test_account_artifacts_are_deduplicated(self):
        account_id, _ = self.database.insert_account(
            {"email": "one@example.com", "sso": "token"}
        )
        self.assertTrue(self.database.add_artifact(account_id, "legacy_txt", "/tmp/a.txt", "txt"))
        self.assertFalse(self.database.add_artifact(account_id, "legacy_txt", "/tmp/a.txt", "txt"))

    def test_batch_insert_rolls_back_when_a_record_is_invalid(self):
        with self.assertRaises(ValueError):
            self.database.insert_accounts([
                {"email": "valid@example.com", "sso": "token"},
                {"email": "missing-sso@example.com"},
            ])
        self.assertEqual(self.database.account_count(), 0)

    def test_concurrent_duplicate_inserts_create_one_account(self):
        from concurrent.futures import ThreadPoolExecutor

        record = {"email": "same@example.com", "sso": "token"}
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(self.database.insert_account, [record] * 8))
        self.assertEqual(sum(inserted for _account_id, inserted in results), 1)
        self.assertEqual(len({account_id for account_id, _inserted in results}), 1)
        self.assertEqual(self.database.account_count(), 1)

    def test_bulk_artifacts_are_deduplicated(self):
        first, _ = self.database.insert_account({"email": "one@example.com", "sso": "one"})
        second, _ = self.database.insert_account({"email": "two@example.com", "sso": "two"})
        self.assertEqual(self.database.add_artifacts([first, second], "legacy_txt", "/tmp/a.txt", "txt"), 2)
        self.assertEqual(self.database.add_artifacts([first, second], "legacy_txt", "/tmp/a.txt", "txt"), 0)
        with self.database.connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM account_artifacts").fetchone()[0]
        self.assertEqual(count, 2)

    def test_concurrent_initialization_applies_schema_once(self):
        from concurrent.futures import ThreadPoolExecutor

        path = Path(self.temp_dir.name) / "shared" / "accounts.sqlite3"
        with ThreadPoolExecutor(max_workers=4) as executor:
            databases = list(executor.map(lambda _index: Database(path), range(4)))
        self.assertEqual({database.schema_version() for database in databases}, {SCHEMA_VERSION})
        self.assertEqual(databases[0].account_count(), 0)

    def test_registered_native_record_promotes_pending_source(self):
        pending = {
            "email": "native@example.com", "sso": "token",
            "registration_status": "pending_recovery", "source": "legacy_pending",
        }
        registered = {
            "email": "native@example.com", "sso": "token", "source": "native",
        }
        self.database.insert_account(pending)
        self.database.insert_account(registered)
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM accounts").fetchone()
        self.assertEqual(row["registration_status"], "registered")
        self.assertEqual(row["source"], "native")

    def test_newer_database_schema_is_rejected(self):
        with self.database.transaction() as connection:
            connection.execute("PRAGMA user_version = %s" % (SCHEMA_VERSION + 1))
        with self.assertRaisesRegex(RuntimeError, "newer than supported"):
            Database(self.database_path)

    def test_foreign_keys_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.add_artifact(999, "legacy_txt", "/tmp/a.txt", "txt")


if __name__ == "__main__":
    unittest.main()
