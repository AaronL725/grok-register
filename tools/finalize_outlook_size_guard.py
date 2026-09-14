from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new, label):
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit("%s: expected exactly one match, got %s" % (label, count))
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "outlook_mailbox_pool.py",
    '''def load_outlook_mailbox_pool(path: Union[str, os.PathLike]) -> dict:\n    target = _canonical_path(path)\n    try:\n        data = target.read_text(encoding="utf-8")\n    except FileNotFoundError:\n        data = ""\n    except OSError as exc:\n        raise RuntimeError("读取 Outlook 账号池失败: %s" % exc) from exc\n    return {"path": str(target), "data": data, **inspect_outlook_mailbox_pool(data)}\n''',
    '''def load_outlook_mailbox_pool(path: Union[str, os.PathLike]) -> dict:\n    target = _canonical_path(path)\n    try:\n        with target.open("rb") as handle:\n            raw = handle.read(_MAX_POOL_BYTES + 1)\n    except FileNotFoundError:\n        data = ""\n    except OSError as exc:\n        raise RuntimeError("读取 Outlook 账号池失败: %s" % exc) from exc\n    else:\n        if len(raw) > _MAX_POOL_BYTES:\n            raise ValueError("Outlook 账号池过大")\n        try:\n            data = raw.decode("utf-8")\n        except UnicodeDecodeError as exc:\n            raise ValueError("Outlook 账号池必须是 UTF-8 文本") from exc\n    return {"path": str(target), "data": data, **inspect_outlook_mailbox_pool(data)}\n''',
    "bounded mailbox pool load",
)

path = ROOT / "tests/test_outlook_audit.py"
text = path.read_text(encoding="utf-8")
needle = '''    def test_graph_oauth_tries_compatibility_endpoint_after_first_terminal_error(self):\n'''
test = '''    def test_load_rejects_oversized_pool_before_parsing(self):\n        with tempfile.TemporaryDirectory() as tmp:\n            path = Path(tmp) / "outlook.txt"\n            path.write_bytes(b"x" * 1_000_001)\n            with self.assertRaisesRegex(ValueError, "过大"):\n                pool.load_outlook_mailbox_pool(path)\n\n'''
if text.count(needle) != 1:
    raise SystemExit("oversized-load test insertion point did not match exactly once")
path.write_text(text.replace(needle, test + needle, 1), encoding="utf-8")

print("Outlook pool bounded-load hardening applied")
