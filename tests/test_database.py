"""Tests for the SQLite database schema and account persistence."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from storage.database import Database, SCHEMA_VERSION, _MIGRATIONS


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

    def test_migrates_pr01_v1_database_to_latest_schema(self):
        path = Path(self.temp_dir.name) / "legacy-v1.sqlite3"
        connection = sqlite3.connect(path)
        for statement in _MIGRATIONS[1]:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
        connection.close()

        migrated = Database(path)
        self.assertEqual(migrated.schema_version(), SCHEMA_VERSION)
        with migrated.connection() as connection:
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(registration_runs)")
            }
        self.assertIn("registered_unsaved_count", columns)
        with migrated.connection() as connection:
            attempt_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(registration_attempts)")
            }
        self.assertTrue({"failure_type", "screenshot_path"}.issubset(attempt_columns))

    def test_migrates_existing_v2_database_to_latest_schema(self):
        path = Path(self.temp_dir.name) / "legacy-v2.sqlite3"
        connection = sqlite3.connect(path)
        for version in (1, 2):
            for statement in _MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute("PRAGMA user_version = %s" % version)
        connection.commit()
        connection.close()

        migrated = Database(path)
        self.assertEqual(migrated.schema_version(), SCHEMA_VERSION)
        with migrated.connection() as connection:
            account_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(accounts)")
            }
            attempt_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(registration_attempts)")
            }
            tombstones = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'account_tombstones'"
            ).fetchone()
        self.assertTrue({
            "cpa_status", "grok2api_local_status", "grok2api_remote_status",
        }.issubset(account_columns))
        self.assertIn("slot_index", attempt_columns)
        self.assertIsNotNone(tombstones)

    def test_migrates_v6_accounts_to_reauthentication_schema(self):
        from storage.database import _MIGRATIONS

        path = Path(self.temp_dir.name) / "legacy-v6.sqlite3"
        connection = sqlite3.connect(path)
        for version in range(1, 7):
            for statement in _MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute("PRAGMA user_version = %s" % version)
        connection.execute(
            """INSERT INTO accounts (email, password, sso, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("legacy@example.com", "old-password", "old-token", "2026-10-30", "2026-10-30"),
        )
        connection.commit()
        connection.close()

        migrated = Database(path)
        self.assertEqual(migrated.schema_version(), SCHEMA_VERSION)
        self.assertEqual(migrated.account_count(), 1)
        with migrated.connection() as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'job_accounts'"
            ).fetchone())

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


    def test_registration_attempts_store_failure_diagnostics(self):
        run_id = self.database.create_registration_run(1)
        account_id, _ = self.database.insert_account({
            "email": "diagnostic@example.com", "sso": "token",
        })
        self.database.create_registration_attempt(
            run_id,
            slot_index=1,
            attempt_number=2,
            worker_key="grok-register_1",
            stage="browser_start",
            status="failed",
            account_id=account_id,
            error_type="TimeoutError",
            error_message="browser timed out",
            traceback="Traceback (most recent call last): ...",
            failure_type="browser",
            screenshot_path="/private/diagnostics/failure.png",
        )

        attempts = self.database.list_registration_attempts(run_id)
        account = self.database.get_account(account_id)
        attempt = attempts["items"][0]

        self.assertEqual(attempts["total"], 1)
        self.assertEqual(attempt["failure_type"], "browser")
        self.assertEqual(attempt["screenshot_path"], "/private/diagnostics/failure.png")
        self.assertIn("Traceback", attempt["traceback"])
        self.assertEqual(account["attempts"][0]["screenshot_path"], attempt["screenshot_path"])

    def test_registration_attempt_list_rejects_unknown_run(self):
        self.assertIsNone(self.database.list_registration_attempts(999))

    def test_registration_attempt_keeps_legacy_positional_timestamp_arguments(self):
        run_id = self.database.create_registration_run(1)
        self.database.create_registration_attempt(
            run_id, 1, 1, "worker-1", "browser_start", "failed",
            None, "RuntimeError", "browser failed",
            "2026-10-31T12:00:00+00:00", "2026-10-31T12:00:01+00:00",
        )

        attempt = self.database.list_registration_attempts(run_id)["items"][0]
        self.assertEqual(attempt["started_at"], "2026-10-31T12:00:00+00:00")
        self.assertEqual(attempt["finished_at"], "2026-10-31T12:00:01+00:00")
        self.assertEqual(attempt["traceback"], "")

    def test_registration_attempts_and_run_success_rate(self):
        run_id = self.database.create_registration_run(2, {"email_provider": "duckmail"})
        self.database.create_registration_attempt(
            run_id,
            slot_index=1,
            attempt_number=1,
            worker_key="worker-1",
            stage="browser_start",
            status="retrying",
            error_type="TimeoutError",
            error_message="browser timed out",
        )
        self.database.create_registration_attempt(
            run_id,
            slot_index=1,
            attempt_number=2,
            worker_key="worker-1",
            stage="postprocess",
            status="succeeded",
        )
        self.database.update_registration_run(
            run_id, status="completed", processed_count=2, success_count=1,
            failed_count=1, registered_unsaved_count=1,
            finished_at="2026-10-31T10:00:00+00:00",
        )
        history = self.database.list_registration_runs()
        row = history["items"][0]
        self.assertEqual(row["attempt_count"], 2)
        self.assertEqual(row["success_rate"], 50.0)
        self.assertEqual(row["registered_unsaved_count"], 1)

    def test_integration_statuses_are_independent(self):
        account_id, _ = self.database.insert_account({
            "email": "integration@example.com", "sso": "token",
        })
        self.database.update_account_integration_status(
            account_id,
            "warning", "CPA copied with warning",
            "succeeded", "",
            "failed", "remote timeout",
        )
        account = self.database.get_account(account_id)
        self.assertEqual(account["registration_status"], "registered")
        self.assertEqual(account["authorization_status"], "unknown")
        self.assertEqual(account["cpa_status"], "warning")
        self.assertEqual(account["grok2api_local_status"], "succeeded")
        self.assertEqual(account["grok2api_remote_status"], "failed")
        self.assertEqual(account["grok2api_remote_error"], "remote timeout")

    def test_account_filters_pagination_and_date_range(self):
        run_id = self.database.create_registration_run(2)
        first, _ = self.database.insert_account({
            "email": "first@example.com", "sso": "one",
            "email_provider": "duckmail", "registration_status": "pending_recovery",
            "created_at": "2026-10-30T12:00:00+00:00",
        })
        second, _ = self.database.insert_account({
            "email": "second@example.com", "sso": "two",
            "email_provider": "cloudmail", "registration_status": "registered",
            "authorization_status": "authorized", "registration_run_id": run_id,
            "created_at": "2026-10-31T12:00:00+00:00",
        })
        self.database.update_account_integration_status(
            second, "succeeded", "", "succeeded", "", "failed", "remote timeout"
        )
        result = self.database.list_accounts(
            limit=1, offset=0, search="second", registration_status="registered",
            authorization_status="authorized", email_provider="cloudmail",
            registration_run_id=run_id, cpa_status="succeeded",
            grok2api_local_status="succeeded", grok2api_remote_status="failed",
            date_from="2026-10-31T00:00:00+00:00",
            date_to="2026-10-31T23:59:59+00:00",
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], second)
        page = self.database.list_accounts(limit=1, offset=1)
        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["items"]), 1)
        self.assertNotEqual(first, second)

    def test_reauthentication_history_retains_items_after_account_deletion(self):
        first, _ = self.database.insert_account({
            "email": "one@example.com", "password": "pw1", "sso": "token1",
        })
        second, _ = self.database.insert_account({
            "email": "two@example.com", "password": "pw2", "sso": "token2",
        })
        job_id = self.database.create_job("reauthentication", [
            self.database.get_account(first),
            self.database.get_account(second),
        ])

        self.database.delete_accounts([first, second])
        job = self.database.get_job(job_id)

        self.assertEqual([item["email"] for item in job["items"]], [
            "one@example.com", "two@example.com",
        ])
        self.assertEqual([item["account_id"] for item in job["items"]], [None, None])

    def test_bulk_delete_removes_accounts_and_related_artifacts(self):
        first, _ = self.database.insert_account({"email": "one@example.com", "sso": "one"})
        second, _ = self.database.insert_account({"email": "two@example.com", "sso": "two"})
        self.database.add_artifact(first, "legacy_txt", "/tmp/accounts.txt", "txt")
        self.assertEqual(self.database.delete_accounts([first, second, first]), 2)
        self.assertEqual(self.database.account_count(), 0)
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM account_artifacts").fetchone()[0], 0)

    def test_account_stats_include_integration_status_and_overall_success_rate(self):
        self.database.insert_account({"email": "one@example.com", "sso": "one"})
        run_id = self.database.create_registration_run(2)
        self.database.update_registration_run(
            run_id, processed_count=2, success_count=1, failed_count=1,
            registered_unsaved_count=1,
        )
        stats = self.database.account_stats()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["by_cpa_status"], {"unknown": 1})
        self.assertEqual(stats["by_grok2api_local_status"], {"unknown": 1})
        self.assertEqual(stats["success_rate"], 50.0)
        self.assertEqual(stats["registered_unsaved_count"], 1)
        self.assertEqual(stats["attempt_count"], 0)

    def test_native_duplicate_refreshes_latest_batch_metadata(self):
        account_id, _ = self.database.insert_account({
            "email": "same@example.com", "password": "old", "sso": "token",
            "source": "legacy_txt", "email_provider": "",
        })
        run_id = self.database.create_registration_run(1)
        duplicate_id, inserted = self.database.insert_account({
            "email": "same@example.com", "password": "new", "sso": "token",
            "source": "native", "email_provider": "duckmail",
            "registration_run_id": run_id,
        })
        self.assertFalse(inserted)
        self.assertEqual(duplicate_id, account_id)
        account = self.database.get_account(account_id)
        self.assertEqual(account["password"], "new")
        self.assertEqual(account["email_provider"], "duckmail")
        self.assertEqual(account["registration_run_id"], run_id)


if __name__ == "__main__":
    unittest.main()
