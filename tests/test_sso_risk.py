"""验证 SSO botFlag / policy 早停：解析、判定、隔离、恢复和入库拦截。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sso_risk
from registration_flow import (
    RegistrationCallbacks,
    RegistrationOperations,
    persist_account_result,
    run_batch,
)


class Cancelled(Exception):
    pass


class Retryable(Exception):
    pass


def _html(source=2, details="risk=0.95,policy=deny,event=$registration"):
    return r'self.__next_f.push([1, "{\"botFlagSource\":%s,\"botFlagDetails\":\"%s\"}"])' % (
        "null" if source is None else source,
        details,
    )


def _html_sequence(*states):
    return "\n".join(_html(source, details) for source, details in states)


class ParseAndPolicyTests(unittest.TestCase):
    def test_parse_escaped_next_payload(self):
        state = sso_risk.parse_grok_account_state(_html())
        self.assertTrue(state["found"])
        self.assertEqual(state["bot_flag_source"], 2)
        self.assertEqual(state["bot_flag_sources"], [2])
        self.assertEqual(state["policy"], "deny")
        self.assertEqual(state["event"], "$registration")
        self.assertTrue(state["denied"])
        self.assertAlmostEqual(state["risk"], 0.95)

    def test_later_flagged_source_cannot_be_hidden_by_earlier_clean_state(self):
        state = sso_risk.parse_grok_account_state(
            _html_sequence(
                (0, "risk=0.01,policy=allow,event=$registration"),
                (2, "risk=0.95,policy=deny,event=$registration"),
            )
        )
        self.assertEqual(state["bot_flag_sources"], [0, 2])
        self.assertEqual(state["bot_flag_source"], 2)
        blocked, detail = sso_risk.registration_risk_should_block(state)
        self.assertTrue(blocked)
        self.assertIn("policy=deny", detail)

    def test_later_clean_state_cannot_clear_earlier_flagged_state(self):
        state = sso_risk.parse_grok_account_state(
            _html_sequence(
                (1, "risk=0.90,policy=deny,event=$login"),
                (0, "risk=0.01,policy=allow,event=$registration"),
            )
        )
        self.assertEqual(state["bot_flag_sources"], [1, 0])
        self.assertEqual(state["bot_flag_source"], 1)
        self.assertTrue(sso_risk.registration_risk_should_block(state)[0])

    def test_null_then_flagged_is_blocked(self):
        state = sso_risk.parse_grok_account_state(
            _html_sequence(
                (None, ""),
                (2, "risk=0.9,policy=allow,event=$registration"),
            )
        )
        self.assertEqual(state["bot_flag_sources"], [None, 2])
        self.assertTrue(sso_risk.registration_risk_should_block(state)[0])

    def test_any_deny_detail_blocks_even_when_sources_are_clean(self):
        state = sso_risk.parse_grok_account_state(
            _html_sequence(
                (0, "risk=0.01,policy=allow,event=$registration"),
                (0, "risk=0.80,policy=deny,event=$login"),
            )
        )
        self.assertEqual(state["bot_flag_source"], 0)
        blocked, detail = sso_risk.registration_risk_should_block(state)
        self.assertTrue(blocked)
        self.assertIn("event=$login", detail)

    def test_malformed_risk_float_does_not_crash(self):
        state = sso_risk.parse_grok_account_state(
            _html(0, "risk=not-a-number,policy=allow,event=$registration")
        )
        self.assertTrue(state["found"])
        self.assertIsNone(state["risk"])
        self.assertFalse(sso_risk.registration_risk_should_block(state)[0])

    def test_block_policy_legacy_state_shape(self):
        blocked_cases = (
            ({"denied": True}, "policy=deny,event=$registration"),
            ({"bot_flag_source": 1}, "botFlagSource=1"),
            ({"bot_flag_source": 2}, "botFlagSource=2"),
            ({"policy": "deny", "event": "$login"}, "policy=deny,event=$login"),
        )
        for state, expected in blocked_cases:
            blocked, detail = sso_risk.registration_risk_should_block(state)
            self.assertTrue(blocked)
            self.assertEqual(detail, expected)

        for state in ({"found": True, "bot_flag_source": 0}, {"found": False}, {}, None):
            self.assertEqual(sso_risk.registration_risk_should_block(state), (False, ""))


class EnsureEligibleTests(unittest.TestCase):
    def setUp(self):
        self._prev_config = sso_risk.config
        self._prev_http = sso_risk._http_get
        self.tmp = tempfile.TemporaryDirectory()
        rejected = str(Path(self.tmp.name) / "sso_risk_rejected.txt")
        sso_risk.configure_risk_runtime(
            {"sso_risk_gate_enabled": True, "sso_risk_rejected_file": rejected},
            None,
        )

    def tearDown(self):
        sso_risk.config = self._prev_config
        sso_risk._http_get = self._prev_http
        self.tmp.cleanup()

    def test_flagged_sso_is_quarantined_and_raised(self):
        response = SimpleNamespace(status_code=200, url="https://grok.com/", text=_html(2))
        with self.assertRaises(sso_risk.RegistrationRiskDenied):
            sso_risk.ensure_sso_eligible(
                "sso=flagged-token",
                email="risk@example.test",
                http_get=lambda *_args, **_kwargs: response,
            )
        text = Path(sso_risk.resolve_rejected_file()).read_text(encoding="utf-8")
        self.assertIn("risk@example.test----flagged-token----", text)
        self.assertIn("policy=deny", text)
        self.assertFalse(Path(sso_risk.resolve_rejected_pending_file()).exists())

    def test_primary_quarantine_failure_falls_back_to_risk_pending(self):
        response = SimpleNamespace(status_code=200, url="https://grok.com/", text=_html(2))
        with patch("sso_risk._append_rejected_line", side_effect=OSError("disk full")):
            with self.assertRaises(sso_risk.RegistrationRiskDenied):
                sso_risk.ensure_sso_eligible(
                    "flagged-token",
                    email="risk@example.test",
                    http_get=lambda *_args, **_kwargs: response,
                )

        pending = Path(sso_risk.resolve_rejected_pending_file())
        self.assertTrue(pending.exists())
        rows = [json.loads(line) for line in pending.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email"], "risk@example.test")
        self.assertEqual(rows[0]["sso"], "flagged-token")
        self.assertIn("disk full", rows[0]["primary_error"])

    def test_risk_pending_recovery_is_idempotent(self):
        sso_risk.queue_sso_risk_rejected_pending(
            "risk@example.test",
            "flagged-token",
            "risk=0.95,policy=deny,event=$registration",
            primary_error="disk full",
        )
        first = sso_risk.retry_sso_risk_pending_file()
        second = sso_risk.retry_sso_risk_pending_file()

        self.assertEqual(first["processed"], 1)
        self.assertEqual(first["recovered"], 1)
        self.assertEqual(first["remaining"], 0)
        self.assertEqual(second["processed"], 0)
        lines = Path(sso_risk.resolve_rejected_file()).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("risk@example.test----flagged-token----"))

    def test_primary_and_pending_failure_raise_dedicated_error(self):
        with patch("sso_risk._append_rejected_line", side_effect=OSError("disk full")), patch(
            "sso_risk.queue_sso_risk_rejected_pending",
            side_effect=OSError("pending unavailable"),
        ):
            with self.assertRaises(sso_risk.RegistrationRiskPersistenceError):
                sso_risk.append_sso_risk_rejected(
                    "risk@example.test",
                    "flagged-token",
                    "policy=deny,event=$registration",
                )

    def test_unknown_state_continues(self):
        response = SimpleNamespace(status_code=200, url="https://grok.com/", text="<html></html>")
        state = sso_risk.ensure_sso_eligible(
            "clean-or-unknown-token",
            http_get=lambda *_args, **_kwargs: response,
        )
        self.assertFalse(state["found"])
        self.assertFalse(Path(sso_risk.resolve_rejected_file()).exists())

    def test_transport_failure_is_fail_open_but_reports_suspected_health(self):
        class TransportError(RuntimeError):
            pass

        manager = Mock()
        lease = object()

        def failing_get(*_args, **_kwargs):
            raise TransportError("proxy connection reset")

        with patch("proxy_pool.is_proxy_transport_exception", return_value=True), patch(
            "proxy_pool.current_proxy_lease", return_value=lease
        ), patch("proxy_pool.get_manager", return_value=manager):
            state = sso_risk.ensure_sso_eligible(
                "transport-unknown-token",
                http_get=failing_get,
            )

        self.assertFalse(state["found"])
        self.assertTrue(state["transport_error"])
        self.assertIn("proxy connection reset", state["error"])
        manager.report_suspected_transport_failure.assert_called_once_with(
            lease, "proxy connection reset"
        )
        self.assertFalse(Path(sso_risk.resolve_rejected_file()).exists())

    def test_disabled_gate_skips_http(self):
        sso_risk.config["sso_risk_gate_enabled"] = False
        called = []
        state = sso_risk.ensure_sso_eligible(
            "any-token",
            http_get=lambda *_args, **_kwargs: called.append(True),
        )
        self.assertTrue(state.get("skipped"))
        self.assertEqual(called, [])


def _ops(screen_sso=None, events=None):
    events = events if events is not None else []
    return RegistrationOperations(
        start_browser=lambda: None,
        restart_browser=lambda: None,
        browser_missing=lambda: False,
        open_signup_page=lambda: None,
        fill_email_and_submit=lambda: ("user@example.com", "mail-token"),
        save_mail_credential=lambda email, token: True,
        fill_code_and_submit=lambda email, token: "123456",
        fill_profile_and_submit=lambda: {"given_name": "A", "family_name": "B", "password": "pw"},
        wait_for_sso_cookie=lambda: "sso-token",
        enable_nsfw=lambda sso: (True, "ok"),
        persist_account_line=lambda email, password, sso: events.append(("persist", email, sso)),
        queue_unsaved_result=lambda payload, error: events.append(("pending", payload, error)) or True,
        add_tokens=lambda sso, email: events.append(("tokens", sso, email)) or {
            "local": {"enabled": False, "ok": None, "error": None},
            "remote": {"enabled": False, "ok": None, "error": None},
        },
        export_cpa=lambda email, password, sso: events.append(("cpa", email, sso)) or {"ok": False, "skipped": True},
        cleanup=lambda reason: events.append(("cleanup", reason)),
        sleep=lambda seconds: None,
        cancelled_exception=Cancelled,
        retry_exception=Retryable,
        screen_sso=screen_sso,
    ), events


class FlowGateTests(unittest.TestCase):
    def test_screen_failure_skips_persist_and_pools(self):
        def screen(_sso, _email):
            raise sso_risk.RegistrationRiskDenied("botFlagSource=2 policy=deny")

        ops, events = _ops(screen_sso=screen)
        logs = []
        batch = run_batch(1, RegistrationCallbacks(log=logs.append, cancelled=lambda: False), lambda *a: None, ops)
        self.assertEqual(batch.success_count, 0)
        self.assertEqual(batch.fail_count, 1)
        self.assertEqual(batch.processed_count, 1)
        self.assertFalse(any(item[0] == "persist" for item in events))
        self.assertFalse(any(item[0] == "tokens" for item in events))
        self.assertFalse(any(item[0] == "cpa" for item in events))
        self.assertTrue(any("注册风控拒绝" in line for line in logs))

    def test_risk_persistence_failure_also_never_enters_normal_pool(self):
        def screen(_sso, _email):
            raise sso_risk.RegistrationRiskPersistenceError("quarantine unavailable")

        ops, events = _ops(screen_sso=screen)
        logs = []
        batch = run_batch(
            1,
            RegistrationCallbacks(log=logs.append, cancelled=lambda: False),
            lambda *a: None,
            ops,
        )
        self.assertEqual(batch.success_count, 0)
        self.assertEqual(batch.fail_count, 1)
        self.assertFalse(any(item[0] == "persist" for item in events))
        self.assertFalse(any(item[0] == "pending" for item in events))
        self.assertFalse(any(item[0] == "tokens" for item in events))
        self.assertFalse(any(item[0] == "cpa" for item in events))

    def test_persist_calls_screen_before_write(self):
        seen = []

        def screen(sso, email):
            seen.append((sso, email))

        ops, events = _ops(screen_sso=screen)
        result = SimpleNamespace(email="a@example.com", password="pw", sso="sso-token", profile={})
        persist_account_result(result, RegistrationCallbacks(log=lambda *_: None, cancelled=lambda: False), ops)
        self.assertEqual(seen, [("sso-token", "a@example.com")])
        self.assertEqual(events[0], ("persist", "a@example.com", "sso-token"))


if __name__ == "__main__":
    unittest.main()
