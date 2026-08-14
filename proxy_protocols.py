"""Parse native and advanced proxy subscription nodes into stable descriptors."""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional

NATIVE_SCHEMES = {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}
ADVANCED_SCHEMES = {"vless", "vmess", "trojan", "hysteria2", "hy2", "tuic", "ss"}
SUPPORTED_SCHEMES = NATIVE_SCHEMES | ADVANCED_SCHEMES | {"socks"}
MAX_SOURCE_BYTES = 2 << 20
MAX_SOURCE_ENTRIES = 10000
_PROXY_ACCOUNT_PLACEHOLDER = "{account}"


class ProxyProtocolError(ValueError):
    pass


@dataclass
class ProxyDescriptor:
    protocol: str
    raw_uri: str
    canonical_uri: str
    name: str
    server: str
    port: int
    backend: str
    outbound_config: Optional[dict] = None
    node_id: str = ""

    def __post_init__(self):
        if not self.node_id:
            stable = self.canonical_uri if self.backend == "native" else json.dumps(
                self.outbound_config or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            self.node_id = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]


@dataclass
class SubscriptionParseResult:
    nodes: List[ProxyDescriptor] = field(default_factory=list)
    total_lines: int = 0
    decoded_base64: bool = False
    protocol_counts: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    @property
    def skipped(self):
        return len(self.errors)

    def as_dict(self):
        return {
            "total_lines": int(self.total_lines),
            "decoded_base64": bool(self.decoded_base64),
            "supported": len(self.nodes),
            "skipped": self.skipped,
            "protocol_counts": dict(sorted(self.protocol_counts.items())),
            "errors": list(self.errors[:50]),
        }


def _unquote(value):
    return urllib.parse.unquote(str(value or ""))


def _first(query, *keys, default=""):
    for key in keys:
        values = query.get(key)
        if values:
            return str(values[0])
    return default


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(value):
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _decode_b64_bytes(value):
    compact = "".join(str(value or "").strip().split())
    if not compact:
        raise ProxyProtocolError("empty Base64 payload")
    padding = "=" * ((4 - len(compact) % 4) % 4)
    last = None
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return decoder((compact + padding).encode("ascii"))
        except Exception as exc:
            last = exc
    raise ProxyProtocolError("invalid Base64 payload: %s" % last)


def _decode_b64_text(value):
    try:
        return _decode_b64_bytes(value).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProxyProtocolError("invalid UTF-8 Base64 payload") from exc


def _normalize_native(raw):
    value = str(raw or "").strip()
    if not value:
        raise ProxyProtocolError("empty proxy URL")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
        raise ProxyProtocolError("proxy URL contains control characters")
    if "://" not in value:
        if ":" not in value:
            raise ProxyProtocolError("proxy URL is missing scheme or port")
        value = "http://" + value
    if value.count(_PROXY_ACCOUNT_PLACEHOLDER) > 1:
        raise ProxyProtocolError("proxy URL may contain at most one {account} placeholder")
    sentinel = "grok_register_account_placeholder"
    if sentinel in value:
        raise ProxyProtocolError("proxy URL contains reserved placeholder text")
    parse_value = value.replace(_PROXY_ACCOUNT_PLACEHOLDER, sentinel)
    parsed = urllib.parse.urlsplit(parse_value)
    scheme = (parsed.scheme or "http").lower()
    if scheme == "socks":
        scheme = "socks5"
    if scheme not in NATIVE_SCHEMES or not parsed.hostname:
        raise ProxyProtocolError("unsupported native proxy protocol")
    # Fragment is metadata commonly used as the subscription display name.
    # It never enters the canonical routing URL or node identity.
    if parsed.path not in ("", "/") or parsed.query:
        raise ProxyProtocolError("native proxy URL cannot contain path or query")
    try:
        port = parsed.port
    except Exception as exc:
        raise ProxyProtocolError("invalid proxy port: %s" % exc) from exc
    if not port:
        raise ProxyProtocolError("native proxy URL is missing port")
    has_placeholder = _PROXY_ACCOUNT_PLACEHOLDER in value
    if has_placeholder:
        if parsed.username is None or sentinel not in parsed.username:
            raise ProxyProtocolError("{account} may only appear in the proxy username")
        if sentinel in (parsed.password or "") or sentinel in (parsed.hostname or ""):
            raise ProxyProtocolError("{account} may only appear in the proxy username")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = "[%s]" % host
    userinfo = ""
    if parsed.username is not None:
        username = _unquote(parsed.username).replace(sentinel, _PROXY_ACCOUNT_PLACEHOLDER)
        userinfo = urllib.parse.quote(username, safe="{}-._~")
        if parsed.password is not None:
            userinfo += ":" + urllib.parse.quote(_unquote(parsed.password), safe="-._~")
        userinfo += "@"
    return "%s://%s%s:%s" % (scheme, userinfo, host, port)


def _transport_from_values(kind, host="", path="", service_name=""):
    value = str(kind or "").strip().lower()
    if value in ("", "tcp", "raw", "none"):
        return None
    if value in ("ws", "websocket"):
        result = {"type": "ws"}
        if path:
            result["path"] = path
        if host:
            result["headers"] = {"Host": host}
        return result
    if value == "grpc":
        result = {"type": "grpc"}
        if service_name:
            result["service_name"] = service_name
        return result
    if value in ("http", "h2"):
        result = {"type": "http"}
        if host:
            result["host"] = [host]
        if path:
            result["path"] = path
        return result
    if value in ("httpupgrade", "http-upgrade"):
        result = {"type": "httpupgrade"}
        if host:
            result["host"] = host
        if path:
            result["path"] = path
        return result
    if value == "quic":
        return {"type": "quic"}
    raise ProxyProtocolError("unsupported transport: %s" % value)


def _tls_from_query(query, default_enabled=False):
    security = _first(query, "security", "tls", default="")
    enabled = default_enabled or security.lower() in ("tls", "reality", "1", "true")
    if security.lower() in ("none", "0", "false"):
        enabled = False
    if not enabled:
        return None
    tls = {"enabled": True}
    server_name = _first(query, "sni", "serverName", "servername", "peer")
    if server_name:
        tls["server_name"] = server_name
    alpn = _split_csv(_first(query, "alpn"))
    if alpn:
        tls["alpn"] = alpn
    if _truthy(_first(query, "insecure", "allowInsecure", "allow_insecure", "skip-cert-verify", "skip_cert_verify")):
        tls["insecure"] = True
    fingerprint = _first(query, "fp", "fingerprint")
    if fingerprint:
        tls["utls"] = {"enabled": True, "fingerprint": fingerprint}
    if security.lower() == "reality" or _first(query, "pbk", "publicKey"):
        public_key = _first(query, "pbk", "publicKey")
        if not public_key:
            raise ProxyProtocolError("Reality node is missing public key")
        reality = {"enabled": True, "public_key": public_key}
        short_id = _first(query, "sid", "shortId")
        if short_id:
            reality["short_id"] = short_id
        tls["reality"] = reality
    return tls


def _uri_parts(raw):
    parsed = urllib.parse.urlsplit(raw)
    if not parsed.hostname:
        raise ProxyProtocolError("node is missing server host")
    try:
        port = parsed.port
    except Exception as exc:
        raise ProxyProtocolError("invalid node port: %s" % exc) from exc
    if not port:
        raise ProxyProtocolError("node is missing server port")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    name = _unquote(parsed.fragment)
    return parsed, query, name, parsed.hostname, int(port)


def _advanced_descriptor(protocol, raw, name, server, port, outbound):
    outbound = dict(outbound)
    outbound.setdefault("type", protocol)
    outbound["server"] = server
    outbound["server_port"] = int(port)
    stable = json.dumps(outbound, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    canonical = "%s://%s" % (protocol, hashlib.sha256(stable.encode("utf-8")).hexdigest())
    return ProxyDescriptor(protocol, raw, canonical, name, server, int(port), "sing-box", outbound)


def _parse_vless(raw):
    parsed, query, name, server, port = _uri_parts(raw)
    uuid = _unquote(parsed.username)
    if not uuid:
        raise ProxyProtocolError("VLESS node is missing UUID")
    outbound = {"uuid": uuid}
    flow = _first(query, "flow")
    if flow:
        outbound["flow"] = flow
    packet_encoding = _first(query, "packetEncoding", "packet_encoding")
    if packet_encoding:
        outbound["packet_encoding"] = packet_encoding
    header_type = _first(query, "headerType", "header_type")
    if header_type and header_type.lower() not in ("none",):
        raise ProxyProtocolError("unsupported VLESS headerType: %s" % header_type)
    transport = _transport_from_values(_first(query, "type", "network"), _first(query, "host"), _first(query, "path"), _first(query, "serviceName", "service_name"))
    if transport:
        outbound["transport"] = transport
    tls = _tls_from_query(query)
    if tls:
        outbound["tls"] = tls
    return _advanced_descriptor("vless", raw, name, server, port, outbound)


def _parse_trojan(raw):
    parsed, query, name, server, port = _uri_parts(raw)
    password = _unquote(parsed.username)
    if parsed.password is not None:
        password += ":" + _unquote(parsed.password)
    if not password:
        raise ProxyProtocolError("Trojan node is missing password")
    outbound = {"password": password}
    transport = _transport_from_values(_first(query, "type", "network"), _first(query, "host"), _first(query, "path"), _first(query, "serviceName", "service_name"))
    if transport:
        outbound["transport"] = transport
    tls = _tls_from_query(query, default_enabled=True)
    if tls:
        outbound["tls"] = tls
    return _advanced_descriptor("trojan", raw, name, server, port, outbound)


def _parse_hysteria2(raw):
    parsed, query, name, server, port = _uri_parts(raw)
    password = _unquote(parsed.username)
    if parsed.password is not None:
        password += ":" + _unquote(parsed.password)
    outbound = {"password": password}
    outbound["tls"] = _tls_from_query(query, default_enabled=True) or {"enabled": True}
    obfs_type = _first(query, "obfs").strip()
    if obfs_type and obfs_type.lower() != "none":
        obfs = {"type": obfs_type}
        obfs_password = _first(query, "obfs-password", "obfs_password")
        if obfs_password:
            obfs["password"] = obfs_password
        outbound["obfs"] = obfs
    up = _first(query, "upmbps", "up_mbps")
    down = _first(query, "downmbps", "down_mbps")
    if up.isdigit():
        outbound["up_mbps"] = int(up)
    if down.isdigit():
        outbound["down_mbps"] = int(down)
    return _advanced_descriptor("hysteria2", raw, name, server, port, outbound)


def _parse_tuic(raw):
    parsed, query, name, server, port = _uri_parts(raw)
    uuid = _unquote(parsed.username)
    password = _unquote(parsed.password)
    if not uuid or not password:
        raise ProxyProtocolError("TUIC node requires UUID and password")
    outbound = {"uuid": uuid, "password": password, "tls": _tls_from_query(query, default_enabled=True) or {"enabled": True}}
    congestion = _first(query, "congestion_control", "congestion-control")
    if congestion:
        outbound["congestion_control"] = congestion
    relay = _first(query, "udp_relay_mode", "udp-relay-mode")
    if relay:
        outbound["udp_relay_mode"] = relay
    if _truthy(_first(query, "zero_rtt_handshake", "zero-rtt-handshake", "zero_rtt")):
        outbound["zero_rtt_handshake"] = True
    heartbeat = _first(query, "heartbeat")
    if heartbeat:
        outbound["heartbeat"] = heartbeat
    return _advanced_descriptor("tuic", raw, name, server, port, outbound)


def _parse_vmess(raw):
    payload = raw.split("://", 1)[1].split("#", 1)[0]
    try:
        value = json.loads(_decode_b64_text(payload))
    except Exception as exc:
        raise ProxyProtocolError("invalid VMess Base64 JSON: %s" % exc) from exc
    if not isinstance(value, dict):
        raise ProxyProtocolError("VMess payload must be a JSON object")
    server = str(value.get("add") or "").strip()
    try:
        port = int(value.get("port") or 0)
    except Exception as exc:
        raise ProxyProtocolError("invalid VMess port") from exc
    uuid = str(value.get("id") or "").strip()
    if not server or not port or not uuid:
        raise ProxyProtocolError("VMess node is missing server, port or UUID")
    name = str(value.get("ps") or "")
    header_type = str(value.get("type") or "").strip()
    if header_type and header_type.lower() not in ("none",):
        raise ProxyProtocolError("unsupported VMess header type: %s" % header_type)
    security = str(value.get("scy") or value.get("security") or "auto")
    outbound = {"uuid": uuid, "security": security}
    try:
        alter_id = int(value.get("aid") or 0)
    except Exception:
        alter_id = 0
    if alter_id:
        outbound["alter_id"] = alter_id
    transport = _transport_from_values(value.get("net"), str(value.get("host") or ""), str(value.get("path") or ""), str(value.get("serviceName") or value.get("service_name") or ""))
    if transport:
        outbound["transport"] = transport
    query = {
        "security": ["tls" if str(value.get("tls") or "").lower() in ("tls", "1", "true") else "none"],
        "sni": [str(value.get("sni") or "")],
        "alpn": [str(value.get("alpn") or "")],
        "fp": [str(value.get("fp") or "")],
        "insecure": [str(value.get("allowInsecure") or value.get("insecure") or "")],
    }
    tls = _tls_from_query(query)
    if tls:
        outbound["tls"] = tls
    return _advanced_descriptor("vmess", raw, name, server, port, outbound)


def _parse_shadowsocks(raw):
    payload = raw[len("ss://"):]
    payload, _, fragment = payload.partition("#")
    name = _unquote(fragment)
    payload, sep, raw_query = payload.partition("?")
    if sep and raw_query:
        query = urllib.parse.parse_qs(raw_query, keep_blank_values=True)
        if any(key != "plugin" for key in query):
            raise ProxyProtocolError("unsupported Shadowsocks query parameters")
        if _first(query, "plugin"):
            raise ProxyProtocolError("Shadowsocks plugins are not supported")
    method = password = host = ""
    port = 0
    if "@" in payload:
        user_part, server_part = payload.rsplit("@", 1)
        decoded_user = _unquote(user_part)
        if ":" not in decoded_user:
            decoded_user = _decode_b64_text(decoded_user)
        if ":" not in decoded_user:
            raise ProxyProtocolError("Shadowsocks credentials are invalid")
        method, password = decoded_user.split(":", 1)
        parsed_server = urllib.parse.urlsplit("ss://" + server_part)
        host = parsed_server.hostname or ""
        port = int(parsed_server.port or 0)
    else:
        decoded = _decode_b64_text(payload)
        if "@" not in decoded:
            raise ProxyProtocolError("legacy Shadowsocks URL is invalid")
        user_part, server_part = decoded.rsplit("@", 1)
        if ":" not in user_part:
            raise ProxyProtocolError("Shadowsocks credentials are invalid")
        method, password = user_part.split(":", 1)
        parsed_server = urllib.parse.urlsplit("ss://" + server_part)
        host = parsed_server.hostname or ""
        port = int(parsed_server.port or 0)
    method = method.strip().lower()
    if not method or not password or not host or not port:
        raise ProxyProtocolError("Shadowsocks URL is missing method, password, server or port")
    supported_methods = {
        "aes-128-gcm", "aes-192-gcm", "aes-256-gcm", "chacha20-ietf-poly1305",
        "2022-blake3-aes-128-gcm", "2022-blake3-aes-256-gcm",
    }
    if method not in supported_methods:
        raise ProxyProtocolError("unsupported Shadowsocks method: %s" % method)
    outbound = {"type": "shadowsocks", "method": method, "password": password}
    return _advanced_descriptor("ss", raw, name, host, port, outbound)


def parse_proxy_line(line):
    raw = str(line or "").strip()
    if not raw:
        raise ProxyProtocolError("empty proxy line")
    if "://" not in raw:
        canonical = _normalize_native(raw)
        parsed = urllib.parse.urlsplit(canonical)
        return ProxyDescriptor(parsed.scheme, raw, canonical, "", parsed.hostname or "", int(parsed.port or 0), "native")
    scheme = raw.split("://", 1)[0].lower()
    if scheme in NATIVE_SCHEMES or scheme == "socks":
        canonical = _normalize_native(raw)
        parsed = urllib.parse.urlsplit(canonical)
        name = _unquote(urllib.parse.urlsplit(raw).fragment)
        return ProxyDescriptor(parsed.scheme, raw, canonical, name, parsed.hostname or "", int(parsed.port or 0), "native")
    if scheme == "vless":
        return _parse_vless(raw)
    if scheme == "vmess":
        return _parse_vmess(raw)
    if scheme == "trojan":
        return _parse_trojan(raw)
    if scheme in ("hysteria2", "hy2"):
        return _parse_hysteria2(raw)
    if scheme == "tuic":
        return _parse_tuic(raw)
    if scheme == "ss":
        return _parse_shadowsocks(raw)
    raise ProxyProtocolError("unsupported proxy protocol: %s" % scheme)


def _looks_like_node_text(text):
    lower = str(text or "").lower()
    return any((scheme + "://") in lower for scheme in SUPPORTED_SCHEMES)


def parse_subscription_source(text):
    raw_text = str(text or "").lstrip("\ufeff")
    if len(raw_text.encode("utf-8", "ignore")) > MAX_SOURCE_BYTES:
        raise ProxyProtocolError("代理源内容超过 2 MiB 限制")
    decoded_base64 = False
    candidate = raw_text
    if not _looks_like_node_text(candidate):
        try:
            decoded = _decode_b64_text(candidate)
            if _looks_like_node_text(decoded):
                candidate = decoded
                decoded_base64 = True
        except Exception:
            pass
    result = SubscriptionParseResult(decoded_base64=decoded_base64)
    seen = set()
    for raw_line in candidate.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        result.total_lines += 1
        if result.total_lines > MAX_SOURCE_ENTRIES:
            raise ProxyProtocolError("代理源超过 10000 个节点限制")
        try:
            descriptor = parse_proxy_line(line)
        except Exception as exc:
            result.errors.append("line %s: %s" % (result.total_lines, exc))
            continue
        if descriptor.node_id in seen:
            continue
        seen.add(descriptor.node_id)
        result.nodes.append(descriptor)
        result.protocol_counts[descriptor.protocol] = result.protocol_counts.get(descriptor.protocol, 0) + 1
    if not result.nodes:
        detail = result.errors[0] if result.errors else "no recognizable proxy nodes"
        raise ProxyProtocolError("代理源中没有可用节点: %s" % detail)
    return result
