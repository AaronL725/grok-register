"""Normalize provider email payloads and extract verification codes from MIME content."""

import base64
import binascii
from email import policy
from email.header import decode_header
from email.message import Message
from email.parser import BytesParser
from html.parser import HTMLParser
import re


_MIME_HEADER = re.compile(
    rb"(?im)^(?:from|to|cc|subject|date|mime-version|content-type|content-transfer-encoding):"
)
_RAW_FIELDS = {"raw", "raw_email", "rawemail", "mime", "source"}
_BODY_FIELDS = ("text", "content", "intro", "body", "snippet", "raw")
_BLOCK_TAGS = {
    "address", "article", "blockquote", "br", "div", "footer", "h1", "h2",
    "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "section",
    "table", "tr", "ul",
}
_CODE = r"(?:[A-Z0-9]{3}-[A-Z0-9]{3}|(?=[A-Z0-9]{0,7}\d)[A-Z0-9]{4,8})"
_CODE_BOUNDARY = r"(?<![A-Z0-9])" + _CODE + r"(?![A-Z0-9])"
_SUBJECT_PATTERNS = (
    rf"^\s*({_CODE})\s+xAI\b",
    rf"(?:confirmation|verification)\s+code\s*[:：]\s*({_CODE})",
)
_BODY_PATTERNS = (
    rf"(?:your\s+)?(?:xAI\s+)?(?:confirmation|verification|security|one[-\s]?time)\s+"
    rf"(?:code|pin|passcode)\s*(?:is|:|：)?\s*{_CODE_BOUNDARY}",
    rf"(?:your\s+)?(?:code|pin|passcode)\s*(?:is|:|：)\s*{_CODE_BOUNDARY}",
    rf"(?:验证码|确认码|校验码|一次性密码)\s*(?:是|为|:|：)?\s*{_CODE_BOUNDARY}",
    rf"{_CODE_BOUNDARY}\s+(?:is\s+your\s+(?:xAI\s+)?"
    rf"(?:confirmation|verification|security)\s+(?:code|pin|passcode)|作为您的验证码)",
)


class _VisibleHtmlText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, _attrs):
        if tag in ("script", "style"):
            self.ignored_depth += 1
        elif not self.ignored_depth and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignored_depth = max(0, self.ignored_depth - 1)
        elif not self.ignored_depth and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored_depth and data:
            self.parts.append(data)


def normalize_text(value):
    """Convert plain or HTML text to readable text without non-visible markup."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    raw = str(value or "")
    if not raw:
        return ""
    parser = _VisibleHtmlText()
    parser.feed(raw)
    parser.close()
    text = "".join(parser.parts)
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line))


def decode_mime_header(value):
    """Decode RFC 2047 encoded header fragments without rejecting bad charsets."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    parts = []
    for fragment, charset in decode_header(str(value or "")):
        if not isinstance(fragment, bytes):
            parts.append(str(fragment))
            continue
        try:
            parts.append(fragment.decode(charset or "utf-8"))
        except (LookupError, UnicodeDecodeError):
            try:
                parts.append(fragment.decode("utf-8"))
            except UnicodeDecodeError:
                parts.append(fragment.decode("latin-1", errors="replace"))
    return "".join(parts)


def _decode_part(part: Message):
    payload = part.get_payload(decode=True)
    if payload is None:
        payload = part.get_payload()
        if isinstance(payload, str):
            return payload
        if not isinstance(payload, bytes):
            return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def parse_mime_message(raw):
    """Parse RFC 822/MIME bytes or text into subject, plain body, HTML body, sender."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8", errors="replace")
    if not isinstance(raw, bytes) or not raw:
        return "", "", "", ""

    message = BytesParser(policy=policy.default).parsebytes(raw)
    subject = decode_mime_header(message.get("Subject", ""))
    sender = decode_mime_header(message.get("From", ""))
    plain_parts = []
    html_parts = []
    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment" or part.get_filename():
            continue
        content_type = part.get_content_type().lower()
        if content_type == "text/plain":
            plain_parts.append(normalize_text(_decode_part(part)))
        elif content_type == "text/html":
            html_parts.append(normalize_text(_decode_part(part)))
    plain = "\n".join(part for part in plain_parts if part)
    html = "\n".join(part for part in html_parts if part)
    return subject, plain, html, sender


def _looks_like_mime(raw):
    return bool(_MIME_HEADER.search(raw[:8192]))


def _decode_raw_message(value):
    raw = value if isinstance(value, bytes) else str(value or "").encode("utf-8", errors="replace")
    if _looks_like_mime(raw):
        return parse_mime_message(raw)

    compact = re.sub(rb"\s+", b"", raw)
    if len(compact) >= 16 and re.fullmatch(rb"[A-Za-z0-9+/]*={0,2}", compact):
        try:
            decoded = base64.b64decode(compact + b"=" * (-len(compact) % 4), validate=True)
        except (binascii.Error, ValueError):
            decoded = b""
        if decoded:
            if _looks_like_mime(decoded):
                return parse_mime_message(decoded)
            if b"\x00" not in decoded:
                try:
                    return "", normalize_text(decoded.decode("utf-8")), "", ""
                except UnicodeDecodeError:
                    pass
    return "", normalize_text(raw), "", ""


def _append_value(key, value, subjects, bodies, senders):
    if value is None:
        return
    if key == "subject":
        decoded = decode_mime_header(value).strip()
        if decoded:
            subjects.append(decoded)
        return
    if key in ("from", "sender"):
        decoded = decode_mime_header(value).strip()
        if decoded:
            senders.append(decoded)
        return
    if key in _RAW_FIELDS and isinstance(value, (str, bytes)):
        subject, plain, html, sender = _decode_raw_message(value)
        if subject:
            subjects.append(subject)
        if plain:
            bodies.append(plain)
        if html:
            bodies.append(html)
        if sender:
            senders.append(sender)
        if subject or plain or html or sender:
            return
    if isinstance(value, dict):
        content_type = str(value.get("contentType") or value.get("content_type") or "").lower()
        content = value.get("content")
        if content is not None:
            _append_value("html" if content_type == "html" else "text", content, subjects, bodies, senders)
        else:
            _append_source(value, subjects, bodies, senders)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _append_value(key, item, subjects, bodies, senders)
        return
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str) and value.strip():
        normalized = normalize_text(value)
        if normalized:
            bodies.append(normalized)


def _append_source(source, subjects, bodies, senders):
    if not isinstance(source, dict):
        if isinstance(source, (str, bytes)):
            subject, plain, html, sender = _decode_raw_message(source)
            if subject:
                subjects.append(subject)
            if plain:
                bodies.append(plain)
            if html:
                bodies.append(html)
            if sender:
                senders.append(sender)
        return
    for key in ("subject", "from", "sender"):
        if source.get(key) is not None:
            _append_value(key, source[key], subjects, bodies, senders)
    for key in _BODY_FIELDS:
        if source.get(key) is not None:
            _append_value(key, source[key], subjects, bodies, senders)
    html = source.get("html")
    if html is not None:
        _append_value("html", html, subjects, bodies, senders)


def normalize_mail_sources(*sources):
    """Return normalized subject, body, and sender from legacy or MIME payloads."""
    subjects = []
    bodies = []
    senders = []
    for source in sources:
        _append_source(source, subjects, bodies, senders)
    return (
        next((value for value in subjects if value), ""),
        "\n".join(dict.fromkeys(value for value in bodies if value)),
        next((value for value in senders if value), ""),
    )


def extract_verification_code(text, subject=""):
    """Extract a code only when the subject or visible body explicitly identifies it."""
    decoded_subject = decode_mime_header(subject)
    if isinstance(text, dict) or isinstance(text, bytes):
        parsed_subject, body, _html, _sender = normalize_mail_sources(text)
        decoded_subject = decoded_subject or parsed_subject
        normalized_body = body
    else:
        raw = str(text or "")
        parsed_subject, body, _html, _sender = _decode_raw_message(raw)
        if parsed_subject:
            decoded_subject = decoded_subject or parsed_subject
        normalized_body = body or normalize_text(raw)

    for pattern in _SUBJECT_PATTERNS:
        match = re.search(pattern, decoded_subject, re.IGNORECASE)
        if match:
            return match.group(1).upper()

    for pattern in _BODY_PATTERNS:
        match = re.search(pattern, normalized_body, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None
