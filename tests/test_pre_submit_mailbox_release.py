import unittest
from unittest.mock import patch

import registration_browser
from registration_flow import _set_registration_stage, current_registration_stage


class PreSubmitMailboxReleaseTests(unittest.TestCase):
    def test_pre_submit_failure_releases_mailbox_lease_even_if_caller_marked_submit_stage(self):
        released = []
        _set_registration_stage("email_submit")
        with patch.object(registration_browser, "raise_if_cancelled", return_value=None, create=True), \
             patch.object(registration_browser, "get_email_and_token", return_value=("user@example.com", "outlook:handle"), create=True), \
             patch.object(registration_browser, "_fill_email_form", side_effect=RuntimeError("page not ready"), create=True), \
             patch.object(registration_browser, "release_email_lease", side_effect=lambda token, email: released.append((token, email)), create=True):
            with self.assertRaisesRegex(RuntimeError, "page not ready"):
                registration_browser.fill_email_and_submit()

        self.assertEqual(current_registration_stage(), "page_open")
        self.assertEqual(released, [("outlook:handle", "user@example.com")])

    def test_ambiguous_email_submit_keeps_mailbox_lease(self):
        released = []

        def fail_after_submit(*_args, **_kwargs):
            _set_registration_stage("email_submit")
            raise RuntimeError("submit result unknown")

        with patch.object(registration_browser, "raise_if_cancelled", return_value=None, create=True), \
             patch.object(registration_browser, "get_email_and_token", return_value=("user@example.com", "outlook:handle"), create=True), \
             patch.object(registration_browser, "_fill_email_form", side_effect=fail_after_submit, create=True), \
             patch.object(registration_browser, "release_email_lease", side_effect=lambda token, email: released.append((token, email)), create=True):
            with self.assertRaisesRegex(RuntimeError, "submit result unknown"):
                registration_browser.fill_email_and_submit()

        self.assertEqual(released, [])


if __name__ == "__main__":
    unittest.main()
