import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import cpa_export
from proxy_bridge import prepare_http_compatible_proxy
from registration_flow import OutputResult, RegistrationResult, _feedback_from_output


class ProxyNetworkUnificationTests(unittest.TestCase):
    def test_raw_socks_can_be_exposed_as_local_http_endpoint(self):
        endpoint, bridge = prepare_http_compatible_proxy("socks5://user:pass@127.0.0.1:1080")
        try:
            self.assertTrue(endpoint.startswith("http://127.0.0.1:"))
            self.assertIsNotNone(bridge)
        finally:
            if bridge is not None:
                bridge.stop()

    def test_plain_http_does_not_add_unnecessary_bridge(self):
        endpoint, bridge = prepare_http_compatible_proxy("http://127.0.0.1:8080")
        self.assertEqual(endpoint, "http://127.0.0.1:8080")
        self.assertIsNone(bridge)

    def test_cpa_explicit_socks_is_normalized_before_mint(self):
        captured = {}

        @contextmanager
        def compatible(_proxy, log=None):
            yield "http://127.0.0.1:32123"

        def mint(**kwargs):
            captured.update(kwargs)
            path = Path(kwargs["auth_dir"]) / "ok.json"
            path.write_text("{}", encoding="utf-8")
            return {"ok": True, "email": kwargs["email"], "path": str(path)}

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(cpa_export, "http_compatible_proxy", compatible), \
                patch.object(cpa_export, "_load_mint_and_export", return_value=mint):
            result = cpa_export.export_cpa_xai_for_account(
                "user@example.com", "password", sso="token",
                config={
                    "cpa_export_enabled": True,
                    "cpa_auth_dir": tmp,
                    "cpa_proxy": "socks5://user:pass@127.0.0.1:1080",
                    "cpa_mint_cookie_inject": False,
                },
            )
        self.assertTrue(result["ok"])
        self.assertEqual(captured["proxy"], "http://127.0.0.1:32123")

    def test_cpa_compatibility_error_does_not_become_proxy_failure_feedback(self):
        account = RegistrationResult(ok=True)
        output = OutputResult(
            registered=True,
            saved=True,
            cpa={"ok": False, "skipped": False, "error": "xAI discovery request failed: unknown url type: socks5"},
        )
        kind, _ = _feedback_from_output(account, output)
        self.assertEqual(kind, "application")

    def test_nsfw_tls_error_is_suspected_not_hard(self):
        account = RegistrationResult(
            ok=True,
            proxy_feedback_kind="suspected",
            proxy_feedback_error="curl: (35) TLS connect error",
        )
        output = OutputResult(registered=True, saved=True, cpa={"ok": True})
        kind, error = _feedback_from_output(account, output)
        self.assertEqual(kind, "suspected")
        self.assertIn("TLS connect error", error)


if __name__ == "__main__":
    unittest.main()
