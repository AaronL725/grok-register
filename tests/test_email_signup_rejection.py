"""Issue #110: recognize explicit xAI email signup refusal without changing normal retries."""
import json
import unittest
from unittest.mock import Mock, patch

import registration_browser
import registration_flow
from tests.test_registration_flow import FakeOps
from registration_flow import EmailSignupUnavailable, VerificationCodeUnavailable


REJECTION = "Email sign-up isn’t available right now. Sign up another way."


class EmailSignupRejectionTests(unittest.TestCase):
    def test_exact_visible_banner_is_required(self):
        page = Mock()
        with patch.object(registration_browser, "page", page):
            for message in (REJECTION, REJECTION.replace("’", "'"), "  Email sign-up isn’t\n available right now."):
                page.run_js.return_value = message
                with self.assertRaises(EmailSignupUnavailable):
                    registration_browser._raise_if_email_signup_unavailable()
            for message in ("Sign up with email", "Please check your email", ""):
                page.run_js.return_value = message
                registration_browser._raise_if_email_signup_unavailable()

    def test_navigation_context_loss_does_not_change_diagnostic_outcome(self):
        page = Mock()
        page.run_js.side_effect = registration_browser.ContextLostError("navigating")
        with patch.object(registration_browser, "page", page):
            registration_browser._raise_if_email_signup_unavailable()

    def test_entry_refusal_stops_before_mailbox_creation(self):
        page = Mock()
        page.run_js.side_effect = ["Sign up with email", REJECTION]
        create_mail = Mock()
        with patch.object(registration_browser, "page", page), \
             patch.object(registration_browser, "_run_pre_submit_js", return_value="Sign up with email"), \
             patch.object(registration_browser, "raise_if_cancelled", return_value=None, create=True), \
             patch.object(registration_browser, "sleep_with_cancel", return_value=None, create=True), \
             patch.object(registration_browser, "get_email_and_token", create=True, new=create_mail):
            with self.assertRaises(EmailSignupUnavailable):
                registration_browser.click_email_signup_button()
        create_mail.assert_not_called()

    def test_refusal_in_place_of_email_input_does_not_reclick(self):
        class Page:
            def run_js(self, script, *_args):
                if "const email = arguments[0]" in script:
                    return json.dumps({"state": "not-ready", "url": "https://accounts.x.ai/sign-up"})
                if "document.body ? document.body.innerText" in script:
                    return REJECTION
                raise AssertionError("email entry should not be clicked again")

        with patch.object(registration_browser, "page", Page()), \
             patch.object(registration_browser, "get_email_and_token", return_value=("u@example.com", "credential"), create=True), \
             patch.object(registration_browser, "raise_if_cancelled", return_value=None, create=True), \
             patch.object(registration_browser, "sleep_with_cancel", return_value=None, create=True):
            with self.assertRaises(EmailSignupUnavailable):
                registration_browser.fill_email_and_submit(timeout=3)

    def test_refusal_after_email_submit_preserves_commit_boundary(self):
        events = []

        class Page:
            def run_js(self, script, *_args):
                if "const email = arguments[0]" in script:
                    return json.dumps({"state": "filled"})
                if "return (input.getAttribute('type')" in script:
                    return True
                if "const submitButton = buttons.find" in script:
                    events.append("submit-click")
                    return "clicked"
                if "document.body ? document.body.innerText" in script:
                    return REJECTION
                raise AssertionError("unexpected browser script")

        with patch.object(registration_browser, "page", Page()), \
             patch.object(registration_browser, "get_email_and_token", return_value=("u@example.com", "credential"), create=True), \
             patch.object(registration_browser, "raise_if_cancelled", return_value=None, create=True), \
             patch.object(registration_browser, "sleep_with_cancel", return_value=None, create=True), \
             patch.object(registration_browser, "_mark_registration_stage", side_effect=lambda stage: events.append(stage)):
            with self.assertRaises(EmailSignupUnavailable):
                registration_browser.fill_email_and_submit(timeout=3)
        self.assertEqual(events, ["email_submit", "submit-click"])

    def test_no_banner_preserves_successful_email_submission(self):
        class Page:
            def run_js(self, script, *_args):
                if "const email = arguments[0]" in script:
                    return json.dumps({"state": "filled"})
                if "return (input.getAttribute('type')" in script:
                    return True
                if "const submitButton = buttons.find" in script:
                    return "clicked"
                if "document.body ? document.body.innerText" in script:
                    return "Check your inbox"
                raise AssertionError("unexpected browser script")

        with patch.object(registration_browser, "page", Page()), \
             patch.object(registration_browser, "get_email_and_token", return_value=("u@example.com", "credential"), create=True), \
             patch.object(registration_browser, "raise_if_cancelled", return_value=None, create=True), \
             patch.object(registration_browser, "sleep_with_cancel", return_value=None, create=True), \
             patch.object(registration_browser, "_mark_registration_stage"):
            self.assertEqual(
                registration_browser.fill_email_and_submit(timeout=3),
                ("u@example.com", "credential"),
            )

    def test_mail_poll_timeout_rechecks_upstream_refusal(self):
        for message, expected in ((REJECTION, EmailSignupUnavailable), ("Check your inbox", VerificationCodeUnavailable)):
            with self.subTest(message=message), \
                 patch.object(registration_browser, "page", Mock()), \
                 patch.object(registration_browser, "get_oai_code", side_effect=VerificationCodeUnavailable("no mail"), create=True), \
                 patch.object(registration_browser, "_mark_registration_stage"):
                registration_browser.page.run_js.return_value = message
                with self.assertRaises(expected):
                    registration_browser.fill_code_and_submit("u@example.com", "credential")

    def test_confirmed_refusal_stops_batch_without_uncertain_result_or_retry(self):
        for stage in ("open_signup_page", "fill_email_and_submit", "fill_code_and_submit"):
            fake = FakeOps()
            ops = fake.operations()
            setattr(ops, stage, Mock(side_effect=EmailSignupUnavailable(REJECTION)))
            logs, observations = [], []
            signal = Mock()
            callbacks = registration_flow.RegistrationCallbacks(
                log=logs.append, cancelled=lambda: False, stop_all=signal
            )
            with self.subTest(stage=stage), \
                 patch.object(registration_flow, "begin_registration_slot"), \
                 patch.object(registration_flow, "end_registration_slot") as release:
                result = registration_flow.run_batch(
                    count=5, callbacks=callbacks,
                    observer=lambda *args: observations.append(args),
                    ops=ops, enable_nsfw=False,
                )
            self.assertEqual((result.processed_count, result.fail_count, result.uncertain_count), (1, 1, 0))
            self.assertEqual(result.success_count, 0)
            self.assertEqual(len(observations), 1)
            self.assertNotIn("restart", fake.events)
            self.assertTrue(any("停止本批次" in entry for entry in logs))
            signal.assert_called_once_with()
            release.assert_called_once_with(success=False, transport_error=None)


if __name__ == "__main__":
    unittest.main()
