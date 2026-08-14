"""Lazy local runtime that exposes advanced proxy nodes as localhost HTTP proxies."""
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
from dataclasses import dataclass
from typing import Optional

from proxy_protocols import ProxyDescriptor


class ProxyRuntimeError(RuntimeError):
    pass


@dataclass
class RuntimeEntry:
    node_id: str
    process: subprocess.Popen
    port: int
    config_path: str
    refcount: int = 0

    @property
    def proxy_url(self):
        return "http://127.0.0.1:%s" % self.port


class ProtocolRuntimeManager:
    def __init__(self, config=None, log=None):
        self.config = dict(config or {})
        self.log = log or (lambda _message: None)
        self.backend = str(self.config.get("proxy_protocol_backend") or "auto").strip().lower()
        self.executable = str(self.config.get("proxy_singbox_path") or "").strip()
        self.start_timeout = max(3, int(self.config.get("proxy_protocol_start_timeout_sec") or 10))
        self._condition = threading.Condition(threading.RLock())
        self._entries = {}
        self._starting = set()

    def _find_executable(self):
        if self.backend == "native-only":
            raise ProxyRuntimeError("高级代理协议已被 proxy_protocol_backend=native-only 禁用")
        candidate = os.path.expanduser(self.executable) if self.executable else shutil.which("sing-box")
        if not candidate:
            raise ProxyRuntimeError("检测到 VLESS/VMess/Trojan/Hysteria2/TUIC 节点，但未找到 sing-box；请安装到 PATH 或配置 proxy_singbox_path")
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
            "inbounds": [
                {
                    "type": "http",
                    "tag": "local-http",
                    "listen": "127.0.0.1",
                    "listen_port": int(port),
                }
            ],
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
                [executable, "check", "-c", path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.start_timeout,
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
        port = self._free_port()
        path = self._write_config(self._build_config(descriptor, port))
        process = None
        try:
            self._check_config(executable, path)
            process = subprocess.Popen(
                [executable, "run", "-c", path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.time() + self.start_timeout
            while time.time() < deadline:
                code = process.poll()
                if code is not None:
                    raise ProxyRuntimeError("sing-box 在本地代理就绪前退出，code=%s" % code)
                if self._port_ready(port):
                    self.log("[*] 高级代理运行时已就绪: %s -> 127.0.0.1:%s" % (descriptor.protocol, port))
                    return RuntimeEntry(descriptor.node_id, process, port, path, 0)
                time.sleep(0.05)
            raise ProxyRuntimeError("等待 sing-box 本地代理启动超时")
        except Exception:
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
            try:
                os.unlink(path)
            except Exception:
                pass
            raise

    @staticmethod
    def _stop_entry(entry):
        try:
            if entry.process.poll() is None:
                entry.process.terminate()
                try:
                    entry.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    entry.process.kill()
                    entry.process.wait(timeout=2)
        except Exception:
            pass
        try:
            os.unlink(entry.config_path)
        except Exception:
            pass

    def acquire(self, descriptor):
        if descriptor.backend == "native":
            return descriptor.canonical_uri, None
        if descriptor.backend != "sing-box":
            raise ProxyRuntimeError("未知高级代理后端: %s" % descriptor.backend)
        while True:
            with self._condition:
                current = self._entries.get(descriptor.node_id)
                if current is not None and current.process.poll() is None:
                    current.refcount += 1
                    return current.proxy_url, descriptor.node_id
                if current is not None:
                    self._entries.pop(descriptor.node_id, None)
                if descriptor.node_id not in self._starting:
                    self._starting.add(descriptor.node_id)
                    break
                self._condition.wait(timeout=0.2)
        entry = None
        try:
            entry = self._start_entry(descriptor)
            entry.refcount = 1
            with self._condition:
                self._entries[descriptor.node_id] = entry
                return entry.proxy_url, descriptor.node_id
        finally:
            with self._condition:
                self._starting.discard(descriptor.node_id)
                self._condition.notify_all()

    def release(self, runtime_key):
        if not runtime_key:
            return
        entry = None
        with self._condition:
            current = self._entries.get(runtime_key)
            if current is None:
                return
            current.refcount = max(0, current.refcount - 1)
            if current.refcount == 0:
                entry = self._entries.pop(runtime_key, None)
            self._condition.notify_all()
        if entry is not None:
            self._stop_entry(entry)

    def active_snapshot(self):
        with self._condition:
            return {
                key: {"port": value.port, "refcount": value.refcount, "alive": value.process.poll() is None}
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
