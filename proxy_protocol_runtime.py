"""Lazy local runtime that exposes every managed proxy through a common endpoint."""
from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from proxy_bridge import LocalProxyBridge, proxy_has_auth
from proxy_protocols import ProxyDescriptor


class ProxyRuntimeError(RuntimeError):
    pass


@dataclass
class RuntimeEntry:
    node_id: str
    process: Optional[subprocess.Popen]
    port: int
    config_path: str
    refcount: int = 0
    bridge: Optional[LocalProxyBridge] = None
    kind: str = "sing-box"
    idle_since: Optional[float] = None
    last_used: float = 0.0

    @property
    def proxy_url(self):
        return "http://127.0.0.1:%s" % self.port

    @property
    def alive(self):
        if self.kind == "bridge":
            return bool(self.bridge is not None and self.bridge.server is not None)
        return bool(self.process is not None and self.process.poll() is None)

    def diagnostic(self):
        if self.bridge is None:
            return None
        return self.bridge.diagnostic()


class ProtocolRuntimeManager:
    """Resolve native and advanced nodes into consumer-compatible endpoints."""

    def __init__(self, config=None, log=None):
        self.config = dict(config or {})
        self.log = log or (lambda _message: None)
        self.backend = str(self.config.get("proxy_protocol_backend") or "auto").strip().lower()
        self.executable = str(self.config.get("proxy_singbox_path") or "").strip()
        self.start_timeout = max(3, int(self.config.get("proxy_protocol_start_timeout_sec") or 10))
        self.idle_ttl = max(0, int(self.config.get("proxy_runtime_idle_ttl_sec", 120)))
        self.cache_max = max(1, int(self.config.get("proxy_runtime_cache_max", 32)))
        self._condition = threading.Condition(threading.RLock())
        self._entries = {}
        self._starting = set()

    def _find_executable(self):
        if self.backend == "native-only":
            raise ProxyRuntimeError("高级代理协议已被 proxy_protocol_backend=native-only 禁用")
        candidate = os.path.expanduser(self.executable) if self.executable else shutil.which("sing-box")
        if not candidate:
            raise ProxyRuntimeError("检测到高级代理节点，但未找到 sing-box；请安装到 PATH 或配置 proxy_singbox_path")
        if not os.path.isfile(candidate):
            resolved = shutil.which(candidate)
            if not resolved:
                raise ProxyRuntimeError("sing-box 可执行文件不存在: %s" % candidate)
            candidate = resolved
        return candidate

    @staticmethod
    def _free_port():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])
        finally:
            sock.close()

    @staticmethod
    def _build_config(descriptor, port):
        outbound = copy.deepcopy(descriptor.outbound_config or {})
        if not outbound:
            raise ProxyRuntimeError("高级代理节点缺少 outbound 配置")
        outbound["tag"] = "proxy"
        return {
            "log": {"level": "warn", "timestamp": True},
            "inbounds": [{"type": "http", "tag": "local-http", "listen": "127.0.0.1", "listen_port": int(port)}],
            "outbounds": [outbound],
            "route": {"final": "proxy"},
        }

    @staticmethod
    def _write_config(value):
        fd, path = tempfile.mkstemp(prefix="grok-register-proxy-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
            return path
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            try:
                os.unlink(path)
            except Exception:
                pass
            raise

    def _check_config(self, executable, path):
        try:
            completed = subprocess.run(
                [executable, "check", "-c", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=self.start_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProxyRuntimeError("sing-box 配置检查超时") from exc
        except Exception as exc:
            raise ProxyRuntimeError("无法执行 sing-box check: %s" % exc) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "configuration rejected").strip()
            raise ProxyRuntimeError("sing-box 配置检查失败: %s" % detail[:500])

    @staticmethod
    def _port_ready(port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            return sock.connect_ex(("127.0.0.1", int(port))) == 0
        finally:
            sock.close()

    def _start_entry(self, descriptor):
        executable = self._find_executable()
        errors = []
        for attempt in range(1, 5):
            port = self._free_port()
            path = self._write_config(self._build_config(descriptor, port))
            process = None
            try:
                self._check_config(executable, path)
                process = subprocess.Popen([executable, "run", "-c", path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.time() + self.start_timeout
                while time.time() < deadline:
                    code = process.poll()
                    if code is not None:
                        raise ProxyRuntimeError("sing-box 在本地代理就绪前退出，code=%s" % code)
                    if self._port_ready(port):
                        self.log("[*] 高级代理运行时已就绪: %s -> 127.0.0.1:%s" % (descriptor.protocol, port))
                        now = time.time()
                        return RuntimeEntry(descriptor.node_id, process, port, path, 0, last_used=now)
                    time.sleep(0.05)
                raise ProxyRuntimeError("等待 sing-box 本地代理启动超时")
            except Exception as exc:
                errors.append("attempt=%s port=%s: %s" % (attempt, port, exc))
                if process is not None:
                    try:
                        process.terminate(); process.wait(timeout=2)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass
                try:
                    os.unlink(path)
                except Exception:
                    pass
        raise ProxyRuntimeError("sing-box startup failed after 4 attempts: %s" % "; ".join(errors))

    def _start_bridge_entry(self, descriptor):
        bridge = LocalProxyBridge(descriptor.canonical_uri)
        try:
            endpoint = bridge.start()
        except Exception as exc:
            raise ProxyRuntimeError("本地 HTTP 代理桥启动失败: %s" % exc) from exc
        try:
            port = int(urllib.parse.urlsplit(endpoint).port or 0)
        except Exception:
            port = 0
        if port <= 0:
            bridge.stop()
            raise ProxyRuntimeError("本地 HTTP 代理桥未返回有效端口")
        self.log("[*] 原生代理已标准化为本地 HTTP 出口: %s -> %s" % (descriptor.protocol, endpoint))
        return RuntimeEntry(descriptor.node_id, None, port, "", 0, bridge=bridge, kind="bridge", last_used=time.time())

    @staticmethod
    def _stop_entry(entry):
        if entry.kind == "bridge":
            if entry.bridge is not None:
                try:
                    entry.bridge.stop()
                except Exception:
                    pass
            return
        try:
            if entry.process is not None and entry.process.poll() is None:
                entry.process.terminate()
                try:
                    entry.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    entry.process.kill(); entry.process.wait(timeout=2)
        except Exception:
            pass
        if entry.config_path:
            try:
                os.unlink(entry.config_path)
            except Exception:
                pass

    @staticmethod
    def _native_requires_bridge(descriptor):
        parsed = urllib.parse.urlsplit(descriptor.canonical_uri)
        scheme = (parsed.scheme or "http").lower()
        return scheme != "http" or proxy_has_auth(descriptor.canonical_uri)

    def _cleanup_locked(self, now=None):
        now = time.time() if now is None else float(now)
        stale = []
        for key, entry in list(self._entries.items()):
            if not entry.alive:
                stale.append(self._entries.pop(key))
            elif entry.refcount == 0 and entry.idle_since is not None and (self.idle_ttl == 0 or now - entry.idle_since >= self.idle_ttl):
                stale.append(self._entries.pop(key))
        idle = sorted(
            ((key, value) for key, value in self._entries.items() if value.refcount == 0),
            key=lambda pair: pair[1].last_used,
        )
        while len(self._entries) > self.cache_max and idle:
            key, entry = idle.pop(0)
            if self._entries.pop(key, None) is entry:
                stale.append(entry)
        return stale

    def cleanup_idle(self):
        with self._condition:
            stale = self._cleanup_locked()
        for entry in stale:
            self._stop_entry(entry)
        return len(stale)

    def _acquire_runtime_entry(self, descriptor, starter):
        while True:
            stale = []
            with self._condition:
                stale = self._cleanup_locked()
                current = self._entries.get(descriptor.node_id)
                if current is not None and current.alive:
                    current.refcount += 1
                    current.idle_since = None
                    current.last_used = time.time()
                    endpoint = current.proxy_url
                    runtime_key = descriptor.node_id
                    self._condition.notify_all()
                    break
                if descriptor.node_id not in self._starting:
                    self._starting.add(descriptor.node_id)
                    endpoint = runtime_key = None
                    break
                self._condition.wait(timeout=0.2)
            for entry in stale:
                self._stop_entry(entry)
            if endpoint is not None:
                return endpoint, runtime_key
        for entry in stale:
            self._stop_entry(entry)
        if endpoint is not None:
            return endpoint, runtime_key
        try:
            entry = starter(descriptor)
            entry.refcount = 1
            entry.last_used = time.time()
            with self._condition:
                self._entries[descriptor.node_id] = entry
                return entry.proxy_url, descriptor.node_id
        finally:
            with self._condition:
                self._starting.discard(descriptor.node_id)
                self._condition.notify_all()

    def acquire(self, descriptor):
        if descriptor.backend == "native":
            if not self._native_requires_bridge(descriptor):
                return descriptor.canonical_uri, None
            return self._acquire_runtime_entry(descriptor, self._start_bridge_entry)
        if descriptor.backend != "sing-box":
            raise ProxyRuntimeError("未知高级代理后端: %s" % descriptor.backend)
        return self._acquire_runtime_entry(descriptor, self._start_entry)

    def release(self, runtime_key):
        if not runtime_key:
            return
        stop_now = None
        with self._condition:
            current = self._entries.get(runtime_key)
            if current is None:
                return
            current.refcount = max(0, current.refcount - 1)
            current.last_used = time.time()
            if current.refcount == 0:
                current.idle_since = current.last_used
                if self.idle_ttl == 0:
                    stop_now = self._entries.pop(runtime_key, None)
            self._condition.notify_all()
        if stop_now is not None:
            self._stop_entry(stop_now)

    def diagnostic_for(self, runtime_key, max_age=15):
        if not runtime_key:
            return None
        with self._condition:
            entry = self._entries.get(runtime_key)
            diagnostic = None if entry is None else entry.diagnostic()
        if not diagnostic:
            return None
        if time.time() - float(diagnostic.get("at") or 0) > float(max_age):
            return None
        return diagnostic

    def active_snapshot(self):
        self.cleanup_idle()
        with self._condition:
            now = time.time()
            return {
                key: {
                    "port": value.port,
                    "refcount": value.refcount,
                    "alive": value.alive,
                    "kind": value.kind,
                    "idle_sec": int(max(0, now - value.idle_since)) if value.idle_since else 0,
                    "diagnostic": value.diagnostic(),
                }
                for key, value in self._entries.items()
            }

    def shutdown(self):
        with self._condition:
            entries = list(self._entries.values())
            self._entries.clear()
            self._starting.clear()
            self._condition.notify_all()
        for entry in entries:
            self._stop_entry(entry)
