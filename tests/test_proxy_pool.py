import base64
import tempfile
import unittest
from pathlib import Path

from app_config import DEFAULT_CONFIG, validate_config_structure, validate_run_requirements
from proxy_pool import (
    ProxyAcquireCancelled, ProxyPoolManager, parse_proxy_source, safe_proxy_error_text,
)


class ProxyPoolTests(unittest.TestCase):
    def _config(self, **updates):
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(updates)
        return validate_config_structure(cfg)

    def test_legacy_auto_defaults_remain_compatible(self):
        cfg = self._config()
        self.assertEqual(cfg["proxy_mode"], "auto")
        self.assertEqual(cfg["proxy_fallback"], "none")
        self.assertFalse(ProxyPoolManager(cfg).managed)

    def test_run_validation_requires_pool_source(self):
        cfg = self._config(proxy_mode="pool")
        with self.assertRaises(Exception):
            validate_run_requirements(cfg)

    def test_plain_and_base64_proxy_sources_are_parsed_and_deduplicated(self):
        text = "# comment\nhttp://127.0.0.1:8080\nhttp://127.0.0.1:8080\nsocks5://user:pass@127.0.0.2:1080\n"
        values, skipped = parse_proxy_source(text)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(values), 2)
        encoded = base64.b64encode(text.encode()).decode()
        decoded, skipped = parse_proxy_source(encoded)
        self.assertEqual(decoded, values)
        self.assertEqual(skipped, 0)

    def test_file_pool_uses_stable_affinity_and_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.txt"
            path.write_text("http://127.0.0.1:8001\nhttp://127.0.0.1:8002\n", encoding="utf-8")
            manager = ProxyPoolManager(self._config(
                proxy_mode="pool", proxy_pool_file=str(path), proxy_pool_max_concurrent_per_node=1,
            ))
            first = manager.acquire("worker:slot:1", "worker", 1, 1, "session-a", timeout=1)
            second = manager.acquire("worker:slot:1", "worker", 1, 1, "session-b", timeout=1)
            self.assertNotEqual(first.node_id, second.node_id)
            manager.release(first)
            manager.release(second)
            again = manager.acquire("worker:slot:1", "worker", 1, 2, "session-c", timeout=1)
            self.assertIn(again.node_id, {first.node_id, second.node_id})
            manager.release(again)

    def test_fixed_transport_failure_enters_exponential_cooldown(self):
        manager = ProxyPoolManager(self._config(proxy_mode="single", proxy="http://127.0.0.1:8001"))
        manager._schedule_failure_probe = lambda _node_id: None
        lease = manager.acquire("a", "worker", 1, 1, "session", timeout=1)
        manager.report_transport_failure(lease, RuntimeError("proxy connect failed"))
        state = manager.snapshot()["nodes"][0]
        self.assertAlmostEqual(state["health"], 0.7)
        self.assertEqual(state["failure_count"], 1)
        self.assertGreaterEqual(state["cooldown_sec"], 29)
        manager.release(lease)

    def test_rotating_gateway_does_not_cool_entire_node(self):
        manager = ProxyPoolManager(self._config(
            proxy_mode="single",
            proxy="http://user-{account}:pass@127.0.0.1:8001",
            proxy_pool_endpoint_mode="auto",
        ))
        lease = manager.acquire("a", "worker", 1, 1, "abc123", timeout=1)
        self.assertIn("user-abc123", lease.proxy_url)
        manager.report_transport_failure(lease, RuntimeError("proxy connect failed"))
        state = manager.snapshot()["nodes"][0]
        self.assertTrue(state["rotating"])
        self.assertEqual(state["failure_count"], 0)
        self.assertEqual(state["cooldown_sec"], 0)
        manager.release(lease)

    def test_snapshot_exposes_proxy_credentials(self):
        manager = ProxyPoolManager(self._config(proxy_mode="single", proxy="http://secret:password@127.0.0.1:8001"))
        label = manager.snapshot()["nodes"][0]["proxy"]
        self.assertEqual(label, "http://secret:password@127.0.0.1:8001")

    def test_acquire_honors_cancellation(self):
        manager = ProxyPoolManager(self._config(proxy_mode="single", proxy="http://127.0.0.1:8001"))
        with self.assertRaises(ProxyAcquireCancelled):
            manager.acquire("a", "worker", 1, 1, "session", timeout=30, cancel_callback=lambda: True)

    def test_release_is_idempotent(self):
        manager = ProxyPoolManager(self._config(proxy_mode="single", proxy="http://127.0.0.1:8001"))
        lease = manager.acquire("a", "worker", 1, 1, "session", timeout=1)
        manager.release(lease)
        manager.release(lease)
        self.assertEqual(manager.snapshot()["nodes"][0]["inflight"], 0)

    def test_proxy_error_text_preserves_credentials(self):
        value = safe_proxy_error_text("failed via socks5://secret:password@127.0.0.1:1080")
        self.assertIn("secret", value)
        self.assertIn("password", value)
        self.assertIn("socks5://secret:password@127.0.0.1:1080", value)


if __name__ == "__main__":
    unittest.main()
