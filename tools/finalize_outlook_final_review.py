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
    '''def load_outlook_mailbox_pool(path: Union[str, os.PathLike]) -> dict:\n    target = _canonical_path(path)\n    try:\n        with target.open("rb") as handle:\n            raw = handle.read(_MAX_POOL_BYTES + 1)\n    except FileNotFoundError:\n        data = ""\n    except OSError as exc:\n        raise RuntimeError("读取 Outlook 账号池失败: %s" % exc) from exc\n    else:\n        if len(raw) > _MAX_POOL_BYTES:\n            raise ValueError("Outlook 账号池过大")\n        try:\n            data = raw.decode("utf-8")\n        except UnicodeDecodeError as exc:\n            raise ValueError("Outlook 账号池必须是 UTF-8 文本") from exc\n    return {"path": str(target), "data": data, **inspect_outlook_mailbox_pool(data)}\n\n\ndef _read_validated_pool(path: Union[str, os.PathLike]):\n    target = _canonical_path(path)\n    try:\n        data = target.read_text(encoding="utf-8")\n    except FileNotFoundError as exc:\n        raise ValueError("Outlook 账号池文件不存在: %s" % target) from exc\n    normalized, accounts = _validate_pool_data(data)\n    return target, normalized, accounts\n''',
    '''def _read_pool_text(path: Union[str, os.PathLike], missing_ok: bool = False):\n    target = _canonical_path(path)\n    try:\n        with target.open("rb") as handle:\n            raw = handle.read(_MAX_POOL_BYTES + 1)\n    except FileNotFoundError as exc:\n        if missing_ok:\n            return target, ""\n        raise ValueError("Outlook 账号池文件不存在: %s" % target) from exc\n    except OSError as exc:\n        raise RuntimeError("读取 Outlook 账号池失败: %s" % exc) from exc\n    if len(raw) > _MAX_POOL_BYTES:\n        raise ValueError("Outlook 账号池过大")\n    try:\n        return target, raw.decode("utf-8")\n    except UnicodeDecodeError as exc:\n        raise ValueError("Outlook 账号池必须是 UTF-8 文本") from exc\n\n\ndef load_outlook_mailbox_pool(path: Union[str, os.PathLike]) -> dict:\n    target, data = _read_pool_text(path, missing_ok=True)\n    return {"path": str(target), "data": data, **inspect_outlook_mailbox_pool(data)}\n\n\ndef _read_validated_pool(path: Union[str, os.PathLike]):\n    target, data = _read_pool_text(path, missing_ok=False)\n    normalized, accounts = _validate_pool_data(data)\n    return target, normalized, accounts\n''',
    "shared bounded pool reader",
)

replace_once(
    "outlook_mail.py",
    '''def _select_folder_count(client: imaplib.IMAP4_SSL, folder: str) -> Optional[int]:\n    status, data = client.select(folder, readonly=True)\n    if status != "OK":\n        return None\n    raw = data[0] if data else b"0"\n    if isinstance(raw, bytes):\n        raw = raw.decode("ascii", errors="ignore")\n    try:\n        return int(raw or 0)\n    except (TypeError, ValueError):\n        return None\n''',
    '''def _quote_imap_mailbox(folder: str) -> str:\n    raw = str(folder or "")\n    return '"' + raw.replace("\\\\", "\\\\\\\\").replace('"', '\\\\"') + '"'\n\n\ndef _select_folder_count(client: imaplib.IMAP4_SSL, folder: str) -> Optional[int]:\n    try:\n        status, data = client.select(_quote_imap_mailbox(folder), readonly=True)\n    except (imaplib.IMAP4.error, UnicodeError):\n        # A missing localized fallback folder or a name that cannot be encoded\n        # must not invalidate other folders that were already readable.\n        return None\n    if status != "OK":\n        return None\n    raw = data[0] if data else b"0"\n    if isinstance(raw, bytes):\n        raw = raw.decode("ascii", errors="ignore")\n    try:\n        return int(raw or 0)\n    except (TypeError, ValueError):\n        return None\n''',
    "safe IMAP mailbox selection",
)

replace_once(
    "grok_register_ttk.py",
    '''        try:\n            current = load_outlook_mailbox_pool(path)\n            editor.insert("1.0", current.get("data", ""))\n        except Exception as exc:\n            status_var.set("读取失败: %s" % exc)\n        editor.bind("<KeyRelease>", update_summary)\n        update_summary()\n        buttons = tk.Frame(window, bg=UI_BG)\n        buttons.pack(fill=tk.X, padx=12, pady=(0, 12))\n        tk_button(buttons, text="保存邮箱池", command=save_pool).pack(side=tk.LEFT)\n        tk_button(buttons, text="关闭", command=window.destroy).pack(side=tk.RIGHT)\n''',
    '''        def load_pool():\n            try:\n                current = load_outlook_mailbox_pool(path)\n                editor.delete("1.0", tk.END)\n                editor.insert("1.0", current.get("data", ""))\n                update_summary()\n            except Exception as exc:\n                status_var.set("读取失败: %s" % exc)\n\n        editor.bind("<KeyRelease>", update_summary)\n        load_pool()\n        buttons = tk.Frame(window, bg=UI_BG)\n        buttons.pack(fill=tk.X, padx=12, pady=(0, 12))\n        tk_button(buttons, text="保存邮箱池", command=save_pool).pack(side=tk.LEFT)\n        tk_button(buttons, text="重新加载", command=load_pool).pack(side=tk.LEFT, padx=(8, 0))\n        tk_button(buttons, text="关闭", command=window.destroy).pack(side=tk.RIGHT)\n''',
    "GUI pool reload behavior",
)

path = ROOT / "tests/test_outlook_audit.py"
text = path.read_text(encoding="utf-8")
needle = '''    def test_graph_oauth_tries_compatibility_endpoint_after_first_terminal_error(self):\n'''
extra = r'''    def test_capacity_path_uses_same_bounded_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outlook.txt"
            path.write_bytes(b"x" * 1_000_001)
            with self.assertRaisesRegex(ValueError, "过大"):
                pool.get_outlook_mailbox_pool_capacity(path)

    def test_imap_folder_with_spaces_is_quoted(self):
        client = Mock()
        client.select.return_value = ("OK", [b"5"])
        self.assertEqual(outlook_mail._select_folder_count(client, "Junk Email"), 5)
        client.select.assert_called_once_with('"Junk Email"', readonly=True)

    def test_unencodable_localized_fallback_does_not_break_other_folders(self):
        client = Mock()
        client.select.side_effect = UnicodeEncodeError(
            "ascii", "垃圾邮件", 0, 1, "ordinal not in range"
        )
        self.assertIsNone(outlook_mail._select_folder_count(client, "垃圾邮件"))

    def test_graph_fetch_is_capped_to_scan_depth(self):
        counts = {outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: 1}
        with patch.object(outlook_mail, "_graph_inbox_count", return_value=100), \
             patch.object(outlook_mail, "_graph_get", return_value={"value": []}) as get:
            self.assertIsNone(outlook_mail._scan_graph_once("token", counts))
        self.assertEqual(get.call_args.args[2]["$top"], str(outlook_mail.OUTLOOK_GRAPH_SCAN_DEPTH))

'''
if text.count(needle) != 1:
    raise SystemExit("final audit test insertion point did not match exactly once")
path.write_text(text.replace(needle, extra + needle, 1), encoding="utf-8")

print("final Outlook review fixes applied")
