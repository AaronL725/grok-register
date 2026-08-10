"""解析认证代理并为 Chromium/CPA 浏览器提供本地代理桥。"""

import base64
import ipaddress
import os
import select
import socket
import socketserver
import ssl
import struct
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


def _recv_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise OSError("unexpected EOF from proxy")
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


def _split_host_port(value, default_port):
    text = str(value or "").strip()
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise ValueError("invalid IPv6 target")
        host = text[1:end]
        suffix = text[end + 1:]
        port = int(suffix[1:]) if suffix.startswith(":") else default_port
        return host, port
    if text.count(":") == 1:
        host, port = text.rsplit(":", 1)
        return host, int(port)
    if ":" in text:
        return text, default_port
    return text, default_port


def _rewrite_http_request(initial):
    head, body = initial.split(b"\r\n\r\n", 1)
    lines = head.split(b"\r\n")
    first = lines[0].decode("latin1", "ignore")
    parts = first.split(" ", 2)
    if len(parts) != 3:
        raise ValueError("invalid HTTP request line")
    method, target, version = parts
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme in ("http", "https") and parsed.hostname:
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        lines[0] = ("%s %s %s" % (method, path, version)).encode("latin1")
    else:
        host_header = ""
        for line in lines[1:]:
            if line.lower().startswith(b"host:"):
                host_header = line.split(b":", 1)[1].strip().decode("latin1", "ignore")
                break
        host, port = _split_host_port(host_header, 80)
    filtered = [line for line in lines if not line.lower().startswith(b"proxy-authorization:")]
    return host, port, b"\r\n".join(filtered) + b"\r\n\r\n" + body


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
                if bridge.is_http_upstream:
                    upstream = bridge.open_proxy_socket()
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
                else:
                    host, port = _split_host_port(target, 443)
                    upstream = bridge.open_socks_target(host, port)
                    self.request.sendall(b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: local-bridge\r\n\r\n")
                _relay(self.request, upstream, timeout=bridge.relay_timeout)
                return

            if bridge.is_http_upstream:
                upstream = bridge.open_proxy_socket()
                upstream.sendall(bridge.inject_proxy_auth(initial))
            else:
                host, port, request_data = _rewrite_http_request(initial)
                upstream = bridge.open_socks_target(host, port)
                upstream.sendall(request_data)
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
        if scheme not in ("http", "https", "socks4", "socks4a", "socks5", "socks5h"):
            raise ValueError("authenticated proxy bridge supports HTTP, HTTPS, SOCKS4 and SOCKS5")
        self.upstream_scheme = scheme
        self.upstream_host = parsed.hostname
        self.upstream_port = _safe_port(parsed) or (443 if scheme == "https" else 1080 if scheme.startswith("socks") else 80)
        self.username = urllib.parse.unquote(parsed.username or "")
        self.password = urllib.parse.unquote(parsed.password or "")
        raw_auth = ("%s:%s" % (self.username, self.password)).encode("utf-8")
        self.auth_header = base64.b64encode(raw_auth).decode("ascii") if (self.username or self.password) else ""
        self.timeout = 20
        self.relay_timeout = 90
        self.server = None
        self.thread = None
        self.local_proxy = ""

    @property
    def is_http_upstream(self):
        return self.upstream_scheme in ("http", "https")

    def open_proxy_socket(self):
        sock = socket.create_connection((self.upstream_host, self.upstream_port), timeout=self.timeout)
        if self.upstream_scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=self.upstream_host)
        sock.settimeout(self.timeout)
        return sock

    def _socks5_connect(self, sock, host, port):
        methods = [0x00]
        if self.username or self.password:
            methods.insert(0, 0x02)
        sock.sendall(bytes([0x05, len(methods)] + methods))
        version, method = _recv_exact(sock, 2)
        if version != 0x05 or method == 0xFF:
            raise OSError("SOCKS5 authentication method rejected")
        if method == 0x02:
            username = self.username.encode("utf-8")
            password = self.password.encode("utf-8")
            if len(username) > 255 or len(password) > 255:
                raise OSError("SOCKS5 credentials too long")
            sock.sendall(bytes([0x01, len(username)]) + username + bytes([len(password)]) + password)
            auth_version, status = _recv_exact(sock, 2)
            if auth_version != 0x01 or status != 0x00:
                raise OSError("SOCKS5 authentication failed")
        try:
            address = ipaddress.ip_address(host)
            if address.version == 4:
                encoded = bytes([0x01]) + address.packed
            else:
                encoded = bytes([0x04]) + address.packed
        except ValueError:
            raw_host = host.encode("idna")
            if len(raw_host) > 255:
                raise OSError("SOCKS5 hostname too long")
            encoded = bytes([0x03, len(raw_host)]) + raw_host
        sock.sendall(bytes([0x05, 0x01, 0x00]) + encoded + struct.pack("!H", int(port)))
        head = _recv_exact(sock, 4)
        if head[0] != 0x05 or head[1] != 0x00:
            raise OSError("SOCKS5 connect failed with status %s" % head[1])
        atyp = head[3]
        if atyp == 0x01:
            _recv_exact(sock, 4)
        elif atyp == 0x04:
            _recv_exact(sock, 16)
        elif atyp == 0x03:
            size = _recv_exact(sock, 1)[0]
            _recv_exact(sock, size)
        else:
            raise OSError("SOCKS5 returned invalid address type")
        _recv_exact(sock, 2)

    def _socks4_connect(self, sock, host, port):
        user = self.username.encode("utf-8")
        remote_dns = self.upstream_scheme == "socks4a"
        try:
            address = socket.inet_aton(host)
        except OSError:
            if remote_dns:
                address = b"\x00\x00\x00\x01"
            else:
                address = socket.inet_aton(socket.gethostbyname(host))
        payload = b"\x04\x01" + struct.pack("!H", int(port)) + address + user + b"\x00"
        if remote_dns and address == b"\x00\x00\x00\x01":
            payload += host.encode("idna") + b"\x00"
        sock.sendall(payload)
        response = _recv_exact(sock, 8)
        if response[1] != 0x5A:
            raise OSError("SOCKS4 connect failed with status %s" % response[1])

    def open_socks_target(self, host, port):
        sock = socket.create_connection((self.upstream_host, self.upstream_port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        try:
            if self.upstream_scheme in ("socks5", "socks5h"):
                self._socks5_connect(sock, host, port)
            else:
                self._socks4_connect(sock, host, port)
            return sock
        except Exception:
            sock.close()
            raise

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
    port = _safe_port(parsed) or (443 if (parsed.scheme or "http").lower() == "https" else 1080 if (parsed.scheme or "").lower().startswith("socks") else 80)
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
