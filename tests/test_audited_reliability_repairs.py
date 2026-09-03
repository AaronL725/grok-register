"""Regression coverage for the audited reliability repair set."""

import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import account_outputs
import browser_runtime
import grok_register_ttk as app
import mail_service
import proxy_bridge
import registration_browser
import registration_flow
from app_config import DEFAULT_CONFIG
from proxy_pool import ProxyPoolManager
from registration_flow import (
    OUTCOME_UNCERTAIN,
    SAME_LEASE_RECOVERY,
    STAGE_CODE_SUBMIT,
    STAGE_CODE_WAIT,
    RegistrationCallbacks,
    RegistrationOperations,
    VerificationSubmissionUnconfirmed,
    registration_retry_disposition,
    run_batch,
)


class Cancelled(Exception):
    pass


class RetryNeeded(Exception):
    pass


def _base_ops(fill_code, state=None):
    state = state if state is not None else {}
    return RegistrationOperations(
        start_browser=lambda: None,
        restart_browser=lambda: state.__setitem__("restarts", state.get("restarts", 0) + 1),
        browser_missing=lambda: False,
        open_signup_page=lambda: None,
        fill_email_and_submit=lambda: ("user@example.com", "mail-token"),
        save_mail_credential=lambda _email, _token: True,
        fill_code_and_submit=fill_code,
        fill_profile_and_submit=lambda: {
            "given_name": "A",
            "family_name": "B",
            "password": "pw",
        },
        wait_for_sso_cookie=lambda: "sso-token",
        enable_nsfw=lambda _sso: (True, "ok"),
        persist_account_line=lambda *_args: None,
        queue_unsaved_result=lambda *_args: True,
        add_tokens=lambda *_args: {},
        export_cpa=lambda *_args: {"ok": True, "skipped": False},
        cleanup=lambda _reason: None,
        sleep=lambda _seconds: None,
        cancelled_exception=Cancelled,
        retry_exception=RetryNeeded,
        internal_stage_markers=True,
    )


class AuditedReliabilityRepairTests(unittest.TestCase):
    def test_retry_disposition_distinguishes_code_wait_from_code_submit(self):
        self.assertEqual(registration_retry_disposition(STAGE_CODE_WAIT), SAME_LEASE_RECOVERY)
        self.assertEqual(registration_retry_disposition(STAGE_CODE_SUBMIT), OUTCOME_UNCERTAIN)

    def test_verification_text_no_longer_triggers_mailbox_retry(self):
        state = {"restarts": 0}

        def fail_after_code_submit(_email, _token):
            registration_flow._set_registration_stage(STAGE_CODE_SUBMIT)
            raise RuntimeError("验证码已获取，但自动填写/提交失败")

        callbacks = RegistrationCallbacks(log=lambda _message: None, cancelled=lambda: False)
        ops = _base_ops(fail_after_code_submit, state)
        begins = []

        with patch("registration_flow.begin_registration_slot", side_effect=lambda **kw: begins.append(kw)), \
             patch("registration_flow.end_registration_slot"), \
             patch("registration_flow.current_proxy_lease", return_value=object()):
            result = run_batch(
                1,
                callbacks,
                lambda *_args: None,
                ops,
                enable_nsfw=False,
                max_mail_retry=3,
            )

        self.assertEqual(len(begins), 1)
        self.assertEqual(state["restarts"], 0)
        self.assertEqual(result.fail_count, 1)
        self.assertEqual(result.uncertain_count, 1)

    def test_mail_credential_write_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(account_outputs.save_mail_credential(directory, "u@example.com", "token"))
            self.assertTrue(account_outputs.save_mail_credential(directory, "u@example.com", "token"))
            lines = (Path(directory) / "mail_credentials.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, ["u@example.com\ttoken"])

    def test_mail_credential_is_persisted_before_email_submit_boundary(self):
        events = []

        class FakePage:
            url = "https://accounts.x.ai/sign-up"

            def run_js(self, script, *args):
                if "const email = arguments[0]" in script:
                    events.append("fill-email")
                    return {"state": "filled", "url": self.url}
                if "return (input.getAttribute('type')" in script:
                    return True
                if "const submitButton = buttons.find" in script:
                    events.append("submit-dom")
                    return "clicked"
                raise AssertionError("unexpected JS path")

        def mark(stage):
            events.append("stage:" + stage)
            return stage

        with patch.object(registration_browser, "page", FakePage()), \
             patch.object(registration_browser, "get_email_and_token", return_value=("u@example.com", "token"), create=True), \
             patch.object(registration_browser, "raise_if_cancelled", return_value=None, create=True), \
             patch.object(registration_browser, "sleep_with_cancel", return_value=None, create=True), \
             patch.object(registration_browser, "_mark_registration_stage", side_effect=mark):
            result = registration_browser.fill_email_and_submit(
                timeout=2,
                on_mail_created=lambda email, token: events.append("persist") or True,
            )

        self.assertEqual(result, ("u@example.com", "token"))
        self.assertLess(events.index("persist"), events.index("stage:email_submit"))
        self.assertLess(events.index("stage:email_submit"), events.index("submit-dom"))

    def test_no_button_otp_requires_confirmed_page_transition(self):
        markers = []

        class FakePage:
            def run_js(self, script, *args):
                if "if (!code) return 'not-ready'" in script:
                    return "aggregate"
                if "function setInputValue" in script:
                    return "filled-aggregate"
                if "if (!btn) return 'no-button'" in script:
                    return "no-button"
                if "const markers = [" in script:
                    return ""
                raise AssertionError("unexpected JS path")

        class Clock:
            def __init__(self):
                self.value = 0.0

            def now(self):
                self.value += 0.25
                return self.value

        clock = Clock()
        with patch.object(registration_browser, "page", FakePage()), \
             patch.object(registration_browser, "get_oai_code", return_value="ABC-123", create=True), \
             patch.object(registration_browser, "raise_if_cancelled", return_value=None, create=True), \
             patch.object(registration_browser, "sleep_with_cancel", return_value=None, create=True), \
             patch.object(registration_browser, "has_profile_form", return_value=False), \
             patch.object(registration_browser, "_mark_registration_stage", side_effect=lambda stage: markers.append(stage)), \
             patch.object(registration_browser.time, "time", side_effect=clock.now):
            with self.assertRaises(VerificationSubmissionUnconfirmed):
                registration_browser.fill_code_and_submit(
                    "u@example.com",
                    "mail-token",
                    timeout=5,
                    transition_timeout=1,
                )

        self.assertIn(STAGE_CODE_WAIT, markers)
        self.assertIn(STAGE_CODE_SUBMIT, markers)

    def test_no_button_otp_can_succeed_after_auto_transition(self):
        class FakePage:
            def run_js(self, script, *args):
                if "if (!code) return 'not-ready'" in script:
                    return "aggregate"
                if "function setInputValue" in script:
                    return "filled-aggregate"
                if "if (!btn) return 'no-button'" in script:
                    return "no-button"
                raise AssertionError("unexpected JS path")

        with patch.object(registration_browser, "page", FakePage()), \
             patch.object(registration_browser, "get_oai_code", return_value="ABC-123", create=True), \
             patch.object(registration_browser, "raise_if_cancelled", return_value=None, create=True), \
             patch.object(registration_browser, "sleep_with_cancel", return_value=None, create=True), \
             patch.object(registration_browser, "has_profile_form", return_value=True), \
             patch.object(registration_browser, "_mark_registration_stage"):
            code = registration_browser.fill_code_and_submit(
                "u@example.com",
                "mail-token",
                timeout=5,
                transition_timeout=1,
            )
        self.assertEqual(code, "ABC-123")

    def test_same_mail_message_can_be_retried_more_than_five_times(self):
        message = {"id": "m1", "to": [{"address": "user@example.com"}]}
        clock = {"now": 0.0}
        calls = {"detail": 0}

        def now():
            return clock["now"]

        def sleep(seconds, _cancel=None):
            clock["now"] += max(float(seconds), 3.0)

        def detail(_token, _message_id):
            calls["detail"] += 1
            if calls["detail"] <= 5:
                return {"subject": "xAI verification"}
            return {"subject": "ABC-123 xAI"}

        with patch.object(mail_service, "get_messages", return_value=[message]), \
             patch.object(mail_service, "get_message_detail", side_effect=detail), \
             patch.object(mail_service, "sleep_with_cancel", side_effect=sleep), \
             patch.object(mail_service.time, "time", side_effect=now):
            code = mail_service.duckmail_get_oai_code(
                "token",
                "user@example.com",
                timeout=100,
                poll_interval=3,
            )

        self.assertEqual(code, "ABC-123")
        self.assertGreaterEqual(calls["detail"], 6)

    def test_chromium_normalizes_socks5h_and_bridges_socks4a(self):
        self.assertEqual(
            proxy_bridge.proxy_for_chromium("socks5h://127.0.0.1:1080"),
            "socks5://127.0.0.1:1080",
        )

        class FakeBridge:
            def __init__(self, raw):
                self.raw = raw

            def start(self):
                return "http://127.0.0.1:32123"

        with patch.object(proxy_bridge, "LocalProxyBridge", FakeBridge):
            endpoint, bridge = proxy_bridge.prepare_chromium_proxy(
                "socks4a://127.0.0.1:1080"
            )
        self.assertEqual(endpoint, "http://127.0.0.1:32123")
        self.assertEqual(bridge.raw, "socks4a://127.0.0.1:1080")

    def test_scheduled_proxy_refresh_is_single_flight(self):
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({
            "proxy_mode": "single",
            "proxy": "http://127.0.0.1:8001",
            "proxy_pool_refresh_interval_sec": 1,
            "proxy_pool_probe_interval_sec": 0,
        })
        manager = ProxyPoolManager(cfg)
        manager._last_refresh = 0.0
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def fake_reload(force=False):
            calls.append(force)
            entered.set()
            release.wait(2)
            with manager._lock:
                manager._last_refresh = time.time()
            return manager.snapshot()

        manager._reload_sources_locked = fake_reload
        first = threading.Thread(target=manager.refresh_if_due)
        second = threading.Thread(target=manager.refresh_if_due)
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        second.join(1)
        self.assertFalse(second.is_alive(), "second scheduled refresh should not wait on the refresh lock")
        release.set()
        first.join(2)
        try:
            self.assertEqual(calls, [True])
        finally:
            manager.shutdown()

    def test_gui_registration_start_is_blocked_by_proxy_test(self):
        gui = object.__new__(app.GrokRegisterGUI)
        gui.operation_lock = threading.Lock()
        gui.is_running = False
        gui.registration_starting = False
        gui.proxy_test_running = True
        logs = []
        gui.log = logs.append
        gui.start_registration()
        self.assertTrue(any("代理池测试进行中" in line for line in logs))

    def test_gui_proxy_test_is_blocked_while_registration_is_reserved(self):
        gui = object.__new__(app.GrokRegisterGUI)
        gui.operation_lock = threading.Lock()
        gui.is_running = False
        gui.registration_starting = True
        gui.proxy_test_running = False
        logs = []
        gui.log = logs.append
        gui.test_proxy_pool()
        self.assertTrue(any("启动或运行期间" in line for line in logs))

    def test_gui_stats_and_yyds_controls_cover_new_fields(self):
        gui = object.__new__(app.GrokRegisterGUI)
        gui.ui_queue = queue.Queue()
        gui.success_count = 1
        gui.fail_count = 2
        gui.uncertain_count = 1
        gui.registered_unsaved_count = 3
        gui.postprocess_warning_count = 4
        gui.update_stats()
        self.assertEqual(gui.ui_queue.get_nowait(), ("stats", 1, 2, 1, 3, 4))

        source = Path(app.__file__).read_text(encoding="utf-8")
        self.assertIn("self.yyds_api_key_var", source)
        self.assertIn("self.yyds_jwt_var", source)
        self.assertIn('config["yyds_api_key"] = self.yyds_api_key_var.get().strip()', source)
        self.assertIn('config["yyds_jwt"] = self.yyds_jwt_var.get().strip()', source)

    def test_legacy_extension_discovery_is_centralized_and_optional(self):
        legacy = str(Path(browser_runtime.__file__).resolve().parent / "turnstilePatch")
        with patch.object(browser_runtime, "_extension_path", ""), \
             patch.object(browser_runtime.os.path, "isdir", side_effect=lambda path: str(path) == legacy):
            self.assertEqual(browser_runtime._resolve_extension_path(), legacy)
        with patch.object(browser_runtime.os.path, "isdir", return_value=False):
            self.assertEqual(browser_runtime._resolve_extension_path(), "")


if __name__ == "__main__":
    unittest.main()
