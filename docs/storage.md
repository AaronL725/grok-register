# SQLite storage

The application database defaults to `./data/grok-register.sqlite3` and can be changed with `database_path` in `config.json`.

The storage package uses Python's built-in `sqlite3` module. It enables foreign keys, a 30-second busy timeout, and WAL mode. Schema changes are tracked with SQLite's `user_version`; a database created by a newer application version is rejected rather than silently modified.

## Schema

- `accounts`: account credentials, registration and authorization status, source, profile data, and timestamps.
- `registration_runs`: batch-level registration counters and configuration snapshot.
- `registration_attempts`: per-attempt stage, outcome, and diagnostic fields.
- `account_artifacts`: paths to related files such as legacy account exports.
- `job_runs`: lifecycle records for registration and account-management jobs.

An account is currently identified by the case-insensitive email address plus its exact SSO value. This preserves the identity rule used by the legacy pending recovery workflow while allowing later account operations to update a specific account row.

## Legacy import

`storage.legacy_import` provides two explicit import functions:

- `import_legacy_accounts(database, path, email_provider="")` reads the legacy `email----password----sso` line format.
- `import_legacy_pending(database, path, email_provider="")` reads pending JSONL records.

Both imports are idempotent, report imported/duplicate/malformed record counts, and leave the source files untouched. The importer records the source file in `account_artifacts`. A normal TXT account record promotes a matching pending record to `registered`; importing a pending record never downgrades an already registered account.

Import is intentionally explicit in this phase. The registration engine and WebUI will begin using the database in the following integration phase, after this storage layer has been independently reviewed.
