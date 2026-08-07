import threading
import time
import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "web dependencies not installed")
class WebControlPlaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from registration_flow import BatchResult
        from web import server

        cls.BatchResult = BatchResult
        cls.server = server
        cls.client = TestClient(server.app)

    def setUp(self):
        server = self.server
        with server._job_lock:
            server._job_state.update({
                "running": False,
                "target": 0,
                "success": 0,
                "fail": 0,
                "pending": 0,
                "warnings": 0,
                "cancelled": False,
                "started_at": None,
                "finished_at": None,
                "accounts_file": "",
                "error": "",
            })
            server._controller = None
            server._job_thread = None
        with server._log_lock:
            server._logs.clear()
            server._log_seq = 0

    def _base_config(self):
        return dict(self.server.engine.DEFAULT_CONFIG)

    def _fake_load(self, config=None):
        cfg = self._base_config() if config is None else dict(config)

        def load():
            self.server.engine.config.clear()
            self.server.engine.config.update(cfg)
            return self.server.engine.config

        return load

    def _wait_finished(self, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.client.get("/api/status").json()
            if not state["running"]:
                return state
            time.sleep(0.01)
        self.fail("web job did not finish")

    def test_index_and_health(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/health").json(), {"ok": True})

    def test_get_config_reads_existing_engine_config(self):
        cfg = self._base_config()
        cfg["register_count"] = 7
        with patch.object(self.server.engine, "load_config", side_effect=self._fake_load(cfg)):
            response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["config"]["register_count"], 7)

    def test_partial_put_preserves_unsent_fields(self):
        cfg = self._base_config()
        cfg["proxy"] = "http://127.0.0.1:7890"
        with patch.object(self.server.engine, "load_config", side_effect=self._fake_load(cfg)), patch.object(
            self.server.engine, "save_config", return_value=None
        ):
            response = self.client.put("/api/config", json={"register_count": 3})
        self.assertEqual(response.status_code, 200)
        result = response.json()["config"]
        self.assertEqual(result["register_count"], 3)
        self.assertEqual(result["proxy"], "http://127.0.0.1:7890")

    def test_unknown_config_field_is_rejected(self):
        response = self.client.put("/api/config", json={"not_a_real_option": True})
        self.assertEqual(response.status_code, 400)

    def test_config_update_is_rejected_while_running(self):
        with self.server._job_lock:
            self.server._job_state["running"] = True
        response = self.client.put("/api/config", json={"register_count": 2})
        self.assertEqual(response.status_code, 409)

    def test_start_reuses_existing_registration_common_and_reports_progress(self):
        cfg = self._base_config()
        cfg["register_count"] = 2
        calls = []

        def run_registration_common(**kwargs):
            calls.append(kwargs)
            batch = self.BatchResult()
            batch.success_count = 2
            batch.processed_count = 2
            kwargs["observer"](batch, None, None)
            return batch

        with patch.object(self.server.engine, "load_config", side_effect=self._fake_load(cfg)), patch.object(
            self.server.engine, "run_registration_common", side_effect=run_registration_common
        ):
            response = self.client.post("/api/start")
            self.assertEqual(response.status_code, 200)
            state = self._wait_finished()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["count"], 2)
        self.assertEqual(state["success"], 2)
        self.assertEqual(state["fail"], 0)
        self.assertTrue(calls[0]["accounts_output_file"].endswith(".txt"))

    def test_double_start_only_starts_one_job(self):
        cfg = self._base_config()
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def blocking_run(**_kwargs):
            calls.append(1)
            entered.set()
            release.wait(2)
            return self.BatchResult()

        with patch.object(self.server.engine, "load_config", side_effect=self._fake_load(cfg)), patch.object(
            self.server.engine, "run_registration_common", side_effect=blocking_run
        ):
            first = self.client.post("/api/start")
            self.assertEqual(first.status_code, 200)
            self.assertTrue(entered.wait(1))
            second = self.client.post("/api/start")
            self.assertEqual(second.status_code, 409)
            release.set()
            self._wait_finished()

        self.assertEqual(len(calls), 1)

    def test_stop_uses_existing_cooperative_controller(self):
        cfg = self._base_config()
        entered = threading.Event()

        def cancellable_run(**kwargs):
            entered.set()
            deadline = time.time() + 2
            while time.time() < deadline and not kwargs["cancel_callback"]():
                time.sleep(0.01)
            batch = self.BatchResult()
            batch.cancelled = kwargs["cancel_callback"]()
            return batch

        with patch.object(self.server.engine, "load_config", side_effect=self._fake_load(cfg)), patch.object(
            self.server.engine, "run_registration_common", side_effect=cancellable_run
        ):
            self.assertEqual(self.client.post("/api/start").status_code, 200)
            self.assertTrue(entered.wait(1))
            stop = self.client.post("/api/stop")
            self.assertEqual(stop.status_code, 200)
            self.assertTrue(stop.json()["stopped"])
            state = self._wait_finished()

        self.assertTrue(state["cancelled"])

    def test_job_exception_is_exposed_in_status(self):
        cfg = self._base_config()
        with patch.object(self.server.engine, "load_config", side_effect=self._fake_load(cfg)), patch.object(
            self.server.engine, "run_registration_common", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(self.client.post("/api/start").status_code, 200)
            state = self._wait_finished()
        self.assertEqual(state["error"], "boom")

    def test_log_cursor_and_buffer_cap(self):
        for index in range(self.server.LOG_LIMIT + 5):
            self.server._append_log("line-%s" % index)
        first = self.client.get("/api/logs?after=0").json()
        self.assertEqual(len(first["entries"]), self.server.LOG_LIMIT)
        latest = first["latest"]
        self.server._append_log("new-line")
        next_page = self.client.get("/api/logs?after=%s" % latest).json()
        self.assertEqual(len(next_page["entries"]), 1)
        self.assertIn("new-line", next_page["entries"][0]["line"])


if __name__ == "__main__":
    unittest.main()
