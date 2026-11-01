import os
import threading
import tempfile
import time
import unittest
from unittest.mock import patch

from proxy_protocol_runtime import ProtocolRuntimeManager, ProxyRuntimeError, RuntimeEntry
from proxy_protocols import parse_proxy_line


class FakeProcess:
    def __init__(self):
        self.code = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self.code = -9


class ProtocolRuntimeTests(unittest.TestCase):
    def test_plain_http_descriptor_returns_direct_endpoint_without_runtime(self):
        manager = ProtocolRuntimeManager({"proxy_protocol_backend": "auto"})
        node = parse_proxy_line("http://127.0.0.1:8080")
        endpoint, key = manager.acquire(node)
        self.assertEqual(endpoint, node.canonical_uri)
        self.assertIsNone(key)
        self.assertEqual(manager.active_snapshot(), {})

    def test_socks_descriptor_is_normalized_to_local_http_endpoint_and_cached_idle(self):
        manager = ProtocolRuntimeManager({"proxy_protocol_backend": "auto", "proxy_runtime_idle_ttl_sec": 120})
        node = parse_proxy_line("socks5://user:pass@127.0.0.1:1080")
        endpoint, key = manager.acquire(node)
        self.assertTrue(endpoint.startswith("http://127.0.0.1:"))
        self.assertIs(key, manager._entries[node.node_id])
        state = manager.active_snapshot()[node.node_id]
        self.assertEqual(state["kind"], "bridge")
        self.assertTrue(state["alive"])
        self.assertEqual(state["refcount"], 1)
        manager.release(key)
        self.assertEqual(manager.active_snapshot()[node.node_id]["refcount"], 0)
        manager.shutdown()
        self.assertEqual(manager.active_snapshot(), {})

    def test_idle_ttl_zero_stops_runtime_immediately(self):
        manager = ProtocolRuntimeManager({"proxy_protocol_backend": "auto", "proxy_runtime_idle_ttl_sec": 0})
        self.assertEqual(manager.idle_ttl, 0)
        node = parse_proxy_line("socks5://127.0.0.1:1080")
        endpoint, key = manager.acquire(node)
        self.assertTrue(endpoint.startswith("http://127.0.0.1:"))
        manager.release(key)
        self.assertEqual(manager.active_snapshot(), {})

    def test_build_config_exposes_local_http_and_advanced_outbound(self):
        manager = ProtocolRuntimeManager({})
        node = parse_proxy_line("trojan://secret@trojan.example.com:443?sni=trojan.example.com")
        value = manager._build_config(node, 32123)
        self.assertEqual(value["inbounds"][0]["type"], "http")
        self.assertEqual(value["inbounds"][0]["listen"], "127.0.0.1")
        self.assertEqual(value["inbounds"][0]["listen_port"], 32123)
        self.assertEqual(value["outbounds"][0]["type"], "trojan")
        self.assertEqual(value["outbounds"][0]["tag"], "proxy")
        self.assertEqual(value["route"]["final"], "proxy")

    def test_advanced_runtime_is_lazy_reused_then_cached_idle(self):
        manager = ProtocolRuntimeManager({"proxy_runtime_idle_ttl_sec": 120})
        node = parse_proxy_line("vless://11111111-1111-1111-1111-111111111111@a.example.com:443?security=tls")
        fd, path = tempfile.mkstemp()
        os.close(fd)
        process = FakeProcess()
        entry = RuntimeEntry(node.node_id, process, 32001, path, 0)
        with patch.object(manager, "_start_entry", return_value=entry) as start, patch.object(manager, "_stop_entry") as stop:
            first, first_key = manager.acquire(node)
            second, second_key = manager.acquire(node)
            self.assertEqual(first, "http://127.0.0.1:32001")
            self.assertEqual(second, first)
            self.assertIs(first_key, entry)
            self.assertIs(second_key, entry)
            self.assertEqual(start.call_count, 1)
            self.assertEqual(manager.active_snapshot()[node.node_id]["refcount"], 2)
            manager.release(first_key)
            self.assertEqual(manager.active_snapshot()[node.node_id]["refcount"], 1)
            manager.release(second_key)
            self.assertEqual(manager.active_snapshot()[node.node_id]["refcount"], 0)
            self.assertEqual(stop.call_count, 0)
            manager._entries[node.node_id].idle_since = time.time() - 121
            manager.cleanup_idle()
            self.assertEqual(manager.active_snapshot(), {})
            stop.assert_called_once()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    def test_concurrent_acquire_waits_for_and_reuses_one_runtime_start(self):
        manager = ProtocolRuntimeManager({"proxy_runtime_idle_ttl_sec": 120})
        node = parse_proxy_line("socks5://127.0.0.1:1080")
        starter_entered = threading.Event()
        waiter_entered = threading.Event()
        waiter_returned = threading.Event()
        finish_start = threading.Event()
        original_wait = manager._condition.wait
        process = FakeProcess()
        entry = RuntimeEntry(node.node_id, process, 32002, "", 0)
        results = []
        errors = []

        def starter(_descriptor):
            starter_entered.set()
            if not finish_start.wait(timeout=3):
                raise AssertionError("test did not release the runtime starter")
            return entry

        def tracked_wait(timeout=None):
            waiter_entered.set()
            result = original_wait(timeout)
            waiter_returned.set()
            return result

        def acquire():
            try:
                results.append(manager.acquire(node))
            except Exception as exc:
                errors.append(exc)

        with patch.object(manager, "_start_bridge_entry", side_effect=starter), \
             patch.object(manager._condition, "wait", side_effect=tracked_wait):
            first = threading.Thread(target=acquire)
            second = threading.Thread(target=acquire)
            first.start()
            self.assertTrue(starter_entered.wait(timeout=2))
            second.start()
            self.assertTrue(waiter_entered.wait(timeout=2))
            returned_before_start = waiter_returned.wait(timeout=0.3)
            finish_start.set()
            first.join(timeout=3)
            second.join(timeout=3)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertFalse(returned_before_start)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0], results[1][0])
        self.assertIs(results[0][1], entry)
        self.assertIs(results[1][1], entry)
        self.assertEqual(manager.active_snapshot()[node.node_id]["refcount"], 2)
        manager.shutdown()

    def test_native_only_allows_native_bridge_but_rejects_advanced_protocols(self):
        manager = ProtocolRuntimeManager({"proxy_protocol_backend": "native-only", "proxy_runtime_idle_ttl_sec": 0})
        socks = parse_proxy_line("socks5://127.0.0.1:1080")
        endpoint, key = manager.acquire(socks)
        try:
            self.assertTrue(endpoint.startswith("http://127.0.0.1:"))
        finally:
            manager.release(key)
        node = parse_proxy_line("trojan://secret@a.example.com:443?sni=a.example.com")
        with self.assertRaises(ProxyRuntimeError):
            manager.acquire(node)

    def test_missing_singbox_reports_actionable_error(self):
        manager = ProtocolRuntimeManager({"proxy_protocol_backend": "auto", "proxy_singbox_path": ""})
        with patch("proxy_protocol_runtime.shutil.which", return_value=None):
            with self.assertRaises(ProxyRuntimeError) as raised:
                manager._find_executable()
        self.assertIn("sing-box", str(raised.exception))
        self.assertIn("proxy_singbox_path", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
