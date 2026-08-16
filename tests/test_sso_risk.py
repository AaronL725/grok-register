"""验证 SSO botFlag / policy 早停：解析、判定、隔离和入库拦截。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


class ParseAndPolicyTests(unittest.TestCase):
    def test_parse_escaped_next_payload(self):
        state = sso_risk.parse_grok_account_state(_html())
        self.assertTrue(state["found"])
        self.assertEqual(state["bot_flag_source"], 2)
        self.assertEqual(state["policy"], "deny")
        self.assertEqual(state["event"], "$registration")
        self.assertTrue(state["denied"])
        self.assertAlmostEqual(state["risk"], 0.95)

    def test_block_policy(self):
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

    def test_unknown_state_continues(self):
        response = SimpleNamespace(status_code=200, url="https://grok.com/", text="<html></html>")
        state = sso_risk.ensure_sso_eligible(
            "clean-or-unknown-token",
            http_get=lambda *_args, **_kwargs: response,
        )
        self.assertFalse(state["found"])
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
