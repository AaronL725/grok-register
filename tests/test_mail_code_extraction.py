"""Regression tests for shared verification-code extraction and Cloud Mail fallback."""

import unittest
from unittest.mock import patch

import mail_service


class VerificationCodeExtractionTests(unittest.TestCase):
    def test_original_xai_subject_format(self):
        self.assertEqual(
            mail_service.extract_verification_code("", "ABC-123 xAI"),
            "ABC-123",
        )

    def test_confirmation_subject_format(self):
        self.assertEqual(
            mail_service.extract_verification_code("", "Confirmation code: DEF-456"),
            "DEF-456",
        )

    def test_longer_code_is_not_partially_matched(self):
        self.assertIsNone(
            mail_service.extract_verification_code("", "ABC-1234 xAI")
        )

    def test_cloudmail_prefers_real_subject_over_api_code(self):
        message = {
            "emailId": "mail-1",
            "toEmail": "target@example.com",
            "subject": "Confirmation code: ABC-123",
            "code": "PER-110",
            "text": "Your xAI confirmation email",
        }
        with patch.object(mail_service, "raise_if_cancelled", return_value=None, create=True), patch.object(
            mail_service, "cloudmail_get_messages", return_value=[message]
        ):
            self.assertEqual(
                mail_service.cloudmail_get_oai_code(
                    "unused",
                    "target@example.com",
                    timeout=1,
                    poll_interval=0,
                ),
                "ABC-123",
            )


if __name__ == "__main__":
    unittest.main()
