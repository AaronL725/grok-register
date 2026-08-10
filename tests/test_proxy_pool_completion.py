import unittest
from unittest.mock import Mock, patch

from app_config import DEFAULT_CONFIG
from proxy_pool import ProxyPoolManager


class ProxyPoolCompletionTests(unittest.TestCase):
    def config(self, **updates):
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(updates)
        return cfg

    def test_ipinfo_probe_provider_is_honored(self):
        manager = ProxyPoolManager(self.config(proxy_mode="single", proxy="http://127.0.0.1:8001", proxy_pool_probe_provider="ipinfo"))
        response = Mock(status_code=200, text="")
        response.json.return_value = {"ip": "203.0.113.7"}
        with patch("proxy_pool.requests.get", return_value=response) as get:
            result = manager.probe_node(manager.snapshot()["nodes"][0]["id"])
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["exit_ip"], "203.0.113.7")
        self.assertEqual(get.call_args.args[0], "https://ipinfo.io/json")

    def test_periodic_probe_scheduler_is_coalesced(self):
        manager = ProxyPoolManager(self.config(proxy_mode="single", proxy="http://127.0.0.1:8001", proxy_pool_probe_interval_sec=1))
        manager._last_probe_all = 0
        import threading, time
        entered = threading.Event()
        release = threading.Event()
        def fake_probe(force=False):
            entered.set(); release.wait(1); return []
        with patch.object(manager, "probe_all", side_effect=fake_probe) as probe:
            manager._schedule_periodic_probe_if_due()
            self.assertTrue(entered.wait(1))
            manager._schedule_periodic_probe_if_due()
            release.set()
            time.sleep(0.05)
        self.assertEqual(probe.call_count, 1)


if __name__ == "__main__":
    unittest.main()
