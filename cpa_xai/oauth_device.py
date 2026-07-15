"""实现 OAuth Device Authorization 的发现、启动和 token 轮询。"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .proxyutil import resolve_proxy


CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
ISSUER = "https://auth.x.ai"
DISCOVERY_URL = ISSUER + "/.well-known/openid-configuration"
SCOPE = "openid profile email offline_access grok-cli:access api:access"


class OAuthDeviceError(RuntimeError):
    pass


class DeviceCodeSession(object):
    def __init__(
        self,
        device_code,
        user_code,
        verification_uri,
        verification_uri_complete,
        expires_in,
        interval,
        token_endpoint,
        raw,
    ):
        self.device_code = device_code
        self.user_code = user_code
        self.verification_uri = verification_uri
        self.verification_uri_complete = verification_uri_complete
        self.expires_in = int(expires_in or 1800)
        self.interval = int(interval or 5)
        self.token_endpoint = token_endpoint
        self.raw = raw


class TokenResult(object):
    def __init__(self, access_token, refresh_token, id_token, token_type, expires_in, raw):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.id_token = id_token
        self.token_type = token_type or "Bearer"
        self.expires_in = int(expires_in or 0)
        self.raw = raw


def _build_opener(proxy=None):
    handlers = []
    resolved = resolve_proxy(proxy)
    if resolved:
        handlers.append(urllib.request.ProxyHandler({"http": resolved, "https": resolved}))
    return urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()


def _validate_endpoint(raw_url, field_name):
    value = str(raw_url or "").strip()
    if not value:
        raise OAuthDeviceError("xAI discovery %s is empty" % field_name)
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https":
        raise OAuthDeviceError("xAI discovery %s must use https: %s" % (field_name, value))
    host = (parsed.hostname or "").lower().strip()
    if host != "x.ai" and not host.endswith(".x.ai"):
        raise OAuthDeviceError("xAI discovery %s host is invalid: %s" % (field_name, host))
    return value


def discover(proxy=None, timeout=30.0, cancel=None, retries=2):
    request = urllib.request.Request(
        DISCOVERY_URL,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "grok-register-cpa/1.0"},
    )
    last_error = None
    for attempt in range(max(int(retries), 0) + 1):
        _check_cancel(cancel)
        opener = _build_opener(proxy)
        try:
            with opener.open(request, timeout=float(timeout)) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = int(getattr(response, "status", 200) or 200)
            _check_cancel(cancel)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise OAuthDeviceError("xAI discovery failed HTTP %s: %s" % (exc.code, body))
        except Exception as exc:
            last_error = exc
            if not _is_transient_net_error(exc) or attempt >= int(retries):
                raise OAuthDeviceError("xAI discovery request failed: %s" % exc)
            _sleep_with_cancel(1.0 * (attempt + 1), cancel)
            continue
        if status != 200:
            raise OAuthDeviceError("xAI discovery failed HTTP %s: %s" % (status, body))
        try:
            payload = json.loads(body)
        except Exception as exc:
            raise OAuthDeviceError("xAI discovery parse failed: %s" % exc)
        return {
            "device_authorization_endpoint": _validate_endpoint(
                payload.get("device_authorization_endpoint"), "device_authorization_endpoint"
            ),
            "token_endpoint": _validate_endpoint(payload.get("token_endpoint"), "token_endpoint"),
        }
    raise OAuthDeviceError("xAI discovery failed: %s" % last_error)


def _check_cancel(cancel):
    if cancel and cancel():
        raise OAuthDeviceError("cancelled")


def _sleep_with_cancel(seconds, cancel=None):
    deadline = time.time() + max(float(seconds), 0.0)
    while time.time() < deadline:
        _check_cancel(cancel)
        time.sleep(min(0.2, max(deadline - time.time(), 0.0)))
    _check_cancel(cancel)


def _is_transient_net_error(exc):
    if isinstance(exc, (TimeoutError, BrokenPipeError, ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, BaseException) and _is_transient_net_error(reason):
            return True
        text = str(exc).lower()
        return any(
            needle in text
            for needle in (
                "broken pipe",
                "connection reset",
                "connection aborted",
                "timed out",
                "timeout",
                "temporarily unavailable",
                "network is unreachable",
                "name or service not known",
                "unexpected_eof",
                "eof occurred",
                "ssl",
                "handshake",
                "remote end closed",
                "bad gateway",
                "connection refused",
            )
        )
    try:
        import ssl as _ssl

        if isinstance(exc, _ssl.SSLError):
            return True
    except Exception:
        pass
    if isinstance(exc, OSError):
        if getattr(exc, "errno", None) in (32, 104, 110, 111, 113, 101):
            return True
        text = str(exc).lower()
        return any(needle in text for needle in ("broken pipe", "timed out", "connection reset", "ssl"))
    return False


def _post_form(url, form, timeout=30.0, proxy=None, retries=0, retry_sleep=1.5, cancel=None):
    data = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "grok-register-cpa/1.0",
        },
    )
    last_error = None
    for attempt in range(max(int(retries), 0) + 1):
        _check_cancel(cancel)
        opener = _build_opener(proxy)
        try:
            with opener.open(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = int(getattr(response, "status", 200) or 200)
            _check_cancel(cancel)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            status = int(exc.code)
        except Exception as exc:
            last_error = exc
            if not _is_transient_net_error(exc) or attempt >= int(retries):
                raise
            _sleep_with_cancel(float(retry_sleep) * (attempt + 1), cancel)
            continue
        try:
            return status, json.loads(body)
        except Exception:
            return status, body
    if last_error is not None:
        raise last_error
    raise OAuthDeviceError("form request failed without response")


def request_device_code(client_id=CLIENT_ID, scope=SCOPE, timeout=15.0, proxy=None, cancel=None, retries=2):
    discovery = discover(proxy=proxy, timeout=timeout, cancel=cancel, retries=retries)
    _check_cancel(cancel)
    device_endpoint = discovery["device_authorization_endpoint"]
    token_endpoint = discovery["token_endpoint"]
    status, payload = _post_form(
        device_endpoint,
        {"client_id": client_id, "scope": scope},
        timeout=timeout,
        proxy=proxy,
        retries=retries,
        retry_sleep=1.0,
        cancel=cancel,
    )
    _check_cancel(cancel)
    if status != 200 or not isinstance(payload, dict):
        raise OAuthDeviceError("device code request failed HTTP %s: %r" % (status, payload))
    device_code = str(payload.get("device_code") or "").strip()
    user_code = str(payload.get("user_code") or "").strip()
    if not device_code or not user_code:
        raise OAuthDeviceError("device code response missing fields: %r" % payload)
    verification_uri = str(payload.get("verification_uri") or "https://accounts.x.ai/oauth2/device").strip()
    verification_uri_complete = str(
        payload.get("verification_uri_complete") or ("%s?user_code=%s" % (verification_uri, user_code))
    ).strip()
    return DeviceCodeSession(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=verification_uri_complete,
        expires_in=int(payload.get("expires_in") or 1800),
        interval=max(int(payload.get("interval") or 5), 1),
        token_endpoint=token_endpoint,
        raw=payload,
    )


def poll_device_token(
    device_code,
    token_endpoint,
    client_id=CLIENT_ID,
    interval=5,
    expires_in=1800,
    timeout=30.0,
    log=None,
    cancel=None,
    proxy=None,
):
    logger = log or (lambda message: None)
    deadline = time.time() + max(int(expires_in) - 5, 30)
    sleep_seconds = max(int(interval), 1)
    net_streak = 0
    max_net_streak = 20
    while time.time() < deadline:
        if cancel and cancel():
            raise OAuthDeviceError("cancelled")
        try:
            status, payload = _post_form(
                token_endpoint,
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": str(device_code).strip(),
                    "client_id": client_id,
                },
                timeout=float(timeout),
                proxy=proxy,
                retries=0,
                retry_sleep=1.0,
                cancel=cancel,
            )
            net_streak = 0
        except Exception as exc:
            if not _is_transient_net_error(exc):
                raise
            net_streak += 1
            wait_seconds = min(sleep_seconds + min(net_streak, 5), 20)
            logger("oauth poll network blip (%s/%s): %s — retry in %ss" % (net_streak, max_net_streak, exc, wait_seconds))
            if net_streak >= max_net_streak:
                raise OAuthDeviceError("device auth aborted after %s network errors: %s" % (net_streak, exc))
            _sleep_with_cancel(wait_seconds, cancel)
            continue
        if status == 200 and isinstance(payload, dict) and payload.get("access_token"):
            access_token = str(payload.get("access_token") or "").strip()
            refresh_token = str(payload.get("refresh_token") or "").strip()
            if not refresh_token:
                raise OAuthDeviceError("token response missing refresh_token")
            return TokenResult(
                access_token=access_token,
                refresh_token=refresh_token,
                id_token=(str(payload.get("id_token") or "").strip() or None),
                token_type=str(payload.get("token_type") or "Bearer"),
                expires_in=int(payload.get("expires_in") or 21600),
                raw=payload,
            )
        error_code = ""
        error_description = ""
        if isinstance(payload, dict):
            error_code = str(payload.get("error") or "")
            error_description = str(payload.get("error_description") or "")
        if error_code in ("authorization_pending", "slow_down"):
            if error_code == "slow_down":
                sleep_seconds = min(sleep_seconds + 5, 30)
            logger("oauth poll: %s (sleep %ss)" % (error_code, sleep_seconds))
            _sleep_with_cancel(sleep_seconds, cancel)
            continue
        if error_code in ("expired_token", "access_denied"):
            raise OAuthDeviceError("device auth failed: %s: %s" % (error_code, error_description))
        if status == 400 and error_code:
            raise OAuthDeviceError("device auth token error: %s: %s" % (error_code, error_description or payload))
        if status >= 500 or not isinstance(payload, dict):
            net_streak += 1
            wait_seconds = min(sleep_seconds + 2, 20)
            logger("oauth poll soft HTTP %s: %r — retry in %ss" % (status, payload, wait_seconds))
            if net_streak >= max_net_streak:
                raise OAuthDeviceError("device auth aborted after repeated soft HTTP failures status=%s" % status)
            _sleep_with_cancel(wait_seconds, cancel)
            continue
        logger("oauth poll unexpected HTTP %s: %r" % (status, payload))
        _sleep_with_cancel(sleep_seconds, cancel)
    raise OAuthDeviceError("device auth timed out waiting for user approval")
