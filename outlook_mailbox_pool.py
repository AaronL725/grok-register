"""Thread-safe Outlook mailbox pool and private local persistence."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from typing import Optional

from outlook_mail import OutlookAccount, OutlookMailbox, normalize_outlook_mode

_MAX_POOL_BYTES = 1_000_000
_HANDLE_PREFIX = "outlook:"


def _split_by_dashes(line: str) -> list[str]:
    parts: list[str] = []
    last = 0
    for match in re.finditer(r"-{4,}", line):
        parts.append(line[last:match.start()] + "-" * (len(match.group(0)) - 4))
        last = match.end()
    parts.append(line[last:])
    return parts


def _split_account_fields(line: str) -> list[str]:
    raw = str(line or "").strip()
    if not raw:
        return []
    if re.search(r"-{4,}", raw):
        return [part.strip() for part in _split_by_dashes(raw)]
    if "|" in raw:
        return [part.strip() for part in raw.split("|")]
    return [raw]


def _entries(data: str) -> list[str]:
    lines = [line.strip() for line in str(data or "").splitlines() if line.strip()]
    if len(lines) == 1:
        return lines[0].split()
    return lines


def parse_outlook_accounts(data: str) -> list[OutlookAccount]:
    accounts: list[OutlookAccount] = []
    for entry in _entries(str(data or "").replace("\r\n", "\n").replace("\r", "\n")):
        parts = _split_account_fields(entry)
        if len(parts) not in {4, 5}:
            continue
        if not parts[0] or not parts[2] or not parts[3]:
            continue
        mode = normalize_outlook_mode(parts[4] if len(parts) == 5 else "auto")
        if len(parts) == 5 and str(parts[4]).strip().lower() not in {"auto", "imap", "graph"}:
            continue
        accounts.append(OutlookAccount(parts[0], parts[1], parts[2], parts[3], mode))
    return accounts


def inspect_outlook_mailbox_pool(data: str) -> dict:
    normalized = str(data or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    entries = _entries(normalized)
    accounts = parse_outlook_accounts(normalized)
    seen: set[str] = set()
    duplicates: list[str] = []
    for account in accounts:
        key = account.email.lower()
        if key in seen and key not in duplicates:
            duplicates.append(key)
        seen.add(key)
    return {
        "count": len(accounts),
        "invalid": max(0, len(entries) - len(accounts)),
        "duplicates": duplicates,
        "accounts": [{"email": account.email, "mode": account.mode} for account in accounts],
    }


def _validate_pool_data(data: str) -> tuple[str, list[OutlookAccount]]:
    normalized = str(data or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("Outlook 账号池不能为空")
    if len(normalized.encode("utf-8")) > _MAX_POOL_BYTES:
        raise ValueError("Outlook 账号池过大")
    summary = inspect_outlook_mailbox_pool(normalized)
    if summary["invalid"]:
        raise ValueError(
            f"账号池中有 {summary['invalid']} 条格式无效的记录；每行应为 "
            "email----password----clientId----refreshToken----auto/imap/graph"
        )
    if summary["duplicates"]:
        raise ValueError("账号池存在重复邮箱: " + ", ".join(summary["duplicates"][:3]))
    accounts = parse_outlook_accounts(normalized)
    if not accounts:
        raise ValueError("Outlook 账号池没有有效账号")
    return normalized + "\n", accounts


def _canonical_path(path: str | os.PathLike[str]) -> Path:
    raw = str(path or "").strip()
    if not raw:
        raw = "./output/mailboxes/outlook-accounts.txt"
    return Path(raw).expanduser().resolve()


def load_outlook_mailbox_pool(path: str | os.PathLike[str]) -> dict:
    target = _canonical_path(path)
    try:
        data = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        data = ""
    except OSError as exc:
        raise RuntimeError(f"读取 Outlook 账号池失败: {exc}") from exc
    return {"path": str(target), "data": data, **inspect_outlook_mailbox_pool(data)}


def _write_private_file(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(path))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def save_outlook_mailbox_pool(path: str | os.PathLike[str], data: str) -> dict:
    normalized, accounts = _validate_pool_data(data)
    target = _canonical_path(path)
    _write_private_file(target, normalized)
    reset_shared_outlook_runtime()
    return {
        "path": str(target),
        "count": len(accounts),
        "accounts": [{"email": account.email, "mode": account.mode} for account in accounts],
    }


@dataclass
class OutlookMailboxLease:
    handle: str
    mailbox: OutlookMailbox
    consumed: bool = False

    @property
    def email(self) -> str:
        return self.mailbox.email


class OutlookAccountPool:
    def __init__(self, accounts: list[OutlookAccount]) -> None:
        if not accounts:
            raise ValueError("Outlook 邮箱池没有有效账号")
        self._accounts = list(accounts)
        self._lock = threading.Lock()
        self._next = 0

    @property
    def count(self) -> int:
        return len(self._accounts)

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, len(self._accounts) - self._next)

    def acquire(self, log_callback=None) -> OutlookMailbox:
        with self._lock:
            if self._next >= len(self._accounts):
                raise RuntimeError("Outlook 邮箱池已耗尽，不会循环复用已领取账号")
            account = self._accounts[self._next]
            self._next += 1
        mailbox = OutlookMailbox(account, log_callback=log_callback)
        mailbox.prepare()
        return mailbox


_runtime_lock = threading.RLock()
_runtime_path = ""
_runtime_fingerprint = ""
_runtime_pool: Optional[OutlookAccountPool] = None
_runtime_leases: dict[str, OutlookMailboxLease] = {}


def _pool_fingerprint(path: Path, data: str) -> str:
    digest = hashlib.sha256(data.encode("utf-8")).hexdigest()
    return f"{path}:{digest}"


def reset_shared_outlook_runtime() -> None:
    global _runtime_path, _runtime_fingerprint, _runtime_pool
    with _runtime_lock:
        _runtime_path = ""
        _runtime_fingerprint = ""
        _runtime_pool = None
        _runtime_leases.clear()


def _ensure_runtime(path: str | os.PathLike[str]) -> OutlookAccountPool:
    global _runtime_path, _runtime_fingerprint, _runtime_pool
    target = _canonical_path(path)
    try:
        data = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"Outlook 账号池文件不存在: {target}") from exc
    normalized, accounts = _validate_pool_data(data)
    fingerprint = _pool_fingerprint(target, normalized)
    with _runtime_lock:
        if _runtime_pool is None or _runtime_fingerprint != fingerprint:
            _runtime_path = str(target)
            _runtime_fingerprint = fingerprint
            _runtime_pool = OutlookAccountPool(accounts)
            _runtime_leases.clear()
        return _runtime_pool


def get_shared_outlook_pool_status(path: str | os.PathLike[str]) -> dict:
    pool = _ensure_runtime(path)
    with _runtime_lock:
        return {"count": pool.count, "remaining": pool.remaining, "leased": len(_runtime_leases)}


def acquire_shared_outlook_mailbox(path: str | os.PathLike[str], log_callback=None) -> tuple[str, str]:
    pool = _ensure_runtime(path)
    mailbox = pool.acquire(log_callback=log_callback)
    handle = _HANDLE_PREFIX + secrets.token_urlsafe(24)
    lease = OutlookMailboxLease(handle=handle, mailbox=mailbox)
    with _runtime_lock:
        _runtime_leases[handle] = lease
    return mailbox.email, handle


def is_outlook_handle(value: str) -> bool:
    return str(value or "").startswith(_HANDLE_PREFIX)


def wait_for_shared_outlook_code(
    handle: str,
    email: str,
    timeout: int = 180,
    poll_interval: int = 3,
    log_callback=None,
    cancel_callback=None,
) -> str:
    with _runtime_lock:
        lease = _runtime_leases.get(str(handle or ""))
    if lease is None:
        raise RuntimeError("Outlook 邮箱会话不存在或已失效")
    if lease.email.lower() != str(email or "").lower():
        raise RuntimeError("Outlook 邮箱会话与目标邮箱不匹配")
    code = lease.mailbox.wait_for_code(
        timeout=int(timeout), interval=int(poll_interval), cancel_callback=cancel_callback
    )
    lease.consumed = True
    if not code:
        # Import here to avoid a module cycle during mail_service import.
        from registration_flow import VerificationCodeUnavailable
        raise VerificationCodeUnavailable(f"Outlook 在 {timeout}s 内未收到验证码邮件")
    return code
