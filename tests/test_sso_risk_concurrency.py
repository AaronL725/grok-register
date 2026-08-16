"""SSO risk gate 的并发持久化与代理租约边界回归。"""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import sso_risk


class SsoRiskConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self._prev_config = sso_risk.config
        self._prev_http = sso_risk._http_get
        self.tmp = tempfile.TemporaryDirectory()
        self.rejected = str(Path(self.tmp.name) / "sso_risk_rejected.txt")
        sso_risk.configure_risk_runtime(
            {"sso_risk_gate_enabled": True, "sso_risk_rejected_file": self.rejected},
            None,
        )

    def tearDown(self):
        sso_risk.config = self._prev_config
        sso_risk._http_get = self._prev_http
        self.tmp.cleanup()

    def test_concurrent_risk_quarantine_appends_remain_complete(self):
        def write_one(index):
            return sso_risk.append_sso_risk_rejected(
                "user%s@example.test" % index,
                "sso-%s" % index,
                "risk=0.95,policy=deny,event=$registration",
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write_one, range(40)))

        lines = Path(self.rejected).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 40)
        self.assertEqual(len(set(lines)), 40)
        for index in range(40):
            prefix = "user%s@example.test----sso-%s----" % (index, index)
            self.assertTrue(any(line.startswith(prefix) for line in lines))

    def test_risk_http_without_explicit_proxy_leaves_proxy_injection_to_runtime(self):
        captured = {}

        def getter(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                status_code=200,
                url="https://grok.com/",
                text='{"botFlagSource":0,"botFlagDetails":null}',
            )

        state = sso_risk.inspect_sso_account_state("sso-token", http_get=getter)
        self.assertTrue(state["found"])
        self.assertEqual(state["bot_flag_source"], 0)
        self.assertNotIn("proxies", captured["kwargs"])

    def test_explicit_proxy_is_still_forwarded_when_requested(self):
        captured = {}

        def getter(_url, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                status_code=200,
                url="https://grok.com/",
                text='{"botFlagSource":0,"botFlagDetails":null}',
            )

        sso_risk.inspect_sso_account_state(
            "sso-token",
            proxy="http://127.0.0.1:8899",
            http_get=getter,
        )
        self.assertEqual(
            captured["proxies"],
            {"http": "http://127.0.0.1:8899", "https": "http://127.0.0.1:8899"},
        )


if __name__ == "__main__":
    unittest.main()
