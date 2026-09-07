"""Regression coverage for Turnstile waiting and interactive completion (issue #79)."""

import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import registration_browser


class Cancelled(Exception):
    pass


def _state(status, token=""):
    return {
        "state": status,
        "present": status != registration_browser.TURNSTILE_ABSENT,
        "token": token,
        "token_length": len(token),
        "widget_present": status in {
            registration_browser.TURNSTILE_WAITING,
            registration_browser.TURNSTILE_SOLVED,
            registration_browser.TURNSTILE_FAILED,
        },
        "iframe_present": status == registration_browser.TURNSTILE_WAITING,
        "script_present": status != registration_browser.TURNSTILE_ABSENT,
        "visible": status == registration_browser.TURNSTILE_WAITING,
    }


class TurnstileRegressionTests(unittest.TestCase):
    def test_existing_short_token_is_accepted_immediately(self):
        with patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value=_state(registration_browser.TURNSTILE_SOLVED, "ok"),
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(registration_browser, "page", object()):
            token = registration_browser.getTurnstileToken(timeout=5)

        self.assertEqual(token, "ok")

    def test_loading_can_finish_automatically(self):
        clock = {"now": 0.0}
        states = [
            _state(registration_browser.TURNSTILE_LOADING),
            _state(registration_browser.TURNSTILE_LOADING),
            _state(registration_browser.TURNSTILE_SOLVED, "ready"),
        ]

        def now():
            return clock["now"]

        def sleep(_seconds, _cancel=None):
            clock["now"] += 1.0

        with patch.object(
            registration_browser, "_read_turnstile_state", side_effect=states
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(
            registration_browser, "sleep_with_cancel", side_effect=sleep, create=True
        ), patch.object(
            registration_browser.time, "time", side_effect=now
        ), patch.object(
            registration_browser, "page", object()
        ):
            token = registration_browser.getTurnstileToken(timeout=10)

        self.assertEqual(token, "ready")

    def test_interactive_wait_can_finish_after_user_completion(self):
        clock = {"now": 0.0}
        logs = []
        states = [
            _state(registration_browser.TURNSTILE_WAITING),
            _state(registration_browser.TURNSTILE_WAITING),
            _state(registration_browser.TURNSTILE_WAITING),
            _state(registration_browser.TURNSTILE_SOLVED, "ready"),
        ]

        def now():
            return clock["now"]

        def sleep(_seconds, _cancel=None):
            clock["now"] += 2.0

        with patch.object(
            registration_browser, "_read_turnstile_state", side_effect=states
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(
            registration_browser, "sleep_with_cancel", side_effect=sleep, create=True
        ), patch.object(
            registration_browser.time, "time", side_effect=now
        ), patch.object(
            registration_browser, "page", object()
        ):
            token = registration_browser.getTurnstileToken(
                timeout=20,
                log_callback=logs.append,
            )

        self.assertEqual(token, "ready")
        self.assertTrue(any("请在当前浏览器窗口完成验证" in item for item in logs))

    def test_absent_challenge_returns_to_page_re_evaluation(self):
        with patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value=_state(registration_browser.TURNSTILE_ABSENT),
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(registration_browser, "page", object()):
            token = registration_browser.getTurnstileToken(timeout=5)

        self.assertEqual(token, "")

    def test_challenge_disappearing_while_waiting_returns_for_re_evaluation(self):
        clock = {"now": 0.0}
        states = [
            _state(registration_browser.TURNSTILE_WAITING),
            _state(registration_browser.TURNSTILE_ABSENT),
        ]

        def now():
            return clock["now"]

        def sleep(_seconds, _cancel=None):
            clock["now"] += 1.0

        with patch.object(
            registration_browser, "_read_turnstile_state", side_effect=states
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(
            registration_browser, "sleep_with_cancel", side_effect=sleep, create=True
        ), patch.object(
            registration_browser.time, "time", side_effect=now
        ), patch.object(
            registration_browser, "page", object()
        ):
            token = registration_browser.getTurnstileToken(timeout=5)

        self.assertEqual(token, "")

    def test_explicit_failed_state_stops_immediately(self):
        with patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value=_state(registration_browser.TURNSTILE_FAILED),
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(registration_browser, "page", object()):
            with self.assertRaisesRegex(Exception, "Cloudflare 人机验证失败"):
                registration_browser.getTurnstileToken(timeout=5)

    def test_interactive_wait_times_out_deterministically(self):
        clock = {"now": 0.0}

        def now():
            return clock["now"]

        def sleep(seconds, _cancel=None):
            clock["now"] += max(float(seconds), 1.0)

        with patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value=_state(registration_browser.TURNSTILE_WAITING),
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(
            registration_browser, "sleep_with_cancel", side_effect=sleep, create=True
        ), patch.object(
            registration_browser.time, "time", side_effect=now
        ), patch.object(
            registration_browser, "page", object()
        ):
            with self.assertRaisesRegex(Exception, "Turnstile 验证超时"):
                registration_browser.getTurnstileToken(timeout=2)

    def test_turnstile_wait_honors_cancel(self):
        with patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value=_state(registration_browser.TURNSTILE_WAITING),
        ), patch.object(
            registration_browser, "raise_if_cancelled", side_effect=Cancelled(), create=True
        ), patch.object(registration_browser, "page", object()):
            with self.assertRaises(Cancelled):
                registration_browser.getTurnstileToken(timeout=5)

    def test_state_reader_distinguishes_interactive_widget(self):
        class FakePage:
            def run_js(self, *_args):
                return json.dumps(
                    {
                        "state": "WAITING",
                        "token": "",
                        "widget_present": True,
                        "iframe_present": True,
                        "script_present": True,
                        "visible": True,
                    }
                )

        with patch.object(registration_browser, "page", FakePage()):
            state = registration_browser._read_turnstile_state()

        self.assertEqual(state["state"], registration_browser.TURNSTILE_WAITING)
        self.assertTrue(state["widget_present"])
        self.assertTrue(state["iframe_present"])
        self.assertTrue(state["visible"])
        self.assertEqual(state["token_length"], 0)

    def test_final_sso_page_uses_same_turnstile_waiter(self):
        class FakePage:
            def __init__(self):
                self.states = iter(["final-page", "not-final-page"])

            def run_js(self, *_args):
                return next(self.states, "not-final-page")

            def cookies(self, **_kwargs):
                return [{"name": "sso", "value": "sso-token"}]

        waiter = Mock(return_value="ready")
        with patch.object(registration_browser, "page", FakePage()), patch.object(
            registration_browser, "refresh_active_page", return_value=None
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value=_state(registration_browser.TURNSTILE_WAITING),
        ), patch.object(
            registration_browser, "getTurnstileToken", waiter
        ):
            token = registration_browser.wait_for_sso_cookie(timeout=2)

        self.assertEqual(token, "sso-token")
        waiter.assert_called_once()

    def test_script_only_is_not_treated_as_an_active_challenge(self):
        source = Path(registration_browser.__file__).read_text(encoding="utf-8")
        reader = source[
            source.index("def _read_turnstile_state("):
            source.index("def _wait_for_turnstile(")
        ]
        self.assertIn("else if (widget) state = 'LOADING';", reader)
        self.assertNotIn("else if (scriptPresent) state = 'LOADING';", reader)

    def test_registration_path_contains_no_legacy_turnstile_interference(self):
        source = Path(registration_browser.__file__).read_text(encoding="utf-8")
        profile = source[
            source.index("def fill_profile_and_submit("):
            source.index("def wait_for_sso_cookie(")
        ]
        sso = source[source.index("def wait_for_sso_cookie("):]

        self.assertNotIn("turnstile.reset", source)
        self.assertNotIn("MouseEvent.prototype", source)
        self.assertNotIn("二次复用 Turnstile", source)
        self.assertNotIn("token.length >= 80", source)
        self.assertNotIn("nativeSetter.call(cfInput", source)

        self.assertIn("_read_turnstile_state()", profile)
        self.assertIn("getTurnstileToken(", profile)
        self.assertIn("_read_turnstile_state()", sso)
        self.assertIn("getTurnstileToken(", sso)

        self.assertNotIn("cf-turnstile-response", profile)
        self.assertNotIn("cf-turnstile-response", sso)
        self.assertNotIn("wait-cloudflare", profile)
        self.assertNotIn("final-page-wait-cf", sso)
        self.assertNotIn('script[src*="turnstile"]', profile)
        self.assertNotIn('script[src*="turnstile"]', sso)


if __name__ == "__main__":
    unittest.main()
