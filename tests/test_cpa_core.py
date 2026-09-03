"""验证 CPA 凭证结构、写入和核心 mint 流程。"""

import base64
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from cpa_xai.mint import mint_and_export
from cpa_xai.schema import build_cpa_xai_auth, jwt_payload
from cpa_xai.writer import write_cpa_xai_auth


class CpaCoreTests(unittest.TestCase):
    @staticmethod
    def _jwt(payload):
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return "header.%s.signature" % encoded

    def test_schema_rejects_missing_tokens(self):
        with self.assertRaises(ValueError):
            build_cpa_xai_auth("a@example.com", "", "refresh")
        with self.assertRaises(ValueError):
            jwt_payload("not-a-jwt")

    def test_explicit_access_token_ttl_controls_expired_timestamp(self):
        now = 1_700_000_000
        id_token = self._jwt({
            "email": "user@example.com",
            "sub": "subject",
            "iat": now,
            "exp": now + 86400,
        })
        with patch("cpa_xai.schema.time.time", return_value=now):
            payload = build_cpa_xai_auth(
                "user@example.com",
                "opaque-access-token",
                "refresh",
                id_token=id_token,
                expires_in=3600,
            )
        expected = datetime.fromtimestamp(now + 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        id_expired = datetime.fromtimestamp(now + 86400, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(payload["expires_in"], 3600)
        self.assertEqual(payload["expired"], expected)
        self.assertNotEqual(payload["expired"], id_expired)
        self.assertEqual(payload["email"], "user@example.com")
        self.assertEqual(payload["sub"], "subject")

    def test_writer_failure_does_not_leave_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("cpa_xai.writer.os.replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    write_cpa_xai_auth(directory, {"email": "a@example.com"}, "a.json")
            self.assertEqual([p.name for p in Path(directory).iterdir()], [])

    def test_mint_rejects_missing_identity_without_browser(self):
        result = mint_and_export("", "", tempfile.gettempdir())
        self.assertFalse(result["ok"])
        self.assertIn("missing", result["error"])


if __name__ == "__main__":
    unittest.main()
