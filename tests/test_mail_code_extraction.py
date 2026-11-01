"""Regression tests for RFC822/MIME normalization and conservative OTP extraction."""

import unittest
from email.header import Header
from email.message import EmailMessage
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

    def test_exact_subject_has_priority_over_body_candidates(self):
        self.assertEqual(
            mail_service.extract_verification_code(
                "Your verification code is DEF-456", "ABC-123 xAI"
            ),
            "ABC-123",
        )
        self.assertEqual(
            mail_service.extract_verification_code(
                "Your verification code is DEF-456", "Confirmation code: ABC-123"
            ),
            "ABC-123",
        )

    def test_explicit_body_context_has_priority_over_other_candidates(self):
        self.assertEqual(
            mail_service.extract_verification_code(
                "Ticket DEF-456 was created. Your verification code is ABC-123."
            ),
            "ABC-123",
        )

    def test_legacy_numeric_contexts_remain_supported(self):
        self.assertEqual(
            mail_service.extract_verification_code("Your code 123456"),
            "123456",
        )
        self.assertEqual(
            mail_service.extract_verification_code("Confirm code: ABC-123"),
            "ABC-123",
        )

    def test_multiple_unlabelled_candidates_are_ambiguous(self):
        self.assertIsNone(
            mail_service.extract_verification_code(
                "Ticket DEF-456 and reference GHI-789."
            )
        )

    def test_multiple_contextual_codes_are_ambiguous(self):
        self.assertIsNone(
            mail_service.extract_verification_code(
                "Verification code: ABC-123. Backup verification code: DEF-456."
            )
        )

    def test_repeated_same_candidate_is_not_ambiguous(self):
        self.assertEqual(
            mail_service.extract_verification_code("xAI email: ABC-123 appears again: ABC-123"),
            "ABC-123",
        )

    def test_untrusted_ticket_identifier_is_not_accepted_as_otp(self):
        self.assertIsNone(
            mail_service.extract_verification_code(
                "Support ticket ABC-123 has been created."
            )
        )

    def test_longer_hyphenated_code_is_not_partially_matched(self):
        self.assertIsNone(
            mail_service.extract_verification_code("", "ABC-1234 xAI")
        )

    def test_long_numeric_values_are_not_partially_matched(self):
        self.assertIsNone(
            mail_service.extract_verification_code(
                "Your verification code is 123456789012"
            )
        )
        self.assertIsNone(
            mail_service.extract_verification_code("Order number 123456789012")
        )

    def test_encoded_rfc2047_subject_is_decoded(self):
        message = EmailMessage()
        message["Subject"] = Header("验证码 Confirmation code: DEF-456", "utf-8").encode()
        message.set_content("Your xAI account is ready.")

        subject, body = mail_service.normalize_mail_message({"raw": message.as_bytes()})

        self.assertEqual(subject, "验证码 Confirmation code: DEF-456")
        self.assertEqual(mail_service.extract_verification_code(body, subject), "DEF-456")

    def test_base64_transfer_encoding_is_decoded(self):
        message = EmailMessage()
        message["Subject"] = "xAI sign-in"
        message.set_content("Your verification code is ABC-123.", cte="base64")

        subject, body = mail_service.normalize_mail_message({"raw": message.as_bytes()})

        self.assertEqual(subject, "xAI sign-in")
        self.assertIn("Your verification code is ABC-123.", body)
        self.assertEqual(mail_service.extract_verification_code(body, subject), "ABC-123")

    def test_quoted_printable_transfer_encoding_is_decoded(self):
        message = EmailMessage()
        message["Subject"] = "xAI verification"
        message.set_content(
            "Your verification code is ABC-123. 验证码",
            cte="quoted-printable",
        )

        _subject, body = mail_service.normalize_mail_message({"raw": message.as_bytes()})

        self.assertIn("Your verification code is ABC-123.", body)
        self.assertIn("验证码", body)
        self.assertEqual(mail_service.extract_verification_code(body), "ABC-123")

    def test_multipart_keeps_plain_and_sanitized_html_alternatives(self):
        message = EmailMessage()
        message["Subject"] = "xAI verification"
        message.set_content("Your verification code is ABC-123.")
        message.add_alternative(
            "<html><head><style>.fake { content: 'CSS-123'; }</style>"
            "<script>const code = 'JS-123';</script><!-- COMMENT-123 --></head>"
            "<body>Your verification code is ABC-123.</body></html>",
            subtype="html",
        )

        _subject, body = mail_service.normalize_mail_message({"raw": message.as_bytes()})

        self.assertIn("ABC-123", body)
        self.assertNotIn("CSS-123", body)
        self.assertNotIn("JS-123", body)
        self.assertNotIn("COMMENT-123", body)
        self.assertEqual(mail_service.extract_verification_code(body), "ABC-123")

    def test_html_in_content_field_is_sanitized(self):
        body = mail_service.normalize_mail_body({
            "content": (
                "<html><style>.fake { content: 'CSS-123'; }</style>"
                "<body>Your verification code is ABC-123.</body></html>"
            )
        })
        self.assertIn("ABC-123", body)
        self.assertNotIn("CSS-123", body)
        self.assertEqual(mail_service.extract_verification_code(body), "ABC-123")

    def test_html_only_body_removes_style_script_and_comments(self):
        body = mail_service.normalize_mail_body({
            "html": (
                "<html><head><style>.x { content: 'CSS-123'; }</style>"
                "<script>var code = 'JS-123';</script><!-- COMMENT-123 --></head>"
                "<body>Your verification code is <b>ABC-123</b>.</body></html>"
            )
        })

        self.assertIn("ABC-123", body)
        self.assertNotIn("CSS-123", body)
        self.assertNotIn("JS-123", body)
        self.assertNotIn("COMMENT-123", body)
        self.assertEqual(mail_service.extract_verification_code(body), "ABC-123")

    def test_html_entities_and_inline_elements_are_normalized(self):
        body = mail_service.normalize_mail_body({
            "html": "<p>Your <strong>verification code</strong> is ABC-123 &amp; valid.</p>"
        })
        self.assertIn("verification code is ABC-123 & valid.", body)
        self.assertEqual(mail_service.extract_verification_code(body), "ABC-123")

    def test_unknown_charset_does_not_discard_other_message_content(self):
        raw = (
            b"Subject: xAI verification\r\n"
            b"Content-Type: text/plain; charset=x-unknown-charset\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\n"
            b"WW91ciB2ZXJpZmljYXRpb24gY29kZSBpcyBBQkMtMTIzLg=="
        )
        subject, body = mail_service.normalize_mail_message({
            "raw": raw,
            "text": "Fallback content remains available.",
        })

        self.assertEqual(subject, "xAI verification")
        self.assertIn("Your verification code is ABC-123.", body)
        self.assertIn("Fallback content remains available.", body)
        self.assertEqual(mail_service.extract_verification_code(body), "ABC-123")

    def test_attached_forwarded_email_is_not_searched_for_otp(self):
        message = EmailMessage()
        message["Subject"] = "xAI account notification"
        message.set_content("Your account settings have been updated.")

        attachment = EmailMessage()
        attachment["Subject"] = "Confirmation code: ABC-123"
        attachment.set_content("Your verification code is ABC-123.")
        message.add_attachment(attachment)

        subject, body = mail_service.normalize_mail_message({"raw": message.as_bytes()})

        self.assertEqual(subject, "xAI account notification")
        self.assertIn("Your account settings have been updated.", body)
        self.assertNotIn("ABC-123", body)
        self.assertIsNone(mail_service.extract_verification_code(body, subject))

    def test_unsupported_mime_part_does_not_discard_provider_text(self):
        message = EmailMessage()
        message.set_content("Your verification code is ABC-123.")
        message.add_attachment(
            b"\x00\x01",
            maintype="application",
            subtype="octet-stream",
        )

        body = mail_service.normalize_mail_body({"raw": message.as_bytes()})
        self.assertIn("Your verification code is ABC-123.", body)
        self.assertEqual(mail_service.extract_verification_code(body), "ABC-123")

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

    def test_duckmail_uses_detail_subject_when_list_subject_is_generic(self):
        with patch.object(mail_service, "raise_if_cancelled", return_value=None, create=True), patch.object(
            mail_service, "get_messages", return_value=[{
                "id": "mail-1",
                "subject": "New message",
                "to": [{"address": "target@example.com"}],
            }]
        ), patch.object(
            mail_service, "get_message_detail", return_value={"subject": "ABC-123 xAI"}
        ):
            self.assertEqual(
                mail_service.duckmail_get_oai_code(
                    "unused", "target@example.com", timeout=1, poll_interval=0
                ),
                "ABC-123",
            )

    def test_cloudflare_and_yyds_use_detail_subjects(self):
        with patch.object(mail_service, "get_cloudflare_api_base", return_value="https://mail.example"), patch.object(
            mail_service, "cloudflare_get_messages", return_value=[{
                "id": "mail-1",
                "subject": "New message",
                "to": [{"address": "target@example.com"}],
            }]
        ), patch.object(
            mail_service, "cloudflare_get_message_detail", return_value={"subject": "Confirmation code: ABC-123"}
        ), patch.object(
            mail_service, "raise_if_cancelled", return_value=None, create=True
        ):
            self.assertEqual(
                mail_service.cloudflare_get_oai_code(
                    "unused", "target@example.com", timeout=1, poll_interval=0
                ),
                "ABC-123",
            )

        with patch.object(mail_service, "raise_if_cancelled", return_value=None, create=True), patch.object(
            mail_service, "yyds_get_messages", return_value=[{
                "id": "mail-1",
                "subject": "New message",
                "to": [{"address": "target@example.com"}],
            }]
        ), patch.object(
            mail_service, "yyds_get_message_detail", return_value={"subject": "ABC-123 xAI"}
        ):
            self.assertEqual(
                mail_service.yyds_get_oai_code(
                    "unused", "target@example.com", timeout=1, poll_interval=0
                ),
                "ABC-123",
            )

    def test_duckmail_extracts_code_from_raw_mime_message(self):
        message = EmailMessage()
        message["Subject"] = Header("Confirmation code: ABC-123", "utf-8").encode()
        message["To"] = "target@example.com"
        message.set_content("Your xAI account is ready.")

        with patch.object(mail_service, "raise_if_cancelled", return_value=None, create=True), patch.object(
            mail_service, "get_messages", return_value=[{
                "id": "mail-1",
                "to": [{"address": "target@example.com"}],
                "raw": message.as_bytes(),
            }]
        ):
            self.assertEqual(
                mail_service.duckmail_get_oai_code(
                    "unused",
                    "target@example.com",
                    timeout=1,
                    poll_interval=0,
                ),
                "ABC-123",
            )


if __name__ == "__main__":
    unittest.main()
