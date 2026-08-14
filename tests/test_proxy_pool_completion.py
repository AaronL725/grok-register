import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app_config import DEFAULT_CONFIG
from proxy_pool import ProxyPoolManager


class ProxyPoolCompletionTests(unittest.TestCase):
    def config(self, **updates):
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(updates)
        return cfg

    def test_ipinfo_probe_provider_is_honored(self):
        manager = ProxyPoolManager(self.config(
            proxy_mode="single", proxy="http://127.0.0.1:8001",
            proxy_pool_probe_provider="ipinfo", proxy_pool_probe_dual_stack=False,
        ))
        response = Mock(status_code=200, text="")
        response.json.return_value = {"ip": "203.0.113.7"}
        with patch("proxy_pool.requests.get", return_value=response) as get:
            result = manager.probe_node(manager.snapshot()["nodes"][0]["id"])
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["exit_ip"], "203.0.113.7")
        self.assertEqual(get.call_args.args[0], "https://ipinfo.io/json")

    def test_managed_browser_policy_is_fail_closed(self):
        source = Path("registration_browser.py").read_text(encoding="utf-8")
        self.assertIn("from proxy_pool import ProxyTransportError", source)
        self.assertIn("if _managed_proxy_mode():\n                raise ProxyTransportError", source)
        self.assertIn("if _managed_proxy_mode() and is_proxy_connection_error(last_exc):", source)

    def test_gui_exposes_compact_proxy_pool_controls(self):
        source = Path("grok_register_ttk.py").read_text(encoding="utf-8")
        for marker in (
            "self.proxy_mode_var", "self.proxy_pool_file_var", "self.proxy_subscription_var",
            "self.proxy_capacity_var", "self.proxy_protocol_backend_var",
            "self.proxy_singbox_path_var", "self.proxy_protocol_start_timeout_var",
            "def test_proxy_pool(self):",
        ):
            self.assertIn(marker, source)
        self.assertIn('config["proxy_protocol_backend"] = self.proxy_protocol_backend_var.get()', source)
        self.assertIn('config["proxy_singbox_path"] = self.proxy_singbox_path_var.get()', source)
        self.assertIn('config["proxy_protocol_start_timeout_sec"] = int(self.proxy_protocol_start_timeout_var.get())', source)


if __name__ == "__main__":
    unittest.main()
