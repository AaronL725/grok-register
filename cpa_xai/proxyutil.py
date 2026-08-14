"""Resolve CPA proxies and expose the shared project-level proxy bridge."""

import os
import threading

from proxy_bridge import (
    LocalAuthProxyBridge,
    LocalProxyBridge,
    http_compatible_proxy,
    parse_proxy_url,
    prepare_chromium_proxy,
    prepare_http_compatible_proxy,
    proxy_for_chromium,
    proxy_has_auth,
    safe_proxy_port,
)


_tls = threading.local()


def set_runtime_proxy(proxy):
    value = str(proxy or "").strip()
    _tls.proxy = value or None


def get_runtime_proxy():
    return getattr(_tls, "proxy", None)


def resolve_proxy(explicit=None):
    for candidate in (
        str(explicit or "").strip(),
        str(get_runtime_proxy() or "").strip(),
        str(os.environ.get("https_proxy") or "").strip(),
        str(os.environ.get("HTTPS_PROXY") or "").strip(),
        str(os.environ.get("http_proxy") or "").strip(),
        str(os.environ.get("HTTP_PROXY") or "").strip(),
    ):
        if candidate:
            return candidate
    return ""


# Keep the previous private helper names available for existing callers/tests.
_parse_proxy = parse_proxy_url
_safe_port = safe_proxy_port
_has_proxy_auth = proxy_has_auth


def proxy_log_label(proxy):
    # This project is used in a local/personal context; preserve the complete
    # endpoint so diagnostics match the actual route in use.
    return str(proxy or "").strip() or ""
