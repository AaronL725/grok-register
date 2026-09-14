from __future__ import print_function

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def write(path, text):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError("%s: expected exactly one match, got %s" % (label, count))
    return text.replace(old, new, 1)


def replace_between(text, start, end, replacement, label):
    start_pos = text.find(start)
    if start_pos < 0:
        raise RuntimeError("%s: start marker not found" % label)
    end_pos = text.find(end, start_pos)
    if end_pos < 0:
        raise RuntimeError("%s: end marker not found" % label)
    if text.find(start, start_pos + 1) >= 0 and text.find(start, start_pos + 1) < end_pos:
        raise RuntimeError("%s: ambiguous start marker" % label)
    return text[:start_pos] + replacement + text[end_pos:]


def patch_outlook_mail():
    path = "outlook_mail.py"
    text = read(path)
    text = replace_once(
        text,
        "from typing import Callable, Optional\n",
        "from typing import Callable, Optional, Union\n",
        "outlook typing import",
    )
    replacements = {
        "def normalize_outlook_mode(mode: str | None) -> str:": "def normalize_outlook_mode(mode: Optional[str]) -> str:",
        "def extract_verification_code(subject: str = \"\", text: str = \"\", html: str = \"\", sender: str = \"\") -> str | None:": "def extract_verification_code(subject: str = \"\", text: str = \"\", html: str = \"\", sender: str = \"\") -> Optional[str]:",
        "def is_terminal_microsoft_token_error(error: Exception | str | None) -> bool:": "def is_terminal_microsoft_token_error(error: Optional[Union[Exception, str]]) -> bool:",
        "def _decode_imap_list_name(raw_line: bytes | str) -> str:": "def _decode_imap_list_name(raw_line: Union[bytes, str]) -> str:",
        "def _select_folder_count(client: imaplib.IMAP4_SSL, folder: str) -> int | None:": "def _select_folder_count(client: imaplib.IMAP4_SSL, folder: str) -> Optional[int]:",
        "def _graph_get(access_token: str, path: str, params: dict[str, str] | None = None) -> dict:": "def _graph_get(access_token: str, path: str, params: Optional[dict[str, str]] = None) -> dict:",
        "def _scan_imap_once(account: OutlookAccount, token: str, counts: dict[str, int], log_callback: LogCallback = None) -> str | None:": "def _scan_imap_once(account: OutlookAccount, token: str, counts: dict[str, int], log_callback: LogCallback = None) -> Optional[str]:",
        "def _scan_graph_once(token: str, counts: dict[str, int], email: str = \"\", log_callback: LogCallback = None) -> str | None:": "def _scan_graph_once(token: str, counts: dict[str, int], email: str = \"\", log_callback: LogCallback = None) -> Optional[str]:",
        "    before_counts: dict[str, int] | None,": "    before_counts: Optional[dict[str, int]],",
        ") -> str | None:\n    mode = normalize_outlook_mode(account.mode)": ") -> Optional[str]:\n    mode = normalize_outlook_mode(account.mode)",
        "    imap_token: str | None = None": "    imap_token: Optional[str] = None",
        "    graph_token: str | None = None": "    graph_token: Optional[str] = None",
        "    def wait_for_code(self, timeout: int = 180, interval: int = 3, cancel_callback=None) -> str | None:": "    def wait_for_code(self, timeout: int = 180, interval: int = 3, cancel_callback=None) -> Optional[str]:",
    }
    for old, new in replacements.items():
        text = replace_once(text, old, new, "outlook python39: %s" % old[:40])

    old_prepare = '''    def prepare(self) -> None:\n        _log(self._log_callback, f"[*] 使用 Outlook 邮箱: {self.email}（认证模式: {normalize_outlook_mode(self.account.mode)}）")\n        try:\n            self._folder_counts = load_folder_counts(self.account)\n            inbox = self._folder_counts.get("INBOX", self._folder_counts.get(OUTLOOK_GRAPH_INBOX_KEY, 0))\n            _log(self._log_callback, f"[*] Outlook 发送前邮件数: {inbox}")\n        except Exception as exc:\n            # Baseline failure should not prevent registration; code polling can still recover.\n            self._folder_counts = {}\n            _log(self._log_callback, f"[!] 获取 Outlook 邮件基线失败，使用 0 继续: {exc}")\n'''
    new_prepare = '''    def prepare(self) -> None:\n        _log(self._log_callback, f"[*] 使用 Outlook 邮箱: {self.email}（认证模式: {normalize_outlook_mode(self.account.mode)}）")\n        try:\n            self._folder_counts = load_folder_counts(self.account)\n            if not self._folder_counts:\n                raise RuntimeError("未能建立 Outlook 邮件基线")\n            inbox = self._folder_counts.get("INBOX", self._folder_counts.get(OUTLOOK_GRAPH_INBOX_KEY, 0))\n            _log(self._log_callback, f"[*] Outlook 发送前邮件数: {inbox}")\n        except Exception as exc:\n            self._folder_counts = {}\n            _log(self._log_callback, f"[!] 获取 Outlook 邮件基线失败，本邮箱不会提交注册: {exc}")\n            raise\n'''
    text = replace_once(text, old_prepare, new_prepare, "Outlook baseline safety")
    leftovers = [line.strip() for line in text.splitlines() if " | None" in line or "Exception |" in line or "bytes |" in line]
    if leftovers:
        raise RuntimeError("outlook_mail.py still contains Python 3.10 union syntax: %r" % leftovers)
    write(path, text)


def write_outlook_pool():
    write("outlook_mailbox_pool.py", r'''"""Thread-safe Outlook mailbox pool, task runtime and private local persistence."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from typing import Optional, Union

from outlook_mail import OutlookAccount, OutlookMailbox, normalize_outlook_mode

_MAX_POOL_BYTES = 1_000_000
_HANDLE_PREFIX = "outlook:"
_DEFAULT_POOL_PATH = "./output/mailboxes/outlook-accounts.txt"


def _split_by_dashes(line: str) -> list[str]:
    parts = []
    last = 0
    for match in re.finditer(r"-{4,}", line):
        parts.append(line[last:match.start()] + "-" * (len(match.group(0)) - 4))
        last = match.end()
    parts.append(line[last:])
    return parts


def _split_account_fields(line: str) -> list[str]:
    raw = str(line or "").strip()
    if not raw:
        return []
    if re.search(r"-{4,}", raw):
        return [part.strip() for part in _split_by_dashes(raw)]
    if "|" in raw:
        return [part.strip() for part in raw.split("|")]
    return [raw]


def _entries(data: str) -> list[str]:
    lines = [line.strip() for line in str(data or "").splitlines() if line.strip()]
    if len(lines) == 1:
        return lines[0].split()
    return lines


def parse_outlook_accounts(data: str) -> list[OutlookAccount]:
    accounts = []
    normalized = str(data or "").replace("\r\n", "\n").replace("\r", "\n")
    for entry in _entries(normalized):
        parts = _split_account_fields(entry)
        if len(parts) not in {4, 5}:
            continue
        if not parts[0] or not parts[2] or not parts[3]:
            continue
        raw_mode = parts[4] if len(parts) == 5 else "auto"
        if len(parts) == 5 and str(raw_mode).strip().lower() not in {"auto", "imap", "graph"}:
            continue
        accounts.append(
            OutlookAccount(
                email=parts[0],
                password=parts[1],
                client_id=parts[2],
                refresh_token=parts[3],
                mode=normalize_outlook_mode(raw_mode),
            )
        )
    return accounts


def inspect_outlook_mailbox_pool(data: str) -> dict:
    normalized = str(data or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    entries = _entries(normalized)
    accounts = parse_outlook_accounts(normalized)
    seen = set()
    duplicates = []
    for account in accounts:
        key = account.email.lower()
        if key in seen and key not in duplicates:
            duplicates.append(key)
        seen.add(key)
    return {
        "count": len(accounts),
        "invalid": max(0, len(entries) - len(accounts)),
        "duplicates": duplicates,
        "accounts": [{"email": account.email, "mode": account.mode} for account in accounts],
    }


def _validate_pool_data(data: str):
    normalized = str(data or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("Outlook 账号池不能为空")
    if len(normalized.encode("utf-8")) > _MAX_POOL_BYTES:
        raise ValueError("Outlook 账号池过大")
    summary = inspect_outlook_mailbox_pool(normalized)
    if summary["invalid"]:
        raise ValueError(
            "账号池中有 %s 条格式无效的记录；每行应为 "
            "email----password----clientId----refreshToken----auto/imap/graph"
            % summary["invalid"]
        )
    if summary["duplicates"]:
        raise ValueError("账号池存在重复邮箱: " + ", ".join(summary["duplicates"][:3]))
    accounts = parse_outlook_accounts(normalized)
    if not accounts:
        raise ValueError("Outlook 账号池没有有效账号")
    return normalized + "\n", accounts


def _canonical_path(path: Union[str, os.PathLike]) -> Path:
    raw = str(path or "").strip() or _DEFAULT_POOL_PATH
    return Path(raw).expanduser().resolve()


def load_outlook_mailbox_pool(path: Union[str, os.PathLike]) -> dict:
    target = _canonical_path(path)
    try:
        data = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        data = ""
    except OSError as exc:
        raise RuntimeError("读取 Outlook 账号池失败: %s" % exc) from exc
    return {"path": str(target), "data": data, **inspect_outlook_mailbox_pool(data)}


def _read_validated_pool(path: Union[str, os.PathLike]):
    target = _canonical_path(path)
    try:
        data = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError("Outlook 账号池文件不存在: %s" % target) from exc
    normalized, accounts = _validate_pool_data(data)
    return target, normalized, accounts


def get_outlook_mailbox_pool_capacity(path: Union[str, os.PathLike]) -> int:
    _target, _normalized, accounts = _read_validated_pool(path)
    return len(accounts)


def _write_private_file(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(path))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def save_outlook_mailbox_pool(path: Union[str, os.PathLike], data: str) -> dict:
    normalized, accounts = _validate_pool_data(data)
    target = _canonical_path(path)
    _write_private_file(target, normalized)
    return {
        "path": str(target),
        "count": len(accounts),
        "accounts": [{"email": account.email, "mode": account.mode} for account in accounts],
    }


@dataclass
class OutlookMailboxLease:
    handle: str
    mailbox: OutlookMailbox
    consumed: bool = False

    @property
    def email(self) -> str:
        return self.mailbox.email


class OutlookAccountPool:
    """Monotonic per-task allocator. Accounts are never wrapped or reused."""

    def __init__(self, accounts: list[OutlookAccount]) -> None:
        if not accounts:
            raise ValueError("Outlook 邮箱池没有有效账号")
        self._accounts = list(accounts)
        self._lock = threading.Lock()
        self._next = 0

    @property
    def count(self) -> int:
        return len(self._accounts)

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, len(self._accounts) - self._next)

    def acquire(self, log_callback=None) -> OutlookMailbox:
        last_error = None
        while True:
            with self._lock:
                if self._next >= len(self._accounts):
                    if last_error is not None:
                        raise RuntimeError(
                            "Outlook 邮箱池已耗尽；剩余邮箱预检均失败，且不会循环复用已领取账号: %s"
                            % last_error
                        ) from last_error
                    raise RuntimeError("Outlook 邮箱池已耗尽，不会循环复用已领取账号")
                account = self._accounts[self._next]
                self._next += 1
            mailbox = OutlookMailbox(account, log_callback=log_callback)
            try:
                mailbox.prepare()
                return mailbox
            except Exception as exc:
                last_error = exc
                if log_callback:
                    log_callback("[!] Outlook 邮箱预检失败，跳过 %s: %s" % (account.email, exc))


class OutlookTaskRuntime:
    """One shared Outlook allocator/lease registry for exactly one registration task."""

    def __init__(self, accounts: list[OutlookAccount], log_callback=None) -> None:
        self._pool = OutlookAccountPool(accounts)
        self._log_callback = log_callback
        self._lock = threading.RLock()
        self._leases = {}
        self._closed = False

    @property
    def count(self) -> int:
        return self._pool.count

    @property
    def remaining(self) -> int:
        return self._pool.remaining

    def acquire(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("Outlook 邮箱任务运行时已关闭")
        mailbox = self._pool.acquire(log_callback=self._log_callback)
        handle = _HANDLE_PREFIX + secrets.token_urlsafe(24)
        lease = OutlookMailboxLease(handle=handle, mailbox=mailbox)
        with self._lock:
            if self._closed:
                raise RuntimeError("Outlook 邮箱任务运行时已关闭")
            self._leases[handle] = lease
        return mailbox.email, handle

    def wait_for_code(
        self,
        handle: str,
        email: str,
        timeout: int = 180,
        poll_interval: int = 3,
        log_callback=None,
        cancel_callback=None,
        resend_callback=None,
    ) -> str:
        del resend_callback
        with self._lock:
            if self._closed:
                raise RuntimeError("Outlook 邮箱任务运行时已关闭")
            lease = self._leases.get(str(handle or ""))
            if lease is None:
                raise RuntimeError("Outlook 邮箱会话不存在或已失效")
            if lease.email.lower() != str(email or "").lower():
                raise RuntimeError("Outlook 邮箱会话与目标邮箱不匹配")
            lease.consumed = True
        code = lease.mailbox.wait_for_code(
            timeout=int(timeout),
            interval=int(poll_interval),
            cancel_callback=cancel_callback,
        )
        if not code:
            from registration_flow import VerificationCodeUnavailable
            raise VerificationCodeUnavailable("Outlook 在 %ss 内未收到验证码邮件" % timeout)
        return str(code)

    def status(self) -> dict:
        with self._lock:
            return {
                "count": self.count,
                "remaining": self.remaining,
                "leased": len(self._leases),
                "closed": self._closed,
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._leases.clear()


def create_outlook_task_runtime(path: Union[str, os.PathLike], log_callback=None) -> OutlookTaskRuntime:
    _target, _normalized, accounts = _read_validated_pool(path)
    return OutlookTaskRuntime(accounts, log_callback=log_callback)


def is_outlook_handle(value: str) -> bool:
    return str(value or "").startswith(_HANDLE_PREFIX)
''')


def patch_mail_service():
    path = "mail_service.py"
    text = read(path)
    marker = '''def get_duckmail_api_key():\n    return config.get("duckmail_api_key", "")\n\ndef get_email_and_token(api_key=None):\n    provider = get_email_provider()\n'''
    replacement = '''def get_duckmail_api_key():\n    return config.get("duckmail_api_key", "")\n\ndef _get_outlook_runtime():\n    runtime = globals().get("outlook_runtime")\n    if runtime is None:\n        raise RuntimeError("Outlook 邮箱运行时未初始化")\n    return runtime\n\ndef get_email_and_token(api_key=None):\n    provider = get_email_provider()\n    if provider == "outlook":\n        return _get_outlook_runtime().acquire()\n'''
    text = replace_once(text, marker, replacement, "mail_service Outlook acquire")
    marker = '''    provider = get_email_provider()\n    if provider == "yyds":\n        return yyds_get_oai_code(\n'''
    replacement = '''    provider = get_email_provider()\n    if provider == "outlook":\n        return _get_outlook_runtime().wait_for_code(\n            dev_token,\n            email,\n            timeout=timeout,\n            poll_interval=poll_interval,\n            log_callback=log_callback,\n            cancel_callback=cancel_callback,\n            resend_callback=resend_callback,\n        )\n    if provider == "yyds":\n        return yyds_get_oai_code(\n'''
    text = replace_once(text, marker, replacement, "mail_service Outlook code")
    write(path, text)


def patch_account_outputs():
    path = "account_outputs.py"
    text = read(path)
    old = '''def save_mail_credential(base_dir, email, credential):\n    path = os.path.join(base_dir, "mail_credentials.txt")\n'''
    new = '''def save_mail_credential(base_dir, email, credential):\n    if str(credential or "").startswith("outlook:"):\n        return True\n    path = os.path.join(base_dir, "mail_credentials.txt")\n'''
    text = replace_once(text, old, new, "skip Outlook session persistence")
    write(path, text)


def patch_registration_flow():
    path = "registration_flow.py"
    text = read(path)
    old = '''        callbacks.log(f"[*] 邮箱: {email}")\n        callbacks.log(f"[Debug] 邮箱credential(jwt): {dev_token}")\n        if not ops.save_mail_credential(email, dev_token):\n'''
    new = '''        callbacks.log(f"[*] 邮箱: {email}")\n        callbacks.log("[Debug] 邮箱服务凭据已创建（内容已隐藏）")\n        if not ops.save_mail_credential(email, dev_token):\n'''
    text = replace_once(text, old, new, "hide mailbox credentials in logs")
    write(path, text)


def patch_parallel():
    path = "registration_parallel.py"
    text = read(path)
    old = '''        mail_runtime = dict(runtime_namespace)\n        mail_runtime["domain_allocator"] = domain_allocator\n        mail_module.bind_runtime(mail_runtime)\n'''
    new = '''        mail_runtime = dict(runtime_namespace)\n        mail_runtime["domain_allocator"] = domain_allocator\n        if runtime_namespace.get("outlook_runtime") is not None:\n            # Every isolated worker receives the same task-scoped allocator.\n            mail_runtime["outlook_runtime"] = runtime_namespace["outlook_runtime"]\n        mail_module.bind_runtime(mail_runtime)\n'''
    text = replace_once(text, old, new, "parallel Outlook runtime injection")
    write(path, text)


def patch_engine():
    path = "grok_register_ttk.py"
    text = read(path)

    old = 'self.email_provider_combo = tk_option_menu(config_frame, self.email_provider_var, ["duckmail", "yyds", "cloudflare", "cloudmail"], width=12)'
    new = 'self.email_provider_combo = tk_option_menu(config_frame, self.email_provider_var, ["duckmail", "yyds", "cloudflare", "cloudmail", "outlook"], width=12)'
    text = replace_once(text, old, new, "GUI provider list")

    old = '''        add_label(21, 2, "YYDS JWT:")\n        self.yyds_jwt_var = tk.StringVar(value=str(config.get("yyds_jwt", "")))\n        self.yyds_jwt_entry = tk_entry(config_frame, textvariable=self.yyds_jwt_var, width=34, show="*")\n        add_field(self.yyds_jwt_entry, 21, 3)\n\n        btn_frame = tk.Frame(main_frame, bg=UI_BG)\n'''
    new = '''        add_label(21, 2, "YYDS JWT:")\n        self.yyds_jwt_var = tk.StringVar(value=str(config.get("yyds_jwt", "")))\n        self.yyds_jwt_entry = tk_entry(config_frame, textvariable=self.yyds_jwt_var, width=34, show="*")\n        add_field(self.yyds_jwt_entry, 21, 3)\n\n        add_label(22, 0, "Outlook 邮箱池:")\n        self.outlook_accounts_file_var = tk.StringVar(\n            value=str(config.get("outlook_accounts_file", "./output/mailboxes/outlook-accounts.txt"))\n        )\n        self.outlook_accounts_file_entry = tk_entry(\n            config_frame, textvariable=self.outlook_accounts_file_var, width=34\n        )\n        add_field(self.outlook_accounts_file_entry, 22, 1)\n        self.outlook_pool_btn = tk_button(\n            config_frame, text="管理 Outlook 邮箱池", command=self.manage_outlook_mailbox_pool\n        )\n        add_field(self.outlook_pool_btn, 22, 3, sticky=tk.W)\n\n        btn_frame = tk.Frame(main_frame, bg=UI_BG)\n'''
    text = replace_once(text, old, new, "GUI Outlook pool row")

    method_marker = '''    def _sync_multithread_controls(self):\n        if not hasattr(self, "multi_thread_workers_spinbox"):\n            return\n        state = tk.NORMAL if bool(self.multi_thread_var.get()) else tk.DISABLED\n        self.multi_thread_workers_spinbox.config(state=state)\n\n    def test_proxy_pool(self):\n'''
    method_replacement = '''    def _sync_multithread_controls(self):\n        if not hasattr(self, "multi_thread_workers_spinbox"):\n            return\n        state = tk.NORMAL if bool(self.multi_thread_var.get()) else tk.DISABLED\n        self.multi_thread_workers_spinbox.config(state=state)\n\n    def manage_outlook_mailbox_pool(self):\n        from outlook_mailbox_pool import (\n            inspect_outlook_mailbox_pool, load_outlook_mailbox_pool,\n            save_outlook_mailbox_pool,\n        )\n        path = self.outlook_accounts_file_var.get().strip() or "./output/mailboxes/outlook-accounts.txt"\n        self.outlook_accounts_file_var.set(path)\n        window = tk.Toplevel(self.root)\n        window.title("Outlook 邮箱池")\n        window.geometry("860x560")\n        window.configure(bg=UI_BG)\n        window.transient(self.root)\n\n        tk_label(\n            window,\n            text="每行格式: email----password----clientId----refreshToken----auto/imap/graph",\n        ).pack(anchor=tk.W, padx=12, pady=(12, 4))\n        tk_label(window, text="文件: %s" % path, fg=UI_MUTED_FG).pack(\n            anchor=tk.W, padx=12, pady=(0, 8)\n        )\n        editor = scrolledtext.ScrolledText(\n            window,\n            bg="#111111", fg=UI_FG, insertbackground=UI_FG,\n            height=24, wrap=tk.NONE, undo=True,\n        )\n        editor.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))\n        status_var = tk.StringVar(value="")\n        tk_label(window, textvariable=status_var, fg=UI_MUTED_FG).pack(\n            anchor=tk.W, padx=12, pady=(0, 8)\n        )\n\n        def update_summary(_event=None):\n            summary = inspect_outlook_mailbox_pool(editor.get("1.0", tk.END))\n            status_var.set(\n                "有效: %s | 无效: %s | 重复: %s"\n                % (summary["count"], summary["invalid"], len(summary["duplicates"]))\n            )\n\n        def save_pool():\n            try:\n                summary = save_outlook_mailbox_pool(path, editor.get("1.0", tk.END))\n                update_summary()\n                self.log("[*] Outlook 邮箱池已保存: %s 个账号" % summary["count"])\n            except Exception as exc:\n                messagebox.showerror("Outlook 邮箱池保存失败", str(exc), parent=window)\n\n        try:\n            current = load_outlook_mailbox_pool(path)\n            editor.insert("1.0", current.get("data", ""))\n        except Exception as exc:\n            status_var.set("读取失败: %s" % exc)\n        editor.bind("<KeyRelease>", update_summary)\n        update_summary()\n        buttons = tk.Frame(window, bg=UI_BG)\n        buttons.pack(fill=tk.X, padx=12, pady=(0, 12))\n        tk_button(buttons, text="保存邮箱池", command=save_pool).pack(side=tk.LEFT)\n        tk_button(buttons, text="关闭", command=window.destroy).pack(side=tk.RIGHT)\n\n    def test_proxy_pool(self):\n'''
    text = replace_once(text, method_marker, method_replacement, "GUI Outlook pool manager")

    old = '''        config["cloudmail_domains"] = self.cloudmail_domains_var.get().strip()\n        config["grok2api_auto_add_local"] = bool(self.grok2api_local_auto_var.get())\n'''
    new = '''        config["cloudmail_domains"] = self.cloudmail_domains_var.get().strip()\n        config["outlook_accounts_file"] = (\n            self.outlook_accounts_file_var.get().strip() or "./output/mailboxes/outlook-accounts.txt"\n        )\n        config["grok2api_auto_add_local"] = bool(self.grok2api_local_auto_var.get())\n'''
    text = replace_once(text, old, new, "GUI save Outlook path")

    start = "def run_registration_common(count, log_callback, cancel_callback, accounts_output_file, observer):\n"
    end = "\n\nclass GrokRegisterGUI:\n"
    replacement = '''def resolve_registration_count(count, log_callback=None):\n    requested = max(1, int(count))\n    provider = str(config.get("email_provider", "") or "").strip().lower()\n    if provider != "outlook":\n        return requested\n    from outlook_mailbox_pool import get_outlook_mailbox_pool_capacity\n    available = get_outlook_mailbox_pool_capacity(config.get("outlook_accounts_file", ""))\n    effective = min(requested, int(available))\n    if effective < requested and log_callback:\n        log_callback(\n            "[*] Outlook 邮箱池有 %s 个有效账号；请求 %s 个，本次最多执行 %s 个"\n            % (available, requested, effective)\n        )\n    return effective\n\n\ndef run_registration_common(count, log_callback, cancel_callback, accounts_output_file, observer):\n    from registration_flow import RegistrationCallbacks, RegistrationOperations, run_batch\n\n    provider = str(config.get("email_provider", "") or "").strip().lower()\n    effective_count = resolve_registration_count(count, log_callback=log_callback)\n    task_outlook_runtime = None\n    if provider == "outlook":\n        from outlook_mailbox_pool import create_outlook_task_runtime\n        task_outlook_runtime = create_outlook_task_runtime(\n            config.get("outlook_accounts_file", ""), log_callback=log_callback\n        )\n        globals()["outlook_runtime"] = task_outlook_runtime\n        effective_count = min(effective_count, task_outlook_runtime.count)\n        _bind_mail_service()\n    elif provider == "cloudmail":\n        _bind_mail_service()\n        _mail_service.cloudmail_preflight(log_callback=log_callback)\n\n    callbacks = RegistrationCallbacks(log=log_callback, cancelled=cancel_callback)\n    try:\n        parallel_enabled = bool(config.get("multi_thread_enabled", False))\n        parallel_workers = int(config.get("multi_thread_workers", 4) or 4)\n        if parallel_enabled and parallel_workers > 1 and effective_count > 1:\n            from registration_parallel import run_parallel_batch\n            return run_parallel_batch(\n                count=effective_count,\n                callbacks=callbacks,\n                observer=observer,\n                runtime_namespace=globals(),\n                accounts_output_file=accounts_output_file,\n                workers=parallel_workers,\n                enable_nsfw=bool(config.get("enable_nsfw", True)),\n                cleanup_interval=MEMORY_CLEANUP_INTERVAL,\n                max_slot_retry=3,\n                max_mail_retry=3,\n            )\n        operations = RegistrationOperations(\n            start_browser=lambda: start_browser(log_callback=log_callback),\n            restart_browser=lambda: restart_browser(log_callback=log_callback),\n            browser_missing=lambda: _registration_browser.browser is None,\n            open_signup_page=lambda: open_signup_page(log_callback=log_callback, cancel_callback=cancel_callback),\n            fill_email_and_submit=lambda: fill_email_and_submit(\n                log_callback=log_callback,\n                cancel_callback=cancel_callback,\n                on_mail_created=lambda email, token: _save_mail_credential(email, token, log_callback),\n            ),\n            save_mail_credential=lambda email, token: _save_mail_credential(email, token, log_callback),\n            fill_code_and_submit=lambda email, token: fill_code_and_submit(email, token, log_callback=log_callback, cancel_callback=cancel_callback),\n            fill_profile_and_submit=lambda: fill_profile_and_submit(log_callback=log_callback, cancel_callback=cancel_callback),\n            wait_for_sso_cookie=lambda: wait_for_sso_cookie(log_callback=log_callback, cancel_callback=cancel_callback),\n            enable_nsfw=lambda sso: enable_nsfw_for_token(sso, log_callback=log_callback),\n            persist_account_line=lambda email, password, sso: _append_account_line(accounts_output_file, email, password, sso),\n            queue_unsaved_result=lambda payload, error: _queue_unsaved_account(accounts_output_file, payload, error, log_callback),\n            add_tokens=lambda sso, email: add_token_to_grok2api_pools(sso, email=email, log_callback=log_callback),\n            export_cpa=lambda email, password, sso: maybe_export_cpa_xai_after_success(\n                email=email, password=password, sso=sso,\n                log_callback=log_callback, cancel_callback=cancel_callback,\n            ),\n            cleanup=lambda reason: cleanup_runtime_memory(log_callback=log_callback, reason=reason),\n            sleep=lambda seconds: sleep_with_cancel(seconds, cancel_callback),\n            cancelled_exception=RegistrationCancelled,\n            retry_exception=AccountRetryNeeded,\n            internal_stage_markers=True,\n            screen_sso=lambda sso, email: _screen_registered_sso(sso, email, log_callback),\n        )\n        return run_batch(\n            count=effective_count,\n            callbacks=callbacks,\n            observer=observer,\n            ops=operations,\n            enable_nsfw=bool(config.get("enable_nsfw", True)),\n            cleanup_interval=MEMORY_CLEANUP_INTERVAL,\n            max_slot_retry=3,\n            max_mail_retry=3,\n        )\n    finally:\n        if task_outlook_runtime is not None:\n            task_outlook_runtime.close()\n            if globals().get("outlook_runtime") is task_outlook_runtime:\n                globals().pop("outlook_runtime", None)\n'''
    text = replace_between(text, start, end, replacement, "task-scoped Outlook runtime")

    old = '''            config.clear()\n            config.update(validated)\n            save_config()\n        except (ValueError, ConfigError) as exc:\n'''
    new = '''            config.clear()\n            config.update(validated)\n            save_config()\n            count = resolve_registration_count(count, log_callback=self.log)\n        except (ValueError, ConfigError, RuntimeError) as exc:\n'''
    text = replace_once(text, old, new, "GUI effective Outlook count")

    old = '''    count = int(config.get("register_count", 1) or 1)\n    cli_log("[*] CLI 已加载配置")\n'''
    new = '''    count = int(config.get("register_count", 1) or 1)\n    try:\n        count = resolve_registration_count(count, log_callback=cli_log)\n    except Exception as exc:\n        cli_log(f"[!] Outlook 邮箱池校验失败: {exc}")\n        return\n    cli_log("[*] CLI 已加载配置")\n'''
    text = replace_once(text, old, new, "CLI effective Outlook count")
    write(path, text)


def patch_web_server():
    path = "web/server.py"
    text = read(path)
    text = replace_once(
        text,
        "from fastapi.responses import FileResponse, HTMLResponse\n",
        "from fastapi.responses import FileResponse, HTMLResponse, JSONResponse\n",
        "web JSONResponse import",
    )
    text = replace_once(
        text,
        'PROXY_POOL_CSS = Path(__file__).resolve().parent / "proxy-pool.css"\n',
        'PROXY_POOL_CSS = Path(__file__).resolve().parent / "proxy-pool.css"\nOUTLOOK_MAILBOX_JS = Path(__file__).resolve().parent / "outlook-mailbox.js"\n',
        "web Outlook asset constant",
    )
    old = '''    if PROXY_POOL_JS.is_file():\n        html = html.replace("</body>", '<script src="/proxy-pool.js"></script>\\n</body>', 1)\n    return HTMLResponse(html, headers={"Cache-Control": "no-store"})\n'''
    new = '''    if PROXY_POOL_JS.is_file():\n        html = html.replace("</body>", '<script src="/proxy-pool.js"></script>\\n</body>', 1)\n    if OUTLOOK_MAILBOX_JS.is_file():\n        html = html.replace("</body>", '<script src="/outlook-mailbox.js"></script>\\n</body>', 1)\n    return HTMLResponse(html, headers={"Cache-Control": "no-store"})\n'''
    text = replace_once(text, old, new, "inject Outlook WebUI asset")
    old = '''@app.get("/proxy-pool.css", include_in_schema=False)\ndef proxy_pool_css():\n    return FileResponse(PROXY_POOL_CSS, media_type="text/css", headers={"Cache-Control": "no-store"})\n\n\n@app.get("/health")\n'''
    new = '''@app.get("/proxy-pool.css", include_in_schema=False)\ndef proxy_pool_css():\n    return FileResponse(PROXY_POOL_CSS, media_type="text/css", headers={"Cache-Control": "no-store"})\n\n\n@app.get("/outlook-mailbox.js", include_in_schema=False)\ndef outlook_mailbox_js():\n    return FileResponse(OUTLOOK_MAILBOX_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})\n\n\n@app.middleware("http")\nasync def protect_outlook_mailbox_api(request: Request, call_next):\n    response = await call_next(request)\n    if request.url.path.startswith("/api/mailboxes/outlook"):\n        response.headers["Cache-Control"] = "no-store"\n        response.headers["X-Content-Type-Options"] = "nosniff"\n    return response\n\n\ndef _require_local_origin(request: Request) -> None:\n    origin = str(request.headers.get("origin") or "").strip()\n    if not origin:\n        return\n    from urllib.parse import urlsplit\n    host = (urlsplit(origin).hostname or "").lower()\n    if host not in {"127.0.0.1", "localhost", "::1"}:\n        raise HTTPException(status_code=403, detail="Outlook 邮箱池只允许本地 WebUI 访问")\n\n\n@app.get("/health")\n'''
    text = replace_once(text, old, new, "web Outlook API security")

    marker = '''@app.get("/api/proxy-pool/status")\ndef proxy_pool_status():\n'''
    endpoints = '''@app.get("/api/mailboxes/outlook")\ndef get_outlook_mailboxes(request: Request):\n    _require_local_origin(request)\n    from outlook_mailbox_pool import load_outlook_mailbox_pool\n    cfg = _load_config_if_idle()\n    try:\n        summary = load_outlook_mailbox_pool(cfg.get("outlook_accounts_file", ""))\n    except Exception as exc:\n        raise HTTPException(status_code=400, detail=str(exc)) from exc\n    return JSONResponse({\n        "ok": True,\n        "path": summary["path"],\n        "data": summary["data"],\n        "count": summary["count"],\n        "invalid": summary["invalid"],\n        "duplicates": summary["duplicates"],\n        "accounts": summary["accounts"],\n    })\n\n\n@app.put("/api/mailboxes/outlook")\nasync def put_outlook_mailboxes(request: Request):\n    _require_local_origin(request)\n    payload = await request.json()\n    if not isinstance(payload, dict) or not isinstance(payload.get("data"), str):\n        raise HTTPException(status_code=400, detail="请求必须包含字符串字段 data")\n    from outlook_mailbox_pool import save_outlook_mailbox_pool\n    with _job_lock:\n        if _job_state["running"]:\n            raise HTTPException(status_code=409, detail="任务运行期间不能修改 Outlook 邮箱池")\n        if _maintenance_state is not None:\n            raise HTTPException(status_code=409, detail="维护操作期间不能修改 Outlook 邮箱池")\n        engine.load_config()\n        path = engine.config.get("outlook_accounts_file", "")\n        try:\n            summary = save_outlook_mailbox_pool(path, payload["data"])\n        except (ValueError, RuntimeError, OSError) as exc:\n            raise HTTPException(status_code=400, detail=str(exc)) from exc\n    _append_log("[*] Outlook 邮箱池已保存: %s 个账号" % summary["count"])\n    return JSONResponse({"ok": True, **summary})\n\n\n''' + marker
    text = replace_once(text, marker, endpoints, "web Outlook endpoints")

    old = '''        count = int(engine.config["register_count"])\n        controller = engine.CliStopController()\n'''
    new = '''        count = engine.resolve_registration_count(\n            int(engine.config["register_count"]), log_callback=_append_log\n        )\n        controller = engine.CliStopController()\n'''
    text = replace_once(text, old, new, "web effective Outlook count")
    write(path, text)


def write_web_outlook_js():
    write("web/outlook-mailbox.js", r'''(function () {
  'use strict';

  const provider = document.getElementById('email_provider');
  const pathInput = document.getElementById('outlook_accounts_file');
  if (!provider || !pathInput) return;

  const wrapper = document.createElement('div');
  wrapper.className = 'field full';
  wrapper.id = 'outlookMailboxPoolEditor';
  wrapper.innerHTML = [
    '<label class="field-label" for="outlookMailboxPoolData">Outlook mailbox pool</label>',
    '<div class="field-help">email----password----clientId----refreshToken----auto/imap/graph</div>',
    '<textarea id="outlookMailboxPoolData" spellcheck="false" autocomplete="off" ',
    'style="width:100%;min-height:180px;resize:vertical;border:1px solid #2d2d32;border-radius:8px;',
    'background:#0b0b0d;color:#f5f5f5;padding:10px;font:12px/1.45 SFMono-Regular,Consolas,monospace;',
    'outline:none"></textarea>',
    '<div style="display:flex;gap:8px;align-items:center;margin-top:8px">',
    '<button id="outlookPoolLoad" type="button" class="mini-btn">Load pool</button>',
    '<button id="outlookPoolSave" type="button" class="mini-btn">Save pool</button>',
    '<span id="outlookPoolStatus" class="field-help" style="margin-left:auto"></span>',
    '</div>'
  ].join('');
  const grid = pathInput.closest('.grid') || pathInput.parentElement.parentElement;
  grid.appendChild(wrapper);

  const editor = document.getElementById('outlookMailboxPoolData');
  const status = document.getElementById('outlookPoolStatus');
  const loadBtn = document.getElementById('outlookPoolLoad');
  const saveBtn = document.getElementById('outlookPoolSave');

  function setStatus(text, error) {
    status.textContent = text || '';
    status.style.color = error ? '#ff7f7f' : '#707079';
  }

  function syncVisibility() {
    wrapper.hidden = provider.value !== 'outlook';
  }

  async function loadPool() {
    setStatus('Loading…', false);
    const response = await fetch('/api/mailboxes/outlook', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to load Outlook mailbox pool');
    editor.value = data.data || '';
    setStatus('Valid: ' + data.count + ' · Invalid: ' + data.invalid + ' · Duplicates: ' + (data.duplicates || []).length, false);
  }

  async function savePool() {
    setStatus('Saving…', false);
    const configResponse = await fetch('/api/config', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({outlook_accounts_file: pathInput.value || './output/mailboxes/outlook-accounts.txt'})
    });
    const configData = await configResponse.json();
    if (!configResponse.ok) throw new Error(configData.detail || 'Failed to save Outlook pool path');
    const response = await fetch('/api/mailboxes/outlook', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({data: editor.value})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to save Outlook mailbox pool');
    setStatus('Saved · Valid: ' + data.count, false);
  }

  provider.addEventListener('change', function () {
    syncVisibility();
    if (provider.value === 'outlook' && !editor.value) {
      loadPool().catch(function (error) { setStatus(error.message, true); });
    }
  });
  loadBtn.addEventListener('click', function () {
    loadPool().catch(function (error) { setStatus(error.message, true); });
  });
  saveBtn.addEventListener('click', function () {
    savePool().catch(function (error) { setStatus(error.message, true); });
  });
  syncVisibility();
  if (provider.value === 'outlook') {
    loadPool().catch(function (error) { setStatus(error.message, true); });
  }
})();
''')


def patch_web_index():
    path = "web/index.html"
    text = read(path)
    text = replace_once(
        text,
        "cloudmail_path_messages:['Cloud Mail 邮件路径','']",
        "cloudmail_path_messages:['Cloud Mail 邮件路径',''],outlook_accounts_file:['Outlook 邮箱池文件','仅保存本地池文件路径，账号密钥不写入 config.json。']",
        "WebUI zh Outlook field",
    )
    text = replace_once(
        text,
        "cloudmail_path_messages:['Cloud Mail messages path','']",
        "cloudmail_path_messages:['Cloud Mail messages path',''],outlook_accounts_file:['Outlook mailbox pool file','Only the local pool path is stored in config.json; mailbox secrets stay in the pool file.']",
        "WebUI en Outlook field",
    )
    text = replace_once(
        text,
        "mail:[['email_provider','select',['duckmail','yyds','cloudflare','cloudmail']],['duckmail_api_key','password']",
        "mail:[['email_provider','select',['duckmail','yyds','cloudflare','cloudmail','outlook']],['outlook_accounts_file','text','full'],['duckmail_api_key','password']",
        "WebUI Outlook provider option",
    )
    write(path, text)


def patch_config_example():
    path = "config.example.json"
    data = json.loads(read(path))
    ordered = {}
    inserted = False
    for key, value in data.items():
        ordered[key] = value
        if key == "cloudmail_path_messages":
            ordered["outlook_accounts_file"] = "./output/mailboxes/outlook-accounts.txt"
            inserted = True
    if not inserted:
        raise RuntimeError("config example insertion point not found")
    write(path, json.dumps(ordered, ensure_ascii=False, indent=2) + "\n")


def patch_gitignore():
    path = ".gitignore"
    text = read(path)
    if "output/mailboxes/" not in text:
        text = replace_once(text, "mail_credentials.txt\n", "mail_credentials.txt\noutput/mailboxes/\n", "Outlook pool ignore")
    write(path, text)


def patch_readme():
    path = "README.md"
    text = read(path)
    text = replace_once(
        text,
        "项目提供 GUI / CLI / WebUI、四种临时邮箱、可选 1–8 线程并发",
        "项目提供 GUI / CLI / WebUI、四种临时邮箱与 Outlook 邮箱池、可选 1–8 线程并发",
        "README intro providers",
    )
    text = replace_once(
        text,
        "- 支持 **DuckMail / YYDS / Cloudflare 临时邮箱 / Cloud Mail** 四种邮箱来源。",
        "- 支持 **DuckMail / YYDS / Cloudflare 临时邮箱 / Cloud Mail / Outlook 邮箱池** 五种邮箱来源。",
        "README provider bullet",
    )
    text = replace_once(
        text,
        "| `email_provider` | `duckmail` / `yyds` / `cloudflare` / `cloudmail` |",
        "| `email_provider` | `duckmail` / `yyds` / `cloudflare` / `cloudmail` / `outlook` |",
        "README provider table",
    )
    marker = "#### Cloudflare 临时邮箱\n"
    outlook_section = '''#### Outlook 邮箱池\n\nOutlook 模式使用已经存在、可通过 OAuth2 读取邮件的 Outlook / Microsoft 邮箱，不负责创建 Microsoft 邮箱。配置中只保存邮箱池文件路径：\n\n```json\n{\n  "email_provider": "outlook",\n  "outlook_accounts_file": "./output/mailboxes/outlook-accounts.txt"\n}\n```\n\n邮箱池每行格式：\n\n```text\nemail----password----clientId----refreshToken----auto\n```\n\n最后一列可选，支持 `auto` / `imap` / `graph`；省略时默认 `auto`。也兼容用 `|` 分隔的相同字段。`password` 字段会保留在池记录中，但验证码读取使用 `clientId + refreshToken` 获取 OAuth2 access token。\n\n- `auto`：优先尝试 IMAP，并在同一轮轮询中使用 Microsoft Graph 作为补充。\n- `imap`：通过 `outlook.office365.com:993` + XOAUTH2 读取收件箱、垃圾邮件、归档等常见文件夹。\n- `graph`：通过 Microsoft Graph 读取 Inbox。\n- 每个邮箱在单次注册任务中最多领取一次；多线程 worker 共用同一个任务级分配器，不会重复领取同一邮箱。\n- 提交邮箱前先记录邮件数量基线，只扫描之后新到达的邮件，避免把旧验证码当成本次验证码。无法建立基线的邮箱不会提交注册。\n- 若请求注册数量大于邮箱池有效账号数，本次任务会自动把目标数量限制为邮箱池容量，不会循环复用已领取邮箱。\n- Outlook refresh token / access token 不会写入 `mail_credentials.txt`、普通日志或 `config.json`。邮箱池文件会尽量以 `0600` 权限原子写入，并已加入 `.gitignore`。\n\nGUI 提供“管理 Outlook 邮箱池”编辑器；WebUI 的邮箱服务页也提供独立邮箱池编辑区。Web 接口仅监听本机，并对邮箱池响应设置 `no-store`。\n\n'''
    text = replace_once(text, marker, outlook_section + marker, "README Outlook section")
    write(path, text)


def write_tests():
    write("tests/test_outlook_mailbox_pool.py", r'''import os
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import account_outputs
import mail_service
import outlook_mail
import outlook_mailbox_pool as pool
from registration_flow import VerificationCodeUnavailable


ACCOUNT_ROWS = "\n".join(
    "user%s@example.com----pw%s----client%s----refresh-token-%s----auto" % (i, i, i, i)
    for i in range(1, 9)
)


class OutlookMailboxPoolTests(unittest.TestCase):
    def test_parser_supports_dash_pipe_and_modes(self):
        data = (
            "a@example.com----pw----client----refresh-token----imap\n"
            "b@example.com|pw|client2|refresh-token-2|graph\n"
            "c@example.com----pw----client3----refresh-token-3\n"
        )
        accounts = pool.parse_outlook_accounts(data)
        self.assertEqual([a.mode for a in accounts], ["imap", "graph", "auto"])
        self.assertEqual(accounts[0].refresh_token, "refresh-token")

    def test_invalid_mode_and_duplicate_are_reported_without_secrets(self):
        secret = "super-secret-refresh-token"
        data = (
            "a@example.com----pw----client----%s----badmode\n" % secret
            + "b@example.com----pw----client----token----auto\n"
            + "B@example.com----pw----client2----token2----imap\n"
        )
        summary = pool.inspect_outlook_mailbox_pool(data)
        self.assertEqual(summary["invalid"], 1)
        self.assertEqual(summary["duplicates"], ["b@example.com"])
        self.assertNotIn(secret, repr(summary["accounts"]))

    def test_private_atomic_save_and_safe_load_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pool.txt"
            saved = pool.save_outlook_mailbox_pool(path, ACCOUNT_ROWS)
            self.assertEqual(saved["count"], 8)
            loaded = pool.load_outlook_mailbox_pool(path)
            self.assertEqual(loaded["count"], 8)
            self.assertEqual(len(loaded["accounts"]), 8)
            self.assertNotIn("refresh-token", repr(loaded["accounts"]))
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_task_runtime_is_shared_thread_safe_and_non_reusing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            pool.OutlookMailbox, "prepare", return_value=None
        ):
            path = Path(tmp) / "pool.txt"
            pool.save_outlook_mailbox_pool(path, ACCOUNT_ROWS)
            runtime = pool.create_outlook_task_runtime(path)
            with ThreadPoolExecutor(max_workers=8) as executor:
                pairs = list(executor.map(lambda _i: runtime.acquire(), range(8)))
            self.assertEqual(len({email for email, _handle in pairs}), 8)
            self.assertEqual(len({handle for _email, handle in pairs}), 8)
            self.assertTrue(all(handle.startswith("outlook:") for _email, handle in pairs))
            with self.assertRaises(RuntimeError):
                runtime.acquire()
            runtime.close()

    def test_new_task_gets_fresh_allocator(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            pool.OutlookMailbox, "prepare", return_value=None
        ):
            path = Path(tmp) / "pool.txt"
            pool.save_outlook_mailbox_pool(path, ACCOUNT_ROWS)
            first = pool.create_outlook_task_runtime(path)
            second = pool.create_outlook_task_runtime(path)
            self.assertEqual(first.acquire()[0], "user1@example.com")
            self.assertEqual(second.acquire()[0], "user1@example.com")

    def test_runtime_resolves_opaque_handle_and_timeout(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            pool.OutlookMailbox, "prepare", return_value=None
        ):
            path = Path(tmp) / "pool.txt"
            pool.save_outlook_mailbox_pool(path, ACCOUNT_ROWS)
            runtime = pool.create_outlook_task_runtime(path)
            email, handle = runtime.acquire()
            with patch.object(pool.OutlookMailbox, "wait_for_code", return_value="ABC123"):
                self.assertEqual(runtime.wait_for_code(handle, email), "ABC123")
            with self.assertRaises(RuntimeError):
                runtime.wait_for_code(handle, "wrong@example.com")
            email2, handle2 = runtime.acquire()
            with patch.object(pool.OutlookMailbox, "wait_for_code", return_value=None):
                with self.assertRaises(VerificationCodeUnavailable):
                    runtime.wait_for_code(handle2, email2, timeout=1)

    def test_mail_service_dispatches_to_injected_runtime(self):
        calls = []

        class FakeRuntime:
            def acquire(self):
                calls.append("acquire")
                return "u@example.com", "outlook:opaque"

            def wait_for_code(self, *args, **kwargs):
                calls.append((args, kwargs))
                return "ABC123"

        previous_config = mail_service.config
        previous_runtime = getattr(mail_service, "outlook_runtime", None)
        had_runtime = hasattr(mail_service, "outlook_runtime")
        try:
            mail_service.bind_runtime({
                "config": {"email_provider": "outlook"},
                "outlook_runtime": FakeRuntime(),
            })
            self.assertEqual(mail_service.get_email_and_token(), ("u@example.com", "outlook:opaque"))
            self.assertEqual(
                mail_service.get_oai_code("outlook:opaque", "u@example.com", timeout=7),
                "ABC123",
            )
            self.assertEqual(calls[0], "acquire")
            self.assertEqual(calls[1][0][:2], ("outlook:opaque", "u@example.com"))
        finally:
            mail_service.config = previous_config
            if had_runtime:
                mail_service.outlook_runtime = previous_runtime
            elif hasattr(mail_service, "outlook_runtime"):
                delattr(mail_service, "outlook_runtime")

    def test_outlook_handle_is_never_persisted_as_mail_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(account_outputs.save_mail_credential(tmp, "u@example.com", "outlook:opaque"))
            self.assertFalse((Path(tmp) / "mail_credentials.txt").exists())

    def test_prepare_requires_pre_send_baseline(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh")
        mailbox = outlook_mail.OutlookMailbox(account)
        with patch.object(outlook_mail, "load_folder_counts", side_effect=RuntimeError("network")):
            with self.assertRaises(RuntimeError):
                mailbox.prepare()

    def test_token_refresh_uses_expected_scopes(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh")

        class Response:
            status_code = 200
            text = ""
            def json(self):
                return {"access_token": "access"}

        with patch.object(outlook_mail.requests, "post", return_value=Response()) as post:
            self.assertEqual(outlook_mail.refresh_outlook_imap_token(account), "access")
            imap_scope = post.call_args.kwargs["data"]["scope"]
            self.assertIn("IMAP.AccessAsUser.All", imap_scope)
        with patch.object(outlook_mail.requests, "post", return_value=Response()) as post:
            self.assertEqual(outlook_mail.refresh_outlook_graph_token(account), "access")
            graph_scope = post.call_args.kwargs["data"]["scope"]
            self.assertIn("Mail.Read", graph_scope)

    def test_graph_scan_only_reads_new_messages(self):
        counts = {outlook_mail.OUTLOOK_GRAPH_INBOX_KEY: 2}
        with patch.object(outlook_mail, "_graph_inbox_count", return_value=2), patch.object(
            outlook_mail, "_graph_get"
        ) as get:
            self.assertIsNone(outlook_mail._scan_graph_once("token", counts))
            get.assert_not_called()

        message = {
            "subject": "ABC-123 xAI verification",
            "bodyPreview": "verification code",
            "body": {"content": "ABC-123"},
            "from": {"emailAddress": {"address": "noreply@x.ai"}},
        }
        with patch.object(outlook_mail, "_graph_inbox_count", return_value=3), patch.object(
            outlook_mail, "_graph_get", return_value={"value": [message]}
        ):
            self.assertEqual(outlook_mail._scan_graph_once("token", counts), "ABC123")
            self.assertEqual(counts[outlook_mail.OUTLOOK_GRAPH_INBOX_KEY], 3)

    def test_auto_mode_can_fall_back_to_graph(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "auto")
        counts = {"INBOX": 0}
        with patch.object(
            outlook_mail, "refresh_outlook_imap_token", side_effect=RuntimeError("temporary imap error")
        ), patch.object(outlook_mail, "refresh_outlook_graph_token", return_value="graph-token"), patch.object(
            outlook_mail, "_scan_graph_once", return_value="ABC123"
        ):
            self.assertEqual(outlook_mail.wait_for_outlook_code(account, counts, timeout=2, interval=1), "ABC123")

    def test_double_terminal_oauth_error_fails_fast(self):
        account = outlook_mail.OutlookAccount("u@example.com", "pw", "client", "refresh", "auto")
        with patch.object(
            outlook_mail, "refresh_outlook_imap_token", side_effect=RuntimeError("invalid_grant")
        ), patch.object(
            outlook_mail, "refresh_outlook_graph_token", side_effect=RuntimeError("invalid_grant")
        ), patch.object(outlook_mail, "_sleep_interruptibly") as sleep:
            with self.assertRaises(RuntimeError):
                outlook_mail.wait_for_outlook_code(account, {"INBOX": 0}, timeout=30, interval=3)
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
''')

    write("tests/test_outlook_web_api.py", r'''import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "web dependencies not installed")
class OutlookWebApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web import server
        cls.server = server
        cls.client = TestClient(server.app)

    def setUp(self):
        with self.server._job_lock:
            self.server._job_state["running"] = False
            self.server._maintenance_state = None

    def _load_for_path(self, path):
        cfg = dict(self.server.engine.DEFAULT_CONFIG)
        cfg["outlook_accounts_file"] = str(path)
        def load():
            self.server.engine.config.clear()
            self.server.engine.config.update(cfg)
            return self.server.engine.config
        return load

    def test_index_includes_outlook_asset(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/outlook-mailbox.js", response.text)

    def test_pool_api_roundtrip_is_no_store_and_config_has_no_secrets(self):
        row = "u@example.com----pw----client----secret-refresh-token----auto\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outlook.txt"
            loader = self._load_for_path(path)
            with patch.object(self.server.engine, "load_config", side_effect=loader):
                put = self.client.put("/api/mailboxes/outlook", json={"data": row})
                self.assertEqual(put.status_code, 200)
                self.assertEqual(put.headers.get("cache-control"), "no-store")
                self.assertEqual(put.headers.get("x-content-type-options"), "nosniff")
                get = self.client.get("/api/mailboxes/outlook")
                self.assertEqual(get.status_code, 200)
                self.assertEqual(get.json()["count"], 1)
                self.assertIn("secret-refresh-token", get.json()["data"])
                self.assertNotIn("secret-refresh-token", repr(get.json()["accounts"]))
                config = self.client.get("/api/config").json()["config"]
                self.assertEqual(config["outlook_accounts_file"], str(path))
                self.assertNotIn("secret-refresh-token", repr(config))

    def test_foreign_origin_is_rejected(self):
        response = self.client.get(
            "/api/mailboxes/outlook",
            headers={"Origin": "https://example.com"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_pool_update_is_rejected_while_job_runs(self):
        with self.server._job_lock:
            self.server._job_state["running"] = True
        response = self.client.put("/api/mailboxes/outlook", json={"data": "x"})
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
''')


def main():
    patch_outlook_mail()
    write_outlook_pool()
    patch_mail_service()
    patch_account_outputs()
    patch_registration_flow()
    patch_parallel()
    patch_engine()
    patch_web_server()
    write_web_outlook_js()
    patch_web_index()
    patch_config_example()
    patch_gitignore()
    patch_readme()
    write_tests()
    print("Outlook mailbox pool integration patch applied")


if __name__ == "__main__":
    main()
