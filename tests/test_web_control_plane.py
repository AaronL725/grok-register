import tempfile
import threading
import time
import unittest
from pathlib import Path
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
            server._maintenance_state = None
        with server._log_lock:
            server._logs.clear()
            server._log_seq = 0
        try:
            import proxy_pool
            proxy_pool.reset_manager()
        except Exception:
            pass

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
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/proxy-pool.js", response.text)
        self.assertIn("/proxy-pool.css", response.text)
        self.assertEqual(self.client.get("/health").json(), {"ok": True})

    def test_react_build_and_hashed_assets_are_served_when_present(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            assets = dist / "assets"
            assets.mkdir()
            (dist / "index.html").write_text(
                '<html><script type="module" src="/assets/app-abc123.js"></script></html>',
                encoding="utf-8",
            )
            (assets / "app-abc123.js").write_text("console.log('react');", encoding="utf-8")
            with patch.object(self.server, "FRONTEND_DIST", dist), patch.object(
                self.server, "FRONTEND_ASSETS", assets
            ):
                index = self.client.get("/")
                asset = self.client.get("/assets/app-abc123.js")
                missing = self.client.get("/assets/missing.js")

        self.assertEqual(index.status_code, 200)
        self.assertEqual(index.headers["cache-control"], "no-store")
        self.assertIn("/assets/app-abc123.js", index.text)
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual(asset.text, "console.log('react');")
        self.assertEqual(missing.status_code, 404)

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

    def test_incomplete_yyds_config_can_be_saved_before_run(self):
        cfg = self._base_config()
        with patch.object(self.server.engine, "load_config", side_effect=self._fake_load(cfg)), patch.object(
            self.server.engine, "save_config", return_value=None
        ):
            response = self.client.put(
                "/api/config",
                json={"email_provider": "yyds", "yyds_api_key": "", "yyds_jwt": ""},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["config"]["email_provider"], "yyds")

    def test_config_update_is_rejected_during_maintenance(self):
        with self.server._job_lock:
            self.server._maintenance_state = "proxy_test"
        response = self.client.put("/api/config", json={"register_count": 2})
        self.assertEqual(response.status_code, 409)

    def test_proxy_pool_status_exposes_credentials(self):
        cfg = self._base_config()
        cfg.update({"proxy_mode": "single", "proxy": "http://secret:password@127.0.0.1:7890"})
        with patch.object(self.server.engine, "load_config", side_effect=self._fake_load(cfg)):
            response = self.client.get("/api/proxy-pool/status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["mode"], "single")
        self.assertEqual(len(payload["nodes"]), 1)
        label = payload["nodes"][0]["proxy"]
        self.assertEqual(label, "http://secret:password@127.0.0.1:7890")

    def test_proxy_pool_reload_and_test_are_rejected_while_running(self):
        with self.server._job_lock:
            self.server._job_state["running"] = True
        self.assertEqual(self.client.post("/api/proxy-pool/reload").status_code, 409)
        self.assertEqual(self.client.post("/api/proxy-pool/test").status_code, 409)

    def test_proxy_test_blocks_registration_start_for_full_probe_window(self):
        cfg = self._base_config()
        cfg.update({"proxy_mode": "single", "proxy": "http://127.0.0.1:7890"})
        entered = threading.Event()
        release = threading.Event()
        responses = []

        class BlockingManager:
            def reload_sources(self, force=False):
                return {"mode": "single", "managed": True, "nodes": []}

            def probe_all(self, force=False):
                entered.set()
                release.wait(2)
                return [{"id": "node", "status": "healthy"}]

            def snapshot(self):
                return {"mode": "single", "managed": True, "nodes": []}

        with patch.object(self.server.engine, "load_config", side_effect=self._fake_load(cfg)), patch(
            "proxy_pool.get_manager", return_value=BlockingManager()
        ):
            thread = threading.Thread(
                target=lambda: responses.append(self.client.post("/api/proxy-pool/test")),
                daemon=True,
            )
            thread.start()
            self.assertTrue(entered.wait(1))
            start_response = self.client.post("/api/start")
            self.assertEqual(start_response.status_code, 409)
            self.assertIn("proxy_test", start_response.json()["detail"])
            release.set()
            thread.join(2)

        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].status_code, 200)
        self.assertIsNone(self.server._maintenance_state)

    def test_proxy_pool_reload_and_probe_use_shared_manager(self):
        cfg = self._base_config()
        cfg.update({"proxy_mode": "single", "proxy": "http://127.0.0.1:7890"})

        class FakeManager:
            def reload_sources(self, force=False):
                return {"mode": "single", "managed": True, "nodes": []}

            def probe_all(self, force=False):
                return [{"id": "node", "status": "healthy"}]

            def snapshot(self):
                return {"mode": "single", "managed": True, "nodes": []}

        fake = FakeManager()
        with patch.object(self.server.engine, "load_config", side_effect=self._fake_load(cfg)), patch(
            "proxy_pool.get_manager", return_value=fake
        ):
            reload_response = self.client.post("/api/proxy-pool/reload")
            test_response = self.client.post("/api/proxy-pool/test")
        self.assertEqual(reload_response.status_code, 200)
        self.assertTrue(reload_response.json()["managed"])
        self.assertEqual(test_response.status_code, 200)
        self.assertEqual(test_response.json()["results"][0]["status"], "healthy")

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


    def _new_database(self):
        from storage.database import Database

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Database(Path(directory.name) / "accounts.sqlite3")

    def test_account_api_filters_details_and_exports_legacy_format(self):
        database = self._new_database()
        run_id = database.create_registration_run(2)
        first, _ = database.insert_account({
            "email": "first@example.com", "password": "pw1", "sso": "token1",
            "email_provider": "duckmail", "created_at": "2026-10-30T12:00:00+00:00",
            "registration_run_id": run_id,
        })
        second, _ = database.insert_account({
            "email": "second@example.com", "password": "pw2", "sso": "token2",
            "email_provider": "cloudmail", "created_at": "2026-10-31T12:00:00+00:00",
            "registration_run_id": run_id,
        })
        database.update_account_integration_status(
            second, "succeeded", "", "not_requested", "", "failed", "remote error",
        )
        database.create_registration_attempt(
            run_id, slot_index=1, attempt_number=1, worker_key="worker-1",
            stage="postprocess", status="succeeded", account_id=second,
        )
        database.create_registration_attempt(
            run_id, slot_index=2, attempt_number=1, worker_key="worker-2",
            stage="browser_start", status="failed", error_type="TimeoutError",
            error_message="browser start timed out", traceback="Traceback: timeout",
            failure_type="browser", screenshot_path="/private/failure.png",
        )
        database.update_registration_run(
            run_id, status="completed", processed_count=2, success_count=1, failed_count=1,
        )

        with patch.object(self.server, "_get_database", return_value=database):
            listed = self.client.get(
                "/api/accounts?limit=1&offset=0&search=second&email_provider=cloudmail"
                "&cpa_status=succeeded&date_from=2026-10-31&date_to=2026-10-31"
            )
            detail = self.client.get("/api/accounts/%s" % second)
            exported = self.client.post("/api/accounts/export", json={"ids": [second]})
            invalid_export = self.client.post("/api/accounts/export", json={"ids": [1.5]})
            stats = self.client.get("/api/stats")
            history = self.client.get("/api/jobs/history")
            attempts = self.client.get("/api/jobs/history/%s/attempts" % run_id)
            attempts_page = self.client.get(
                "/api/jobs/history/%s/attempts?limit=1&offset=1" % run_id
            )
            missing_attempts = self.client.get("/api/jobs/history/999/attempts")

        self.assertEqual(listed.status_code, 200)
        payload = listed.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["limit"], 1)
        self.assertEqual(payload["items"][0]["id"], second)
        self.assertNotIn("password", payload["items"][0])
        self.assertNotIn("sso", payload["items"][0])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.headers["cache-control"], "no-store")
        self.assertEqual(detail.json()["account"]["attempts"][0]["account_id"], second)
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(invalid_export.status_code, 400)
        self.assertEqual(exported.text, "second@example.com----pw2----token2\n")
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json()["success_rate"], 50.0)
        self.assertEqual(stats.json()["by_grok2api_remote_status"], {"failed": 1, "unknown": 1})
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["items"][0]["attempt_count"], 2)
        self.assertEqual(history.json()["items"][0]["success_rate"], 50.0)
        self.assertEqual(attempts.status_code, 200)
        self.assertEqual(attempts.headers["cache-control"], "no-store")
        self.assertEqual(attempts.json()["total"], 2)
        self.assertEqual(attempts_page.status_code, 200)
        self.assertEqual(attempts_page.json()["total"], 2)
        self.assertEqual(attempts_page.json()["limit"], 1)
        self.assertEqual(attempts_page.json()["offset"], 1)
        self.assertEqual(len(attempts_page.json()["items"]), 1)
        diagnostic = next(item for item in attempts.json()["items"] if item["failure_type"])
        self.assertEqual(diagnostic["failure_type"], "browser")
        self.assertEqual(diagnostic["screenshot_path"], "/private/failure.png")
        self.assertIn("Traceback", diagnostic["traceback"])
        self.assertEqual(missing_attempts.status_code, 404)
        self.assertNotEqual(first, second)

    def test_account_api_delete_single_and_bulk(self):
        database = self._new_database()
        first, _ = database.insert_account({"email": "one@example.com", "sso": "one"})
        second, _ = database.insert_account({"email": "two@example.com", "sso": "two"})
        with patch.object(self.server, "_get_database", return_value=database):
            single = self.client.delete("/api/accounts/%s" % first)
            bulk = self.client.request("DELETE", "/api/accounts", json={"ids": [second, second]})
            invalid_bool = self.client.request("DELETE", "/api/accounts", json={"ids": [True]})
            invalid_float = self.client.request("DELETE", "/api/accounts", json={"ids": [1.5]})
        self.assertEqual(single.status_code, 200)
        self.assertEqual(bulk.status_code, 200)
        self.assertEqual(bulk.json()["deleted_count"], 1)
        self.assertEqual(invalid_bool.status_code, 400)
        self.assertEqual(invalid_float.status_code, 400)
        self.assertEqual(database.account_count(), 0)

    def test_account_api_rejects_invalid_date_filter(self):
        database = self._new_database()
        with patch.object(self.server, "_get_database", return_value=database):
            response = self.client.get("/api/accounts?date_from=not-a-date")
        self.assertEqual(response.status_code, 400)

    def test_account_api_requires_same_origin_for_sensitive_detail(self):
        database = self._new_database()
        account_id, _ = database.insert_account({"email": "one@example.com", "sso": "token"})
        run_id = database.create_registration_run(1)
        with patch.object(self.server, "_get_database", return_value=database):
            response = self.client.get(
                "/api/accounts/%s" % account_id,
                headers={"origin": "https://attacker.example"},
            )
            attempts = self.client.get(
                "/api/jobs/history/%s/attempts" % run_id,
                headers={"origin": "https://attacker.example"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(attempts.status_code, 403)

    def test_database_api_imports_existing_legacy_files_before_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "accounts-old.txt").write_text(
                "legacy@example.com----legacy-pw----legacy-token\n",
                encoding="utf-8",
            )
            with patch.object(self.server, "ROOT", root), patch.object(
                self.server, "_load_config_if_idle",
                return_value={"database_path": str(root / "accounts.sqlite3")},
            ), patch.object(self.server, "_database_cache", {}):
                database = self.server._get_database()
                self.assertEqual(database.account_count(), 1)
                account = database.list_accounts()["items"][0]
                self.assertEqual(account["email"], "legacy@example.com")
                self.assertEqual(account["email_provider"], "")


if __name__ == "__main__":
    unittest.main()
