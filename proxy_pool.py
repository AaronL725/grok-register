"""Proxy pool, health tracking, multi-protocol subscriptions, and registration-scoped leases."""
from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

from curl_cffi import requests

from proxy_protocol_runtime import ProtocolRuntimeManager, ProxyRuntimeError
from proxy_protocols import (
    NATIVE_SCHEMES,
    ProxyDescriptor,
    ProxyProtocolError,
    parse_proxy_line,
    parse_subscription_source,
)

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
    failure_count: int = 0
    cooldown_until: Optional[float] = None
    last_error: str = ""
    probe_status: str = "unknown"
    last_probed_at: Optional[float] = None
    probe_latency_ms: int = 0
    exit_ip: str = ""
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


def _config_signature(config):
    keys = (
        "proxy_mode", "proxy", "proxy_fallback", "proxy_pool_file",
        "proxy_pool_subscription_url", "proxy_pool_subscription_proxy",
        "proxy_pool_endpoint_mode", "proxy_pool_refresh_interval_sec",
        "proxy_pool_probe_interval_sec", "proxy_pool_probe_timeout_sec",
        "proxy_pool_probe_provider", "proxy_pool_max_concurrent_per_node",
        "proxy_pool_acquire_timeout_sec", "proxy_protocol_backend",
        "proxy_singbox_path", "proxy_protocol_start_timeout_sec",
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
    """Backward-compatible parser API; now recognizes native and advanced nodes."""
    try:
        result = parse_subscription_source(text)
    except ProxyProtocolError as exc:
        raise ProxyPoolError(str(exc)) from exc
    return [node.canonical_uri for node in result.nodes], result.skipped


def _expand_account_placeholder(proxy_url, session_key):
    if "{account}" not in proxy_url:
        return proxy_url
    return proxy_url.replace("{account}", session_key)


def _is_transport_error_text(value):
    text = str(value or "").lower()
    markers = (
        "err_proxy", "proxy connection", "proxy server", "proxy authentication",
        "proxy connect", "tunnel connection", "tunnel failed", "socks",
        "connection refused", "connection reset", "failed to connect",
        "could not connect", "connect error", "timed out", "timeout",
    )
    return any(marker in text for marker in markers)


def is_proxy_transport_exception(exc):
    return isinstance(exc, ProxyTransportError) or _is_transport_error_text(exc)


class ProxyPoolManager:
    def __init__(self, config, log=None):
        self.config = dict(config or {})
        self.log = log or (lambda message: None)
        self.signature = _config_signature(self.config)
        self.mode = str(self.config.get("proxy_mode") or "auto").strip().lower()
        self.fallback = str(self.config.get("proxy_fallback") or "none").strip().lower()
        self.endpoint_mode = str(self.config.get("proxy_pool_endpoint_mode") or "auto").strip().lower()
        self.probe_provider = str(self.config.get("proxy_pool_probe_provider") or "cloudflare").strip().lower()
        self.capacity = max(1, int(self.config.get("proxy_pool_max_concurrent_per_node") or 1))
        self.acquire_timeout = max(1, int(self.config.get("proxy_pool_acquire_timeout_sec") or 30))
        self.refresh_interval = max(0, int(self.config.get("proxy_pool_refresh_interval_sec", 900)))
        self.probe_interval = max(0, int(self.config.get("proxy_pool_probe_interval_sec", 900)))
        self.probe_timeout = max(3, int(self.config.get("proxy_pool_probe_timeout_sec") or 15))
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._nodes = {}
        self._probe_events = {}
        self._last_refresh = 0.0
        self._last_probe_all = 0.0
        self._probe_all_running = False
        self._source_diagnostics = {}
        self._runtime = ProtocolRuntimeManager(self.config, log=self.log)
        self.reload_sources(force=True)

    @property
    def managed(self):
        return self.mode in ("single", "pool")

    def total_inflight(self):
        with self._lock:
            return sum(node.inflight for node in self._nodes.values())

    def shutdown(self):
        self._runtime.shutdown()

    def _rotating_for(self, descriptor):
        if self.endpoint_mode == "rotating":
            return True
        if self.endpoint_mode == "fixed":
            return False
        return descriptor.backend == "native" and "{account}" in descriptor.canonical_uri

    def _remember_diagnostics(self, source, result):
        self._source_diagnostics[source] = result.as_dict()
        if result.skipped:
            self.log("[!] %s 跳过 %s 个无法解析的节点" % (source, result.skipped))

    def _read_file_source(self):
        path = str(self.config.get("proxy_pool_file") or "").strip()
        if not path:
            return []
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(_ROOT, path)
        with open(path, "r", encoding="utf-8-sig") as handle:
            try:
                result = parse_subscription_source(handle.read())
            except ProxyProtocolError as exc:
                raise ProxyPoolError(str(exc)) from exc
        self._remember_diagnostics("file", result)
        return [("file", item) for item in result.nodes]

    def _fetch_subscription(self):
        url = str(self.config.get("proxy_pool_subscription_url") or "").strip()
        if not url:
            return []
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ProxyPoolError("代理订阅必须是有效的 http/https URL")
        via = str(self.config.get("proxy_pool_subscription_proxy") or "").strip()
        if via:
            via = normalize_proxy_url(via)
        proxies = {"http": via, "https": via} if via else {}
        current_url = url
        response = None
        for redirect_count in range(4):
            try:
                response = requests.get(
                    current_url,
                    proxies=proxies,
                    timeout=min(max(self.probe_timeout, 5), 60),
                    allow_redirects=False,
                    headers={"Accept": "text/plain, text/*;q=0.9, */*;q=0.1"},
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
        if response is None or not 200 <= int(response.status_code) < 300:
            status_code = int(getattr(response, "status_code", 0) or 0)
            raise ProxyPoolError("代理订阅返回 HTTP %s" % status_code)
        body = str(response.text or "")
        if len(body.encode("utf-8", "ignore")) > _MAX_SOURCE_BYTES:
            raise ProxyPoolError("代理订阅内容超过 2 MiB 限制")
        try:
            result = parse_subscription_source(body)
        except ProxyProtocolError as exc:
            raise ProxyPoolError(str(exc)) from exc
        self._remember_diagnostics("subscription", result)
        return [("subscription", item) for item in result.nodes]

    def _source_entries(self):
        if self.mode == "single":
            try:
                descriptor = parse_proxy_line(self.config.get("proxy"))
            except ProxyProtocolError as exc:
                raise ProxyPoolError(str(exc)) from exc
            return [("single", descriptor)]
        if self.mode != "pool":
            return []
        values = []
        errors = []
        self._source_diagnostics = {}
        for loader in (self._read_file_source, self._fetch_subscription):
            try:
                values.extend(loader())
            except Exception as exc:
                errors.append(safe_proxy_error_text(exc))
        unique = []
        seen = set()
        for source, descriptor in values:
            if descriptor.node_id not in seen:
                seen.add(descriptor.node_id)
                unique.append((source, descriptor))
        if not unique:
            detail = "; ".join(errors) if errors else "未配置代理池文件或订阅"
            raise ProxyPoolError("代理池没有可用节点: %s" % detail)
        if errors:
            self._source_diagnostics["errors"] = list(errors)
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
            previous = self._nodes
            updated = {}
            for source, descriptor in entries:
                node_id = descriptor.node_id
                old = previous.get(node_id)
                if old is not None:
                    old.source = source
                    old.proxy_url = descriptor.canonical_uri
                    old.descriptor = descriptor
                    old.protocol = descriptor.protocol
                    old.name = descriptor.name
                    old.backend = descriptor.backend
                    old.rotating = self._rotating_for(descriptor)
                    old.retired = False
                    updated[node_id] = old
                else:
                    updated[node_id] = ProxyNode(
                        id=node_id,
                        source=source,
                        proxy_url=descriptor.canonical_uri,
                        descriptor=descriptor,
                        protocol=descriptor.protocol,
                        name=descriptor.name,
                        backend=descriptor.backend,
                        rotating=self._rotating_for(descriptor),
                    )
            for node_id, old in previous.items():
                if node_id not in updated and old.inflight > 0:
                    old.retired = True
                    updated[node_id] = old
            self._nodes = updated
            self._last_refresh = now
            self._condition.notify_all()
        return self.snapshot()

    def refresh_if_due(self):
        if not self.managed:
            return
        now = time.time()
        with self._lock:
            due = self.refresh_interval > 0 and now - self._last_refresh >= self.refresh_interval
        if due:
            try:
                self.reload_sources(force=True)
            except Exception as exc:
                self.log("[!] 代理池刷新失败，继续使用当前节点: %s" % safe_proxy_error_text(exc))

    def _schedule_periodic_probe_if_due(self):
        if not self.managed or self.probe_interval <= 0:
            return
        now = time.time()
        with self._lock:
            if self._probe_all_running or now - self._last_probe_all < self.probe_interval:
                return
            self._probe_all_running = True
            self._last_probe_all = now

        def runner():
            try:
                self.probe_all(force=True)
            finally:
                with self._lock:
                    self._probe_all_running = False

        threading.Thread(target=runner, name="proxy-probe-all", daemon=True).start()

    def _eligible_locked(self, now):
        values = []
        for node in self._nodes.values():
            if not node.enabled or node.retired:
                continue
            if node.inflight >= self.capacity:
                continue
            if not node.rotating and node.cooldown_until is not None and now < node.cooldown_until:
                continue
            values.append(node)
        return values

    def _select_locked(self, nodes, affinity):
        nodes = sorted(nodes, key=lambda value: value.id)
        digest = hashlib.sha256(str(affinity or "").encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % len(nodes)
        selected = nodes[index]
        if selected.health >= 0.8 or len(nodes) == 1:
            return selected
        return max(nodes, key=lambda value: (value.health, -value.inflight, value.id))

    def _fallback_lease_locked(self, worker_key, slot_index, attempt_index, affinity, session_key):
        if self.fallback == "direct":
            return ProxyLease("direct", "", worker_key, slot_index, attempt_index, affinity, session_key, protocol="direct")
        if self.fallback == "single":
            proxy_url = normalize_proxy_url(self.config.get("proxy"))
            if proxy_url:
                endpoint = _expand_account_placeholder(proxy_url, session_key)
                return ProxyLease("fallback-single", endpoint, worker_key, slot_index, attempt_index, affinity, session_key, source_uri=proxy_url, protocol=urllib.parse.urlsplit(proxy_url).scheme)
        return None

    def _resolve_node_endpoint(self, node, session_key):
        descriptor = node.descriptor
        if descriptor.backend == "native":
            return _expand_account_placeholder(descriptor.canonical_uri, session_key), None
        return self._runtime.acquire(descriptor)

    def _rollback_runtime_failure(self, node_id, error):
        with self._condition:
            node = self._nodes.get(node_id)
            if node is not None:
                node.inflight = max(0, node.inflight - 1)
                node.enabled = False
                node.probe_status = "unavailable"
                node.last_error = "backend: %s" % safe_proxy_error_text(error)[:300]
            self._condition.notify_all()

    def acquire(self, affinity, worker_key, slot_index, attempt_index, session_key, timeout=None, cancel_callback=None):
        if not self.managed:
            return None
        self.refresh_if_due()
        self._schedule_periodic_probe_if_due()
        deadline = time.time() + float(timeout if timeout is not None else self.acquire_timeout)
        last_runtime_error = None
        while True:
            selected = None
            with self._condition:
                while selected is None:
                    if cancel_callback and cancel_callback():
                        raise ProxyAcquireCancelled("代理租约等待已取消")
                    now = time.time()
                    eligible = self._eligible_locked(now)
                    if eligible:
                        selected = self._select_locked(eligible, affinity)
                        selected.inflight += 1
                        break
                    active_nodes = [node for node in self._nodes.values() if node.enabled and not node.retired]
                    if not active_nodes:
                        fallback = self._fallback_lease_locked(worker_key, slot_index, attempt_index, affinity, session_key)
                        if fallback is not None:
                            return fallback
                        detail = ": %s" % last_runtime_error if last_runtime_error else ""
                        raise ProxyPoolError("代理池当前没有可用节点%s" % detail)
                    remaining = deadline - now
                    if remaining <= 0:
                        fallback = self._fallback_lease_locked(worker_key, slot_index, attempt_index, affinity, session_key)
                        if fallback is not None:
                            return fallback
                        raise ProxyAcquireTimeout("等待可用代理超时")
                    wake_after = min(remaining, 1.0)
                    cooldowns = [node.cooldown_until for node in active_nodes if node.cooldown_until and node.cooldown_until > now]
                    if cooldowns:
                        wake_after = min(wake_after, max(0.05, min(cooldowns) - now))
                    self._condition.wait(timeout=wake_after)
            try:
                endpoint, runtime_key = self._resolve_node_endpoint(selected, session_key)
                return ProxyLease(
                    selected.id, endpoint, worker_key, slot_index, attempt_index, affinity, session_key,
                    source_uri=selected.descriptor.raw_uri,
                    protocol=selected.protocol,
                    runtime_key=runtime_key,
                )
            except Exception as exc:
                last_runtime_error = safe_proxy_error_text(exc)
                self._rollback_runtime_failure(selected.id, exc)
                if time.time() >= deadline:
                    raise ProxyPoolError("代理协议运行时不可用: %s" % last_runtime_error) from exc

    def release(self, lease):
        if lease is None or lease.released:
            return
        lease.released = True
        if lease.runtime_key:
            self._runtime.release(lease.runtime_key)
        if lease.node_id in ("direct", "fallback-single"):
            return
        with self._condition:
            node = self._nodes.get(lease.node_id)
            if node is not None:
                node.inflight = max(0, node.inflight - 1)
                if node.retired and node.inflight == 0:
                    self._nodes.pop(node.id, None)
            self._condition.notify_all()

    def report_success(self, lease):
        if lease is None or lease.node_id in ("direct", "fallback-single"):
            return
        with self._condition:
            node = self._nodes.get(lease.node_id)
            if node is None:
                return
            node.health = min(1.0, node.health + 0.1)
            node.failure_count = 0
            node.cooldown_until = None
            node.last_error = ""
            node.probe_status = "healthy" if node.probe_status == "unavailable" else node.probe_status
            self._condition.notify_all()

    def report_soft_failure(self, lease, error):
        if lease is None or lease.node_id in ("direct", "fallback-single"):
            return
        with self._lock:
            node = self._nodes.get(lease.node_id)
            if node is not None:
                node.last_error = "soft: %s" % safe_proxy_error_text(error)[:300]

    def report_transport_failure(self, lease, error):
        if lease is None or lease.node_id in ("direct", "fallback-single"):
            return
        node_for_probe = None
        with self._condition:
            node = self._nodes.get(lease.node_id)
            if node is None:
                return
            if node.rotating:
                node.last_error = "transport: rotating exit"
                return
            node.failure_count += 1
            node.health = max(0.05, node.health * 0.7)
            exponent = min(max(node.failure_count - 1, 0), 4)
            cooldown = min(600, 30 * (2 ** exponent))
            node.cooldown_until = time.time() + cooldown
            node.last_error = "transport"
            node_for_probe = node.id
            self._condition.notify_all()
        if node_for_probe:
            self._schedule_failure_probe(node_for_probe)

    def _probe_endpoint(self):
        if self.probe_provider == "ipinfo":
            return "https://ipinfo.io/json"
        return "https://www.cloudflare.com/cdn-cgi/trace"

    def _parse_probe_ip(self, response):
        text = str(response.text or "")
        if self.probe_provider == "ipinfo":
            try:
                value = response.json()
                return str(value.get("ip") or "").strip() if isinstance(value, dict) else ""
            except Exception:
                return ""
        for line in text.splitlines():
            if line.startswith("ip="):
                return line[3:].strip()
        return ""

    def probe_node(self, node_id):
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                raise ProxyPoolError("代理节点不存在")
            descriptor = node.descriptor
            session_key = secrets.token_hex(8)
        runtime_key = None
        started = time.monotonic()
        status = "unhealthy"
        exit_ip = ""
        error = ""
        try:
            if descriptor.backend == "native":
                proxy_url = _expand_account_placeholder(descriptor.canonical_uri, session_key)
            else:
                proxy_url, runtime_key = self._runtime.acquire(descriptor)
            response = requests.get(
                self._probe_endpoint(),
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=self.probe_timeout,
                allow_redirects=False,
            )
            if not 200 <= int(response.status_code) < 300:
                raise ProxyPoolError("HTTP %s" % response.status_code)
            exit_ip = self._parse_probe_ip(response)
            status = "healthy"
        except Exception as exc:
            error = safe_proxy_error_text(exc)
        finally:
            if runtime_key:
                self._runtime.release(runtime_key)
        latency = int((time.monotonic() - started) * 1000)
        with self._condition:
            node = self._nodes.get(node_id)
            if node is not None:
                node.probe_status = status
                node.last_probed_at = time.time()
                node.probe_latency_ms = latency
                node.exit_ip = exit_ip
                if status == "healthy":
                    node.enabled = True
                    node.health = max(node.health, 0.8)
                    if node.last_error == "transport" or node.last_error.startswith("backend:"):
                        node.health = 1.0
                        node.failure_count = 0
                        node.cooldown_until = None
                        node.last_error = ""
                elif not node.rotating:
                    node.last_error = node.last_error or ("probe: %s" % error[:300])
                self._condition.notify_all()
        return {"id": node_id, "status": status, "latency_ms": latency, "exit_ip": exit_ip, "error": error}

    def _schedule_failure_probe(self, node_id):
        with self._lock:
            existing = self._probe_events.get(node_id)
            if existing is not None and not existing.is_set():
                return
            event = threading.Event()
            self._probe_events[node_id] = event

        def runner():
            try:
                self.probe_node(node_id)
            except Exception:
                pass
            finally:
                event.set()
                with self._lock:
                    if self._probe_events.get(node_id) is event:
                        self._probe_events.pop(node_id, None)

        threading.Thread(target=runner, name="proxy-probe-%s" % node_id[:8], daemon=True).start()

    def probe_all(self, force=False):
        now = time.time()
        with self._lock:
            if not force and self.probe_interval > 0 and now - self._last_probe_all < self.probe_interval:
                return []
            node_ids = [node.id for node in self._nodes.values() if not node.retired]
            self._last_probe_all = now
        results = []
        if not node_ids:
            return results
        with ThreadPoolExecutor(max_workers=min(8, len(node_ids)), thread_name_prefix="proxy-probe") as executor:
            futures = {executor.submit(self.probe_node, node_id): node_id for node_id in node_ids}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append({"id": futures[future], "status": "unhealthy", "error": safe_proxy_error_text(exc)})
        return results

    def snapshot(self):
        with self._lock:
            now = time.time()
            nodes = []
            for node in sorted(self._nodes.values(), key=lambda value: value.id):
                cooldown = 0
                if node.cooldown_until is not None and node.cooldown_until > now:
                    cooldown = int(max(1, node.cooldown_until - now))
                nodes.append({
                    "id": node.id,
                    "source": node.source,
                    "proxy": node.descriptor.raw_uri,
                    "name": node.name,
                    "protocol": node.protocol,
                    "backend": node.backend,
                    "enabled": bool(node.enabled),
                    "rotating": bool(node.rotating),
                    "health": round(float(node.health), 3),
                    "failure_count": int(node.failure_count),
                    "cooldown_sec": cooldown,
                    "last_error": str(node.last_error or "")[:300],
                    "probe_status": node.probe_status,
                    "probe_latency_ms": int(node.probe_latency_ms or 0),
                    "exit_ip": node.exit_ip,
                    "inflight": int(node.inflight),
                    "retired": bool(node.retired),
                })
            return {
                "mode": self.mode,
                "managed": self.managed,
                "fallback": self.fallback,
                "capacity": self.capacity,
                "nodes": nodes,
                "sources": dict(self._source_diagnostics),
                "runtime": self._runtime.active_snapshot(),
            }


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
            old = _MANAGER
            _MANAGER = ProxyPoolManager(config, log=log)
            old.shutdown()
        elif log is not None:
            _MANAGER.log = log
            _MANAGER._runtime.log = log
        return _MANAGER


def reset_manager():
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is not None and _MANAGER.total_inflight() > 0:
            raise ProxyPoolError("仍有代理租约使用中，不能重置代理池")
        old = _MANAGER
        _MANAGER = None
    if old is not None:
        old.shutdown()


def current_proxy_lease():
    return getattr(_TLS, "lease", None)


def current_proxy_url():
    lease = current_proxy_lease()
    return None if lease is None else str(lease.proxy_url or "")


def managed_proxy_active():
    return current_proxy_lease() is not None


def begin_registration_slot(slot_index, attempt_index=1, worker_key=None, log=None, cancel_callback=None):
    if current_proxy_lease() is not None:
        raise ProxyPoolError("当前线程已有未释放的代理租约")
    manager = get_manager(log=log)
    if not manager.managed:
        return None
    worker = str(worker_key or threading.current_thread().name or "worker")
    slot = int(slot_index)
    attempt = int(attempt_index)
    affinity = "%s:slot:%s" % (worker, slot)
    session_seed = "%s:%s:%s:%s" % (worker, slot, attempt, secrets.token_hex(8))
    session_key = hashlib.sha256(session_seed.encode("utf-8")).hexdigest()[:16]
    lease = manager.acquire(
        affinity=affinity,
        worker_key=worker,
        slot_index=slot,
        attempt_index=attempt,
        session_key=session_key,
        cancel_callback=cancel_callback,
    )
    _TLS.lease = lease
    if log is not None:
        label = lease.source_uri or lease.proxy_url
        log("[*] 当前账号代理: %s" % proxy_log_label(label))
        if lease.source_uri and lease.proxy_url and lease.source_uri != lease.proxy_url:
            log("[*] 当前代理本地出口: %s" % lease.proxy_url)
    return lease


def end_registration_slot(success=False, transport_error=None):
    lease = current_proxy_lease()
    if lease is None:
        return
    manager = get_manager()
    try:
        if transport_error is not None:
            manager.report_transport_failure(lease, transport_error)
        elif success:
            manager.report_success(lease)
    finally:
        manager.release(lease)
        _TLS.lease = None


def report_current_transport_failure(error):
    lease = current_proxy_lease()
    if lease is None:
        return
    get_manager().report_transport_failure(lease, error)


def manager_snapshot(config=None):
    try:
        return get_manager(config=config).snapshot()
    except Exception as exc:
        return {
            "mode": str((config or {}).get("proxy_mode") or "auto"),
            "managed": False,
            "nodes": [],
            "sources": {},
            "error": safe_proxy_error_text(exc),
        }
