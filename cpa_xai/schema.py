"""定义并规范化 CPA xAI 凭证文件的数据结构。"""

import base64
import json
import time
from datetime import datetime, timezone


DEFAULT_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
DEFAULT_TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:56121/callback"


def _sanitize_file_segment(value):
    text = str(value or "").strip()
    if not text:
        return ""
    out = []
    for ch in text:
        if ch.isalnum() or ch in ("@", ".", "_", "-"):
            out.append(ch)
        else:
            out.append("-")
    return "".join(out).strip("-")


def credential_file_name(email="", sub=""):
    safe_email = _sanitize_file_segment(email)
    if safe_email:
        return "xai-%s.json" % safe_email
    safe_sub = _sanitize_file_segment(sub)
    if safe_sub:
        return "xai-%s.json" % safe_sub
    return "xai-%d.json" % int(time.time() * 1000)


def jwt_payload(token):
    parts = str(token or "").split(".")
    if len(parts) < 2:
        raise ValueError("not a JWT")
    payload = parts[1] + ("=" * (-len(parts[1]) % 4))
    return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))


def parse_identity(id_token=None, access_token=None):
    """Return the historical identity tuple; expiry consumers choose their token explicitly."""
    for candidate in (id_token, access_token):
        if not candidate:
            continue
        try:
            payload = jwt_payload(candidate)
        except Exception:
            continue
        return (
            str(payload.get("email") or "").strip(),
            str(payload.get("sub") or payload.get("principal_id") or "").strip(),
            int(payload.get("exp") or 0),
            int(payload.get("iat") or 0),
        )
    return "", "", 0, 0


def _jwt_times(token):
    if not token:
        return 0, 0
    try:
        payload = jwt_payload(token)
    except Exception:
        return 0, 0
    return int(payload.get("exp") or 0), int(payload.get("iat") or 0)


def build_cpa_xai_auth(
    email,
    access_token,
    refresh_token,
    id_token=None,
    expires_in=None,
    base_url=DEFAULT_BASE_URL,
    token_endpoint=DEFAULT_TOKEN_ENDPOINT,
    redirect_uri=DEFAULT_REDIRECT_URI,
):
    access = str(access_token or "").strip()
    refresh = str(refresh_token or "").strip()
    if not access:
        raise ValueError("access_token is required")
    if not refresh:
        raise ValueError("refresh_token is required")
    parsed_email, subject, _identity_exp, _identity_iat = parse_identity(
        id_token=id_token,
        access_token=access,
    )
    final_email = str(email or parsed_email or "").strip()
    access_exp, access_iat = _jwt_times(access)
    now_ts = int(time.time())
    provided_expires_in = expires_in is not None
    if expires_in is None:
        if access_exp and access_iat and access_exp >= access_iat:
            expires_in = access_exp - access_iat
        elif access_exp:
            expires_in = max(access_exp - now_ts, 0)
        else:
            expires_in = 21600
    expires_in = max(0, int(expires_in or 0))
    if provided_expires_in:
        expired_ts = now_ts + expires_in
    elif access_exp:
        expired_ts = access_exp
    else:
        expired_ts = now_ts + expires_in
    expired = (
        datetime.fromtimestamp(expired_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if expired_ts
        else ""
    )
    refreshed_at = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "type": "xai",
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "expired": expired,
        "last_refresh": refreshed_at,
        "email": final_email,
        "sub": subject,
        "base_url": str(base_url or DEFAULT_BASE_URL).rstrip("/") or DEFAULT_BASE_URL,
        "redirect_uri": str(redirect_uri or DEFAULT_REDIRECT_URI).strip(),
        "token_endpoint": str(token_endpoint or DEFAULT_TOKEN_ENDPOINT).strip(),
        "auth_kind": "oauth",
    }
    if id_token:
        payload["id_token"] = str(id_token).strip()
    return payload
