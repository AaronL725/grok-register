"""Regression coverage for passive Turnstile waiting (issue #79)."""

import unittest
from pathlib import Path
from unittest.mock import patch

import registration_browser


class Cancelled(Exception):
    pass


class TurnstileRegressionTests(unittest.TestCase):
    def test_existing_short_token_is_accepted_immediately(self):
        with patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value={"present": True, "token": "ok", "token_length": 2},
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(registration_browser, "page", object()):
            token = registration_browser.getTurnstileToken(timeout=5)

        self.assertEqual(token, "ok")

    def test_issue_79_waits_past_old_retry_threshold_without_reset(self):
        clock = {"now": 0.0}
        states = [
            {"present": True, "token": "", "token_length": 0},
            {"present": True, "token": "", "token_length": 0},
            {"present": True, "token": "", "token_length": 0},
            {"present": True, "token": "", "token_length": 0},
            {"present": True, "token": "ready", "token_length": 5},
        ]

        def now():
            return clock["now"]

        def sleep(_seconds, _cancel=None):
            clock["now"] += 5.0

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
            token = registration_browser.getTurnstileToken(timeout=30)

        self.assertEqual(token, "ready")
        self.assertGreaterEqual(clock["now"], 15.0)

    def test_turnstile_wait_times_out_deterministically(self):
        clock = {"now": 0.0}

        def now():
            return clock["now"]

        def sleep(seconds, _cancel=None):
            clock["now"] += max(float(seconds), 1.0)

        with patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value={"present": True, "token": "", "token_length": 0},
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
            return_value={"present": True, "token": "", "token_length": 0},
        ), patch.object(
            registration_browser, "raise_if_cancelled", side_effect=Cancelled(), create=True
        ), patch.object(registration_browser, "page", object()):
            with self.assertRaises(Cancelled):
                registration_browser.getTurnstileToken(timeout=5)

    def test_registration_path_contains_no_legacy_turnstile_interference(self):
        source = Path(registration_browser.__file__).read_text(encoding="utf-8")
        self.assertNotIn("turnstile.reset", source)
        self.assertNotIn("MouseEvent.prototype", source)
        self.assertNotIn("二次复用 Turnstile", source)
        self.assertNotIn("token.length >= 80", source)


if __name__ == "__main__":
    unittest.main()
