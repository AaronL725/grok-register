"""Outlook OAuth2 mailbox access through IMAP or Microsoft Graph."""
from __future__ import annotations

import base64
import imaplib
from datetime import datetime, timezone
import random
import re
import threading
import time
from dataclasses import dataclass, field
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Callable, Optional, Union
from urllib.parse import quote

import requests

MICROSOFT_CONSUMERS_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
MICROSOFT_COMMON_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
OUTLOOK_IMAP_HOST = "outlook.office365.com"
OUTLOOK_IMAP_PORT = 993
OUTLOOK_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
OUTLOOK_GRAPH_INBOX_KEY = "GRAPH:INBOX"
OUTLOOK_GRAPH_JUNK_KEY = "GRAPH:JUNKEMAIL"
OUTLOOK_GRAPH_FOLDERS = (
    ("inbox", OUTLOOK_GRAPH_INBOX_KEY),
    ("junkemail", OUTLOOK_GRAPH_JUNK_KEY),
)
OUTLOOK_SCAN_DEPTH = 15
OUTLOOK_GRAPH_SCAN_DEPTH = 15
OUTLOOK_FALLBACK_FOLDERS = (
    "INBOX", "Junk Email", "Junk", "Spam", "Archive", "Deleted Items",
    "垃圾邮件", "垃圾箱", "归档", "已删除邮件", "已删除项目",
)
MICROSOFT_HTTP_MAX_ATTEMPTS = 4
MICROSOFT_HTTP_MAX_BACKOFF = 20.0
MICROSOFT_HTTP_CONCURRENCY = 6

LogCallback = Optional[Callable[[str], None]]
_MICROSOFT_HTTP_SEMAPHORE = threading.BoundedSemaphore(MICROSOFT_HTTP_CONCURRENCY)

_CODE_TOKEN_PATTERN = r"([A-Z0-9]{3}-[A-Z0-9]{3}|[A-Z0-9]{4,8})"
_CONTEXT_CODE_TOKEN_PATTERN = r"([A-Z0-9]{3}-[A-Z0-9]{3}|(?=[A-Z0-9]{0,7}\d)[A-Z0-9]{4,8})"
_VERIFICATION_PATTERNS = (
    rf"(?:confirmation|verification|security|one[-\s]?time)\s*(?:code|pin|passcode)\s*(?:is|:|：)\s*{_CODE_TOKEN_PATTERN}",
    rf"(?:confirmation|verification|security|one[-\s]?time)\s*(?:code|pin|passcode)\s+{_CONTEXT_CODE_TOKEN_PATTERN}",
    rf"(?:your\s+code|code|pin|passcode)\s*(?:is|:|：)\s*{_CODE_TOKEN_PATTERN}",
    rf"(?:验证码|确认码|校验码|一次性密码)\s*(?:是|为|:|：)\s*{_CODE_TOKEN_PATTERN}",
    rf"{_CODE_TOKEN_PATTERN}[\s\S]{{0,80}}?(?:is\s+your\s+(?:confirmation|verification|security)?\s*(?:code|pin|passcode)|(?:confirmation|verification)\s*code|作为您的验证码)",
)



@dataclass(frozen=True)
class OutlookAccount:
    email: str
    password: str
    client_id: str
    refresh_token: str
    mode: str = "auto"


@dataclass(frozen=True)
class ImapFolderRef:
    name: str
    wire_name: str
    attributes: frozenset[str] = frozenset()


@dataclass
class ImapFolderCursor:
    folder: ImapFolderRef
    uidvalidity: str
    last_uid: int


@dataclass
class GraphFolderCursor:
    folder_name: str
    key: str
    newest_received: str = ""
    seen_ids: set[str] = field(default_factory=set)
    baseline_epoch: float = 0.0


@dataclass
class OutlookMailboxState:
    imap: dict[str, ImapFolderCursor] = field(default_factory=dict)
    graph: dict[str, GraphFolderCursor] = field(default_factory=dict)
    graph_seen_ids: set[str] = field(default_factory=set)
    imap_token: Optional[str] = None
    graph_token: Optional[str] = None
    imap_client: Optional[imaplib.IMAP4_SSL] = None
    errors: dict[str, str] = field(default_factory=dict)
    closed: bool = False

    @property
    def has_imap(self) -> bool:
        return bool(self.imap)

    @property
    def has_graph(self) -> bool:
        return bool(self.graph)

    @property
    def usable(self) -> bool:
        return self.has_imap or self.has_graph

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.imap_client is not None:
            try:
                self.imap_client.logout()
            except Exception:
                pass
        self.imap_client = None
        self.imap_token = None
        self.graph_token = None
        self.imap.clear()
        self.graph.clear()
        self.graph_seen_ids.clear()


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
    return str(code or "").replace("-", "").strip().upper()


def _trusted_xai_sender(sender: str) -> bool:
    address = parseaddr(str(sender or ""))[1].strip().lower()
    if not address and "@" in str(sender or ""):
        address = str(sender).strip().lower().strip("<>")
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    return domain in {"x.ai", "grok.com"} or domain.endswith((".x.ai", ".grok.com"))


def extract_verification_code(
    subject: str = "", text: str = "", html: str = "", sender: str = ""
) -> Optional[str]:
    """Extract an OTP only from explicit verification context or trusted xAI/Grok mail."""
    combined = "\n".join(str(value or "") for value in (subject, text, html))
    for pattern in _VERIFICATION_PATTERNS:
        match = re.search(pattern, combined, re.I | re.S)
        if match:
            return match.group(1)

    if _trusted_xai_sender(sender):
        # Trusted senders may use a terse subject/body. Keep the fallback narrow
        # enough that ordinary words and ticket identifiers are not accepted.
        match = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3}|\d{6})\b", combined, re.I)
        if match:
            return match.group(1)
    return None


def _sleep_interruptibly(seconds: float, cancel_callback=None) -> None:
    deadline = time.monotonic() + max(float(seconds or 0), 0.0)
    while time.monotonic() < deadline:
        if cancel_callback and cancel_callback():
            raise RuntimeError("任务已停止")
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))


def _retry_after_seconds(response) -> Optional[float]:
    value = str(getattr(response, "headers", {}).get("Retry-After") or "").strip()
    if not value:
        return None
    try:
        return max(0.0, min(float(value), MICROSOFT_HTTP_MAX_BACKOFF))
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                return None
            delay = when.timestamp() - time.time()
            return max(0.0, min(delay, MICROSOFT_HTTP_MAX_BACKOFF))
        except Exception:
            return None


def _request_with_backoff(method: str, url: str, cancel_callback=None, **kwargs):
    """Microsoft HTTP request with bounded concurrency and transient backoff."""
    last_error = None
    for attempt in range(MICROSOFT_HTTP_MAX_ATTEMPTS):
        if cancel_callback and cancel_callback():
            raise RuntimeError("任务已停止")
        try:
            with _MICROSOFT_HTTP_SEMAPHORE:
                response = requests.request(method, url, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            response = None
        except requests.RequestException:
            raise

        transient = response is None or response.status_code == 429 or response.status_code >= 500
        if not transient or attempt + 1 >= MICROSOFT_HTTP_MAX_ATTEMPTS:
            if response is not None:
                return response
            raise last_error or RuntimeError("Microsoft 请求失败")

        explicit = _retry_after_seconds(response) if response is not None else None
        if explicit is not None:
            delay = explicit
        else:
            delay = min(1.0 * (2 ** attempt) + random.uniform(0.0, 0.75), MICROSOFT_HTTP_MAX_BACKOFF)
        _sleep_interruptibly(delay, cancel_callback)
    raise last_error or RuntimeError("Microsoft 请求失败")


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


def _refresh_access_token(
    account: OutlookAccount,
    scope: str,
    token_urls: tuple[str, ...],
    label: str,
    cancel_callback=None,
) -> str:
    last_error = ""
    for token_url in token_urls:
        try:
            response = _request_with_backoff(
                "POST",
                token_url,
                cancel_callback=cancel_callback,
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
            # Continue through compatibility endpoints even for tenant/terminal
            # errors; a refresh token may be valid on the alternate endpoint.
            continue
        token = str(data.get("access_token") or "").strip()
        if token:
            return token
        last_error = f"{label} token 响应缺少 access_token"
    raise RuntimeError(last_error or f"{label} token 刷新失败")


def refresh_outlook_imap_token(account: OutlookAccount, cancel_callback=None) -> str:
    return _refresh_access_token(
        account,
        "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
        (MICROSOFT_CONSUMERS_TOKEN_URL,),
        "Outlook IMAP",
        cancel_callback=cancel_callback,
    )


def refresh_outlook_graph_token(account: OutlookAccount, cancel_callback=None) -> str:
    return _refresh_access_token(
        account,
        "https://graph.microsoft.com/Mail.Read offline_access",
        (MICROSOFT_COMMON_TOKEN_URL, MICROSOFT_CONSUMERS_TOKEN_URL),
        "Outlook Graph",
        cancel_callback=cancel_callback,
    )


def _xoauth2_auth_string(email: str, access_token: str) -> bytes:
    return f"user={email}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")


def _connect_imap(account: OutlookAccount, access_token: str) -> imaplib.IMAP4_SSL:
    client = imaplib.IMAP4_SSL(OUTLOOK_IMAP_HOST, OUTLOOK_IMAP_PORT, timeout=20)
    client.authenticate("XOAUTH2", lambda _: _xoauth2_auth_string(account.email, access_token))
    return client


def _normalize_folder_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip()).lower()


def _decode_modified_utf7(value: str) -> str:
    def decode_match(match) -> str:
        payload = match.group(1)
        if payload == "":
            return "&"
        raw = payload.replace(",", "/")
        raw += "=" * ((4 - len(raw) % 4) % 4)
        try:
            return base64.b64decode(raw).decode("utf-16-be")
        except Exception:
            return "&" + payload + "-"

    return re.sub(r"&([^-]*)-", decode_match, str(value or ""))


def _encode_modified_utf7(value: str) -> str:
    output: list[str] = []
    non_ascii: list[str] = []

    def flush() -> None:
        if not non_ascii:
            return
        raw = "".join(non_ascii).encode("utf-16-be")
        encoded = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        output.append("&" + encoded + "-")
        non_ascii.clear()

    for char in str(value or ""):
        code = ord(char)
        if 0x20 <= code <= 0x7E:
            flush()
            output.append("&-" if char == "&" else char)
        else:
            non_ascii.append(char)
    flush()
    return "".join(output)


def _unquote_imap_token(value: str) -> str:
    raw = str(value or "").strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1]
        raw = re.sub(r"\\([\\\"])", r"\1", raw)
    return raw


def _parse_imap_list_line(raw_line: Union[bytes, str]) -> ImapFolderRef:
    if isinstance(raw_line, bytes):
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            line = raw_line.decode("ascii", errors="replace")
    else:
        line = str(raw_line)
    match = re.match(r'^\((?P<attrs>[^)]*)\)\s+(?P<delimiter>NIL|"(?:\\.|[^"])*")\s+(?P<name>.+)$', line.strip())
    if match:
        attrs = frozenset(part.lower() for part in match.group("attrs").split() if part)
        wire_name = _unquote_imap_token(match.group("name"))
    else:
        attrs = frozenset()
        quoted = re.findall(r'"([^\"]+)"', line)
        wire_name = quoted[-1].strip() if quoted else (line.split()[-1].strip().strip('"') if line.split() else "")
    return ImapFolderRef(name=_decode_modified_utf7(wire_name), wire_name=wire_name, attributes=attrs)


def _decode_imap_list_name(raw_line: Union[bytes, str]) -> str:
    return _parse_imap_list_line(raw_line).name


def _looks_like_verification_folder(folder: ImapFolderRef) -> bool:
    special = {"\\inbox", "\\junk", "\\spam", "\\archive", "\\trash"}
    if folder.attributes.intersection(special):
        return True
    normalized = _normalize_folder_name(folder.name)
    keywords = (
        "inbox", "junk", "spam", "archive", "deleted", "trash",
        "收件箱", "垃圾", "归档", "已删除",
    )
    return any(keyword in normalized for keyword in keywords)


def _discover_folders(client: imaplib.IMAP4_SSL) -> list[ImapFolderRef]:
    discovered: list[ImapFolderRef] = []
    try:
        status, data = client.list()
        if status == "OK":
            for raw in data or []:
                folder = _parse_imap_list_line(raw)
                if folder.name:
                    discovered.append(folder)
    except Exception:
        pass

    preferred = [folder for folder in discovered if _looks_like_verification_folder(folder)]
    source = preferred or discovered
    ordered: list[ImapFolderRef] = []
    seen: set[str] = set()
    for folder in source:
        key = _normalize_folder_name(folder.name)
        if key and key not in seen:
            seen.add(key)
            ordered.append(folder)
    for name in OUTLOOK_FALLBACK_FOLDERS:
        key = _normalize_folder_name(name)
        if key and key not in seen:
            seen.add(key)
            ordered.append(ImapFolderRef(name=name, wire_name=_encode_modified_utf7(name)))
    return ordered


def _quote_imap_mailbox(folder: Union[str, ImapFolderRef]) -> str:
    raw = folder.wire_name if isinstance(folder, ImapFolderRef) else str(folder or "")
    return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _select_folder_count(client: imaplib.IMAP4_SSL, folder: Union[str, ImapFolderRef]) -> Optional[int]:
    try:
        status, data = client.select(_quote_imap_mailbox(folder), readonly=True)
    except (imaplib.IMAP4.error, UnicodeError):
        return None
    if status != "OK":
        return None
    raw = data[0] if data else b"0"
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return None


def _selected_uidvalidity(client: imaplib.IMAP4_SSL) -> str:
    values = []
    try:
        response = client.response("UIDVALIDITY")
        if response and len(response) > 1:
            values = response[1] or []
    except Exception:
        values = []
    if not values:
        try:
            values = getattr(client, "untagged_responses", {}).get("UIDVALIDITY") or []
        except Exception:
            values = []
    for value in values:
        if isinstance(value, bytes):
            value = value.decode("ascii", errors="ignore")
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _select_folder_uidvalidity(
    client: imaplib.IMAP4_SSL, folder: ImapFolderRef
) -> Optional[str]:
    try:
        status, _data = client.select(_quote_imap_mailbox(folder), readonly=True)
    except (imaplib.IMAP4.error, UnicodeError):
        return None
    if status != "OK":
        return None
    uidvalidity = _selected_uidvalidity(client)
    return uidvalidity or None


def _parse_uid_search(data) -> list[int]:
    values: list[int] = []
    for item in data or []:
        if isinstance(item, bytes):
            item = item.decode("ascii", errors="ignore")
        for token in str(item or "").split():
            try:
                values.append(int(token))
            except ValueError:
                continue
    return values


def _search_all_uids(client: imaplib.IMAP4_SSL) -> list[int]:
    status, data = client.uid("search", None, "ALL")
    if status != "OK":
        raise RuntimeError(f"UID SEARCH ALL 失败: {status}")
    return _parse_uid_search(data)


def _search_uids_after(client: imaplib.IMAP4_SSL, last_uid: int) -> list[int]:
    status, data = client.uid("search", None, "UID", f"{max(1, int(last_uid) + 1)}:*")
    if status != "OK":
        raise RuntimeError(f"UID SEARCH 失败: {status}")
    return [uid for uid in _parse_uid_search(data) if uid > int(last_uid)]


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


def _message_from_fetch_data(data) -> tuple[str, str, str, str]:
    raw_parts = [
        item[1] for item in data or []
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes)
    ]
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


def _fetch_message_content(client: imaplib.IMAP4_SSL, seq: int) -> tuple[str, str, str, str]:
    status, data = client.fetch(str(seq), "(BODY.PEEK[])")
    if status != "OK":
        raise RuntimeError(f"FETCH {seq} 失败: {status}")
    return _message_from_fetch_data(data)


def _fetch_message_content_by_uid(client: imaplib.IMAP4_SSL, uid: int) -> tuple[str, str, str, str]:
    status, data = client.uid("fetch", str(uid), "(BODY.PEEK[])")
    if status != "OK":
        raise RuntimeError(f"UID FETCH {uid} 失败: {status}")
    return _message_from_fetch_data(data)


def _graph_get(
    access_token: str,
    path: str,
    params: Optional[dict[str, str]] = None,
    cancel_callback=None,
) -> dict:
    response = _request_with_backoff(
        "GET",
        f"{OUTLOOK_GRAPH_BASE_URL}{path}",
        cancel_callback=cancel_callback,
        params=params or {},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Prefer": 'outlook.body-content-type="text", IdType="ImmutableId"',
        },
        timeout=30,
    )
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}
    if not response.ok:
        raise RuntimeError(f"Outlook Graph 请求失败 {response.status_code}: {str(data)[:300]}")
    return data


def _graph_messages(
    access_token: str,
    folder_name: str,
    cancel_callback=None,
    include_content: bool = False,
) -> list[dict]:
    select = "id,receivedDateTime"
    if include_content:
        select += ",subject,bodyPreview,body,from"
    data = _graph_get(
        access_token,
        f"/me/mailFolders/{folder_name}/messages",
        {
            "$top": str(OUTLOOK_GRAPH_SCAN_DEPTH),
            "$orderby": "receivedDateTime desc",
            "$select": select,
        },
        cancel_callback=cancel_callback,
    )
    return [item for item in (data.get("value") or []) if isinstance(item, dict)]


def _graph_message_detail(access_token: str, message_id: str, cancel_callback=None) -> dict:
    return _graph_get(
        access_token,
        "/me/messages/" + quote(str(message_id or ""), safe=""),
        {"$select": "id,subject,bodyPreview,body,receivedDateTime,from"},
        cancel_callback=cancel_callback,
    )


def _received_epoch(value: str) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return 0.0


def _graph_cursor_from_messages(folder_name: str, key: str, messages: list[dict]) -> GraphFolderCursor:
    ids = {str(item.get("id") or "").strip() for item in messages if str(item.get("id") or "").strip()}
    received = [str(item.get("receivedDateTime") or "").strip() for item in messages]
    received = [value for value in received if value]
    return GraphFolderCursor(
        folder_name=folder_name,
        key=key,
        newest_received=max(received, key=_received_epoch) if received else "",
        seen_ids=ids,
        # An empty or stale folder still needs a pre-send time frontier so an
        # old message moved into the folder later cannot masquerade as new.
        baseline_epoch=time.time() - 15.0,
    )


def _prepare_imap_state(account: OutlookAccount, state: OutlookMailboxState, cancel_callback=None) -> None:
    token = refresh_outlook_imap_token(account, cancel_callback=cancel_callback)
    client = _connect_imap(account, token)
    cursors: dict[str, ImapFolderCursor] = {}
    try:
        for folder in _discover_folders(client):
            uidvalidity = _select_folder_uidvalidity(client, folder)
            if not uidvalidity:
                continue
            uids = _search_all_uids(client)
            cursors[folder.name] = ImapFolderCursor(
                folder=folder,
                uidvalidity=uidvalidity,
                last_uid=max(uids) if uids else 0,
            )
        if not cursors:
            raise RuntimeError("未发现可建立 UID 基线的 Outlook IMAP 文件夹")
    except Exception:
        try:
            client.logout()
        except Exception:
            pass
        raise
    state.imap_token = token
    state.imap_client = client
    state.imap = cursors


def _prepare_graph_state(account: OutlookAccount, state: OutlookMailboxState, cancel_callback=None) -> None:
    token = refresh_outlook_graph_token(account, cancel_callback=cancel_callback)
    cursors: dict[str, GraphFolderCursor] = {}
    errors: list[str] = []
    for folder_name, key in OUTLOOK_GRAPH_FOLDERS:
        try:
            messages = _graph_messages(token, folder_name, cancel_callback=cancel_callback)
            cursors[key] = _graph_cursor_from_messages(folder_name, key, messages)
        except Exception as exc:
            errors.append(f"{folder_name}: {exc}")
    if not cursors:
        raise RuntimeError("；".join(errors) or "Graph 未能建立任何邮件文件夹基线")
    state.graph_token = token
    state.graph = cursors
    state.graph_seen_ids = set().union(*(cursor.seen_ids for cursor in cursors.values()))
    if errors:
        state.errors["graph_folders"] = "；".join(errors)


def prepare_outlook_state(
    account: OutlookAccount, log_callback: LogCallback = None, cancel_callback=None
) -> OutlookMailboxState:
    """Build independent pre-send baselines for every usable channel."""
    mode = normalize_outlook_mode(account.mode)
    state = OutlookMailboxState()

    if mode in {"imap", "auto"}:
        try:
            _prepare_imap_state(account, state, cancel_callback=cancel_callback)
        except Exception as exc:
            state.errors["imap"] = str(exc)
            if mode == "imap":
                state.close()
                raise

    if mode in {"graph", "auto"}:
        try:
            _prepare_graph_state(account, state, cancel_callback=cancel_callback)
        except Exception as exc:
            state.errors["graph"] = str(exc)
            if mode == "graph":
                state.close()
                raise

    if not state.usable:
        detail = "；".join(f"{key}: {value}" for key, value in state.errors.items())
        state.close()
        raise RuntimeError(detail or "未能建立 Outlook 邮件基线")

    if state.has_imap:
        _log(log_callback, "[*] Outlook IMAP 已建立 UID 基线: %s" % ", ".join(state.imap))
    if state.has_graph:
        labels = [cursor.folder_name for cursor in state.graph.values()]
        _log(log_callback, "[*] Outlook Graph 已建立稳定消息基线: %s" % ", ".join(labels))
    return state


def load_folder_counts(account: OutlookAccount) -> OutlookMailboxState:
    """Backward-compatible name; returns the stronger cursor-based mailbox state."""
    return prepare_outlook_state(account)


def _ensure_imap_client(account: OutlookAccount, state: OutlookMailboxState, cancel_callback=None) -> imaplib.IMAP4_SSL:
    if state.imap_client is not None:
        return state.imap_client
    token = state.imap_token
    if token:
        try:
            state.imap_client = _connect_imap(account, token)
            return state.imap_client
        except Exception:
            pass
    state.imap_token = refresh_outlook_imap_token(account, cancel_callback=cancel_callback)
    state.imap_client = _connect_imap(account, state.imap_token)
    return state.imap_client


def _scan_imap_once(
    account: OutlookAccount,
    state: OutlookMailboxState,
    log_callback: LogCallback = None,
    cancel_callback=None,
) -> Optional[str]:
    client = _ensure_imap_client(account, state, cancel_callback=cancel_callback)
    try:
        for cursor in state.imap.values():
            uidvalidity = _select_folder_uidvalidity(client, cursor.folder)
            if not uidvalidity:
                continue
            if uidvalidity != cursor.uidvalidity:
                # UIDVALIDITY changed, so old UIDs are no longer comparable.
                # Re-baseline instead of risking an old-message false positive.
                uids = _search_all_uids(client)
                cursor.uidvalidity = uidvalidity
                cursor.last_uid = max(uids) if uids else 0
                _log(log_callback, f"[!] Outlook IMAP {cursor.folder.name} UIDVALIDITY 已变化，已安全重建基线")
                continue

            new_uids = _search_uids_after(client, cursor.last_uid)
            if not new_uids:
                continue
            newest_uid = max(new_uids)
            candidates = sorted(new_uids, reverse=True)[:OUTLOOK_SCAN_DEPTH]
            _log(
                log_callback,
                f"[*] Outlook IMAP 新邮件: {cursor.folder.name} UID {cursor.last_uid} -> {newest_uid}",
            )
            for uid in candidates:
                subject, text, html, sender = _fetch_message_content_by_uid(client, uid)
                code = extract_verification_code(subject, text, html, sender)
                if code:
                    cursor.last_uid = newest_uid
                    _log(log_callback, f"[*] Outlook IMAP 已获取验证码（文件夹: {cursor.folder.name}）")
                    return _normalize_code(code)
            cursor.last_uid = newest_uid
        return None
    except Exception:
        if state.imap_client is not None:
            try:
                state.imap_client.logout()
            except Exception:
                pass
        state.imap_client = None
        raise


def _graph_message_is_new(
    message: dict,
    cursor: GraphFolderCursor,
    global_seen_ids: Optional[set[str]] = None,
) -> bool:
    message_id = str(message.get("id") or "").strip()
    received = str(message.get("receivedDateTime") or "").strip()
    if (
        not message_id
        or message_id in cursor.seen_ids
        or (global_seen_ids is not None and message_id in global_seen_ids)
    ):
        return False
    received_epoch = _received_epoch(received)
    if cursor.baseline_epoch and (not received_epoch or received_epoch < cursor.baseline_epoch):
        return False
    newest_epoch = _received_epoch(cursor.newest_received)
    if newest_epoch and (not received_epoch or received_epoch < newest_epoch):
        return False
    return True


def _advance_graph_cursor(
    cursor: GraphFolderCursor,
    messages: list[dict],
    global_seen_ids: Optional[set[str]] = None,
) -> None:
    for message in messages:
        message_id = str(message.get("id") or "").strip()
        if message_id:
            cursor.seen_ids.add(message_id)
            if global_seen_ids is not None:
                global_seen_ids.add(message_id)
        received = str(message.get("receivedDateTime") or "").strip()
        if _received_epoch(received) > _received_epoch(cursor.newest_received):
            cursor.newest_received = received
    if len(cursor.seen_ids) > OUTLOOK_GRAPH_SCAN_DEPTH * 4:
        cursor.seen_ids = {
            str(item.get("id") or "").strip()
            for item in messages
            if str(item.get("id") or "").strip()
        }


def _scan_graph_once(
    token: str,
    state: OutlookMailboxState,
    email: str = "",
    log_callback: LogCallback = None,
    cancel_callback=None,
) -> Optional[str]:
    for cursor in state.graph.values():
        frontier = _graph_messages(token, cursor.folder_name, cancel_callback=cancel_callback)
        new_messages = [
            message
            for message in frontier
            if _graph_message_is_new(message, cursor, state.graph_seen_ids)
        ]
        if new_messages:
            _log(log_callback, f"[*] Outlook Graph {cursor.folder_name} 发现 {len(new_messages)} 封新邮件")

        # Do not advance the cursor until every newly discovered message in
        # this frontier has been successfully fetched and inspected. A transient
        # detail-fetch failure must remain retryable on the next poll.
        for metadata in new_messages:
            if any(key in metadata for key in ("subject", "bodyPreview", "body", "from")):
                message = metadata
            else:
                message = _graph_message_detail(
                    token, str(metadata.get("id") or ""), cancel_callback=cancel_callback
                )
            body = message.get("body") if isinstance(message.get("body"), dict) else {}
            sender_data = message.get("from") if isinstance(message.get("from"), dict) else {}
            address = sender_data.get("emailAddress") if isinstance(sender_data.get("emailAddress"), dict) else {}
            code = extract_verification_code(
                str(message.get("subject") or ""),
                str(message.get("bodyPreview") or ""),
                str(body.get("content") or ""),
                str(address.get("address") or ""),
            )
            if code:
                _advance_graph_cursor(cursor, frontier, state.graph_seen_ids)
                _log(log_callback, f"[*] Outlook Graph 已获取验证码（文件夹: {cursor.folder_name}）")
                return _normalize_code(code)

        _advance_graph_cursor(cursor, frontier, state.graph_seen_ids)
    return None


def wait_for_outlook_code(
    account: OutlookAccount,
    state: OutlookMailboxState,
    timeout: int = 180,
    interval: int = 3,
    cancel_callback=None,
    log_callback: LogCallback = None,
    resend_callback=None,
) -> Optional[str]:
    mode = normalize_outlook_mode(account.mode)
    deadline = time.monotonic() + max(int(timeout), 1)
    imap_terminal = mode == "graph" or not state.has_imap
    graph_terminal = mode == "imap" or not state.has_graph
    terminal_errors: list[str] = []
    attempt = 0
    next_resend_at = time.monotonic() + 35

    _log(log_callback, f"[*] Outlook 等待验证码: {account.email}（模式: {mode}）")
    if mode == "auto" and imap_terminal:
        _log(log_callback, "[*] Outlook auto: IMAP 未建立发送前基线，本次禁用 IMAP 通道")
    if mode == "auto" and graph_terminal:
        _log(log_callback, "[*] Outlook auto: Graph 未建立发送前基线，本次禁用 Graph 通道")

    while time.monotonic() < deadline:
        if cancel_callback and cancel_callback():
            raise RuntimeError("任务已停止")
        attempt += 1
        if attempt == 1 or attempt % 3 == 0:
            _log(log_callback, f"[*] 仍在等待 Outlook 验证码，剩余约 {max(0, int(deadline-time.monotonic()))}s")
        if resend_callback and time.monotonic() >= next_resend_at:
            try:
                resend_callback()
                _log(log_callback, "[*] 已触发重新发送验证码")
            except Exception as exc:
                _log(log_callback, f"[Debug] 触发重发验证码失败: {exc}")
            next_resend_at = time.monotonic() + 35

        if not imap_terminal:
            try:
                code = _scan_imap_once(
                    account, state, log_callback=log_callback, cancel_callback=cancel_callback
                )
                if code:
                    return code
            except Exception as exc:
                _log(log_callback, f"[!] Outlook IMAP 读取失败，稍后重试: {exc}")
                if is_terminal_microsoft_token_error(exc):
                    imap_terminal = True
                    terminal_errors.append(str(exc))
                elif state.imap_client is None:
                    # Keep the pre-send token for one reconnect attempt. If it
                    # can no longer authenticate, _ensure_imap_client refreshes it.
                    pass

        if not graph_terminal:
            if not state.graph_token:
                try:
                    state.graph_token = refresh_outlook_graph_token(account, cancel_callback=cancel_callback)
                except Exception as exc:
                    if is_terminal_microsoft_token_error(exc):
                        graph_terminal = True
                        terminal_errors.append(str(exc))
                    _log(log_callback, f"[!] Outlook Graph token 刷新失败: {exc}")
            if state.graph_token and not graph_terminal:
                try:
                    code = _scan_graph_once(
                        state.graph_token,
                        state,
                        account.email,
                        log_callback=log_callback,
                        cancel_callback=cancel_callback,
                    )
                    if code:
                        return code
                except Exception as exc:
                    _log(log_callback, f"[!] Outlook Graph 读取失败，稍后重试: {exc}")
                    state.graph_token = None

        if ((mode == "imap" and imap_terminal) or (mode == "graph" and graph_terminal)
                or (mode == "auto" and imap_terminal and graph_terminal)):
            detail = "；".join(terminal_errors[-2:])
            raise RuntimeError(
                "Outlook refresh_token 无效或所有预检通道均不可用，无法读取邮箱"
                + (f"：{detail}" if detail else "")
            )

        jitter = random.uniform(0.0, min(0.75, max(float(interval), 0.0) * 0.25))
        _sleep_interruptibly(float(interval) + jitter, cancel_callback)

    _log(log_callback, f"[!] Outlook 在 {timeout}s 内未收到验证码邮件")
    return None


def _redact_probe_error(account: OutlookAccount, error: object) -> str:
    text = str(error or "").strip()
    for secret in (account.password, account.refresh_token):
        if secret:
            text = text.replace(secret, "<redacted>")
    return text[:500]


def probe_outlook_account(account: OutlookAccount) -> dict:
    """Safely preflight one mailbox without sending mail or exposing credentials."""
    state: Optional[OutlookMailboxState] = None
    try:
        state = prepare_outlook_state(account)
        imap_error = state.errors.get("imap", "")
        graph_error = state.errors.get("graph", state.errors.get("graph_folders", ""))
        return {
            "email": account.email,
            "mode": normalize_outlook_mode(account.mode),
            "usable": state.usable,
            "imap": {
                "ok": state.has_imap,
                "folders": list(state.imap.keys()),
                "error": _redact_probe_error(account, imap_error),
            },
            "graph": {
                "ok": state.has_graph,
                "folders": [cursor.folder_name for cursor in state.graph.values()],
                "error": _redact_probe_error(account, graph_error),
            },
        }
    except Exception as exc:
        return {
            "email": account.email,
            "mode": normalize_outlook_mode(account.mode),
            "usable": False,
            "imap": {"ok": False, "folders": [], "error": _redact_probe_error(account, exc)},
            "graph": {"ok": False, "folders": [], "error": _redact_probe_error(account, exc)},
        }
    finally:
        if state is not None:
            state.close()


class OutlookMailbox:
    def __init__(self, account: OutlookAccount, log_callback: LogCallback = None) -> None:
        self.account = account
        self.email = account.email
        self._log_callback = log_callback
        self._state: Optional[OutlookMailboxState] = None

    def prepare(self) -> None:
        _log(
            self._log_callback,
            f"[*] 使用 Outlook 邮箱: {self.email}（认证模式: {normalize_outlook_mode(self.account.mode)}）",
        )
        try:
            self._state = prepare_outlook_state(self.account, log_callback=self._log_callback)
            channels = []
            if self._state.has_imap:
                channels.append("IMAP")
            if self._state.has_graph:
                channels.append("Graph")
            _log(self._log_callback, "[*] Outlook 发送前基线已建立: " + " + ".join(channels))
        except Exception as exc:
            self.close()
            _log(self._log_callback, f"[!] 获取 Outlook 邮件基线失败，本邮箱不会提交注册: {exc}")
            raise

    def wait_for_code(
        self, timeout: int = 180, interval: int = 3, cancel_callback=None, resend_callback=None
    ) -> Optional[str]:
        if self._state is None or self._state.closed:
            raise RuntimeError("Outlook 邮箱尚未完成发送前预检")
        return wait_for_outlook_code(
            self.account,
            self._state,
            timeout=timeout,
            interval=interval,
            cancel_callback=cancel_callback,
            log_callback=self._log_callback,
            resend_callback=resend_callback,
        )

    def close(self) -> None:
        if self._state is not None:
            self._state.close()
            self._state = None
