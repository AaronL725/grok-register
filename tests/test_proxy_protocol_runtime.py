import os
import tempfile
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
    def test_native_descriptor_returns_direct_endpoint_without_process(self):
        manager = ProtocolRuntimeManager({"proxy_protocol_backend": "auto"})
        node = parse_proxy_line("socks5://user:pass@127.0.0.1:1080")
        endpoint, key = manager.acquire(node)
        self.assertEqual(endpoint, node.canonical_uri)
        self.assertIsNone(key)
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

    def test_advanced_runtime_is_lazy_reused_and_stopped_at_zero_refs(self):
        manager = ProtocolRuntimeManager({})
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
            self.assertEqual(first_key, node.node_id)
            self.assertEqual(second_key, node.node_id)
            self.assertEqual(start.call_count, 1)
            self.assertEqual(manager.active_snapshot()[node.node_id]["refcount"], 2)
            manager.release(first_key)
            self.assertEqual(manager.active_snapshot()[node.node_id]["refcount"], 1)
            manager.release(second_key)
            self.assertEqual(manager.active_snapshot(), {})
            stop.assert_called_once()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    def test_native_only_rejects_advanced_protocols(self):
        manager = ProtocolRuntimeManager({"proxy_protocol_backend": "native-only"})
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
