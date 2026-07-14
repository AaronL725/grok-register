import ast
import unittest
from pathlib import Path

import grok_register_ttk as app


class ConfigValidationTests(unittest.TestCase):
    def test_module_does_not_call_load_config_at_import_scope(self):
        source = Path(app.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_calls = [
            node
            for node in tree.body
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "load_config"
        ]
        self.assertEqual(top_level_calls, [])

    def test_string_false_is_rejected_for_boolean(self):
        raw = app.DEFAULT_CONFIG.copy()
        raw["enable_nsfw"] = "false"
        with self.assertRaises(app.ConfigError):
            app.validate_config(raw)

    def test_invalid_register_count_is_rejected(self):
        raw = app.DEFAULT_CONFIG.copy()
        raw["register_count"] = -1
        with self.assertRaises(app.ConfigError):
            app.validate_config(raw)

    def test_unknown_pool_name_is_rejected(self):
        raw = app.DEFAULT_CONFIG.copy()
        raw["grok2api_pool_name"] = "unknown"
        with self.assertRaises(app.ConfigError):
            app.validate_config(raw)

    def test_oidc_timeout_range_is_enforced(self):
        raw = app.DEFAULT_CONFIG.copy()
        raw["cpa_oidc_poll_timeout_sec"] = 0
        with self.assertRaises(app.ConfigError):
            app.validate_config(raw)

    def test_defaults_are_normalized(self):
        normalized = app.validate_config({})
        self.assertEqual(normalized["register_count"], 1)
        self.assertEqual(normalized["email_provider"], "duckmail")
        self.assertFalse(normalized["grok2api_allow_legacy_full_replace"])
        self.assertEqual(normalized["cpa_oidc_poll_timeout_sec"], 12)


if __name__ == "__main__":
    unittest.main()
