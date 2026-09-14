import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "web dependencies not installed")
class OutlookWebApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web import server
        cls.server = server
        cls.client = TestClient(server.app)

    def setUp(self):
        with self.server._job_lock:
            self.server._job_state["running"] = False
            self.server._maintenance_state = None

    def _load_for_path(self, path):
        cfg = dict(self.server.engine.DEFAULT_CONFIG)
        cfg["outlook_accounts_file"] = str(path)
        def load():
            self.server.engine.config.clear()
            self.server.engine.config.update(cfg)
            return self.server.engine.config
        return load

    def test_index_includes_outlook_asset_and_health_button(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/outlook-mailbox.js", response.text)
        asset = self.client.get("/outlook-mailbox.js")
        self.assertEqual(asset.status_code, 200)
        self.assertIn("Test pool", asset.text)
        self.assertIn("/api/mailboxes/outlook/test", asset.text)

    def test_pool_api_roundtrip_is_no_store_and_config_has_no_secrets(self):
        row = "u@example.com----pw----client----secret-refresh-token----auto\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outlook.txt"
            loader = self._load_for_path(path)
            with patch.object(self.server.engine, "load_config", side_effect=loader):
                put = self.client.put("/api/mailboxes/outlook", json={"data": row})
                self.assertEqual(put.status_code, 200)
                self.assertEqual(put.headers.get("cache-control"), "no-store")
                self.assertEqual(put.headers.get("x-content-type-options"), "nosniff")
                get = self.client.get("/api/mailboxes/outlook")
                self.assertEqual(get.status_code, 200)
                self.assertEqual(get.json()["count"], 1)
                self.assertIn("secret-refresh-token", get.json()["data"])
                self.assertNotIn("secret-refresh-token", repr(get.json()["accounts"]))
                config = self.client.get("/api/config").json()["config"]
                self.assertEqual(config["outlook_accounts_file"], str(path))
                self.assertNotIn("secret-refresh-token", repr(config))

    def test_health_api_returns_only_safe_status_and_no_store_headers(self):
        row = "u@example.com----pw----client----secret-refresh-token----auto\n"
        result = {
            "count": 1,
            "healthy": 1,
            "unhealthy": 0,
            "imap": 1,
            "graph": 1,
            "results": [
                {
                    "email": "u@example.com",
                    "mode": "auto",
                    "usable": True,
                    "imap": {"ok": True, "folders": ["INBOX"], "error": ""},
                    "graph": {"ok": True, "folders": ["inbox", "junkemail"], "error": ""},
                }
            ],
        }
        with patch(
            "outlook_mailbox_pool.probe_outlook_mailbox_pool_data",
            return_value=result,
        ) as probe:
            response = self.client.post("/api/mailboxes/outlook/test", json={"data": row})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(response.json()["healthy"], 1)
        self.assertNotIn("secret-refresh-token", repr(response.json()))
        probe.assert_called_once_with(row)

    def test_foreign_origin_is_rejected_for_pool_and_health(self):
        headers = {"Origin": "https://example.com"}
        response = self.client.get("/api/mailboxes/outlook", headers=headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        response = self.client.post(
            "/api/mailboxes/outlook/test",
            json={"data": "x"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_pool_update_and_health_are_rejected_while_job_runs(self):
        with self.server._job_lock:
            self.server._job_state["running"] = True
        response = self.client.put("/api/mailboxes/outlook", json={"data": "x"})
        self.assertEqual(response.status_code, 409)
        response = self.client.post("/api/mailboxes/outlook/test", json={"data": "x"})
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
