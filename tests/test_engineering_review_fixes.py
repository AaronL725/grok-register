"""Regression tests for the engineering review fixes."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cpa_export
from app_config import DEFAULT_CONFIG
from proxy_pool_v3 import ProxyPoolManager, get_manager, reset_manager
from proxy_protocol_runtime import ProtocolRuntimeManager, RuntimeEntry
from proxy_protocols import parse_proxy_line

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


class RuntimeLeaseRegressionTests(unittest.TestCase):
    def test_old_lease_cannot_release_replacement_runtime(self):
        manager = ProtocolRuntimeManager({"proxy_runtime_idle_ttl_sec": 0})
        node = parse_proxy_line("socks5://127.0.0.1:1080")
        old_bridge = Mock(server=object())
        new_bridge = Mock(server=object())
        old = RuntimeEntry(node.node_id, None, 32001, "", bridge=old_bridge, kind="bridge")
        new = RuntimeEntry(node.node_id, None, 32002, "", bridge=new_bridge, kind="bridge")
        with patch.object(manager, "_start_bridge_entry", side_effect=[old, new]):
            _, old_lease = manager.acquire(node)
            old_bridge.server = None  # The first runtime dies while still leased.
            endpoint, new_lease = manager.acquire(node)
            self.assertEqual(endpoint, "http://127.0.0.1:32002")
            self.assertIsNot(old_lease, new_lease)
            manager.release(old_lease)
            self.assertEqual(manager.active_snapshot()[node.node_id]["refcount"], 1)
            new_bridge.stop.assert_not_called()
            manager.release(new_lease)
            self.assertEqual(manager.active_snapshot(), {})
            new_bridge.stop.assert_called_once()


class ProxyManagerRegressionTests(unittest.TestCase):
    def setUp(self):
        reset_manager()

    def tearDown(self):
        reset_manager()

    def test_reconfiguration_does_not_restore_old_health_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.json")
            cfg = dict(DEFAULT_CONFIG, proxy_mode="single",
                       proxy="http://127.0.0.1:8011",
                       proxy_pool_state_file=path, proxy_pool_persist_health=True,
                       proxy_pool_probe_interval_sec=0)
            old = get_manager(cfg)
            lease = old.acquire("a", "worker", 1, 1, "session", timeout=1)
            old.report_success(lease)
            old.release(lease)
            newer = get_manager({**cfg, "proxy": "http://127.0.0.1:8012"})
            current_ids = set(newer._nodes)
            saved_ids = set(json.loads(Path(path).read_text(encoding="utf-8"))["nodes"])
            self.assertEqual(saved_ids, current_ids)
            self.assertNotIn(next(iter(old._nodes)), saved_ids)
            reset_manager()

    def test_fallback_runtime_initializes_outside_pool_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "pool.txt"
            source.write_text("http://127.0.0.1:8011\n", encoding="utf-8")
            cfg = dict(DEFAULT_CONFIG, proxy_mode="pool", proxy_pool_file=str(source),
                       proxy_pool_probe_interval_sec=0, proxy_fallback="single",
                       proxy="socks5://127.0.0.1:1080")
            manager = ProxyPoolManager(cfg)
            next(iter(manager._nodes.values())).enabled = False

            def start_runtime(descriptor):
                self.assertFalse(manager._lock._is_owned())
                return "http://127.0.0.1:32003", None

            with patch.object(manager._runtime, "acquire", side_effect=start_runtime):
                lease = manager.acquire("fallback", "worker", 1, 1, "session", timeout=1)
                self.assertEqual(lease.node_id, "fallback-single")
                self.assertEqual(lease.proxy_url, "http://127.0.0.1:32003")
                manager.release(lease)
            manager.shutdown()


class CpaHotloadRegressionTests(unittest.TestCase):
    def test_target_is_not_replaced_until_copy_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "auth" / "xai-test.json"
            source.parent.mkdir()
            source.write_text('{"token":"new"}', encoding="utf-8")
            hotload = root / "hotload"
            hotload.mkdir()
            target = hotload / source.name
            target.write_text('{"token":"old"}', encoding="utf-8")

            def mint(**_):
                return {"ok": True, "path": str(source)}

            actual_replace = os.replace
            observed = []

            def replace(temp, destination):
                observed.append(target.read_text(encoding="utf-8"))
                return actual_replace(temp, destination)

            config = {"cpa_export_enabled": True, "cpa_auth_dir": str(source.parent),
                      "cpa_copy_to_hotload": True, "cpa_hotload_dir": str(hotload)}
            with patch.object(cpa_export, "_load_mint_and_export", return_value=mint), \
                 patch.object(cpa_export.os, "replace", side_effect=replace):
                result = cpa_export.export_cpa_xai_for_account("user@example.com", "password", config=config)
            self.assertTrue(result["ok"])
            self.assertEqual(observed, ['{"token":"old"}'])
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"token": "new"})
            self.assertEqual(list(hotload.glob(".cpa-hotload-*")), [])

    def test_copy_failure_keeps_previous_target_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.json"
            source.write_text("new", encoding="utf-8")
            hotload = root / "hotload"
            hotload.mkdir()
            target = hotload / "original.json"
            target.write_text("old", encoding="utf-8")
            config = {"cpa_export_enabled": True, "cpa_copy_to_hotload": True,
                      "cpa_hotload_dir": str(hotload)}
            with patch.object(cpa_export, "_load_mint_and_export",
                              return_value=lambda **_: {"ok": True, "path": str(source)}), \
                 patch.object(cpa_export.shutil, "copy2", side_effect=OSError("disk full")):
                result = cpa_export.export_cpa_xai_for_account("user@example.com", "pw", config=config)
            self.assertTrue(result["partial"])
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(hotload.glob(".cpa-hotload-*")), [])


@unittest.skipIf(TestClient is None, "web dependencies not installed")
class WebOriginRegressionTests(unittest.TestCase):
    def setUp(self):
        from web.server import app
        self.client = TestClient(app)

    def test_cross_origin_control_requests_do_not_run(self):
        for path, method in (
            ("/api/config", "put"),
            ("/api/start", "post"),
            ("/api/stop", "post"),
            ("/api/proxy-pool/reload", "post"),
            ("/api/proxy-pool/test", "post"),
            ("/api/proxy-pool/preflight?node_id=x", "post"),
            ("/api/mailboxes/outlook/test", "post"),
        ):
            with self.subTest(path=path):
                response = getattr(self.client, method)(
                    path, headers={"Origin": "https://example.com"}
                )
                self.assertEqual(response.status_code, 403)
                response = getattr(self.client, method)(
                    path, headers={"Sec-Fetch-Site": "cross-site"}
                )
                self.assertEqual(response.status_code, 403)

    def test_correct_local_origin_allowed_but_other_local_port_denied(self):
        from web.server import app
        with TestClient(app, base_url="http://127.0.0.1:8092") as client:
            valid = client.post("/api/stop", headers={"Origin": "http://127.0.0.1:8092"})
            invalid = client.post("/api/stop", headers={"Origin": "http://127.0.0.1:8093"})
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(invalid.status_code, 403)
