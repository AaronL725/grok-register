import unittest
from unittest.mock import patch

import browser_runtime
import proxy_pool
from cpa_export import CpaExportSettings
from proxy_pool import ProxyLease


class ProxyRuntimeTests(unittest.TestCase):
    def setUp(self):
        proxy_pool._TLS.lease = None
        browser_runtime.configure_runtime({"proxy_mode": "auto", "proxy": "http://legacy:pass@127.0.0.1:7890"})

    def tearDown(self):
        proxy_pool._TLS.lease = None

    def test_auto_mode_keeps_legacy_proxy(self):
        self.assertEqual(browser_runtime.get_configured_proxy(), "http://legacy:pass@127.0.0.1:7890")

    def test_managed_lease_overrides_legacy_proxy(self):
        proxy_pool._TLS.lease = ProxyLease("node", "http://lease:pass@127.0.0.2:7890", "w", 1, 1, "a", "s")
        self.assertEqual(browser_runtime.get_configured_proxy(), "http://lease:pass@127.0.0.2:7890")

    def test_direct_lease_suppresses_legacy_proxy(self):
        proxy_pool._TLS.lease = ProxyLease("direct", "", "w", 1, 1, "a", "s")
        self.assertEqual(browser_runtime.get_configured_proxy(), "")
        self.assertEqual(browser_runtime.get_proxies(), {})

    def test_explicit_empty_proxies_bypasses_active_lease(self):
        proxy_pool._TLS.lease = ProxyLease("node", "http://lease:pass@127.0.0.2:7890", "w", 1, 1, "a", "s")
        response = object()
        with patch.object(browser_runtime.requests, "post", return_value=response) as request:
            self.assertIs(browser_runtime.http_post("https://example.invalid", proxies={}), response)
        kwargs = request.call_args.kwargs
        self.assertNotIn("proxies", kwargs)

    def test_cpa_inherits_lease_unless_explicitly_overridden(self):
        proxy_pool._TLS.lease = ProxyLease("node", "http://lease:pass@127.0.0.2:7890", "w", 1, 1, "a", "s")
        inherited = CpaExportSettings.from_config({"proxy": "http://legacy:7890", "cpa_proxy": ""})
        self.assertEqual(inherited.proxy, "http://lease:pass@127.0.0.2:7890")
        explicit = CpaExportSettings.from_config({"proxy": "http://legacy:7890", "cpa_proxy": "http://cpa:9999"})
        self.assertEqual(explicit.proxy, "http://cpa:9999")


if __name__ == "__main__":
    unittest.main()
