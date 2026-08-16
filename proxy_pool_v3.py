"""Proxy pool V3: resilient sources, precise health semantics and safe registration leases."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import socket
import tempfile
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from curl_cffi import requests

from proxy_protocol_runtime import ProtocolRuntimeManager
from proxy_protocols import ProxyDescriptor, ProxyProtocolError, parse_proxy_line, parse_subscription_source

_ROOT = os.path.dirname(os.path.abspath(__file__))
_MAX_SOURCE_BYTES = 2 << 20
_TLS = threading.local()
_MANAGER_LOCK = threading.RLock()
_MANAGER = None


class ProxyPoolError(RuntimeError):
    pass


class ProxyAcquireTimeout(ProxyPoolError):
    pass


class ProxyAcquireCancelled(ProxyPoolError):
    pass


class ProxyTransportError(ProxyPoolError):
    pass


class ProxyConfigurationError(ProxyPoolError):
    pass


@dataclass
class ProbeFamilyState:
    status: str = "unknown"
    tested_at: Optional[float] = None
    latency_ms: int = 0
    exit_ip: str = ""
    error: str = ""


@dataclass
class ProxyNode:
    id: str
    source: str
    proxy_url: str
    descriptor: ProxyDescriptor
    protocol: str = "http"
    name: str = ""
    backend: str = "native"
    enabled: bool = True
    rotating: bool = False
    health: float = 1.0
    business_samples: int = 0
    registration_successes: int = 0
    transport_failures: int = 0
    suspected_failures: int = 0
    configuration_failures: int = 0
    exit_successes: int = 0
    exit_failures: int = 0
    failure_count: int = 0
    cooldown_until: Optional[float] = None
    last_error: str = ""
    last_success_at: Optional[float] = None
    last_failure_at: Optional[float] = None
    probe_status: str = "unknown"
    last_probed_at: Optional[float] = None
    probe_latency_ms: int = 0
    probe_error: str = ""
    exit_ip: str = ""
    ipv4_probe: ProbeFamilyState = field(default_factory=ProbeFamilyState)
    ipv6_probe: ProbeFamilyState = field(default_factory=ProbeFamilyState)
    inflight: int = 0
    retired: bool = False


@dataclass
class ProxyLease:
    node_id: str
    proxy_url: str
    worker_key: str
    slot_index: int
    attempt_index: int
    affinity: str
    session_key: str
    source_uri: str = ""
    protocol: str = ""
    runtime_key: Optional[str] = None
    released: bool = False
    feedback_sampled: bool = False
    suspected_feedback: bool = False


@dataclass
class SourceState:
    descriptors: List[ProxyDescriptor] = field(default_factory=list)
    last_success_at: Optional[float] = None
    last_error: str = ""
    generation: int = 0
    diagnostics: Dict = field(default_factory=dict)
    configured: bool = False


def _config_signature(config):
    keys = (
        "proxy_mode", "proxy", "proxy_fallback", "proxy_pool_file",
        "proxy_pool_subscription_url", "proxy_pool_subscription_proxy",
        "proxy_pool_endpoint_mode", "proxy_pool_refresh_interval_sec",
        "proxy_pool_probe_interval_sec", "proxy_pool_probe_timeout_sec",
        "proxy_pool_probe_provider", "proxy_pool_probe_dual_stack",
        "proxy_pool_max_concurrent_per_node", "proxy_pool_acquire_timeout_sec",
        "proxy_protocol_backend", "proxy_singbox_path", "proxy_protocol_start_timeout_sec",
        "proxy_runtime_idle_ttl_sec", "proxy_runtime_cache_max",
        "proxy_pool_persist_health", "proxy_pool_state_file",
        "proxy_pool_subscription_public_only",
    )
    return tuple((key, config.get(key)) for key in keys)


def normalize_proxy_url(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        descriptor = parse_proxy_line(raw)
    except ProxyProtocolError as exc:
        raise ProxyPoolError(str(exc)) from exc
    if descriptor.backend != "native":
        raise ProxyPoolError("该配置项只接受 HTTP/HTTPS/SOCKS 代理")
    return descriptor.canonical_uri


def proxy_log_label(value):
    raw = str(value or "").strip()
    return raw or "direct"


def safe_proxy_error_text(value):
    return str(value or "")


def _node_id(proxy_url):
    try:
        return parse_proxy_line(proxy_url).node_id
    except Exception:
        return hashlib.sha256(str(proxy_url).encode("utf-8")).hexdigest()[:20]


def parse_proxy_source(text):
    try:
        result = parse_subscription_source(text)
    except ProxyProtocolError as exc:
        raise ProxyPoolError(str(exc)) from exc
    return [node.canonical_uri for node in result.nodes], result.skipped


def _expand_account_placeholder(proxy_url, session_key):
    return proxy_url.replace("{account}", session_key) if "{account}" in proxy_url else proxy_url


def classify_proxy_network_error(value):
    """Return compatibility/configuration/hard_transport/suspected_transport/application."""
    kind = getattr(value, "kind", "")
    if kind in {"socks_auth", "http_proxy_auth", "configuration"}:
        return "configuration"
    if kind in {"upstream_connect", "http_connect", "socks_connect"}:
        return "hard_transport"
    if kind in {"https_proxy_tls", "remote_reset", "local_dns", "remote_dns", "bridge"}:
        return "suspected_transport"
    text = str(value or "").lower()
    if not text:
        return "application"
    compatibility = (
        "unknown url type", "unsupported proxy scheme", "http-compatible proxy endpoint",
        "does not support scheme", "代理协议不受", "proxy scheme is unsupported",
        "unsupported proxy protocol", "native-only",
    )
    if any(marker in text for marker in compatibility):
        return "compatibility"
    configuration = (
        "proxy authentication", "proxy auth", "authentication failed", "authentication method rejected",
        "credentials rejected", "credential", "http_proxy_auth", "socks_auth", "407 proxy authentication",
    )
    if any(marker in text for marker in configuration):
        return "configuration"
    hard = (
        "socks4 connect failed", "socks5 connect failed", "proxy connection failed", "proxy server refused",
        "tunnel connection failed", "could not connect to proxy", "failed to connect to proxy",
        "err_proxy_connection_failed", "err_tunnel_connection_failed", "connection refused",
        "no route to host", "network is unreachable", "upstream_connect", "http_connect", "socks_connect",
    )
    if any(marker in text for marker in hard):
        return "hard_transport"
    suspected = (
        "tls connect error", "ssl", "handshake", "unexpected eof", "unexpected_eof",
        "connection reset", "connection aborted", "remote end closed", "broken pipe",
        "timed out", "timeout", "temporarily unavailable", "connect error", "failed to connect",
        "could not connect", "remote_reset", "https_proxy_tls", "local_dns", "remote_dns",
    )
    if any(marker in text for marker in suspected):
        return "suspected_transport"
    return "application"


def _is_transport_error_text(value):
    return classify_proxy_network_error(value) in ("hard_transport", "suspected_transport")


def is_proxy_transport_exception(exc):
    return isinstance(exc, ProxyTransportError) or _is_transport_error_text(exc)


def _public_ip(address):
    try:
        value = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return False
    return not (
        value.is_private or value.is_loopback or value.is_link_local or value.is_multicast
        or value.is_reserved or value.is_unspecified
    )


def _validate_public_url(url):
    parsed = urllib.parse.urlsplit(str(url or ""))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ProxyPoolError("代理订阅必须是有效的 http/https URL")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except Exception as exc:
        raise ProxyPoolError("代理订阅域名解析失败: %s" % exc) from exc
    addresses = {item[4][0] for item in infos}
    if not addresses or any(not _public_ip(item) for item in addresses):
        raise ProxyPoolError("代理订阅 public-only 模式拒绝非公网目标")


class ProxyPoolManager:
    def __init__(self, config, log=None):
        self.config = dict(config or {})
        self.log = log or (lambda message: None)
        self.signature = _config_signature(self.config)
        self.mode = str(self.config.get("proxy_mode") or "auto").strip().lower()
        self.fallback = str(self.config.get("proxy_fallback") or "none").strip().lower()
        self.endpoint_mode = str(self.config.get("proxy_pool_endpoint_mode") or "auto").strip().lower()
        self.probe_provider = str(self.config.get("proxy_pool_probe_provider") or "cloudflare").strip().lower()
        self.dual_stack = bool(self.config.get("proxy_pool_probe_dual_stack", True))
        self.capacity = max(1, int(self.config.get("proxy_pool_max_concurrent_per_node") or 1))
        self.acquire_timeout = max(1, int(self.config.get("proxy_pool_acquire_timeout_sec") or 30))
        self.refresh_interval = max(0, int(self.config.get("proxy_pool_refresh_interval_sec", 900)))
        self.probe_interval = max(0, int(self.config.get("proxy_pool_probe_interval_sec", 900)))
        self.probe_timeout = max(3, int(self.config.get("proxy_pool_probe_timeout_sec") or 15))
        self.persist_health = bool(self.config.get("proxy_pool_persist_health", False))
        self.subscription_public_only = bool(self.config.get("proxy_pool_subscription_public_only", False))
        state_file = str(self.config.get("proxy_pool_state_file") or "./proxy_pool_state.json").strip()
        state_file = os.path.expanduser(state_file)
        self.state_path = state_file if os.path.isabs(state_file) else os.path.join(_ROOT, state_file)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._nodes = {}
        self._probe_events = {}
        self._last_refresh = 0.0
        self._last_probe_all = 0.0
        self._probe_all_running = False
        self._source_states = {"file": SourceState(), "subscription": SourceState()}
        self._source_diagnostics = {}
        self._persisted_state = self._load_state_file()
        self._runtime = ProtocolRuntimeManager(self.config, log=self.log)
        self.reload_sources(force=True)

    @property
    def managed(self):
        return self.mode in ("single", "pool")

    def total_inflight(self):
        with self._lock:
            return sum(node.inflight for node in self._nodes.values())

    def shutdown(self):
        self._save_state_file()
        self._runtime.shutdown()

    def _load_state_file(self):
        if not self.persist_health:
            return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value.get("nodes", {}) if isinstance(value, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as exc:
            self.log("[!] 代理健康状态读取失败，忽略旧状态: %s" % exc)
            return {}

    def _save_state_file(self):
        if not self.persist_health:
            return
        with self._lock:
            nodes = {}
            for node in self._nodes.values():
                if node.retired:
                    continue
                nodes[node.id] = {
                    "health": node.health, "business_samples": node.business_samples,
                    "registration_successes": node.registration_successes, "transport_failures": node.transport_failures,
                    "suspected_failures": node.suspected_failures, "configuration_failures": node.configuration_failures,
                    "exit_successes": node.exit_successes, "exit_failures": node.exit_failures,
                    "failure_count": node.failure_count, "cooldown_until": node.cooldown_until,
                    "last_error": node.last_error, "last_success_at": node.last_success_at, "last_failure_at": node.last_failure_at,
                }
        directory = os.path.dirname(os.path.abspath(self.state_path))
        os.makedirs(directory, exist_ok=True)
        fd = path = None
        try:
            fd, path = tempfile.mkstemp(prefix=".proxy-state-", suffix=".json.tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                json.dump({"version": 1, "saved_at": time.time(), "nodes": nodes}, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush(); os.fsync(handle.fileno())
            os.replace(path, self.state_path)
            path = None
        finally:
            if fd is not None:
                try: os.close(fd)
                except Exception: pass
            if path:
                try: os.unlink(path)
                except Exception: pass

    def _restore_node_state(self, node):
        saved = self._persisted_state.get(node.id)
        if not isinstance(saved, dict):
            return
        for key in (
            "health", "business_samples", "registration_successes", "transport_failures", "suspected_failures",
            "configuration_failures", "exit_successes", "exit_failures", "failure_count", "cooldown_until",
            "last_error", "last_success_at", "last_failure_at",
        ):
            if key in saved:
                setattr(node, key, saved[key])

    def _rotating_for(self, descriptor):
        if self.endpoint_mode == "rotating":
            return True
        if self.endpoint_mode == "fixed":
            return False
        return descriptor.backend == "native" and "{account}" in descriptor.canonical_uri

    def _read_file_source(self):
        path = str(self.config.get("proxy_pool_file") or "").strip()
        if not path:
            return None
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(_ROOT, path)
        with open(path, "r", encoding="utf-8-sig") as handle:
            return parse_subscription_source(handle.read())

    def _fetch_subscription(self):
        url = str(self.config.get("proxy_pool_subscription_url") or "").strip()
        if not url:
            return None
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ProxyPoolError("代理订阅必须是有效的 http/https URL")
        if self.subscription_public_only:
            _validate_public_url(url)
        via = str(self.config.get("proxy_pool_subscription_proxy") or "").strip()
        if via:
            via = normalize_proxy_url(via)
        proxies = {"http": via, "https": via} if via else {}
        current_url = url
        response = None
        for redirect_count in range(4):
            try:
                response = requests.get(
                    current_url, proxies=proxies, timeout=min(max(self.probe_timeout, 5), 60),
                    allow_redirects=False, headers={"Accept": "text/plain, text/*;q=0.9, */*;q=0.1"},
                )
            except Exception as exc:
                raise ProxyPoolError("代理订阅请求失败: %s" % safe_proxy_error_text(exc)) from exc
            status_code = int(response.status_code)
            if status_code not in (301, 302, 303, 307, 308):
                break
            if redirect_count >= 3:
                raise ProxyPoolError("代理订阅重定向次数超过 3 次")
            location = str((getattr(response, "headers", {}) or {}).get("location") or "").strip()
            if not location:
                raise ProxyPoolError("代理订阅重定向缺少 Location")
            current_url = urllib.parse.urljoin(current_url, location)
            redirected = urllib.parse.urlsplit(current_url)
            if redirected.scheme not in ("http", "https") or not redirected.netloc:
                raise ProxyPoolError("代理订阅重定向地址无效")
            if self.subscription_public_only:
                _validate_public_url(current_url)
        if response is None or not 200 <= int(response.status_code) < 300:
            raise ProxyPoolError("代理订阅返回 HTTP %s" % int(getattr(response, "status_code", 0) or 0))
        body = str(response.text or "")
        if len(body.encode("utf-8", "ignore")) > _MAX_SOURCE_BYTES:
            raise ProxyPoolError("代理订阅内容超过 2 MiB 限制")
        return parse_subscription_source(body)

    def _refresh_source(self, name, loader):
        state = self._source_states[name]
        configured = bool(self.config.get("proxy_pool_file") if name == "file" else self.config.get("proxy_pool_subscription_url"))
        state.configured = configured
        if not configured:
            state.descriptors = []
            state.last_error = ""
            state.diagnostics = {}
            return
        try:
            result = loader()
            if result is None:
                state.descriptors = []
                return
            state.descriptors = list(result.nodes)
            state.last_success_at = time.time()
            state.last_error = ""
            state.generation += 1
            state.diagnostics = result.as_dict()
            state.diagnostics.update({"stale": False, "generation": state.generation, "last_success_at": state.last_success_at})
            if result.skipped:
                self.log("[!] %s 跳过 %s 个无法解析的节点" % (name, result.skipped))
        except Exception as exc:
            state.last_error = safe_proxy_error_text(exc)
            if state.descriptors:
                state.diagnostics = dict(state.diagnostics)
                state.diagnostics.update({"stale": True, "error": state.last_error, "generation": state.generation})
                self.log("[!] %s 刷新失败，继续使用最近一次成功节点: %s" % (name, state.last_error))
            else:
                state.diagnostics = {"stale": True, "error": state.last_error, "generation": state.generation}

    def _source_entries(self):
        if self.mode == "single":
            try:
                return [("single", parse_proxy_line(self.config.get("proxy")))]
            except ProxyProtocolError as exc:
                raise ProxyPoolError(str(exc)) from exc
        if self.mode != "pool":
            return []
        self._refresh_source("file", self._read_file_source)
        self._refresh_source("subscription", self._fetch_subscription)
        values = []
        for name in ("file", "subscription"):
            values.extend((name, item) for item in self._source_states[name].descriptors)
        unique, seen = [], set()
        for source, descriptor in values:
            if descriptor.node_id not in seen:
                seen.add(descriptor.node_id); unique.append((source, descriptor))
        self._source_diagnostics = {name: dict(state.diagnostics) for name, state in self._source_states.items() if state.configured}
        if not unique:
            errors = [state.last_error for state in self._source_states.values() if state.last_error]
            detail = "; ".join(errors) if errors else "未配置代理池文件或订阅"
            raise ProxyPoolError("代理池没有可用节点: %s" % detail)
        return unique

    def reload_sources(self, force=False):
        if not self.managed:
            return self.snapshot()
        now = time.time()
        with self._lock:
            if not force and self.refresh_interval > 0 and now - self._last_refresh < self.refresh_interval:
                return self.snapshot()
        entries = self._source_entries()
        with self._condition:
            previous, updated = self._nodes, {}
            for source, descriptor in entries:
                node_id = descriptor.node_id
                old = previous.get(node_id)
                if old is not None:
                    old.source, old.proxy_url, old.descriptor = source, descriptor.canonical_uri, descriptor
                    old.protocol, old.name, old.backend = descriptor.protocol, descriptor.name, descriptor.backend
                    old.rotating, old.retired = self._rotating_for(descriptor), False
                    updated[node_id] = old
                else:
                    node = ProxyNode(
                        id=node_id, source=source, proxy_url=descriptor.canonical_uri, descriptor=descriptor,
                        protocol=descriptor.protocol, name=descriptor.name, backend=descriptor.backend,
                        rotating=self._rotating_for(descriptor),
                    )
                    self._restore_node_state(node)
                    updated[node_id] = node
            for node_id, old in previous.items():
                if node_id not in updated and old.inflight > 0:
                    old.retired = True; updated[node_id] = old
            self._nodes = updated
            self._last_refresh = now
            self._condition.notify_all()
        self._save_state_file()
        return self.snapshot()

    def refresh_if_due(self):
        if not self.managed:
            return
        now = time.time()
        with self._lock:
            due = self.refresh_interval > 0 and now - self._last_refresh >= self.refresh_interval
        if due:
            try: self.reload_sources(force=True)
            except Exception as exc: self.log("[!] 代理池刷新失败，继续使用当前节点: %s" % safe_proxy_error_text(exc))

    def _schedule_periodic_probe_if_due(self):
        if not self.managed or self.probe_interval <= 0:
            return
        now = time.time()
        with self._lock:
            if self._probe_all_running or now - self._last_probe_all < self.probe_interval:
                return
            self._probe_all_running = True; self._last_probe_all = now
        def runner():
            try: self.probe_all(force=True)
            finally:
                with self._lock: self._probe_all_running = False
        threading.Thread(target=runner, name="proxy-probe-all", daemon=True).start()

    def _eligible_locked(self, now):
        return [
            node for node in self._nodes.values()
            if node.enabled and not node.retired and node.inflight < self.capacity
            and (node.rotating or node.cooldown_until is None or now >= node.cooldown_until)
        ]

    def _probe_tier(self, node, now):
        freshness = max(60, (self.probe_interval * 2) if self.probe_interval > 0 else 300)
        if not node.last_probed_at or now - node.last_probed_at > freshness:
            return 1
        if node.probe_status == "healthy":
            return 0
        if node.probe_status == "unhealthy":
            return 2
        return 1

    def _select_locked(self, nodes, affinity):
        now = time.time()
        best_tier = min(self._probe_tier(node, now) for node in nodes)
        pool = sorted((node for node in nodes if self._probe_tier(node, now) == best_tier), key=lambda value: value.id)
        digest = hashlib.sha256(str(affinity or "").encode("utf-8")).digest()
        selected = pool[int.from_bytes(digest[:8], "big") % len(pool)]
        if selected.rotating or selected.health >= 0.8 or len(pool) == 1:
            return selected
        return max(pool, key=lambda value: (value.health, -value.inflight, value.id))

    def _descriptor_for_session(self, descriptor, session_key):
        if descriptor.backend != "native" or "{account}" not in descriptor.canonical_uri:
            return descriptor
        try: return parse_proxy_line(_expand_account_placeholder(descriptor.canonical_uri, session_key))
        except ProxyProtocolError as exc: raise ProxyPoolError(str(exc)) from exc

    def _fallback_lease_locked(self, worker_key, slot_index, attempt_index, affinity, session_key):
        if self.fallback == "direct":
            return ProxyLease("direct", "", worker_key, slot_index, attempt_index, affinity, session_key, protocol="direct")
        if self.fallback == "single":
            proxy_url = normalize_proxy_url(self.config.get("proxy"))
            if proxy_url:
                descriptor = parse_proxy_line(_expand_account_placeholder(proxy_url, session_key))
                endpoint, runtime_key = self._runtime.acquire(descriptor)
                return ProxyLease("fallback-single", endpoint, worker_key, slot_index, attempt_index, affinity, session_key, source_uri=proxy_url, protocol=descriptor.protocol, runtime_key=runtime_key)
        return None

    def _resolve_node_endpoint(self, node, session_key):
        return self._runtime.acquire(self._descriptor_for_session(node.descriptor, session_key))

    def _rollback_runtime_failure(self, node_id, error):
        kind = classify_proxy_network_error(error)
        with self._condition:
            node = self._nodes.get(node_id)
            if node is not None:
                node.inflight = max(0, node.inflight - 1)
                node.enabled = False
                node.probe_status = "unavailable"
                node.probe_error = safe_proxy_error_text(error)[:300]
                node.configuration_failures += 1 if kind in ("configuration", "compatibility") else 0
                node.last_error = "%s: %s" % ("configuration" if kind in ("configuration", "compatibility") else "backend", safe_proxy_error_text(error)[:260])
            self._condition.notify_all()
        self._save_state_file()

    def acquire(self, affinity, worker_key, slot_index, attempt_index, session_key, timeout=None, cancel_callback=None):
        if not self.managed:
            return None
        self.refresh_if_due(); self._schedule_periodic_probe_if_due(); self._runtime.cleanup_idle()
        deadline = time.time() + float(timeout if timeout is not None else self.acquire_timeout)
        last_runtime_error = None
        while True:
            selected = None
            with self._condition:
                while selected is None:
                    if cancel_callback and cancel_callback():
                        raise ProxyAcquireCancelled("代理租约等待已取消")
                    now = time.time(); eligible = self._eligible_locked(now)
                    if eligible:
                        selected = self._select_locked(eligible, affinity); selected.inflight += 1; break
                    active_nodes = [node for node in self._nodes.values() if node.enabled and not node.retired]
                    if not active_nodes:
                        fallback = self._fallback_lease_locked(worker_key, slot_index, attempt_index, affinity, session_key)
                        if fallback is not None: return fallback
                        detail = ": %s" % last_runtime_error if last_runtime_error else ""
                        raise ProxyPoolError("代理池当前没有可用节点%s" % detail)
                    remaining = deadline - now
                    if remaining <= 0:
                        fallback = self._fallback_lease_locked(worker_key, slot_index, attempt_index, affinity, session_key)
                        if fallback is not None: return fallback
                        raise ProxyAcquireTimeout("等待可用代理超时")
                    wake_after = min(remaining, 1.0)
                    cooldowns = [node.cooldown_until for node in active_nodes if node.cooldown_until and node.cooldown_until > now]
                    if cooldowns: wake_after = min(wake_after, max(0.05, min(cooldowns) - now))
                    self._condition.wait(timeout=wake_after)
            try:
                endpoint, runtime_key = self._resolve_node_endpoint(selected, session_key)
                return ProxyLease(selected.id, endpoint, worker_key, slot_index, attempt_index, affinity, session_key, source_uri=selected.descriptor.raw_uri, protocol=selected.protocol, runtime_key=runtime_key)
            except Exception as exc:
                last_runtime_error = safe_proxy_error_text(exc); self._rollback_runtime_failure(selected.id, exc)
                if time.time() >= deadline:
                    raise ProxyPoolError("代理协议运行时不可用: %s" % last_runtime_error) from exc

    def release(self, lease):
        if lease is None or lease.released:
            return
        lease.released = True
        if lease.runtime_key: self._runtime.release(lease.runtime_key)
        if lease.node_id in ("direct", "fallback-single"): return
        with self._condition:
            node = self._nodes.get(lease.node_id)
            if node is not None:
                node.inflight = max(0, node.inflight - 1)
                if node.retired and node.inflight == 0: self._nodes.pop(node.id, None)
            self._condition.notify_all()

    def _count_feedback_sample(self, node, lease):
        if lease is not None and lease.feedback_sampled:
            return False
        if lease is not None: lease.feedback_sampled = True
        node.business_samples += 1
        return True

    def report_success(self, lease):
        if lease is None or lease.node_id in ("direct", "fallback-single"): return
        with self._condition:
            node = self._nodes.get(lease.node_id)
            if node is None: return
            self._count_feedback_sample(node, lease)
            node.registration_successes += 1; node.last_success_at = time.time()
            if node.rotating:
                node.exit_successes += 1
                node.last_error = ""
            else:
                node.health = min(1.0, node.health + 0.1); node.failure_count = 0; node.cooldown_until = None
                if not node.last_error.startswith("backend:") and not node.last_error.startswith("configuration:"): node.last_error = ""
            self._condition.notify_all()
        self._save_state_file()

    def report_soft_failure(self, lease, error):
        if lease is None or lease.node_id in ("direct", "fallback-single"): return
        with self._lock:
            node = self._nodes.get(lease.node_id)
            if node is not None: node.last_error = "soft: %s" % safe_proxy_error_text(error)[:300]

    def _apply_configuration_failure(self, node_id, error, lease=None):
        with self._condition:
            node = self._nodes.get(node_id)
            if node is None: return
            node.configuration_failures += 1; node.last_failure_at = time.time(); node.enabled = False
            node.last_error = "configuration: %s" % safe_proxy_error_text(error)[:260]
            self._condition.notify_all()
        self._save_state_file()

    def _apply_transport_failure(self, node_id, error, schedule_probe=True, lease=None):
        node_for_probe = None
        with self._condition:
            node = self._nodes.get(node_id)
            if node is None: return
            self._count_feedback_sample(node, lease)
            node.transport_failures += 1; node.last_failure_at = time.time()
            if node.rotating:
                node.exit_failures += 1; node.last_error = "transport: rotating exit"; self._condition.notify_all()
            else:
                node.failure_count += 1; node.health = max(0.05, node.health * 0.7)
                cooldown = min(600, 30 * (2 ** min(max(node.failure_count - 1, 0), 4)))
                node.cooldown_until = time.time() + cooldown; node.last_error = "transport"; node_for_probe = node.id
                self._condition.notify_all()
        self._save_state_file()
        if node_for_probe and schedule_probe: self._schedule_failure_probe(node_for_probe)

    def classify_error(self, lease, error):
        if lease is not None and lease.runtime_key:
            diagnostic = self._runtime.diagnostic_for(lease.runtime_key)
            if diagnostic:
                return classify_proxy_network_error(type("BridgeDiagnostic", (), diagnostic)())
        return classify_proxy_network_error(error)

    def report_transport_failure(self, lease, error):
        if lease is None or lease.node_id in ("direct", "fallback-single"): return
        kind = self.classify_error(lease, error)
        if kind in ("configuration", "compatibility"):
            self._apply_configuration_failure(lease.node_id, error, lease=lease)
        elif kind == "suspected_transport":
            self.report_suspected_transport_failure(lease, error)
        else:
            self._apply_transport_failure(lease.node_id, error, schedule_probe=True, lease=lease)

    def report_suspected_transport_failure(self, lease, error):
        if lease is None or lease.node_id in ("direct", "fallback-single"): return
        with self._lock:
            node = self._nodes.get(lease.node_id)
            if node is not None: node.suspected_failures += 1
        lease.suspected_feedback = True
        self._schedule_failure_probe(lease.node_id, penalize_on_failure=True, suspected_error=error, lease=lease)

    def _probe_endpoint(self, family="ipv4"):
        if self.probe_provider == "ipinfo":
            return "https://v6.ipinfo.io/json" if family == "ipv6" else "https://ipinfo.io/json"
        return "https://[2606:4700:4700::1111]/cdn-cgi/trace" if family == "ipv6" else "https://1.1.1.1/cdn-cgi/trace"

    def _parse_probe_ip(self, response):
        text = str(response.text or "")
        if self.probe_provider == "ipinfo":
            try:
                value = response.json()
                return str(value.get("ip") or "").strip() if isinstance(value, dict) else ""
            except Exception:
                return ""
        for line in text.splitlines():
            if line.startswith("ip="): return line[3:].strip()
        return ""

    def _probe_family(self, descriptor, session_key, family):
        runtime_key = None; started = time.monotonic(); status = "unhealthy"; exit_ip = ""; error = ""
        try:
            resolved = self._descriptor_for_session(descriptor, session_key)
            proxy_url, runtime_key = self._runtime.acquire(resolved)
            response = requests.get(self._probe_endpoint(family), proxies={"http": proxy_url, "https": proxy_url}, timeout=self.probe_timeout, allow_redirects=False)
            if not 200 <= int(response.status_code) < 300:
                raise ProxyPoolError("HTTP %s" % response.status_code)
            exit_ip = self._parse_probe_ip(response)
            try: parsed_ip = ipaddress.ip_address(exit_ip)
            except ValueError: raise ProxyPoolError("探测服务返回 2xx 但没有有效 IP")
            expected = 6 if family == "ipv6" else 4
            if parsed_ip.version != expected:
                raise ProxyPoolError("探测服务返回的 IP family 与 %s 不匹配" % family)
            exit_ip = str(parsed_ip); status = "healthy"
        except Exception as exc:
            error = safe_proxy_error_text(exc)
        finally:
            if runtime_key: self._runtime.release(runtime_key)
        return ProbeFamilyState(status=status, tested_at=time.time(), latency_ms=max(1, int((time.monotonic() - started) * 1000)), exit_ip=exit_ip if status == "healthy" else "", error=error[:300] if status != "healthy" else "")

    def probe_node(self, node_id):
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None: raise ProxyPoolError("代理节点不存在")
            descriptor = node.descriptor; session_key = secrets.token_hex(8)
        families = ["ipv6", "ipv4"] if self.dual_stack else ["ipv4"]
        outcomes = {}
        if len(families) == 1:
            outcomes[families[0]] = self._probe_family(descriptor, session_key, families[0])
        else:
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="proxy-family-probe") as executor:
                futures = {executor.submit(self._probe_family, descriptor, session_key, family): family for family in families}
                for future in as_completed(futures): outcomes[futures[future]] = future.result()
        ipv4 = outcomes.get("ipv4", ProbeFamilyState()); ipv6 = outcomes.get("ipv6", ProbeFamilyState())
        healthy = [value for value in (ipv4, ipv6) if value.status == "healthy"]
        status = "healthy" if healthy else "unhealthy"
        chosen = ipv4 if ipv4.status == "healthy" else ipv6 if ipv6.status == "healthy" else ipv4
        error = "; ".join("%s: %s" % (name.upper(), value.error) for name, value in (("ipv4", ipv4), ("ipv6", ipv6)) if value.error)
        with self._condition:
            node = self._nodes.get(node_id)
            if node is not None:
                node.ipv4_probe, node.ipv6_probe = ipv4, ipv6
                node.probe_status = status; node.last_probed_at = time.time()
                node.probe_latency_ms = max(ipv4.latency_ms, ipv6.latency_ms); node.probe_error = error[:300] if status != "healthy" else ""
                node.exit_ip = chosen.exit_ip if status == "healthy" else ""
                if status == "healthy":
                    node.enabled = True
                    if node.last_error == "transport" or node.last_error.startswith("backend:"):
                        if not node.rotating:
                            node.health = 1.0; node.failure_count = 0; node.cooldown_until = None
                        node.last_error = ""
                self._condition.notify_all()
        self._save_state_file()
        return {
            "id": node_id, "status": status, "latency_ms": max(ipv4.latency_ms, ipv6.latency_ms),
            "exit_ip": chosen.exit_ip if status == "healthy" else "", "error": error,
            "ipv4": self._family_dict(ipv4), "ipv6": self._family_dict(ipv6),
        }

    @staticmethod
    def _family_dict(value):
        return {"status": value.status, "tested_at": value.tested_at, "latency_ms": value.latency_ms, "exit_ip": value.exit_ip, "error": value.error}

    def _schedule_failure_probe(self, node_id, penalize_on_failure=False, suspected_error=None, lease=None):
        with self._lock:
            existing = self._probe_events.get(node_id)
            if existing is not None and not existing.is_set(): return
            event = threading.Event(); self._probe_events[node_id] = event
        def runner():
            try:
                result = self.probe_node(node_id)
                if penalize_on_failure and result.get("status") != "healthy":
                    self._apply_transport_failure(node_id, suspected_error or result.get("error") or "probe failed", schedule_probe=False, lease=lease)
            except Exception:
                if penalize_on_failure:
                    self._apply_transport_failure(node_id, suspected_error or "probe failed", schedule_probe=False, lease=lease)
            finally:
                event.set()
                with self._lock:
                    if self._probe_events.get(node_id) is event: self._probe_events.pop(node_id, None)
        threading.Thread(target=runner, name="proxy-probe-%s" % node_id[:8], daemon=True).start()

    def probe_all(self, force=False):
        now = time.time()
        with self._lock:
            if not force and self.probe_interval > 0 and now - self._last_probe_all < self.probe_interval: return []
            node_ids = [node.id for node in self._nodes.values() if not node.retired]; self._last_probe_all = now
        results = []
        if not node_ids: return results
        with ThreadPoolExecutor(max_workers=min(8, len(node_ids)), thread_name_prefix="proxy-probe") as executor:
            futures = {executor.submit(self.probe_node, node_id): node_id for node_id in node_ids}
            for future in as_completed(futures):
                try: results.append(future.result())
                except Exception as exc: results.append({"id": futures[future], "status": "unhealthy", "error": safe_proxy_error_text(exc)})
        return results

    def preflight_node(self, node_id):
        """Non-destructive reachability test against registration-path origins."""
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None: raise ProxyPoolError("代理节点不存在")
            descriptor = node.descriptor; session_key = secrets.token_hex(8)
        runtime_key = None
        try:
            proxy_url, runtime_key = self._runtime.acquire(self._descriptor_for_session(descriptor, session_key))
            results = []
            for url in ("https://accounts.x.ai/", "https://grok.com/"):
                started = time.monotonic()
                try:
                    response = requests.get(url, proxies={"http": proxy_url, "https": proxy_url}, timeout=self.probe_timeout, allow_redirects=False)
                    status_code = int(response.status_code)
                    text = str(getattr(response, "text", "") or "")[:4096].lower()
                    headers = {str(k).lower(): str(v).lower() for k, v in dict(getattr(response, "headers", {}) or {}).items()}
                    cloudflare = "cloudflare" in headers.get("server", "") or "cf-error" in text or "__cf_chl" in text
                    reachable = 100 <= status_code < 600
                    cloudflare_block = bool(cloudflare and status_code in (403, 429, 503))
                    usable = bool(reachable and not cloudflare_block and 200 <= status_code < 400)
                    results.append({"url": url, "reachable": reachable, "usable": usable, "status_code": status_code, "latency_ms": max(1, int((time.monotonic()-started)*1000)), "cloudflare_block": cloudflare_block, "error": ""})
                except Exception as exc:
                    results.append({"url": url, "reachable": False, "usable": False, "status_code": 0, "latency_ms": max(1, int((time.monotonic()-started)*1000)), "cloudflare_block": False, "error": safe_proxy_error_text(exc)[:300]})
            return {"id": node_id, "ok": all(item["usable"] for item in results), "targets": results}
        finally:
            if runtime_key: self._runtime.release(runtime_key)

    def snapshot(self):
        with self._lock:
            now = time.time(); nodes = []
            for node in sorted(self._nodes.values(), key=lambda value: value.id):
                cooldown = int(max(1, node.cooldown_until - now)) if node.cooldown_until and node.cooldown_until > now else 0
                gateway_samples = node.exit_successes + node.exit_failures
                gateway_success_rate = round(node.exit_successes / gateway_samples, 4) if gateway_samples else None
                nodes.append({
                    "id": node.id, "source": node.source, "proxy": node.descriptor.raw_uri, "name": node.name,
                    "protocol": node.protocol, "backend": node.backend, "enabled": bool(node.enabled), "rotating": bool(node.rotating),
                    "health_model": "gateway" if node.rotating else "fixed", "health": None if node.rotating else round(float(node.health), 3),
                    "business_samples": int(node.business_samples), "registration_successes": node.registration_successes,
                    "transport_failures": node.transport_failures, "suspected_failures": node.suspected_failures,
                    "configuration_failures": node.configuration_failures, "exit_successes": node.exit_successes, "exit_failures": node.exit_failures,
                    "gateway_success_rate": gateway_success_rate, "failure_count": int(node.failure_count), "cooldown_sec": 0 if node.rotating else cooldown,
                    "last_error": str(node.last_error or "")[:300], "last_success_at": node.last_success_at, "last_failure_at": node.last_failure_at,
                    "probe_status": node.probe_status, "last_probed_at": node.last_probed_at, "probe_latency_ms": int(node.probe_latency_ms or 0),
                    "probe_error": str(node.probe_error or "")[:300], "exit_ip": node.exit_ip,
                    "ipv4_probe": self._family_dict(node.ipv4_probe), "ipv6_probe": self._family_dict(node.ipv6_probe),
                    "inflight": int(node.inflight), "retired": bool(node.retired),
                })
            return {"mode": self.mode, "managed": self.managed, "fallback": self.fallback, "capacity": self.capacity, "nodes": nodes, "sources": dict(self._source_diagnostics), "runtime": self._runtime.active_snapshot(), "persist_health": self.persist_health}


def get_manager(config=None, log=None):
    global _MANAGER
    if config is None:
        from app_config import config as app_config
        config = app_config
    signature = _config_signature(config)
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = ProxyPoolManager(config, log=log)
        elif _MANAGER.signature != signature and _MANAGER.total_inflight() == 0:
            old = _MANAGER; _MANAGER = ProxyPoolManager(config, log=log); old.shutdown()
        elif log is not None:
            _MANAGER.log = log; _MANAGER._runtime.log = log
        return _MANAGER


def reset_manager():
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is not None and _MANAGER.total_inflight() > 0:
            raise ProxyPoolError("仍有代理租约使用中，不能重置代理池")
        old = _MANAGER; _MANAGER = None
    if old is not None: old.shutdown()


def current_proxy_lease(): return getattr(_TLS, "lease", None)
def current_proxy_url():
    lease = current_proxy_lease(); return None if lease is None else str(lease.proxy_url or "")
def managed_proxy_active(): return current_proxy_lease() is not None


def begin_registration_slot(slot_index, attempt_index=1, worker_key=None, log=None, cancel_callback=None):
    if current_proxy_lease() is not None: raise ProxyPoolError("当前线程已有未释放的代理租约")
    manager = get_manager(log=log)
    if not manager.managed: return None
    worker = str(worker_key or threading.current_thread().name or "worker"); slot = int(slot_index); attempt = int(attempt_index)
    affinity = "%s:slot:%s" % (worker, slot)
    session_seed = "%s:%s:%s:%s" % (worker, slot, attempt, secrets.token_hex(8))
    session_key = hashlib.sha256(session_seed.encode("utf-8")).hexdigest()[:16]
    lease = manager.acquire(affinity=affinity, worker_key=worker, slot_index=slot, attempt_index=attempt, session_key=session_key, cancel_callback=cancel_callback)
    _TLS.lease = lease
    if log is not None:
        label = lease.source_uri or lease.proxy_url; log("[*] 当前账号代理: %s" % proxy_log_label(label))
        if lease.source_uri and lease.proxy_url and lease.source_uri != lease.proxy_url: log("[*] 当前代理本地出口: %s" % lease.proxy_url)
    return lease


def end_registration_slot(success=False, transport_error=None):
    lease = current_proxy_lease()
    if lease is None: return
    manager = get_manager()
    try:
        if transport_error is not None: manager.report_transport_failure(lease, transport_error)
        elif success: manager.report_success(lease)
    finally:
        manager.release(lease); _TLS.lease = None


def report_current_transport_failure(error):
    lease = current_proxy_lease()
    if lease is not None: get_manager().report_transport_failure(lease, error)


def report_current_suspected_transport_failure(error):
    lease = current_proxy_lease()
    if lease is not None: get_manager().report_suspected_transport_failure(lease, error)


def manager_snapshot(config=None):
    try: return get_manager(config=config).snapshot()
    except Exception as exc:
        return {"mode": str((config or {}).get("proxy_mode") or "auto"), "managed": False, "nodes": [], "sources": {}, "error": safe_proxy_error_text(exc)}
