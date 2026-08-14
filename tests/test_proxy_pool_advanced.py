import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app_config import DEFAULT_CONFIG
from proxy_pool import ProxyPoolManager


class AdvancedProxyPoolIntegrationTests(unittest.TestCase):
    def config(self, **updates):
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(updates)
        return cfg

    def test_advanced_node_lease_uses_local_runtime_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.txt"
            source = "vless://11111111-1111-1111-1111-111111111111@vless.example.com:443?security=tls&sni=vless.example.com#VLESS"
            path.write_text(source + "\n", encoding="utf-8")
            manager = ProxyPoolManager(self.config(
                proxy_mode="pool", proxy_pool_file=str(path), proxy_pool_probe_interval_sec=0,
            ))
            with patch.object(manager._runtime, "acquire", return_value=("http://127.0.0.1:32001", "runtime-key")) as acquire, patch.object(manager._runtime, "release") as release:
                lease = manager.acquire("worker:slot:1", "worker", 1, 1, "session", timeout=1)
                self.assertEqual(lease.proxy_url, "http://127.0.0.1:32001")
                self.assertEqual(lease.protocol, "vless")
                self.assertEqual(lease.source_uri, source)
                self.assertEqual(lease.runtime_key, "runtime-key")
                acquire.assert_called_once()
                manager.release(lease)
                release.assert_called_once_with("runtime-key")
            self.assertEqual(manager.snapshot()["nodes"][0]["inflight"], 0)

    def test_native_socks_node_is_normalized_through_shared_runtime(self):
        manager = ProxyPoolManager(self.config(
            proxy_mode="single", proxy="socks5://user:pass@127.0.0.1:1080", proxy_pool_probe_interval_sec=0,
        ))
        with patch.object(manager._runtime, "acquire", return_value=("http://127.0.0.1:32111", "bridge-key")) as acquire, patch.object(manager._runtime, "release") as release:
            lease = manager.acquire("a", "worker", 1, 1, "session", timeout=1)
            self.assertEqual(lease.proxy_url, "http://127.0.0.1:32111")
            self.assertEqual(lease.protocol, "socks5")
            self.assertEqual(lease.runtime_key, "bridge-key")
            acquire.assert_called_once()
            manager.release(lease)
            release.assert_called_once_with("bridge-key")

    def test_advanced_probe_uses_runtime_and_releases_every_dual_stack_acquire(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.txt"
            path.write_text("trojan://secret@trojan.example.com:443?sni=trojan.example.com#Trojan\n", encoding="utf-8")
            manager = ProxyPoolManager(self.config(proxy_mode="pool", proxy_pool_file=str(path)))
            response = Mock(status_code=200, text="ip=203.0.113.5\n")
            node_id = manager.snapshot()["nodes"][0]["id"]
            with patch.object(manager._runtime, "acquire", return_value=("http://127.0.0.1:32002", "runtime-key")) as acquire, patch.object(manager._runtime, "release") as release, patch("proxy_pool.requests.get", return_value=response) as get:
                result = manager.probe_node(node_id)
            self.assertEqual(result["status"], "healthy")
            self.assertEqual(result["exit_ip"], "203.0.113.5")
            self.assertGreaterEqual(acquire.call_count, 1)
            self.assertEqual(release.call_count, acquire.call_count)
            for call in release.call_args_list:
                self.assertEqual(call.args, ("runtime-key",))
            self.assertEqual(get.call_args.kwargs["proxies"]["https"], "http://127.0.0.1:32002")

    def test_subscription_snapshot_reports_protocol_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.txt"
            path.write_text(
                "socks5://127.0.0.1:1080\n"
                "tuic://11111111-1111-1111-1111-111111111111:secret@tuic.example.com:443?sni=tuic.example.com#TUIC\n",
                encoding="utf-8",
            )
            manager = ProxyPoolManager(self.config(proxy_mode="pool", proxy_pool_file=str(path)))
            snapshot = manager.snapshot()
            self.assertEqual(len(snapshot["nodes"]), 2)
            self.assertEqual(snapshot["sources"]["file"]["protocol_counts"]["socks5"], 1)
            self.assertEqual(snapshot["sources"]["file"]["protocol_counts"]["tuic"], 1)

    def test_backend_start_failure_disables_only_that_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.txt"
            path.write_text(
                "vless://11111111-1111-1111-1111-111111111111@vless.example.com:443?security=tls\n"
                "http://127.0.0.1:8080\n",
                encoding="utf-8",
            )
            manager = ProxyPoolManager(self.config(
                proxy_mode="pool", proxy_pool_file=str(path), proxy_pool_probe_interval_sec=0,
            ))
            vless = next(node for node in manager._nodes.values() if node.protocol == "vless")
            http = next(node for node in manager._nodes.values() if node.protocol == "http")

            def acquire(descriptor):
                if descriptor.protocol == "vless":
                    raise RuntimeError("sing-box unavailable")
                return descriptor.canonical_uri, None

            with patch.object(manager, "_select_locked", side_effect=[vless, http]), patch.object(
                manager._runtime, "acquire", side_effect=acquire
            ):
                lease = manager.acquire("pick-vless", "worker", 1, 1, "session", timeout=1)
            self.assertEqual(lease.protocol, "http")
            state = {node["protocol"]: node for node in manager.snapshot()["nodes"]}
            self.assertFalse(state["vless"]["enabled"])
            self.assertIn("backend:", state["vless"]["last_error"])
            manager.release(lease)

    def test_zero_refresh_and_probe_intervals_are_preserved(self):
        manager = ProxyPoolManager(self.config(
            proxy_mode="single",
            proxy="http://127.0.0.1:8080",
            proxy_pool_refresh_interval_sec=0,
            proxy_pool_probe_interval_sec=0,
        ))
        self.assertEqual(manager.refresh_interval, 0)
        self.assertEqual(manager.probe_interval, 0)


if __name__ == "__main__":
    unittest.main()
