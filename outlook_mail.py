"""Outlook OAuth2 mailbox access through IMAP or Microsoft Graph."""
from __future__ import annotations

from dataclasses import dataclass
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
import imaplib
import re
import time
from typing import Callable, Optional, Union

import requests

MICROSOFT_CONSUMERS_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
MICROSOFT_COMMON_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
OUTLOOK_IMAP_HOST = "outlook.office365.com"
OUTLOOK_IMAP_PORT = 993
OUTLOOK_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
OUTLOOK_GRAPH_INBOX_KEY = "GRAPH:INBOX"
OUTLOOK_SCAN_DEPTH = 15
OUTLOOK_GRAPH_SCAN_DEPTH = 15
OUTLOOK_FALLBACK_FOLDERS = (
    "INBOX", "Junk Email", "Junk", "Spam", "Archive", "Deleted Items",
    "垃圾邮件", "垃圾箱", "归档", "已删除邮件", "已删除项目",
)

LogCallback = Optional[Callable[[str], None]]


@dataclass(frozen=True)
class OutlookAccount:
    email: str
    password: str
    client_id: str
    refresh_token: str
    mode: str = "auto"


def normalize_outlook_mode(mode: Optional[str]) -> str:
    normalized = str(mode or "").strip().lower()
    return normalized if normalized in {"auto", "imap", "graph"} else "auto"


def _log(callback: LogCallback, message: str) -> None:
    if callback:
        try:
            callback(message)
        except Exception:
            pass


def _normalize_code(code: str) -> str:
    return str(code or "").replace("-", "").strip()


def extract_verification_code(subject: str = "", text: str = "", html: str = "", sender: str = "") -> Optional[str]:
    combined = "\n".join(str(value or "") for value in (subject, text, html))
    trusted_sender = bool(re.search(r"(?:^|[<@.])(?:x\.ai|accounts\.x\.ai)(?:[>\s]|$)", str(sender or ""), re.I))
    contextual = bool(re.search(r"\b(?:verification|confirmation|security|verify|confirm)\b|验证码|验证|确认", combined, re.I))

    patterns = (
        r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b",
        r"(?:verification|confirmation|security)\s+code\s*[:：]?\s*([A-Z0-9]{4,8})\b",
        r"(?:your\s+code|code)\s*[:：]\s*([A-Z0-9]{4,8})\b",
        r"(?:验证码|校验码|确认码)\s*[:：]?\s*([A-Z0-9]{4,8})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, combined, re.I)
        if match and (contextual or trusted_sender or "-" in match.group(1)):
            return match.group(1)
    return None


def _microsoft_oauth_error_message(label: str, status_code: int, data: dict) -> str:
    error = str(data.get("error") or "").strip()
    description = str(data.get("error_description") or data.get("raw") or data).strip()
    aadsts = ""
    match = re.search(r"\b(AADSTS\d+)\b", description)
    if match:
        aadsts = match.group(1)
        description = description[match.end():].lstrip(": ").strip()
    description = re.split(r"\s+(?:Trace ID|Correlation ID|Timestamp):", description, maxsplit=1)[0].strip()
    code = "/".join(part for part in (error, aadsts) if part) or f"HTTP {status_code}"
    return f"{label} token 刷新失败 {status_code} {code}" + (f": {description}" if description else "")


def is_terminal_microsoft_token_error(error: Optional[Union[Exception, str]]) -> bool:
    text = str(error or "").lower()
    markers = (
        "invalid_grant", "aadsts7000012", "aadsts70000", "aadsts700082", "aadsts700084",
        "refresh token has expired", "refresh token is invalid", "grant was obtained for a different tenant",
    )
    return any(marker in text for marker in markers)


def _refresh_access_token(account: OutlookAccount, scope: str, token_urls: tuple[str, ...], label: str) -> str:
    last_error = ""
    for token_url in token_urls:
        try:
            response = requests.post(
                token_url,
                data={
                    "client_id": account.client_id,
                    "refresh_token": account.refresh_token,
                    "grant_type": "refresh_token",
                    "scope": scope,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
        except requests.RequestException as exc:
            last_error = f"{label} token 请求失败: {exc}"
            continue
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text}
        if response.status_code != 200:
            last_error = _microsoft_oauth_error_message(label, response.status_code, data)
            if is_terminal_microsoft_token_error(last_error):
                raise RuntimeError(last_error)
            continue
        token = str(data.get("access_token") or "").strip()
        if token:
            return token
        last_error = f"{label} token 响应缺少 access_token"
    raise RuntimeError(last_error or f"{label} token 刷新失败")


def refresh_outlook_imap_token(account: OutlookAccount) -> str:
    return _refresh_access_token(
        account,
        "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
        (MICROSOFT_CONSUMERS_TOKEN_URL,),
        "Outlook IMAP",
    )


def refresh_outlook_graph_token(account: OutlookAccount) -> str:
    return _refresh_access_token(
        account,
        "https://graph.microsoft.com/Mail.Read offline_access",
        (MICROSOFT_COMMON_TOKEN_URL, MICROSOFT_CONSUMERS_TOKEN_URL),
        "Outlook Graph",
    )


def _xoauth2_auth_string(email: str, access_token: str) -> bytes:
    return f"user={email}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")


def _connect_imap(account: OutlookAccount, access_token: str) -> imaplib.IMAP4_SSL:
    client = imaplib.IMAP4_SSL(OUTLOOK_IMAP_HOST, OUTLOOK_IMAP_PORT, timeout=20)
    client.authenticate("XOAUTH2", lambda _: _xoauth2_auth_string(account.email, access_token))
    return client


def _normalize_folder_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip()).lower()


def _decode_imap_list_name(raw_line: Union[bytes, str]) -> str:
    line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, bytes) else str(raw_line)
    quoted = re.findall(r'"([^\"]+)"', line)
    if quoted:
        return quoted[-1].strip()
    parts = line.split()
    return parts[-1].strip().strip('"') if parts else ""


def _discover_folders(client: imaplib.IMAP4_SSL) -> list[str]:
    discovered: list[str] = []
    try:
        status, data = client.list()
        if status == "OK":
            for raw in data or []:
                folder = _decode_imap_list_name(raw)
                if folder:
                    discovered.append(folder)
    except Exception:
        pass
    keywords = ("inbox", "junk", "spam", "archive", "deleted", "trash", "收件箱", "垃圾", "归档", "已删除")
    preferred = [f for f in discovered if any(k in _normalize_folder_name(f) for k in keywords)]
    ordered: list[str] = []
    seen: set[str] = set()
    for folder in [*(preferred or discovered), *OUTLOOK_FALLBACK_FOLDERS]:
        key = _normalize_folder_name(folder)
        if key and key not in seen:
            seen.add(key)
            ordered.append(folder)
    return ordered


def _select_folder_count(client: imaplib.IMAP4_SSL, folder: str) -> Optional[int]:
    status, data = client.select(folder, readonly=True)
    if status != "OK":
        return None
    raw = data[0] if data else b"0"
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return None


def _graph_get(access_token: str, path: str, params: Optional[dict[str, str]] = None) -> dict:
    response = requests.get(
        f"{OUTLOOK_GRAPH_BASE_URL}{path}",
        params=params or {},
        headers={"Authorization": f"Bearer {access_token}", "Prefer": 'outlook.body-content-type="text"'},
        timeout=30,
    )
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}
    if not response.ok:
        raise RuntimeError(f"Outlook Graph 请求失败 {response.status_code}: {str(data)[:300]}")
    return data


def _graph_inbox_count(access_token: str) -> int:
    data = _graph_get(access_token, "/me/mailFolders/inbox", {"$select": "totalItemCount"})
    return int(data.get("totalItemCount") or 0)


def load_folder_counts(account: OutlookAccount) -> dict[str, int]:
    mode = normalize_outlook_mode(account.mode)
    errors: list[str] = []
    if mode in {"imap", "auto"}:
        try:
            token = refresh_outlook_imap_token(account)
            client = _connect_imap(account, token)
            try:
                counts: dict[str, int] = {}
                for folder in _discover_folders(client):
                    total = _select_folder_count(client, folder)
                    if total is not None:
                        counts[folder] = total
                if counts or mode == "imap":
                    return counts
                errors.append("IMAP: 未发现可读取的邮件文件夹")
            finally:
                try:
                    client.logout()
                except Exception:
                    pass
        except Exception as exc:
            if mode == "imap":
                raise
            errors.append(f"IMAP: {exc}")
    if mode in {"graph", "auto"}:
        try:
            token = refresh_outlook_graph_token(account)
            return {OUTLOOK_GRAPH_INBOX_KEY: _graph_inbox_count(token)}
        except Exception as exc:
            if mode == "graph":
                raise
            errors.append(f"Graph: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return {}


def _decode_header_value(value: str) -> str:
    parts: list[str] = []
    for part, charset in decode_header(value or ""):
        if isinstance(part, bytes):
            parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(str(part))
    return "".join(parts)


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")


def _fetch_message_content(client: imaplib.IMAP4_SSL, seq: int) -> tuple[str, str, str, str]:
    status, data = client.fetch(str(seq), "(BODY.PEEK[])")
    if status != "OK":
        raise RuntimeError(f"FETCH {seq} 失败: {status}")
    raw_parts = [item[1] for item in data or [] if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes)]
    if not raw_parts:
        return "", "", "", ""
    message = message_from_bytes(b"".join(raw_parts))
    subject = _decode_header_value(message.get("Subject", ""))
    sender = _decode_header_value(message.get("From", ""))
    text_parts: list[str] = []
    html_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart():
            continue
        content_type = (part.get_content_type() or "").lower()
        if content_type == "text/plain":
            text_parts.append(_decode_payload(part))
        elif content_type == "text/html":
            html_parts.append(_decode_payload(part))
    return subject, "\n".join(text_parts), "\n".join(html_parts), sender


def _scan_imap_once(account: OutlookAccount, token: str, counts: dict[str, int], log_callback: LogCallback = None) -> Optional[str]:
    client = _connect_imap(account, token)
    try:
        for folder in _discover_folders(client):
            total = _select_folder_count(client, folder)
            if total is None or total <= 0:
                continue
            before = counts.get(folder, 0)
            if total <= before:
                continue
            start = max(1, total - OUTLOOK_SCAN_DEPTH + 1, before + 1)
            _log(log_callback, f"[*] Outlook 文件夹更新: {folder} {before} -> {total}")
            for seq in range(total, start - 1, -1):
                subject, text, html, sender = _fetch_message_content(client, seq)
                code = extract_verification_code(subject, text, html, sender)
                if code:
                    counts[folder] = max(counts.get(folder, 0), total)
                    _log(log_callback, f"[*] Outlook IMAP 已获取验证码（文件夹: {folder}）")
                    return _normalize_code(code)
        return None
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _scan_graph_once(token: str, counts: dict[str, int], email: str = "", log_callback: LogCallback = None) -> Optional[str]:
    before = counts.get(OUTLOOK_GRAPH_INBOX_KEY, counts.get("INBOX", 0))
    total = _graph_inbox_count(token)
    if total <= before:
        return None
    limit = min(max(1, total - max(before, 0)), OUTLOOK_GRAPH_SCAN_DEPTH)
    _log(log_callback, f"[*] Outlook Graph 收件箱更新: {before} -> {total}")
    data = _graph_get(token, "/me/mailFolders/inbox/messages", {
        "$top": str(limit), "$orderby": "receivedDateTime desc",
        "$select": "subject,bodyPreview,body,receivedDateTime,from",
    })
    for message in data.get("value") or []:
        if not isinstance(message, dict):
            continue
        body = message.get("body") if isinstance(message.get("body"), dict) else {}
        sender_data = message.get("from") if isinstance(message.get("from"), dict) else {}
        address = sender_data.get("emailAddress") if isinstance(sender_data.get("emailAddress"), dict) else {}
        code = extract_verification_code(
            str(message.get("subject") or ""), str(message.get("bodyPreview") or ""),
            str(body.get("content") or ""), str(address.get("address") or ""),
        )
        if code:
            counts[OUTLOOK_GRAPH_INBOX_KEY] = max(counts.get(OUTLOOK_GRAPH_INBOX_KEY, 0), total)
            _log(log_callback, "[*] Outlook Graph 已获取验证码")
            return _normalize_code(code)
    return None


def _sleep_interruptibly(seconds: float, cancel_callback=None) -> None:
    deadline = time.time() + max(float(seconds or 0), 0.0)
    while time.time() < deadline:
        if cancel_callback and cancel_callback():
            raise RuntimeError("任务已停止")
        time.sleep(min(0.2, max(0.0, deadline - time.time())))


def wait_for_outlook_code(
    account: OutlookAccount,
    before_counts: Optional[dict[str, int]],
    timeout: int = 180,
    interval: int = 3,
    cancel_callback=None,
    log_callback: LogCallback = None,
) -> Optional[str]:
    mode = normalize_outlook_mode(account.mode)
    deadline = time.time() + max(int(timeout), 1)
    counts = before_counts if before_counts is not None else {}
    if not counts:
        counts["INBOX"] = 0

    imap_token: Optional[str] = None
    graph_token: Optional[str] = None
    imap_terminal = mode == "graph"
    graph_terminal = mode == "imap"
    terminal_errors: list[str] = []
    attempt = 0

    _log(log_callback, f"[*] Outlook 等待验证码: {account.email}（模式: {mode}）")
    while time.time() < deadline:
        if cancel_callback and cancel_callback():
            raise RuntimeError("任务已停止")
        attempt += 1
        if attempt == 1 or attempt % 3 == 0:
            _log(log_callback, f"[*] 仍在等待 Outlook 验证码，剩余约 {max(0, int(deadline-time.time()))}s")

        if mode in {"imap", "auto"} and not imap_terminal:
            if imap_token is None:
                try:
                    imap_token = refresh_outlook_imap_token(account)
                except Exception as exc:
                    if is_terminal_microsoft_token_error(exc):
                        imap_terminal = True
                        terminal_errors.append(str(exc))
                    _log(log_callback, f"[!] Outlook IMAP token 刷新失败: {exc}")
            if imap_token:
                try:
                    code = _scan_imap_once(account, imap_token, counts, log_callback)
                    if code:
                        return code
                except Exception as exc:
                    _log(log_callback, f"[!] Outlook IMAP 读取失败: {exc}")
                    imap_token = None

        if mode in {"graph", "auto"} and not graph_terminal:
            if graph_token is None:
                try:
                    graph_token = refresh_outlook_graph_token(account)
                except Exception as exc:
                    if is_terminal_microsoft_token_error(exc):
                        graph_terminal = True
                        terminal_errors.append(str(exc))
                    _log(log_callback, f"[!] Outlook Graph token 刷新失败: {exc}")
            if graph_token:
                try:
                    code = _scan_graph_once(graph_token, counts, account.email, log_callback)
                    if code:
                        return code
                except Exception as exc:
                    _log(log_callback, f"[!] Outlook Graph 读取失败: {exc}")
                    graph_token = None

        if ((mode == "imap" and imap_terminal) or (mode == "graph" and graph_terminal)
                or (mode == "auto" and imap_terminal and graph_terminal)):
            detail = "；".join(terminal_errors[-2:])
            raise RuntimeError(f"Outlook refresh_token 无效或租户不匹配，无法读取邮箱" + (f"：{detail}" if detail else ""))
        _sleep_interruptibly(interval, cancel_callback)

    _log(log_callback, f"[!] Outlook 在 {timeout}s 内未收到验证码邮件")
    return None


class OutlookMailbox:
    def __init__(self, account: OutlookAccount, log_callback: LogCallback = None) -> None:
        self.account = account
        self.email = account.email
        self._log_callback = log_callback
        self._folder_counts: dict[str, int] = {}

    def prepare(self) -> None:
        _log(self._log_callback, f"[*] 使用 Outlook 邮箱: {self.email}（认证模式: {normalize_outlook_mode(self.account.mode)}）")
        try:
            self._folder_counts = load_folder_counts(self.account)
            if not self._folder_counts:
                raise RuntimeError("未能建立 Outlook 邮件基线")
            inbox = self._folder_counts.get("INBOX", self._folder_counts.get(OUTLOOK_GRAPH_INBOX_KEY, 0))
            _log(self._log_callback, f"[*] Outlook 发送前邮件数: {inbox}")
        except Exception as exc:
            self._folder_counts = {}
            _log(self._log_callback, f"[!] 获取 Outlook 邮件基线失败，本邮箱不会提交注册: {exc}")
            raise

    def wait_for_code(self, timeout: int = 180, interval: int = 3, cancel_callback=None) -> Optional[str]:
        return wait_for_outlook_code(
            self.account, self._folder_counts, timeout=timeout, interval=interval,
            cancel_callback=cancel_callback, log_callback=self._log_callback,
        )
