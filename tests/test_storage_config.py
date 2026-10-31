"""Tests for the database path configuration added with SQLite storage."""

import os
import unittest
from unittest.mock import patch

from app_config import DEFAULT_CONFIG, validate_config_structure


class StorageConfigTests(unittest.TestCase):
    def test_database_path_has_a_default_and_old_config_remains_valid(self):
        self.assertEqual(DEFAULT_CONFIG["database_path"], "./data/grok-register.sqlite3")
        validated = validate_config_structure({"email_provider": "duckmail"})
        self.assertEqual(validated["database_path"], DEFAULT_CONFIG["database_path"])

    def test_database_path_expands_home_directory(self):
        with patch.dict(os.environ, {"HOME": "/tmp/grok-home"}):
            validated = validate_config_structure({"database_path": "~/grok-register.sqlite3"})
        self.assertEqual(validated["database_path"], "/tmp/grok-home/grok-register.sqlite3")


if __name__ == "__main__":
    unittest.main()
