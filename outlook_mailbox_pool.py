"""Thread-safe Outlook mailbox pool, task runtime and private local persistence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from typing import Optional, Union

from outlook_mail import (
    OutlookAccount,
    OutlookMailbox,
    normalize_outlook_mode,
    probe_outlook_account,
)

_MAX_POOL_BYTES = 1_000_000
_HANDLE_PREFIX = "outlook:"
_DEFAULT_POOL_PATH = "./output/mailboxes/outlook-accounts.txt"
_HEALTH_MAX_WORKERS = 4


def _split_by_dashes(line: str) -> list[str]:
    parts = []
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
    accounts = []
    normalized = str(data or "").replace("\r\n", "\n").replace("\r", "\n")
    for entry in _entries(normalized):
        parts = _split_account_fields(entry)
        if len(parts) not in {4, 5}:
            continue
        if not parts[0] or not parts[2] or not parts[3]:
            continue
        raw_mode = parts[4] if len(parts) == 5 else "auto"
        if len(parts) == 5 and str(raw_mode).strip().lower() not in {"auto", "imap", "graph"}:
            continue
        accounts.append(
            OutlookAccount(
                email=parts[0],
                password=parts[1],
                client_id=parts[2],
                refresh_token=parts[3],
                mode=normalize_outlook_mode(raw_mode),
            )
        )
    return accounts


def inspect_outlook_mailbox_pool(data: str) -> dict:
    normalized = str(data or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    entries = _entries(normalized)
    accounts = parse_outlook_accounts(normalized)
    seen = set()
    duplicates = []
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


def _validate_pool_data(data: str):
    normalized = str(data or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("Outlook 账号池不能为空")
    if len(normalized.encode("utf-8")) > _MAX_POOL_BYTES:
        raise ValueError("Outlook 账号池过大")
    summary = inspect_outlook_mailbox_pool(normalized)
    if summary["invalid"]:
        raise ValueError(
            "账号池中有 %s 条格式无效的记录；每行应为 "
            "email----password----clientId----refreshToken----auto/imap/graph"
            % summary["invalid"]
        )
    if summary["duplicates"]:
        raise ValueError("账号池存在重复邮箱: " + ", ".join(summary["duplicates"][:3]))
    accounts = parse_outlook_accounts(normalized)
    if not accounts:
        raise ValueError("Outlook 账号池没有有效账号")
    return normalized + "\n", accounts


def _canonical_path(path: Union[str, os.PathLike]) -> Path:
    raw = str(path or "").strip() or _DEFAULT_POOL_PATH
    return Path(raw).expanduser().resolve()


def _read_pool_text(path: Union[str, os.PathLike], missing_ok: bool = False):
    target = _canonical_path(path)
    try:
        with target.open("rb") as handle:
            raw = handle.read(_MAX_POOL_BYTES + 1)
    except FileNotFoundError as exc:
        if missing_ok:
            return target, ""
        raise ValueError("Outlook 账号池文件不存在: %s" % target) from exc
    except OSError as exc:
        raise RuntimeError("读取 Outlook 账号池失败: %s" % exc) from exc
    if len(raw) > _MAX_POOL_BYTES:
        raise ValueError("Outlook 账号池过大")
    try:
        return target, raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Outlook 账号池必须是 UTF-8 文本") from exc


def load_outlook_mailbox_pool(path: Union[str, os.PathLike]) -> dict:
    target, data = _read_pool_text(path, missing_ok=True)
    return {"path": str(target), "data": data, **inspect_outlook_mailbox_pool(data)}


def _read_validated_pool(path: Union[str, os.PathLike]):
    target, data = _read_pool_text(path, missing_ok=False)
    normalized, accounts = _validate_pool_data(data)
    return target, normalized, accounts


def get_outlook_mailbox_pool_capacity(path: Union[str, os.PathLike]) -> int:
    _target, _normalized, accounts = _read_validated_pool(path)
    return len(accounts)


def _write_private_file(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
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


def save_outlook_mailbox_pool(path: Union[str, os.PathLike], data: str) -> dict:
    normalized, accounts = _validate_pool_data(data)
    target = _canonical_path(path)
    _write_private_file(target, normalized)
    return {
        "path": str(target),
        "count": len(accounts),
        "accounts": [{"email": account.email, "mode": account.mode} for account in accounts],
    }


def _probe_accounts(accounts: list[OutlookAccount], max_workers: int = _HEALTH_MAX_WORKERS) -> dict:
    workers = max(1, min(int(max_workers or 1), _HEALTH_MAX_WORKERS, len(accounts)))
    if workers == 1:
        results = [probe_outlook_account(account) for account in accounts]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="outlook-health") as executor:
            results = list(executor.map(probe_outlook_account, accounts))
    healthy = sum(1 for result in results if result.get("usable"))
    imap = sum(1 for result in results if result.get("imap", {}).get("ok"))
    graph = sum(1 for result in results if result.get("graph", {}).get("ok"))
    return {
        "count": len(results),
        "healthy": healthy,
        "unhealthy": len(results) - healthy,
        "imap": imap,
        "graph": graph,
        "results": results,
    }


def probe_outlook_mailbox_pool_data(data: str, max_workers: int = _HEALTH_MAX_WORKERS) -> dict:
    """Validate and preflight unsaved pool data without persisting any credentials."""
    _normalized, accounts = _validate_pool_data(data)
    return _probe_accounts(accounts, max_workers=max_workers)


def probe_outlook_mailbox_pool(
    path: Union[str, os.PathLike], max_workers: int = _HEALTH_MAX_WORKERS
) -> dict:
    """Preflight a saved pool and return only safe status metadata."""
    _target, _normalized, accounts = _read_validated_pool(path)
    return _probe_accounts(accounts, max_workers=max_workers)


@dataclass
class OutlookMailboxLease:
    handle: str
    mailbox: OutlookMailbox

    @property
    def email(self) -> str:
        return self.mailbox.email


class OutlookAccountPool:
    """Monotonic per-task allocator. Accounts are never wrapped or reused."""

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
        last_error = None
        while True:
            with self._lock:
                if self._next >= len(self._accounts):
                    if last_error is not None:
                        raise RuntimeError(
                            "Outlook 邮箱池已耗尽；剩余邮箱预检均失败，且不会循环复用已领取账号: %s"
                            % last_error
                        ) from last_error
                    raise RuntimeError("Outlook 邮箱池已耗尽，不会循环复用已领取账号")
                account = self._accounts[self._next]
                self._next += 1
            mailbox = OutlookMailbox(account, log_callback=log_callback)
            try:
                mailbox.prepare()
                return mailbox
            except Exception as exc:
                mailbox.close()
                last_error = exc
                if log_callback:
                    log_callback("[!] Outlook 邮箱预检失败，跳过 %s: %s" % (account.email, exc))


class OutlookTaskRuntime:
    """One shared Outlook allocator/lease registry for exactly one registration task."""

    def __init__(
        self, accounts: list[OutlookAccount], log_callback=None, cancelled_exception=None
    ) -> None:
        self._pool = OutlookAccountPool(accounts)
        self._log_callback = log_callback
        self._cancelled_exception = cancelled_exception
        self._lock = threading.RLock()
        self._leases = {}
        self._closed = False

    def _raise_if_cancelled(self, cancel_callback=None) -> None:
        if not cancel_callback or not cancel_callback():
            return
        if self._cancelled_exception is not None:
            raise self._cancelled_exception("用户停止注册")
        raise RuntimeError("任务已停止")

    @property
    def count(self) -> int:
        return self._pool.count

    @property
    def remaining(self) -> int:
        return self._pool.remaining

    def acquire(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("Outlook 邮箱任务运行时已关闭")
        mailbox = self._pool.acquire(log_callback=self._log_callback)
        handle = _HANDLE_PREFIX + secrets.token_urlsafe(24)
        lease = OutlookMailboxLease(handle=handle, mailbox=mailbox)
        with self._lock:
            if self._closed:
                mailbox.close()
                raise RuntimeError("Outlook 邮箱任务运行时已关闭")
            self._leases[handle] = lease
        return mailbox.email, handle

    def wait_for_code(
        self,
        handle: str,
        email: str,
        timeout: int = 180,
        poll_interval: int = 3,
        log_callback=None,
        cancel_callback=None,
        resend_callback=None,
    ) -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("Outlook 邮箱任务运行时已关闭")
            key = str(handle or "")
            lease = self._leases.get(key)
            if lease is None:
                raise RuntimeError("Outlook 邮箱会话不存在、已使用或已失效")
            if lease.email.lower() != str(email or "").lower():
                raise RuntimeError("Outlook 邮箱会话与目标邮箱不匹配")
            # One-shot capability: once polling begins the opaque handle cannot
            # be replayed, and its credential-bearing mailbox is released as
            # soon as this wait finishes.
            del self._leases[key]
        try:
            self._raise_if_cancelled(cancel_callback)
            try:
                code = lease.mailbox.wait_for_code(
                    timeout=int(timeout),
                    interval=int(poll_interval),
                    cancel_callback=cancel_callback,
                    resend_callback=resend_callback,
                )
            except Exception:
                self._raise_if_cancelled(cancel_callback)
                raise
            self._raise_if_cancelled(cancel_callback)
            if not code:
                from registration_flow import VerificationCodeUnavailable
                raise VerificationCodeUnavailable("Outlook 在 %ss 内未收到验证码邮件" % timeout)
            return str(code)
        finally:
            lease.mailbox.close()

    def status(self) -> dict:
        with self._lock:
            return {
                "count": self.count,
                "remaining": self.remaining,
                "leased": len(self._leases),
                "closed": self._closed,
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            leases = list(self._leases.values())
            self._leases.clear()
        for lease in leases:
            try:
                lease.mailbox.close()
            except Exception:
                pass


def create_outlook_task_runtime(
    path: Union[str, os.PathLike], log_callback=None, cancelled_exception=None
) -> OutlookTaskRuntime:
    _target, _normalized, accounts = _read_validated_pool(path)
    return OutlookTaskRuntime(
        accounts, log_callback=log_callback, cancelled_exception=cancelled_exception
    )


def is_outlook_handle(value: str) -> bool:
    return str(value or "").startswith(_HANDLE_PREFIX)
