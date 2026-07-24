#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIL_SERVICE = ROOT / "mail_service.py"
TEST_FILE = ROOT / "tests" / "test_mail_code_extraction.py"


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


text = MAIL_SERVICE.read_text(encoding="utf-8")

old_cloudmail = '''            code_value = str(msg.get("code", "") or "").strip()
            combined = normalize_mail_body(msg)
            if code_value:
                combined = f"verification code: {code_value}\\n{combined}"
            subject = str(msg.get("subject", "") or "")
            if log_callback:
                log_callback(f"[Debug] Cloud Mail 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
'''
new_cloudmail = '''            code_value = str(msg.get("code", "") or "").strip()
            combined = normalize_mail_body(msg)
            subject = str(msg.get("subject", "") or "")
            if log_callback:
                log_callback(f"[Debug] Cloud Mail 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
            if not code and code_value:
                code = extract_verification_code(f"verification code: {code_value}")
'''
text = replace_once(text, old_cloudmail, new_cloudmail, "Cloud Mail code fallback order")

old_extractor = '''def extract_verification_code(text, subject=""):
    if subject:
        match = re.search(r"(?:^|(?:confirmation|verification)\\s+code:\\s*)([A-Z0-9]{3}-[A-Z0-9]{3})(?:\\s+xAI)?", subject, re.IGNORECASE)
        if match:
            return match.group(1)
'''
new_extractor = '''def extract_verification_code(text, subject=""):
    if subject:
        subject_patterns = [
            r"^([A-Z0-9]{3}-[A-Z0-9]{3})\\s+xAI\\b",
            r"\\b(?:confirmation|verification)\\s+code\\s*:\\s*([A-Z0-9]{3}-[A-Z0-9]{3})\\b",
        ]
        for pattern in subject_patterns:
            match = re.search(pattern, subject, re.IGNORECASE)
            if match:
                return match.group(1)
'''
text = replace_once(text, old_extractor, new_extractor, "strict subject verification formats")
MAIL_SERVICE.write_text(text, encoding="utf-8")

TEST_FILE.write_text('''"""Regression tests for shared verification-code extraction and Cloud Mail fallback."""

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
        with patch.object(mail_service, "cloudmail_get_messages", return_value=[message]):
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
''', encoding="utf-8")
