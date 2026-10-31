"""SQLite database and repositories used by the application."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

from filelock import FileLock


SCHEMA_VERSION = 1

_MIGRATIONS = {
    1: (
        """CREATE TABLE registration_runs (
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'running',
            requested_count INTEGER NOT NULL DEFAULT 0 CHECK (requested_count >= 0),
            processed_count INTEGER NOT NULL DEFAULT 0 CHECK (processed_count >= 0),
            success_count INTEGER NOT NULL DEFAULT 0 CHECK (success_count >= 0),
            failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
            uncertain_count INTEGER NOT NULL DEFAULT 0 CHECK (uncertain_count >= 0),
            postprocess_warning_count INTEGER NOT NULL DEFAULT 0 CHECK (postprocess_warning_count >= 0),
            config_snapshot_json TEXT NOT NULL DEFAULT '{}',
            started_at TEXT NOT NULL,
            finished_at TEXT
        )""",
        """CREATE TABLE accounts (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL COLLATE NOCASE,
            password TEXT NOT NULL DEFAULT '',
            sso TEXT NOT NULL,
            email_provider TEXT NOT NULL DEFAULT '',
            registration_status TEXT NOT NULL DEFAULT 'registered',
            authorization_status TEXT NOT NULL DEFAULT 'unknown',
            source TEXT NOT NULL DEFAULT 'native',
            profile_json TEXT NOT NULL DEFAULT '{}',
            registration_run_id INTEGER REFERENCES registration_runs(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_authenticated_at TEXT,
            UNIQUE (email, sso)
        )""",
        """CREATE TABLE registration_attempts (
            id INTEGER PRIMARY KEY,
            registration_run_id INTEGER NOT NULL REFERENCES registration_runs(id) ON DELETE CASCADE,
            account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
            attempt_number INTEGER NOT NULL DEFAULT 1 CHECK (attempt_number > 0),
            worker_key TEXT NOT NULL DEFAULT '',
            stage TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            error_type TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            traceback TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT
        )""",
        """CREATE TABLE account_artifacts (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            artifact_type TEXT NOT NULL,
            path TEXT NOT NULL,
            format TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE (account_id, artifact_type, path)
        )""",
        """CREATE TABLE job_runs (
            id INTEGER PRIMARY KEY,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            requested_count INTEGER NOT NULL DEFAULT 0 CHECK (requested_count >= 0),
            processed_count INTEGER NOT NULL DEFAULT 0 CHECK (processed_count >= 0),
            success_count INTEGER NOT NULL DEFAULT 0 CHECK (success_count >= 0),
            failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
            cancelled INTEGER NOT NULL DEFAULT 0 CHECK (cancelled IN (0, 1)),
            summary_json TEXT NOT NULL DEFAULT '{}',
            error_message TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT
        )""",
        "CREATE INDEX idx_accounts_created_at ON accounts(created_at DESC, id DESC)",
        "CREATE INDEX idx_accounts_registration_status ON accounts(registration_status)",
        "CREATE INDEX idx_accounts_authorization_status ON accounts(authorization_status)",
        "CREATE INDEX idx_accounts_registration_run_id ON accounts(registration_run_id)",
        "CREATE INDEX idx_registration_attempts_run_id ON registration_attempts(registration_run_id, id)",
        "CREATE INDEX idx_registration_attempts_account_id ON registration_attempts(account_id, id)",
        "CREATE INDEX idx_account_artifacts_account_id ON account_artifacts(account_id)",
        "CREATE INDEX idx_job_runs_started_at ON job_runs(started_at DESC, id DESC)",
    ),
}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_text(value):
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


class Database:
    """Open short-lived SQLite connections with a versioned, migrated schema."""

    def __init__(self, path):
        raw_path = os.fspath(path)
        if not raw_path or raw_path == ":memory:":
            raise ValueError("Database requires a non-empty file path")
        self.path = str(Path(raw_path).expanduser())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with FileLock(self.path + ".init.lock", timeout=30):
            with self.connection() as connection:
                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                if journal_mode.lower() != "wal":
                    connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    version = connection.execute("PRAGMA user_version").fetchone()[0]
                    if version > SCHEMA_VERSION:
                        raise RuntimeError(
                            "Database schema version %s is newer than supported version %s"
                            % (version, SCHEMA_VERSION)
                        )
                    for target_version in range(version + 1, SCHEMA_VERSION + 1):
                        for statement in _MIGRATIONS[target_version]:
                            connection.execute(statement)
                        connection.execute("PRAGMA user_version = %s" % target_version)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

    @staticmethod
    def _insert_account(connection, record):
        email = str(record.get("email") or "").strip()
        sso = str(record.get("sso") or "").strip()
        if not email or not sso:
            raise ValueError("Account email and SSO token are required")

        created_at = str(record.get("created_at") or utc_now())
        updated_at = str(record.get("updated_at") or created_at)
        profile = record.get("profile")
        registration_status = str(record.get("registration_status") or "registered")
        source = str(record.get("source") or "native")
        existing = connection.execute(
            "SELECT id, registration_status FROM accounts WHERE email = ? COLLATE NOCASE AND sso = ?",
            (email, sso),
        ).fetchone()
        if existing is not None:
            if registration_status == "registered" and existing["registration_status"] == "pending_recovery":
                connection.execute(
                    """UPDATE accounts
                       SET registration_status = 'registered', source = ?, updated_at = ?
                       WHERE id = ?""",
                    (source, updated_at, existing["id"]),
                )
            return existing["id"], False

        cursor = connection.execute(
            """INSERT INTO accounts (
                email, password, sso, email_provider, registration_status,
                authorization_status, source, profile_json, registration_run_id,
                created_at, updated_at, last_authenticated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                email,
                str(record.get("password") or ""),
                sso,
                str(record.get("email_provider") or ""),
                registration_status,
                str(record.get("authorization_status") or "unknown"),
                source,
                _json_text(profile),
                record.get("registration_run_id"),
                created_at,
                updated_at,
                record.get("last_authenticated_at"),
            ),
        )
        return cursor.lastrowid, True

    def insert_account(self, record):
        with self.transaction() as connection:
            return self._insert_account(connection, record)

    def insert_accounts(self, records):
        with self.transaction() as connection:
            return [self._insert_account(connection, record) for record in records]

    def add_artifact(self, account_id, artifact_type, path, file_format=""):
        return self.add_artifacts([account_id], artifact_type, path, file_format) == 1

    def add_artifacts(self, account_ids, artifact_type, path, file_format=""):
        records = [
            (account_id, artifact_type, os.fspath(path), file_format, utc_now())
            for account_id in account_ids
        ]
        if not records:
            return 0
        with self.transaction() as connection:
            cursor = connection.executemany(
                """INSERT INTO account_artifacts
                   (account_id, artifact_type, path, format, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(account_id, artifact_type, path) DO NOTHING""",
                records,
            )
            return cursor.rowcount

    def account_count(self):
        with self.connection() as connection:
            return connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]

    def schema_version(self):
        with self.connection() as connection:
            return connection.execute("PRAGMA user_version").fetchone()[0]
