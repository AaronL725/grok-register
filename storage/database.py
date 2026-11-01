"""SQLite database and repositories used by the application."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from filelock import FileLock


SCHEMA_VERSION = 7

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
    2: (
        "ALTER TABLE registration_runs ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0 CHECK (cancelled IN (0, 1))",
        "ALTER TABLE registration_runs ADD COLUMN error_message TEXT NOT NULL DEFAULT ''",
        "CREATE INDEX idx_registration_runs_started_at ON registration_runs(started_at DESC, id DESC)",
        """CREATE TABLE legacy_imports (
            path TEXT PRIMARY KEY,
            file_size INTEGER NOT NULL,
            modified_ns INTEGER NOT NULL,
            imported_count INTEGER NOT NULL DEFAULT 0,
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            malformed_count INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT NOT NULL
        )""",
    ),
    3: (
        "ALTER TABLE accounts ADD COLUMN cpa_status TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE accounts ADD COLUMN cpa_error TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE accounts ADD COLUMN grok2api_local_status TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE accounts ADD COLUMN grok2api_local_error TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE accounts ADD COLUMN grok2api_remote_status TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE accounts ADD COLUMN grok2api_remote_error TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE registration_attempts ADD COLUMN slot_index INTEGER NOT NULL DEFAULT 0",
        "CREATE INDEX idx_accounts_cpa_status ON accounts(cpa_status)",
        "CREATE INDEX idx_accounts_grok2api_local_status ON accounts(grok2api_local_status)",
        "CREATE INDEX idx_accounts_grok2api_remote_status ON accounts(grok2api_remote_status)",
    ),
    4: (
        """CREATE TABLE account_tombstones (
            identity_hash TEXT PRIMARY KEY,
            deleted_at TEXT NOT NULL
        )""",
    ),
    5: (
        "ALTER TABLE registration_runs ADD COLUMN registered_unsaved_count INTEGER NOT NULL DEFAULT 0 CHECK (registered_unsaved_count >= 0)",
    ),
    6: (
        "ALTER TABLE registration_attempts ADD COLUMN failure_type TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE registration_attempts ADD COLUMN screenshot_path TEXT NOT NULL DEFAULT ''",
    ),
    7: (
        """CREATE TABLE job_accounts (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL REFERENCES job_runs(id) ON DELETE CASCADE,
            account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
            email TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error_message TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE (job_id, account_id)
        )""",
        "CREATE INDEX idx_job_accounts_account_id ON job_accounts(account_id)",
    ),
}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_text(value):
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def _account_identity_hash(email, sso):
    identity = str(email).strip().casefold() + "\0" + str(sso).strip()
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


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
        identity_hash = _account_identity_hash(email, sso)
        tombstone = connection.execute(
            "SELECT 1 FROM account_tombstones WHERE identity_hash = ?",
            (identity_hash,),
        ).fetchone()
        if tombstone is not None:
            if source != "native":
                return None, False
            connection.execute(
                "DELETE FROM account_tombstones WHERE identity_hash = ?",
                (identity_hash,),
            )
        existing = connection.execute(
            "SELECT id, registration_status FROM accounts WHERE email = ? COLLATE NOCASE AND sso = ?",
            (email, sso),
        ).fetchone()
        if existing is not None:
            if source == "native":
                connection.execute(
                    """UPDATE accounts SET
                        password = ?, email_provider = ?, registration_status = ?,
                        source = 'native', profile_json = ?, registration_run_id = ?,
                        updated_at = ?
                    WHERE id = ?""",
                    (
                        str(record.get("password") or ""), str(record.get("email_provider") or ""),
                        registration_status, _json_text(profile),
                        record.get("registration_run_id"), updated_at, existing["id"],
                    ),
                )
            elif registration_status == "registered" and existing["registration_status"] == "pending_recovery":
                connection.execute(
                    """UPDATE accounts
                       SET password = ?, registration_status = 'registered',
                           source = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        str(record.get("password") or ""), source,
                        updated_at, existing["id"],
                    ),
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

    def legacy_import_fingerprint(self, path):
        with self.connection() as connection:
            row = connection.execute(
                "SELECT file_size, modified_ns FROM legacy_imports WHERE path = ?",
                (os.fspath(path),),
            ).fetchone()
        return None if row is None else (row["file_size"], row["modified_ns"])

    def record_legacy_import(self, path, file_size, modified_ns, summary):
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO legacy_imports (
                    path, file_size, modified_ns, imported_count, duplicate_count,
                    malformed_count, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    file_size = excluded.file_size,
                    modified_ns = excluded.modified_ns,
                    imported_count = excluded.imported_count,
                    duplicate_count = excluded.duplicate_count,
                    malformed_count = excluded.malformed_count,
                    imported_at = excluded.imported_at""",
                (
                    os.fspath(path), file_size, modified_ns, summary.imported,
                    summary.duplicates, summary.malformed, utc_now(),
                ),
            )

    def create_registration_run(self, requested_count, config_snapshot=None):
        started_at = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO registration_runs
                   (status, requested_count, config_snapshot_json, started_at)
                   VALUES ('running', ?, ?, ?)""",
                (int(requested_count), _json_text(config_snapshot), started_at),
            )
            return cursor.lastrowid

    def update_registration_run(self, run_id, **updates):
        allowed = {
            "status", "processed_count", "success_count", "failed_count",
            "uncertain_count", "registered_unsaved_count", "postprocess_warning_count", "cancelled",
            "finished_at", "error_message", "requested_count",
        }
        values = [(key, value) for key, value in updates.items() if key in allowed]
        if not values:
            return False
        assignments = ", ".join("%s = ?" % key for key, _value in values)
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE registration_runs SET %s WHERE id = ?" % assignments,
                [value for _key, value in values] + [int(run_id)],
            )
            return cursor.rowcount == 1

    def create_registration_attempt(
        self, registration_run_id, slot_index, attempt_number, worker_key, stage, status,
        account_id=None, error_type="", error_message="", started_at=None,
        finished_at=None, traceback="", failure_type="", screenshot_path="",
    ):
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO registration_attempts (
                    registration_run_id, slot_index, account_id, attempt_number, worker_key,
                    stage, status, error_type, error_message, traceback, failure_type,
                    screenshot_path, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(registration_run_id), int(slot_index), account_id, int(attempt_number),
                    str(worker_key or ""), str(stage or ""), str(status),
                    str(error_type or ""), str(error_message or "")[:500],
                    str(traceback or "")[:8000], str(failure_type or ""),
                    str(screenshot_path or ""), str(started_at or utc_now()),
                    str(finished_at or utc_now()),
                ),
            )
            return cursor.lastrowid

    def list_registration_attempts(self, registration_run_id, limit=50, offset=0):
        with self.connection() as connection:
            if connection.execute(
                "SELECT 1 FROM registration_runs WHERE id = ?", (int(registration_run_id),)
            ).fetchone() is None:
                return None
            total = connection.execute(
                "SELECT COUNT(*) FROM registration_attempts WHERE registration_run_id = ?",
                (int(registration_run_id),),
            ).fetchone()[0]
            rows = connection.execute(
                """SELECT id, registration_run_id, slot_index, account_id, attempt_number,
                          worker_key, stage, status, error_type, error_message, traceback,
                          failure_type, screenshot_path, started_at, finished_at
                   FROM registration_attempts
                   WHERE registration_run_id = ?
                   ORDER BY id DESC LIMIT ? OFFSET ?""",
                (int(registration_run_id), int(limit), int(offset)),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total}

    def get_registration_attempt(self, registration_run_id, attempt_id):
        with self.connection() as connection:
            row = connection.execute(
                """SELECT id, registration_run_id, screenshot_path
                   FROM registration_attempts
                   WHERE registration_run_id = ? AND id = ?""",
                (int(registration_run_id), int(attempt_id)),
            ).fetchone()
        return None if row is None else dict(row)

    def update_account_integration_status(
        self, account_id, cpa_status, cpa_error,
        grok2api_local_status, grok2api_local_error,
        grok2api_remote_status, grok2api_remote_error,
    ):
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE accounts SET
                    cpa_status = ?, cpa_error = ?,
                    grok2api_local_status = ?, grok2api_local_error = ?,
                    grok2api_remote_status = ?, grok2api_remote_error = ?,
                    updated_at = ?
                WHERE id = ?""",
                (
                    cpa_status, str(cpa_error or "")[:500],
                    grok2api_local_status, str(grok2api_local_error or "")[:500],
                    grok2api_remote_status, str(grok2api_remote_error or "")[:500],
                    utc_now(), int(account_id),
                ),
            )
            return cursor.rowcount == 1

    def list_registration_runs(self, limit=50, offset=0):
        with self.connection() as connection:
            total = connection.execute("SELECT COUNT(*) FROM registration_runs").fetchone()[0]
            rows = connection.execute(
                """SELECT id, status, requested_count, processed_count, success_count,
                          failed_count, uncertain_count, registered_unsaved_count,
                          postprocess_warning_count, cancelled, started_at, finished_at, error_message,
                          CASE WHEN processed_count > 0
                               THEN ROUND(success_count * 100.0 / processed_count, 2)
                               ELSE 0 END AS success_rate,
                          (SELECT COUNT(*) FROM registration_attempts a
                           WHERE a.registration_run_id = registration_runs.id) AS attempt_count
                   FROM registration_runs
                   ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?""",
                (int(limit), int(offset)),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total}

    def list_accounts(
        self, limit=50, offset=0, search="", registration_status="",
        authorization_status="", email_provider="", registration_run_id=None,
        date_from="", date_to="", cpa_status="", grok2api_local_status="",
        grok2api_remote_status="",
    ):
        conditions = []
        parameters = []
        if search:
            escaped = str(search).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append("email LIKE ? ESCAPE '\\'")
            parameters.append("%" + escaped + "%")
        for column, value in (
            ("registration_status", registration_status),
            ("authorization_status", authorization_status),
            ("email_provider", email_provider),
            ("cpa_status", cpa_status),
            ("grok2api_local_status", grok2api_local_status),
            ("grok2api_remote_status", grok2api_remote_status),
        ):
            if value:
                conditions.append(column + " = ?")
                parameters.append(value)
        if registration_run_id is not None:
            conditions.append("registration_run_id = ?")
            parameters.append(int(registration_run_id))
        if date_from:
            conditions.append("julianday(created_at) >= julianday(?)")
            parameters.append(date_from)
        if date_to:
            conditions.append("julianday(created_at) <= julianday(?)")
            parameters.append(date_to)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connection() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM accounts" + where, parameters
            ).fetchone()[0]
            rows = connection.execute(
                """SELECT id, email, email_provider, registration_status,
                          authorization_status, cpa_status, grok2api_local_status,
                          grok2api_remote_status, source, registration_run_id,
                          created_at, updated_at, last_authenticated_at
                   FROM accounts""" + where + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                parameters + [int(limit), int(offset)],
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total}

    def get_account(self, account_id):
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (int(account_id),)
            ).fetchone()
            if row is None:
                return None
            account = dict(row)
            account["profile"] = json.loads(account.pop("profile_json"))
            account["attempts"] = [
                dict(item) for item in connection.execute(
                    """SELECT id, registration_run_id, slot_index, account_id, attempt_number, worker_key,
                              stage, status, error_type, error_message, traceback, failure_type,
                              screenshot_path, started_at, finished_at
                       FROM registration_attempts WHERE account_id = ? ORDER BY id DESC""",
                    (int(account_id),),
                ).fetchall()
            ]
            account["artifacts"] = [
                dict(item) for item in connection.execute(
                    """SELECT id, artifact_type, path, format, created_at
                       FROM account_artifacts WHERE account_id = ? ORDER BY id""",
                    (int(account_id),),
                ).fetchall()
            ]
            return account

    @staticmethod
    def _delete_accounts(connection, account_ids):
        ids = list(dict.fromkeys(int(value) for value in account_ids))
        if not ids:
            return 0
        placeholders = ",".join("?" for _value in ids)
        rows = connection.execute(
            "SELECT email, sso FROM accounts WHERE id IN (%s)" % placeholders, ids
        ).fetchall()
        deleted_at = utc_now()
        connection.executemany(
            "INSERT OR IGNORE INTO account_tombstones (identity_hash, deleted_at) VALUES (?, ?)",
            [(_account_identity_hash(row["email"], row["sso"]), deleted_at) for row in rows],
        )
        cursor = connection.execute(
            "DELETE FROM accounts WHERE id IN (%s)" % placeholders, ids
        )
        return cursor.rowcount

    def delete_accounts(self, account_ids):
        with self.transaction() as connection:
            return self._delete_accounts(connection, account_ids)

    def delete_account(self, account_id):
        with self.transaction() as connection:
            return self._delete_accounts(connection, [account_id]) == 1

    def export_accounts(self, account_ids=None):
        with self.connection() as connection:
            if account_ids is None:
                rows = connection.execute(
                    "SELECT email, password, sso FROM accounts ORDER BY id"
                ).fetchall()
            else:
                ids = [int(value) for value in account_ids]
                if not ids:
                    return []
                placeholders = ",".join("?" for _value in ids)
                rows = connection.execute(
                    "SELECT email, password, sso FROM accounts WHERE id IN (%s) ORDER BY id" % placeholders,
                    ids,
                ).fetchall()
        return [dict(row) for row in rows]


    def create_job(self, job_type, accounts):
        if not accounts:
            raise ValueError("No accounts selected")
        started = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO job_runs (job_type, requested_count, started_at)
                   VALUES (?, ?, ?)""",
                (job_type, len(accounts), started),
            )
            job_id = cursor.lastrowid
            connection.executemany(
                """INSERT INTO job_accounts (job_id, account_id, email, updated_at)
                   VALUES (?, ?, ?, ?)""",
                [(job_id, account["id"], account["email"], started) for account in accounts],
            )
            return job_id

    def update_job_item(self, job_id, account_id, status, error_message=""):
        with self.transaction() as connection:
            connection.execute(
                """UPDATE job_accounts SET status = ?, error_message = ?, updated_at = ?
                   WHERE job_id = ? AND account_id = ?""",
                (status, str(error_message or "")[:500], utc_now(), job_id, account_id),
            )
            counts = connection.execute(
                """SELECT COUNT(*) AS processed,
                          COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS successes,
                          COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failures
                   FROM job_accounts
                   WHERE job_id = ? AND status IN ('succeeded', 'failed')""",
                (job_id,),
            ).fetchone()
            connection.execute(
                """UPDATE job_runs
                   SET processed_count = ?, success_count = ?, failed_count = ?
                   WHERE id = ?""",
                (counts["processed"], counts["successes"], counts["failures"], job_id),
            )

    def finish_job(self, job_id, status, cancelled=False, error_message=""):
        with self.transaction() as connection:
            connection.execute(
                """UPDATE job_runs
                   SET status = ?, cancelled = ?, error_message = ?, finished_at = ?
                   WHERE id = ?""",
                (status, int(bool(cancelled)), str(error_message or "")[:500], utc_now(), job_id),
            )

    def get_job(self, job_id):
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM job_runs WHERE id = ?", (int(job_id),)
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["items"] = [dict(item) for item in connection.execute(
                """SELECT account_id, email, status, error_message, updated_at
                   FROM job_accounts WHERE job_id = ? ORDER BY rowid""",
                (job_id,),
            )]
            return result

    def list_jobs(self, job_type="", limit=50, offset=0):
        conditions = " WHERE job_type = ?" if job_type else ""
        args = [job_type] if job_type else []
        with self.connection() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM job_runs" + conditions, args
            ).fetchone()[0]
            items = connection.execute(
                """SELECT id, job_type, status, requested_count, processed_count,
                          success_count, failed_count, cancelled, started_at,
                          finished_at, error_message
                   FROM job_runs""" + conditions + " ORDER BY id DESC LIMIT ? OFFSET ?",
                args + [int(limit), int(offset)],
            ).fetchall()
        return {"total": total, "items": [dict(item) for item in items]}

    def update_account_session(self, account_id, sso):
        new_sso = str(sso or "").strip()
        if not new_sso:
            raise ValueError("New SSO must not be empty")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT email, sso FROM accounts WHERE id = ?", (int(account_id),)
            ).fetchone()
            if row is None:
                raise LookupError("Account not found")
            old_sso = row["sso"]
            if old_sso != new_sso:
                connection.execute(
                    """INSERT OR IGNORE INTO account_tombstones (identity_hash, deleted_at)
                       VALUES (?, ?)""",
                    (_account_identity_hash(row["email"], old_sso), utc_now()),
                )
            connection.execute(
                """UPDATE accounts
                   SET sso = ?, authorization_status = 'authorized',
                       cpa_status = 'unknown', cpa_error = '',
                       grok2api_local_status = 'unknown', grok2api_local_error = '',
                       grok2api_remote_status = 'unknown', grok2api_remote_error = '',
                       last_authenticated_at = ?, updated_at = ?
                   WHERE id = ?""",
                (new_sso, utc_now(), utc_now(), int(account_id)),
            )
            return {"email": row["email"], "old_sso": old_sso, "sso": new_sso}

    def account_stats(self):
        with self.connection() as connection:
            total = connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            grouped = {}
            for column in (
                "registration_status", "authorization_status", "cpa_status",
                "grok2api_local_status", "grok2api_remote_status", "email_provider",
            ):
                rows = connection.execute(
                    "SELECT %s AS value, COUNT(*) AS count FROM accounts GROUP BY %s" % (column, column)
                ).fetchall()
                grouped[column] = {row["value"]: row["count"] for row in rows}
            runs = connection.execute(
                """SELECT COUNT(*) AS run_count,
                          COALESCE(SUM(processed_count), 0) AS processed_count,
                          COALESCE(SUM(success_count), 0) AS success_count,
                          COALESCE(SUM(registered_unsaved_count), 0) AS registered_unsaved_count
                   FROM registration_runs"""
            ).fetchone()
            attempts = connection.execute(
                "SELECT COUNT(*) FROM registration_attempts"
            ).fetchone()[0]
        processed = int(runs["processed_count"])
        successes = int(runs["success_count"])
        registered_unsaved = int(runs["registered_unsaved_count"])
        return {
            "total": total,
            "by_registration_status": grouped["registration_status"],
            "by_authorization_status": grouped["authorization_status"],
            "by_cpa_status": grouped["cpa_status"],
            "by_grok2api_local_status": grouped["grok2api_local_status"],
            "by_grok2api_remote_status": grouped["grok2api_remote_status"],
            "by_email_provider": grouped["email_provider"],
            "run_count": int(runs["run_count"]),
            "attempt_count": attempts,
            "processed_count": processed,
            "success_count": successes,
            "registered_unsaved_count": registered_unsaved,
            "success_rate": round(successes * 100.0 / processed, 2) if processed else 0.0,
        }
