import base64
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app_config import DEFAULT_CONFIG, validate_config_structure, validate_run_requirements
from proxy_pool import (
    ProxyAcquireCancelled, ProxyPoolManager, classify_proxy_network_error,
    parse_proxy_source, safe_proxy_error_text,
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
            manager = ProxyPoolManager(self._config(proxy_mode="pool", proxy_pool_file=str(path), proxy_pool_max_concurrent_per_node=1))
            first = manager.acquire("worker:slot:1", "worker", 1, 1, "session-a", timeout=1)
            second = manager.acquire("worker:slot:1", "worker", 1, 1, "session-b", timeout=1)
            self.assertNotEqual(first.node_id, second.node_id)
            manager.release(first); manager.release(second)
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
        self.assertEqual(state["business_samples"], 1)
        self.assertEqual(state["transport_failures"], 1)
        self.assertEqual(state["failure_count"], 1)
        self.assertGreaterEqual(state["cooldown_sec"], 29)
        manager.release(lease)

    def test_configuration_failure_does_not_count_as_business_health_sample(self):
        manager = ProxyPoolManager(self._config(proxy_mode="single", proxy="http://127.0.0.1:8001", proxy_pool_probe_interval_sec=0))
        lease = manager.acquire("a", "worker", 1, 1, "session", timeout=1)
        manager.report_transport_failure(lease, RuntimeError("SOCKS5 authentication failed"))
        state = manager.snapshot()["nodes"][0]
        self.assertEqual(state["configuration_failures"], 1)
        self.assertEqual(state["business_samples"], 0)
        self.assertEqual(state["health"], 1.0)
        self.assertEqual(state["cooldown_sec"], 0)
        self.assertFalse(state["enabled"])
        manager.release(lease)

    def test_rotating_gateway_has_gateway_statistics_without_global_health_or_cooldown(self):
        manager = ProxyPoolManager(self._config(
            proxy_mode="single", proxy="http://user-{account}:pass@127.0.0.1:8001",
            proxy_pool_endpoint_mode="auto", proxy_pool_probe_interval_sec=0,
        ))
        with patch.object(manager._runtime, "acquire", return_value=("http://127.0.0.1:32100", "bridge-key")), patch.object(manager._runtime, "release"):
            lease = manager.acquire("a", "worker", 1, 1, "abc123", timeout=1)
            manager.report_transport_failure(lease, RuntimeError("proxy connect failed"))
            state = manager.snapshot()["nodes"][0]
            self.assertTrue(state["rotating"])
            self.assertEqual(state["health_model"], "gateway")
            self.assertIsNone(state["health"])
            self.assertEqual(state["exit_failures"], 1)
            self.assertEqual(state["failure_count"], 0)
            self.assertEqual(state["cooldown_sec"], 0)
            manager.release(lease)

    def test_probe_2xx_without_valid_ip_is_not_healthy(self):
        manager = ProxyPoolManager(self._config(proxy_mode="single", proxy="http://127.0.0.1:8001", proxy_pool_probe_interval_sec=0))
        node_id = manager.snapshot()["nodes"][0]["id"]
        response = Mock(status_code=200, text="hello=world\n")
        response.json.return_value = {}
        with patch("proxy_pool.requests.get", return_value=response):
            result = manager.probe_node(node_id)
        self.assertEqual(result["status"], "unhealthy")
        self.assertIn("有效 IP", result["error"])

    def test_probe_failure_does_not_change_business_health(self):
        manager = ProxyPoolManager(self._config(proxy_mode="single", proxy="http://127.0.0.1:8001", proxy_pool_probe_interval_sec=0))
        node_id = manager.snapshot()["nodes"][0]["id"]
        with patch("proxy_pool.requests.get", side_effect=RuntimeError("TLS connect error")):
            result = manager.probe_node(node_id)
        state = manager.snapshot()["nodes"][0]
        self.assertEqual(result["status"], "unhealthy")
        self.assertEqual(state["health"], 1.0)
        self.assertEqual(state["business_samples"], 0)
        self.assertEqual(state["probe_status"], "unhealthy")

    def test_healthy_ipv4_probe_repairs_transport_failure_even_if_ipv6_fails(self):
        manager = ProxyPoolManager(self._config(proxy_mode="single", proxy="http://127.0.0.1:8001", proxy_pool_probe_interval_sec=0))
        manager._schedule_failure_probe = lambda _node_id: None
        lease = manager.acquire("a", "worker", 1, 1, "session", timeout=1)
        manager.report_transport_failure(lease, RuntimeError("proxy connect failed"))
        response = Mock(status_code=200, text="ip=203.0.113.9\n")
        with patch("proxy_pool.requests.get", return_value=response):
            result = manager.probe_node(lease.node_id)
        state = manager.snapshot()["nodes"][0]
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["ipv4"]["status"], "healthy")
        self.assertEqual(result["ipv6"]["status"], "unhealthy")
        self.assertEqual(state["health"], 1.0)
        self.assertEqual(state["failure_count"], 0)
        manager.release(lease)

    def test_source_scoped_last_known_good_survives_subscription_refresh_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.txt"
            path.write_text("http://127.0.0.1:8001\n", encoding="utf-8")
            cfg = self._config(proxy_mode="pool", proxy_pool_file=str(path), proxy_pool_subscription_url="https://example.test/list")
            good = Mock(status_code=200, text="http://127.0.0.1:8002\n", headers={})
            with patch("proxy_pool.requests.get", return_value=good):
                manager = ProxyPoolManager(cfg)
            self.assertEqual(len(manager.snapshot()["nodes"]), 2)
            path.write_text("http://127.0.0.1:8003\n", encoding="utf-8")
            with patch("proxy_pool.requests.get", side_effect=RuntimeError("temporary timeout")):
                manager.reload_sources(force=True)
            snapshot = manager.snapshot()
            proxies = {node["proxy"] for node in snapshot["nodes"]}
            self.assertIn("http://127.0.0.1:8002", proxies)
            self.assertIn("http://127.0.0.1:8003", proxies)
            self.assertTrue(snapshot["sources"]["subscription"]["stale"])

    def test_recent_unhealthy_probe_is_soft_deprioritized(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.txt"
            path.write_text("http://127.0.0.1:8001\nhttp://127.0.0.1:8002\n", encoding="utf-8")
            manager = ProxyPoolManager(self._config(proxy_mode="pool", proxy_pool_file=str(path), proxy_pool_probe_interval_sec=900))
            nodes = sorted(manager._nodes.values(), key=lambda n: n.id)
            nodes[0].probe_status = "unhealthy"; nodes[0].last_probed_at = time.time()
            nodes[1].probe_status = "healthy"; nodes[1].last_probed_at = time.time()
            selected = manager._select_locked(nodes, "affinity")
            self.assertEqual(selected.id, nodes[1].id)

    def test_suspected_failure_counts_one_business_sample_when_same_attempt_succeeds(self):
        manager = ProxyPoolManager(self._config(proxy_mode="single", proxy="http://127.0.0.1:8001", proxy_pool_probe_interval_sec=0))
        lease = manager.acquire("a", "worker", 1, 1, "session", timeout=1)
        with patch.object(manager, "probe_node", return_value={"status": "unhealthy", "error": "probe failed"}):
            manager.report_suspected_transport_failure(lease, RuntimeError("TLS connect error"))
            manager.report_success(lease)
            deadline = time.time() + 2
            while time.time() < deadline and manager.snapshot()["nodes"][0]["transport_failures"] == 0:
                time.sleep(0.02)
        state = manager.snapshot()["nodes"][0]
        self.assertEqual(state["business_samples"], 1)
        self.assertEqual(state["registration_successes"], 1)
        self.assertEqual(state["transport_failures"], 1)
        manager.release(lease)

    def test_error_classifier_separates_compatibility_configuration_and_transport(self):
        self.assertEqual(classify_proxy_network_error("unknown url type: socks5"), "compatibility")
        self.assertEqual(classify_proxy_network_error("SOCKS5 authentication failed"), "configuration")
        self.assertEqual(classify_proxy_network_error("connection refused"), "hard_transport")
        self.assertEqual(classify_proxy_network_error("TLS connect error: handshake"), "suspected_transport")
        self.assertEqual(classify_proxy_network_error("HTTP 401"), "application")

    def test_snapshot_exposes_proxy_credentials(self):
        manager = ProxyPoolManager(self._config(proxy_mode="single", proxy="http://secret:password@127.0.0.1:8001"))
        self.assertEqual(manager.snapshot()["nodes"][0]["proxy"], "http://secret:password@127.0.0.1:8001")

    def test_acquire_honors_cancellation(self):
        manager = ProxyPoolManager(self._config(proxy_mode="single", proxy="http://127.0.0.1:8001"))
        manager._nodes[next(iter(manager._nodes))].inflight = manager.capacity
        with self.assertRaises(ProxyAcquireCancelled):
            manager.acquire("a", "worker", 1, 1, "session", timeout=30, cancel_callback=lambda: True)

    def test_release_is_idempotent(self):
        manager = ProxyPoolManager(self._config(proxy_mode="single", proxy="http://127.0.0.1:8001"))
        lease = manager.acquire("a", "worker", 1, 1, "session", timeout=1)
        manager.release(lease); manager.release(lease)
        self.assertEqual(manager.snapshot()["nodes"][0]["inflight"], 0)

    def test_proxy_error_text_preserves_credentials(self):
        value = safe_proxy_error_text("failed via socks5://secret:password@127.0.0.1:1080")
        self.assertIn("socks5://secret:password@127.0.0.1:1080", value)


if __name__ == "__main__":
    unittest.main()
