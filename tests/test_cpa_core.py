"""验证 CPA 凭证结构、写入和核心 mint 流程。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cpa_xai.mint import mint_and_export
from cpa_xai.schema import build_cpa_xai_auth, jwt_payload
from cpa_xai.writer import write_cpa_xai_auth


class CpaCoreTests(unittest.TestCase):
    def test_schema_rejects_missing_tokens(self):
        with self.assertRaises(ValueError):
            build_cpa_xai_auth("a@example.com", "", "refresh")
        with self.assertRaises(ValueError):
            jwt_payload("not-a-jwt")

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
