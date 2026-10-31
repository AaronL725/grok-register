"""Regression coverage for RFC 822, MIME, HTML normalization, and OTP extraction."""

import base64
import unittest
from email.message import EmailMessage

import email_parser
import mail_service


class EmailParserTests(unittest.TestCase):
    def test_decodes_rfc2047_subject_and_base64_mime_body(self):
        raw = (
            b"From: xAI <no-reply@x.ai>\r\n"
            b"Subject: =?UTF-8?B?QUJDLTEyMyB4QUk=?=\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\n"
            + base64.b64encode("Your verification code is 654321".encode("utf-8"))
        )
        subject, text, html, sender = email_parser.parse_mime_message(raw)

        self.assertEqual(subject, "ABC-123 xAI")
        self.assertEqual(text, "Your verification code is 654321")
        self.assertEqual(html, "")
        self.assertIn("no-reply@x.ai", sender)
        self.assertEqual(mail_service.extract_verification_code(text, subject), "ABC-123")

    def test_decodes_quoted_printable_soft_breaks_in_multipart_html(self):
        message = EmailMessage()
        message["From"] = "xAI <no-reply@x.ai>"
        message["Subject"] = "Verification notice"
        message.set_content("This message contains no visible code.")
        message.add_alternative(
            (
                "<html><body>"
                "<style>.otp { content: 'BAD-111'; }</style>"
                "<!-- BAD-222 -->"
                "<script>const hiddenCode = 'BAD-333';</script>"
                "<p>Your verification code is GOOD-444</p>"
                "</body></html>"
            ),
            subtype="html",
            cte="quoted-printable",
        )
        raw = message.as_bytes()
        subject, text, html, _sender = email_parser.parse_mime_message(raw)

        self.assertEqual(subject, "Verification notice")
        self.assertIn("GOOD-444", html)
        self.assertNotIn("BAD-111", html)
        self.assertNotIn("BAD-222", html)
        self.assertNotIn("BAD-333", html)
        self.assertEqual(
            mail_service.extract_verification_code(text + "\n" + html, subject),
            "GOOD-444",
        )

    def test_html_normalization_removes_style_script_comments_and_decodes_entities(self):
        html = (
            "<style>.code{content:'BAD-111'}</style>"
            "<!-- BAD-222 -->"
            "<script>const hidden='BAD-333';</script>"
            "<p>Your verification code is GOOD&#45;444 &amp; ready.</p>"
        )
        visible = email_parser.normalize_text(html)

        self.assertIn("GOOD-444 & ready.", visible)
        for hidden in ("BAD-111", "BAD-222", "BAD-333"):
            self.assertNotIn(hidden, visible)

    def test_subject_and_body_legacy_formats_are_preserved(self):
        self.assertEqual(
            mail_service.extract_verification_code("", "ABC-123 xAI"),
            "ABC-123",
        )
        self.assertEqual(
            mail_service.extract_verification_code("", "Confirmation code: DEF-456"),
            "DEF-456",
        )
        self.assertEqual(
            mail_service.extract_verification_code("Your code is 123456"),
            "123456",
        )
        self.assertEqual(
            mail_service.extract_verification_code("验证码：654321"),
            "654321",
        )

    def test_bare_ticket_code_is_not_treated_as_an_otp(self):
        self.assertIsNone(
            mail_service.extract_verification_code(
                "Support ticket ABC-123 was created."
            )
        )

    def test_long_numeric_code_is_not_partially_matched(self):
        self.assertIsNone(
            mail_service.extract_verification_code(
                "Your verification code is 123456789"
            )
        )

    def test_explicit_verification_context_wins_over_other_numbers(self):
        body = (
            "Support case 123456 was opened. "
            "Your xAI verification code is 654321. "
            "Reference number 777777."
        )
        self.assertEqual(mail_service.extract_verification_code(body), "654321")

    def test_provider_payloads_support_nested_and_list_legacy_fields(self):
        subject, body, _sender = email_parser.normalize_mail_sources(
            {
                "subject": "=?UTF-8?Q?ABC-123_xAI?=",
                "text": ["plain notice"],
                "body": {
                    "contentType": "html",
                    "content": "<style>.code{content:'BAD-111'}</style><p>Visible body</p>",
                },
                "html": ["<p>second visible body</p>"],
            }
        )

        self.assertEqual(subject, "ABC-123 xAI")
        self.assertIn("plain notice", body)
        self.assertIn("Visible body", body)
        self.assertIn("second visible body", body)
        self.assertNotIn("BAD-111", body)

    def test_base64_encoded_raw_mime_payload_is_decoded(self):
        raw = (
            b"Subject: Confirmation code: QWE-987\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Please use QWE-987"
        )
        encoded = base64.b64encode(raw).decode("ascii")
        subject, body, _sender = email_parser.normalize_mail_sources({"raw": encoded})

        self.assertEqual(subject, "Confirmation code: QWE-987")
        self.assertEqual(mail_service.extract_verification_code(body, subject), "QWE-987")

    def test_unsupported_payload_format_does_not_raise_or_discard_plain_text(self):
        subject, body, _sender = email_parser.normalize_mail_sources(
            {"subject": "Verification notice", "content": "Your code is ABC-123"}
        )
        self.assertEqual(subject, "Verification notice")
        self.assertEqual(body, "Your code is ABC-123")
        self.assertEqual(mail_service.extract_verification_code(body, subject), "ABC-123")


if __name__ == "__main__":
    unittest.main()
