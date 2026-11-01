# SQLite storage

The application database defaults to `./data/grok-register.sqlite3` and can be changed with `database_path` in `config.json`.

The storage package uses Python's built-in `sqlite3` module. It enables foreign keys, a 30-second busy timeout, and WAL mode. Schema changes are tracked with SQLite's `user_version`; a database created by a newer application version is rejected rather than silently modified.

## Schema

- `accounts`: account credentials, registration and authorization state, independent CPA and Grok2API local/remote outcomes, source, profile data, and timestamps.
- `registration_runs`: batch-level counters (including registered-but-unsaved accounts), cancellation/error state, and a non-secret configuration snapshot.
- `registration_attempts`: one record for each serial or parallel worker attempt, including batch slot, retry number, worker, stage, outcome, failure type, redacted error details, traceback, and screenshot path.
- `account_artifacts`: paths to related files such as legacy account exports.
- `legacy_imports`: source file fingerprints used to avoid rescanning unchanged legacy files.
- `account_tombstones`: one-way hashes that prevent explicitly deleted accounts from being restored by a later legacy import.
- `job_runs`: reserved for non-registration background jobs.

An account is identified by the case-insensitive email address plus its exact SSO value. A native registration updates the account's latest registration batch and provider metadata when the same identity already exists.

Registration, authorization, CPA export, and Grok2API local/remote import are separate states. A downstream failure does not change a successfully persisted account's registration status. Legacy accounts and unavailable results remain `unknown`. For new registrations, disabled integrations are recorded as `not_requested`; enabled integrations are recorded as `succeeded`, `failed`, or `warning` as appropriate.

## Legacy compatibility

The importer supports:

- `import_legacy_accounts(database, path, email_provider="")` for `email----password----sso` TXT files.
- `import_legacy_pending(database, path, email_provider="")` for pending JSONL files.
- `import_legacy_directory(database, directory, email_provider="")` for incremental directory imports.

Legacy TXT and pending files remain unchanged. Files are read under the same file locks used by the legacy writers. Imports are idempotent and use file size plus nanosecond modification time to skip unchanged sources. The legacy TXT format does not encode its mailbox provider, so imported records keep an empty provider rather than inheriting the currently selected provider.

The WebUI imports legacy files when the account database is first accessed; the registration engine also scans its output directory before starting a batch. Explicit account deletion writes an identity tombstone, so the same account is not silently reintroduced by a later import. A new native registration can intentionally recreate that identity.

Native registration writes SQLite first and then maintains the legacy TXT export. If the TXT export fails after the SQLite commit, the account remains saved in SQLite and the export warning is recorded separately. If the SQLite write fails, the existing pending recovery path is retained.

## Account APIs

The local WebUI exposes SQLite-backed endpoints:

- `GET /api/accounts`: paginated account list with email search, registration/authorization/CPA/Grok2API status, provider, date, and registration-batch filters.
- `GET /api/accounts/{id}`: account details, related artifacts, and registration attempts.
- `DELETE /api/accounts/{id}` and `DELETE /api/accounts`: single and batch deletion.
- `POST /api/accounts/export`: export selected accounts in the legacy TXT format.
- `GET /api/stats`: account status breakdowns and aggregate registration success rate.
- `GET /api/jobs/history`: paginated registration history with success rate and attempt counts.
- `GET /api/jobs/history/{run_id}/attempts`: paginated attempt diagnostics, including failure type, traceback, and screenshot path.
- `GET /api/jobs/history/{run_id}/attempts/{attempt_id}/screenshot`: same-origin preview of a recorded PNG inside the database's diagnostics directory.

Failed and retrying browser attempts capture a screenshot before final browser cleanup. Screenshots are stored under `diagnostics/registration/` beside the configured SQLite database (under `data/diagnostics/registration/` by default). On POSIX systems, the directory and files use `0700` and `0600` permissions; screenshot errors are logged but never replace the original registration result. Attempts that fail before an account exists remain discoverable through the batch-attempt endpoint.

Account endpoints that expose or mutate account data require same-origin local WebUI requests. Account list responses omit passwords and SSO values; account details, attempt tracebacks, screenshot paths, and explicit exports are sensitive and remain local-only.
