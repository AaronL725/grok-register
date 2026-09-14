from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
path = root / "app_config.py"
text = path.read_text(encoding="utf-8")

obsolete = '''

def _reset_outlook_runtime():
    try:
        from outlook_mailbox_pool import reset_shared_outlook_runtime
        reset_shared_outlook_runtime()
    except Exception:
        pass
'''
if text.count(obsolete) != 1:
    raise SystemExit("obsolete reset hook did not match exactly once")
text = text.replace(obsolete, "", 1)
text, removed_calls = re.subn(r"(?m)^[ \t]*_reset_outlook_runtime\(\)\n", "", text)
if removed_calls != 3:
    raise SystemExit("expected 3 obsolete reset calls, found %s" % removed_calls)

old_validation = '''    if provider == "outlook":
        path = os.path.realpath(os.path.abspath(os.path.expanduser(cfg["outlook_accounts_file"])))
        if not os.path.isfile(path):
            raise ConfigError(f"Outlook 模式需要有效的账号池文件: {path}")
        try:
            from outlook_mailbox_pool import load_outlook_mailbox_pool
            summary = load_outlook_mailbox_pool(path)
        except Exception as exc:
            raise ConfigError(f"Outlook 账号池读取失败: {exc}") from exc
        if summary.get("invalid"):
            raise ConfigError(f"Outlook 账号池存在 {summary['invalid']} 条无效记录")
        if summary.get("duplicates"):
            raise ConfigError("Outlook 账号池存在重复邮箱: " + ", ".join(summary["duplicates"][:3]))
        if int(summary.get("count") or 0) <= 0:
            raise ConfigError("Outlook 账号池没有有效账号")
'''
new_validation = '''    if provider == "outlook":
        path = os.path.realpath(os.path.abspath(os.path.expanduser(cfg["outlook_accounts_file"])))
        if not os.path.isfile(path):
            raise ConfigError(f"Outlook 模式需要有效的账号池文件: {path}")
        try:
            from outlook_mailbox_pool import get_outlook_mailbox_pool_capacity
            count = get_outlook_mailbox_pool_capacity(path)
        except Exception as exc:
            raise ConfigError(f"Outlook 账号池校验失败: {exc}") from exc
        if int(count) <= 0:
            raise ConfigError("Outlook 账号池没有有效账号")
'''
if text.count(old_validation) != 1:
    raise SystemExit("Outlook validation block did not match exactly once")
text = text.replace(old_validation, new_validation, 1)
path.write_text(text, encoding="utf-8")

(root / "tests/test_outlook_config_validation.py").write_text('''import tempfile
import unittest
from pathlib import Path

import app_config
from outlook_mailbox_pool import save_outlook_mailbox_pool


class OutlookConfigValidationTests(unittest.TestCase):
    def _config(self, path):
        cfg = dict(app_config.DEFAULT_CONFIG)
        cfg["email_provider"] = "outlook"
        cfg["outlook_accounts_file"] = str(path)
        return cfg

    def test_valid_pool_passes_full_run_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outlook.txt"
            save_outlook_mailbox_pool(
                path,
                "u@example.com----pw----client----refresh-token----auto\\n",
            )
            validated = app_config.validate_run_requirements(self._config(path))
            self.assertEqual(validated["email_provider"], "outlook")

    def test_oversized_pool_is_reported_as_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outlook.txt"
            path.write_text(
                "u@example.com----pw----client----" + ("x" * 1_000_001) + "----auto\\n",
                encoding="utf-8",
            )
            with self.assertRaises(app_config.ConfigError):
                app_config.validate_run_requirements(self._config(path))

    def test_duplicate_pool_is_reported_as_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outlook.txt"
            path.write_text(
                "u@example.com----pw----client----token1----auto\\n"
                "U@example.com----pw----client2----token2----graph\\n",
                encoding="utf-8",
            )
            with self.assertRaises(app_config.ConfigError):
                app_config.validate_run_requirements(self._config(path))


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

print("final Outlook validation patch applied")
