import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "web dependencies not installed")
class ProxyPoolPreflightWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web import server
        cls.server = server
        cls.client = TestClient(server.app)

    def setUp(self):
        with self.server._job_lock:
            self.server._job_state["running"] = False
        self.server.engine.config.clear()
        self.server.engine.config.update(self.server.engine.DEFAULT_CONFIG)
        try:
            import proxy_pool
            proxy_pool.reset_manager()
        except Exception:
            pass

    def tearDown(self):
        self.server.engine.config.clear()
        self.server.engine.config.update(self.server.engine.DEFAULT_CONFIG)
        try:
            import proxy_pool
            proxy_pool.reset_manager()
        except Exception:
            pass

    def _fake_load(self, cfg):
        def load():
            self.server.engine.config.clear()
            self.server.engine.config.update(cfg)
            return self.server.engine.config
        return load

    def test_preflight_uses_shared_manager_and_is_non_destructive(self):
        cfg = dict(self.server.engine.DEFAULT_CONFIG)
        cfg.update({"proxy_mode": "single", "proxy": "http://127.0.0.1:7890", "proxy_pool_preflight_enabled": True})

        class FakeManager:
            def preflight_node(self, node_id):
                return {
                    "id": node_id,
                    "ok": True,
                    "targets": [
                        {"url": "https://accounts.x.ai/", "reachable": True, "status_code": 302},
                        {"url": "https://grok.com/", "reachable": True, "status_code": 200},
                    ],
                }

            def snapshot(self):
                return {"mode": "single", "managed": True, "nodes": []}

        fake = FakeManager()
        with patch.object(self.server.engine, "load_config", side_effect=self._fake_load(cfg)), patch(
            "proxy_pool.get_manager", return_value=fake
        ):
            response = self.client.post("/api/proxy-pool/preflight?node_id=node-1")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["result"]["ok"])
        self.assertEqual(payload["result"]["id"], "node-1")

    def test_preflight_is_rejected_while_registration_is_running(self):
        with self.server._job_lock:
            self.server._job_state["running"] = True
        response = self.client.post("/api/proxy-pool/preflight?node_id=node-1")
        self.assertEqual(response.status_code, 409)

    def test_preflight_can_be_disabled(self):
        cfg = dict(self.server.engine.DEFAULT_CONFIG)
        cfg.update({"proxy_mode": "single", "proxy": "http://127.0.0.1:7890", "proxy_pool_preflight_enabled": False})
        with patch.object(self.server.engine, "load_config", side_effect=self._fake_load(cfg)):
            response = self.client.post("/api/proxy-pool/preflight?node_id=node-1")
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
