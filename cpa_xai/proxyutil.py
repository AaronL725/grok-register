"""Proxy helpers for CPA/OIDC export.

The OAuth HTTP calls can use ``http://user:pass@host:port`` directly, but
Chromium cannot reliably use proxy credentials embedded in --proxy-server.
When an authenticated HTTP/HTTPS proxy is configured we start a tiny localhost
bridge that injects Proxy-Authorization for Chromium, matching the main
registration browser behavior.
"""

import base64
import os
import select
import socket
import socketserver
import ssl
import threading
import urllib.parse


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


def _parse_proxy(proxy):
    raw = str(proxy or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        return urllib.parse.urlsplit(raw)
    except Exception:
        return None


def _safe_port(parsed):
    try:
        return parsed.port
    except Exception:
        return None


def _has_proxy_auth(proxy):
    parsed = _parse_proxy(proxy)
    return bool(parsed and parsed.hostname and (parsed.username is not None or parsed.password is not None))


def _recv_until_headers(sock, timeout=20, limit=65536):
    sock.settimeout(timeout)
    data = b""
    while b"\r\n\r\n" not in data and len(data) < limit:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _relay(left, right, timeout=90):
    left.settimeout(timeout)
    right.settimeout(timeout)
    sockets = [left, right]
    while True:
        readable, _, _ = select.select(sockets, [], [], timeout)
        if not readable:
            return
        for sock in readable:
            data = sock.recv(65536)
            if not data:
                return
            peer = right if sock is left else left
            peer.sendall(data)


class _BridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _BridgeHandler(socketserver.BaseRequestHandler):
    def handle(self):
        bridge = self.server.bridge
        upstream = None
        try:
            initial = _recv_until_headers(self.request, timeout=bridge.timeout)
            if not initial:
                return
            first_line = initial.split(b"\r\n", 1)[0].decode("latin1", "ignore")
            if first_line.upper().startswith("CONNECT "):
                target = first_line.split()[1]
                upstream = bridge.open_upstream()
                req = ["CONNECT %s HTTP/1.1" % target, "Host: %s" % target]
                if bridge.auth_header:
                    req.append("Proxy-Authorization: Basic %s" % bridge.auth_header)
                upstream.sendall(("\r\n".join(req) + "\r\n\r\n").encode("latin1"))
                response = _recv_until_headers(upstream, timeout=bridge.timeout)
                if response:
                    self.request.sendall(response)
                status = response.split(b"\r\n", 1)[0]
                if b" 200 " not in status:
                    return
                _relay(self.request, upstream, timeout=bridge.relay_timeout)
                return
            upstream = bridge.open_upstream()
            upstream.sendall(bridge.inject_proxy_auth(initial))
            _relay(self.request, upstream, timeout=bridge.relay_timeout)
        except Exception:
            return
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except Exception:
                    pass


class LocalAuthProxyBridge(object):
    def __init__(self, proxy_url):
        parsed = _parse_proxy(proxy_url)
        if not parsed or not parsed.hostname:
            raise ValueError("proxy URL is invalid")
        scheme = (parsed.scheme or "http").lower()
        if scheme not in ("http", "https"):
            raise ValueError("authenticated Chromium proxy bridge only supports http/https upstream proxies")
        self.upstream_scheme = scheme
        self.upstream_host = parsed.hostname
        self.upstream_port = _safe_port(parsed) or (443 if scheme == "https" else 80)
        username = urllib.parse.unquote(parsed.username or "")
        password = urllib.parse.unquote(parsed.password or "")
        raw_auth = ("%s:%s" % (username, password)).encode("utf-8")
        self.auth_header = base64.b64encode(raw_auth).decode("ascii") if (username or password) else ""
        self.timeout = 20
        self.relay_timeout = 90
        self.server = None
        self.thread = None
        self.local_proxy = ""

    def open_upstream(self):
        sock = socket.create_connection((self.upstream_host, self.upstream_port), timeout=self.timeout)
        if self.upstream_scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=self.upstream_host)
        sock.settimeout(self.timeout)
        return sock

    def inject_proxy_auth(self, data):
        if not self.auth_header or b"\r\n\r\n" not in data:
            return data
        if b"\r\nproxy-authorization:" in data.lower():
            return data
        head, body = data.split(b"\r\n\r\n", 1)
        auth_line = ("Proxy-Authorization: Basic %s" % self.auth_header).encode("latin1")
        return head + b"\r\n" + auth_line + b"\r\n\r\n" + body

    def start(self):
        self.server = _BridgeServer(("127.0.0.1", 0), _BridgeHandler)
        self.server.bridge = self
        port = self.server.server_address[1]
        self.local_proxy = "http://127.0.0.1:%s" % port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.local_proxy

    def stop(self):
        if self.server is not None:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
        self.server = None
        self.thread = None
        self.local_proxy = ""


def proxy_for_chromium(proxy):
    raw = str(proxy or "").strip()
    if not raw:
        return ""
    if _has_proxy_auth(raw):
        raise ValueError("authenticated proxy requires prepare_chromium_proxy()")
    parsed = _parse_proxy(raw)
    if not parsed or not parsed.hostname:
        return ""
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = "[%s]" % host
    port = _safe_port(parsed) or (443 if (parsed.scheme or "http").lower() == "https" else 80)
    scheme = parsed.scheme or "http"
    return "%s://%s:%s" % (scheme, host, port)


def prepare_chromium_proxy(proxy, log=None):
    logger = log or (lambda message: None)
    raw = str(proxy or "").strip()
    if not raw:
        return "", None
    if _has_proxy_auth(raw):
        bridge = LocalAuthProxyBridge(raw)
        local_proxy = bridge.start()
        logger("started authenticated proxy bridge: %s" % local_proxy)
        return local_proxy, bridge
    return proxy_for_chromium(raw), None


def proxy_log_label(proxy):
    raw = str(proxy or "").strip()
    if not raw:
        return ""
    parsed = _parse_proxy(raw)
    if not parsed:
        return "(proxy)"
    host = parsed.hostname or "?"
    port = _safe_port(parsed)
    auth = "user:***@" if parsed.username else ""
    suffix = ":%s" % port if port else ""
    return "%s://%s%s%s" % (parsed.scheme or "http", auth, host, suffix)
