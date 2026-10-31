"""Import account records from the legacy TXT and pending JSONL formats."""

from dataclasses import dataclass
import json
from pathlib import Path

from storage.database import Database


@dataclass(frozen=True)
class ImportSummary:
    total: int
    imported: int
    duplicates: int
    malformed: int


def _attach_source(database, account_ids, source_type, path, file_format):
    source_path = str(Path(path).expanduser().resolve())
    database.add_artifacts(account_ids, source_type, source_path, file_format)


def import_legacy_accounts(database, path, email_provider=""):
    """Import the legacy email/password/SSO line format without modifying the file."""
    if not isinstance(database, Database):
        raise TypeError("database must be a Database instance")

    records = []
    malformed = 0
    with open(path, "r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            parts = line.split("----", 2)
            if len(parts) != 3 or not parts[0].strip() or not parts[2].strip():
                malformed += 1
                continue
            records.append({
                "email": parts[0].strip(),
                "password": parts[1],
                "sso": parts[2].strip(),
                "email_provider": email_provider,
                "registration_status": "registered",
                "source": "legacy_txt",
            })

    inserted = database.insert_accounts(records)
    account_ids = [account_id for account_id, _created in inserted]
    _attach_source(database, account_ids, "legacy_txt", path, "txt")
    imported = sum(created for _account_id, created in inserted)
    return ImportSummary(
        total=len(records) + malformed,
        imported=imported,
        duplicates=len(records) - imported,
        malformed=malformed,
    )


def import_legacy_pending(database, path, email_provider=""):
    """Import pending records for visibility while leaving recovery files intact."""
    if not isinstance(database, Database):
        raise TypeError("database must be a Database instance")

    records = []
    malformed = 0
    with open(path, "r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("pending record must be an object")
                email = str(record.get("email") or "").strip()
                sso = str(record.get("sso") or "").strip()
                if not email or not sso:
                    raise ValueError("pending record is missing email or SSO")
            except (json.JSONDecodeError, ValueError):
                malformed += 1
                continue
            records.append({
                "email": email,
                "password": str(record.get("password") or ""),
                "sso": sso,
                "email_provider": email_provider,
                "registration_status": "pending_recovery",
                "source": "legacy_pending",
                "profile": record.get("profile") if isinstance(record.get("profile"), dict) else {},
                "created_at": record.get("queued_at"),
            })

    inserted = database.insert_accounts(records)
    account_ids = [account_id for account_id, _created in inserted]
    _attach_source(database, account_ids, "legacy_pending", path, "jsonl")
    imported = sum(created for _account_id, created in inserted)
    return ImportSummary(
        total=len(records) + malformed,
        imported=imported,
        duplicates=len(records) - imported,
        malformed=malformed,
    )
