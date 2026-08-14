import os
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import proxy_bridge
import registration_flow
from app_config import DEFAULT_CONFIG
from proxy_bridge import LocalProxyBridge
from proxy_pool import ProxyPoolError, ProxyPoolManager, ProxyTransportError
from proxy_protocol_runtime import ProtocolRuntimeManager, RuntimeEntry
from proxy_protocols import ProxyProtocolError, parse_proxy_line
from registration_flow import (
    OUTCOME_UNCERTAIN, SAFE_NEW_LEASE, STAGE_PAGE_OPEN, STAGE_PROFILE_SUBMIT,
    RegistrationCallbacks, RegistrationOperations, registration_retry_disposition, run_batch,
)


class Cancelled(Exception):
    pass


class RetryNeeded(Exception):
    pass


class ProxyPoolV3Tests(unittest.TestCase):
    def cfg(self, **updates):
        value = dict(DEFAULT_CONFIG)
        value.update(updates)
        return value

    def test_strict_native_proxy_rejects_paths_queries_and_bad_placeholder_location(self):
        with self.assertRaises(ProxyProtocolError):
            parse_proxy_line("http://127.0.0.1:8080/path")
        with self.assertRaises(ProxyProtocolError):
            parse_proxy_line("http://127.0.0.1:8080/?x=1")
        with self.assertRaises(ProxyProtocolError):
            parse_proxy_line("http://proxy-{account}.example.com:8080")
        descriptor = parse_proxy_line("http://user-{account}:pass@127.0.0.1:8080")
        self.assertIn("{account}", descriptor.canonical_uri)

    def test_shadowsocks_sip002_is_parsed_for_singbox(self):
        descriptor = parse_proxy_line("ss://YWVzLTI1Ni1nY206c2VjcmV0@127.0.0.1:8388#SS")
        self.assertEqual(descriptor.protocol, "ss")
        self.assertEqual(descriptor.backend, "sing-box")
        self.assertEqual(descriptor.outbound_config["type"], "shadowsocks")
        self.assertEqual(descriptor.outbound_config["method"], "aes-256-gcm")
        self.assertEqual(descriptor.outbound_config["password"], "secret")

    def test_socks5_resolves_locally_while_socks5h_sends_hostname(self):
        class FakeSock:
            def __init__(self): self.sent = []
            def sendall(self, value): self.sent.append(value)
        responses = [b"\x05\x00", b"\x05\x00\x00\x01", b"\x00\x00\x00\x00", b"\x00\x00"]
        local = LocalProxyBridge("socks5://127.0.0.1:1080")
        sock = FakeSock()
        with patch.object(proxy_bridge, "_recv_exact", side_effect=responses), patch.object(socket, "getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.10", 443))]) as resolver:
            local._socks5_connect(sock, "example.com", 443)
        resolver.assert_called_once()
        self.assertIn(b"\x01\xc6\x33\x64\x0a", sock.sent[-1])

        remote = LocalProxyBridge("socks5h://127.0.0.1:1080")
        sock2 = FakeSock()
        with patch.object(proxy_bridge, "_recv_exact", side_effect=responses), patch.object(socket, "getaddrinfo") as resolver2:
            remote._socks5_connect(sock2, "example.com", 443)
        resolver2.assert_not_called()
        self.assertIn(b"\x03\x0bexample.com", sock2.sent[-1])

    def test_runtime_idle_cache_reuses_entry_and_expires_by_ttl(self):
        manager = ProtocolRuntimeManager({"proxy_runtime_idle_ttl_sec": 120, "proxy_runtime_cache_max": 4})
        descriptor = parse_proxy_line("socks5://127.0.0.1:1080")
        fake_bridge = Mock(); fake_bridge.server = object(); fake_bridge.diagnostic.return_value = None
        entry = RuntimeEntry(descriptor.node_id, None, 31234, "", bridge=fake_bridge, kind="bridge", last_used=time.time())
        with patch.object(manager, "_start_bridge_entry", return_value=entry) as starter:
            endpoint, key = manager.acquire(descriptor)
            manager.release(key)
            self.assertIn(key, manager._entries)
            endpoint2, key2 = manager.acquire(descriptor)
            self.assertEqual(endpoint2, endpoint)
            self.assertEqual(key2, key)
            self.assertEqual(starter.call_count, 1)
            manager.release(key)
            manager._entries[key].idle_since = time.time() - 121
            manager.cleanup_idle()
            self.assertNotIn(key, manager._entries)
        fake_bridge.stop.assert_called()

    def test_health_state_persistence_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state.json")
            cfg = self.cfg(proxy_mode="single", proxy="http://127.0.0.1:8001", proxy_pool_persist_health=True, proxy_pool_state_file=state, proxy_pool_probe_interval_sec=0)
            manager = ProxyPoolManager(cfg)
            lease = manager.acquire("a", "w", 1, 1, "s", timeout=1)
            manager.report_success(lease); manager.release(lease); manager.shutdown()
            restored = ProxyPoolManager(cfg)
            node = restored.snapshot()["nodes"][0]
            self.assertEqual(node["registration_successes"], 1)
            self.assertEqual(node["business_samples"], 1)
            restored.shutdown()

    def test_subscription_public_only_rejects_loopback(self):
        cfg = self.cfg(proxy_mode="pool", proxy_pool_subscription_url="http://local.test/list", proxy_pool_subscription_public_only=True)
        with patch.object(socket, "getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]):
            with self.assertRaises(ProxyPoolError):
                ProxyPoolManager(cfg)

    def test_registration_path_preflight_is_non_destructive(self):
        manager = ProxyPoolManager(self.cfg(proxy_mode="single", proxy="http://127.0.0.1:8001", proxy_pool_probe_interval_sec=0))
        node_id = manager.snapshot()["nodes"][0]["id"]
        response = Mock(status_code=302, text="", headers={})
        with patch.object(manager._runtime, "acquire", return_value=("http://127.0.0.1:3128", "key")), patch.object(manager._runtime, "release") as release, patch("proxy_pool.requests.get", return_value=response) as get:
            result = manager.preflight_node(node_id)
        self.assertTrue(result["ok"])
        self.assertEqual(get.call_count, 2)
        release.assert_called_once_with("key")

    def test_retry_disposition_marks_only_pre_submit_stages_safe(self):
        self.assertEqual(registration_retry_disposition(STAGE_PAGE_OPEN), SAFE_NEW_LEASE)
        self.assertEqual(registration_retry_disposition(STAGE_PROFILE_SUBMIT), OUTCOME_UNCERTAIN)

    def _ops(self, failure_stage=None, state=None):
        state = state or {"profile_calls": 0, "page_calls": 0}
        def page():
            state["page_calls"] += 1
            if failure_stage == "page" and state["page_calls"] == 1:
                raise ProxyTransportError("connection refused")
        def profile():
            state["profile_calls"] += 1
            if failure_stage == "profile":
                raise ProxyTransportError("connection reset")
            return {"given_name": "A", "family_name": "B", "password": "pw"}
        return RegistrationOperations(
            start_browser=lambda: None, restart_browser=lambda: None, browser_missing=lambda: False,
            open_signup_page=page, fill_email_and_submit=lambda: ("a@example.com", "token"),
            save_mail_credential=lambda *_: True, fill_code_and_submit=lambda *_: "123456",
            fill_profile_and_submit=profile, wait_for_sso_cookie=lambda: "sso", enable_nsfw=lambda _: (True, "ok"),
            persist_account_line=lambda *_: None, queue_unsaved_result=lambda *_: True, add_tokens=lambda *_: {},
            export_cpa=lambda *_: {"ok": True, "skipped": False}, cleanup=lambda _: None, sleep=lambda _: None,
            cancelled_exception=Cancelled, retry_exception=RetryNeeded,
        )

    def test_profile_transport_failure_is_not_replayed_with_new_lease(self):
        callbacks = RegistrationCallbacks(log=lambda _: None, cancelled=lambda: False)
        begins = []
        with patch.dict(registration_flow.app_config, {"proxy_mode": "single"}, clear=False), patch("registration_flow.begin_registration_slot", side_effect=lambda **kw: begins.append(kw)), patch("registration_flow.end_registration_slot"), patch("registration_flow.current_proxy_lease", return_value=object()):
            result = run_batch(1, callbacks, lambda *_: None, self._ops("profile"), enable_nsfw=False)
        self.assertEqual(len(begins), 1)
        self.assertEqual(result.processed_count, 1)
        self.assertEqual(result.uncertain_count, 1)

    def test_page_transport_failure_can_retry_with_new_lease(self):
        callbacks = RegistrationCallbacks(log=lambda _: None, cancelled=lambda: False)
        begins = []; state = {"profile_calls": 0, "page_calls": 0}
        with patch.dict(registration_flow.app_config, {"proxy_mode": "single"}, clear=False), patch("registration_flow.begin_registration_slot", side_effect=lambda **kw: begins.append(kw)), patch("registration_flow.end_registration_slot"), patch("registration_flow.current_proxy_lease", return_value=object()):
            result = run_batch(1, callbacks, lambda *_: None, self._ops("page", state), enable_nsfw=False, max_slot_retry=2)
        self.assertEqual(len(begins), 2)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.uncertain_count, 0)


if __name__ == "__main__":
    unittest.main()
