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
    '''    def __init__(self, accounts: list[OutlookAccount], log_callback=None) -> None:\n        self._pool = OutlookAccountPool(accounts)\n        self._log_callback = log_callback\n        self._lock = threading.RLock()\n        self._leases = {}\n        self._closed = False\n''',
    '''    def __init__(\n        self, accounts: list[OutlookAccount], log_callback=None, cancelled_exception=None\n    ) -> None:\n        self._pool = OutlookAccountPool(accounts)\n        self._log_callback = log_callback\n        self._cancelled_exception = cancelled_exception\n        self._lock = threading.RLock()\n        self._leases = {}\n        self._closed = False\n\n    def _raise_if_cancelled(self, cancel_callback=None) -> None:\n        if not cancel_callback or not cancel_callback():\n            return\n        if self._cancelled_exception is not None:\n            raise self._cancelled_exception("用户停止注册")\n        raise RuntimeError("任务已停止")\n''',
    "task runtime cancellation adapter",
)

replace_once(
    "outlook_mailbox_pool.py",
    '''        code = lease.mailbox.wait_for_code(\n            timeout=int(timeout),\n            interval=int(poll_interval),\n            cancel_callback=cancel_callback,\n        )\n        if not code:\n            from registration_flow import VerificationCodeUnavailable\n            raise VerificationCodeUnavailable("Outlook 在 %ss 内未收到验证码邮件" % timeout)\n        return str(code)\n''',
    '''        self._raise_if_cancelled(cancel_callback)\n        try:\n            code = lease.mailbox.wait_for_code(\n                timeout=int(timeout),\n                interval=int(poll_interval),\n                cancel_callback=cancel_callback,\n            )\n        except Exception:\n            # The low-level mailbox poller intentionally has no dependency on\n            # the registration engine. Convert its generic stop signal back to\n            # the engine's cancellation exception at this task boundary.\n            self._raise_if_cancelled(cancel_callback)\n            raise\n        self._raise_if_cancelled(cancel_callback)\n        if not code:\n            from registration_flow import VerificationCodeUnavailable\n            raise VerificationCodeUnavailable("Outlook 在 %ss 内未收到验证码邮件" % timeout)\n        return str(code)\n''',
    "wait code cancellation conversion",
)

replace_once(
    "outlook_mailbox_pool.py",
    '''def create_outlook_task_runtime(path: Union[str, os.PathLike], log_callback=None) -> OutlookTaskRuntime:\n    _target, _normalized, accounts = _read_validated_pool(path)\n    return OutlookTaskRuntime(accounts, log_callback=log_callback)\n''',
    '''def create_outlook_task_runtime(\n    path: Union[str, os.PathLike], log_callback=None, cancelled_exception=None\n) -> OutlookTaskRuntime:\n    _target, _normalized, accounts = _read_validated_pool(path)\n    return OutlookTaskRuntime(\n        accounts, log_callback=log_callback, cancelled_exception=cancelled_exception\n    )\n''',
    "runtime factory cancellation dependency",
)

replace_once(
    "grok_register_ttk.py",
    '''        task_outlook_runtime = create_outlook_task_runtime(\n            config.get("outlook_accounts_file", ""), log_callback=log_callback\n        )\n''',
    '''        task_outlook_runtime = create_outlook_task_runtime(\n            config.get("outlook_accounts_file", ""),\n            log_callback=log_callback,\n            cancelled_exception=RegistrationCancelled,\n        )\n''',
    "engine cancellation injection",
)

replace_once(
    "outlook_mail.py",
    '''                for folder in _discover_folders(client):\n                    total = _select_folder_count(client, folder)\n                    if total is not None:\n                        counts[folder] = total\n                return counts\n''',
    '''                for folder in _discover_folders(client):\n                    total = _select_folder_count(client, folder)\n                    if total is not None:\n                        counts[folder] = total\n                if counts or mode == "imap":\n                    return counts\n                errors.append("IMAP: 未发现可读取的邮件文件夹")\n''',
    "auto baseline Graph fallback",
)

(ROOT / "tests/test_outlook_cancellation.py").write_text(
    '''import tempfile\nimport unittest\nfrom pathlib import Path\nfrom unittest.mock import patch\n\nimport outlook_mail\nimport outlook_mailbox_pool as pool\n\n\nROW = "u@example.com----pw----client----refresh-token----auto\\n"\n\n\nclass OutlookCancellationTests(unittest.TestCase):\n    def _runtime(self, tmp, cancelled_exception=None):\n        path = Path(tmp) / "outlook.txt"\n        pool.save_outlook_mailbox_pool(path, ROW)\n        with patch.object(pool.OutlookMailbox, "prepare", return_value=None):\n            return pool.create_outlook_task_runtime(\n                path, cancelled_exception=cancelled_exception\n            )\n\n    def test_immediate_cancel_uses_registration_cancel_exception(self):\n        class Cancelled(Exception):\n            pass\n\n        with tempfile.TemporaryDirectory() as tmp:\n            runtime = self._runtime(tmp, cancelled_exception=Cancelled)\n            email, handle = runtime.acquire()\n            with patch.object(pool.OutlookMailbox, "wait_for_code") as wait:\n                with self.assertRaises(Cancelled):\n                    runtime.wait_for_code(\n                        handle, email, cancel_callback=lambda: True\n                    )\n                wait.assert_not_called()\n\n    def test_low_level_stop_is_converted_to_registration_cancel_exception(self):\n        class Cancelled(Exception):\n            pass\n\n        with tempfile.TemporaryDirectory() as tmp:\n            runtime = self._runtime(tmp, cancelled_exception=Cancelled)\n            email, handle = runtime.acquire()\n            with patch.object(\n                pool.OutlookMailbox,\n                "wait_for_code",\n                side_effect=RuntimeError("任务已停止"),\n            ):\n                with self.assertRaises(Cancelled):\n                    runtime.wait_for_code(\n                        handle, email, cancel_callback=lambda: True\n                    )\n\n    def test_non_cancel_error_is_not_reclassified(self):\n        class Cancelled(Exception):\n            pass\n\n        with tempfile.TemporaryDirectory() as tmp:\n            runtime = self._runtime(tmp, cancelled_exception=Cancelled)\n            email, handle = runtime.acquire()\n            with patch.object(\n                pool.OutlookMailbox,\n                "wait_for_code",\n                side_effect=RuntimeError("mailbox network failure"),\n            ):\n                with self.assertRaisesRegex(RuntimeError, "mailbox network failure"):\n                    runtime.wait_for_code(\n                        handle, email, cancel_callback=lambda: False\n                    )\n\n    def test_auto_baseline_falls_back_to_graph_when_imap_has_no_folders(self):\n        account = outlook_mail.OutlookAccount(\n            "u@example.com", "pw", "client", "refresh", "auto"\n        )\n\n        class FakeImap:\n            def logout(self):\n                return None\n\n        with patch.object(outlook_mail, "refresh_outlook_imap_token", return_value="imap"), \\\n             patch.object(outlook_mail, "_connect_imap", return_value=FakeImap()), \\\n             patch.object(outlook_mail, "_discover_folders", return_value=[]), \\\n             patch.object(outlook_mail, "refresh_outlook_graph_token", return_value="graph"), \\\n             patch.object(outlook_mail, "_graph_inbox_count", return_value=7):\n            self.assertEqual(\n                outlook_mail.load_folder_counts(account),\n                {outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: 7},\n            )\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
    encoding="utf-8",
)

print("Outlook cancellation hardening applied")
