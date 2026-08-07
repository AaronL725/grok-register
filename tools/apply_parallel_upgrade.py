from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def write(path, content):
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


# 1) Configuration: default-off switch, default 4 workers, bounded 1..8.
path = "app_config.py"
text = read(path)
text = replace_once(
    text,
    '    "register_count": 1,\n',
    '    "register_count": 1,\n    "multi_thread_enabled": False,\n    "multi_thread_workers": 4,\n',
    "app_config defaults",
)
text = replace_once(
    text,
    '        "cpa_mint_cookie_inject",\n',
    '        "cpa_mint_cookie_inject", "multi_thread_enabled",\n',
    "app_config bool validation",
)
text = replace_once(
    text,
    '    cfg["register_count"] = _require_int(cfg, "register_count", 1, 2500)\n',
    '    cfg["register_count"] = _require_int(cfg, "register_count", 1, 2500)\n    cfg["multi_thread_workers"] = _require_int(cfg, "multi_thread_workers", 1, 8)\n',
    "app_config worker validation",
)
write(path, text)


# 2) Example config.
path = "config.example.json"
text = read(path)
text = replace_once(
    text,
    '  "register_count": 1,\n',
    '  "register_count": 1,\n  "multi_thread_enabled": false,\n  "multi_thread_workers": 4,\n',
    "config example",
)
write(path, text)


# 3) Thread-safe shared append outputs. Keep pending recovery lock ordering safe.
path = "account_outputs.py"
text = read(path)
old = '''def append_account_line(path, email, password, sso):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{email}----{password}----{sso}\\n")
        handle.flush()
        os.fsync(handle.fileno())


def save_mail_credential(base_dir, email, credential):
    path = os.path.join(base_dir, "mail_credentials.txt")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{email}\\t{credential}\\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def queue_unsaved_account(path, payload, error):
    pending_path = path + ".pending.jsonl"
    record = dict(payload)
    record["save_error"] = str(error)
    record["queued_at"] = datetime.now(timezone.utc).isoformat()
    with open(pending_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(pending_path, 0o600)
    except Exception:
        pass
    return True
'''
new = '''def _append_account_line_unlocked(path, email, password, sso):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{email}----{password}----{sso}\\n")
        handle.flush()
        os.fsync(handle.fileno())


def append_account_line(path, email, password, sso):
    with FileLock(path + ".lock", timeout=30):
        _append_account_line_unlocked(path, email, password, sso)


def save_mail_credential(base_dir, email, credential):
    path = os.path.join(base_dir, "mail_credentials.txt")
    with FileLock(path + ".lock", timeout=30):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{email}\\t{credential}\\n")
            handle.flush()
            os.fsync(handle.fileno())
    return True


def queue_unsaved_account(path, payload, error):
    pending_path = path + ".pending.jsonl"
    record = dict(payload)
    record["save_error"] = str(error)
    record["queued_at"] = datetime.now(timezone.utc).isoformat()
    with FileLock(pending_path + ".lock", timeout=30):
        with open(pending_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(pending_path, 0o600)
        except Exception:
            pass
    return True
'''
text = replace_once(text, old, new, "account output append locks")
text = replace_once(
    text,
    '                    append_account_line(target_path, email, password, sso)\n',
    '                    _append_account_line_unlocked(target_path, email, password, sso)\n',
    "pending recovery avoids nested output lock",
)
write(path, text)


# 4) CPA shared failure log lock; successful credential writes are already atomic per file.
path = "cpa_export.py"
text = read(path)
text = replace_once(
    text,
    'from typing import Optional\n',
    'from typing import Optional\n\nfrom filelock import FileLock\n',
    "cpa filelock import",
)
old = '''        try:
            with open(str(fail_path), "a", encoding="utf-8") as handle:
                handle.write("%s----%s----%s\\n" % (email, result.get("error") or "unknown", int(time.time())))
        except Exception as exc:
'''
new = '''        try:
            with FileLock(str(fail_path) + ".lock", timeout=30):
                with open(str(fail_path), "a", encoding="utf-8") as handle:
                    handle.write("%s----%s----%s\\n" % (email, result.get("error") or "unknown", int(time.time())))
                    handle.flush()
                    os.fsync(handle.fileno())
        except Exception as exc:
'''
text = replace_once(text, old, new, "cpa failure log lock")
write(path, text)


# 5) Main adapter: preserve serial path verbatim unless explicitly enabled.
path = "grok_register_ttk.py"
text = read(path)
text = replace_once(
    text,
    'def maybe_export_cpa_xai_after_success(email, password, sso="", log_callback=None, cancel_callback=None):\n',
    'def maybe_export_cpa_xai_after_success(email, password, sso="", log_callback=None, cancel_callback=None, page_override=None):\n',
    "CPA page override signature",
)
old = '''    current_page = None
    try:
        current_page = _registration_browser.page
    except Exception:
        current_page = None
'''
new = '''    current_page = page_override
    if current_page is None:
        try:
            current_page = _registration_browser.page
        except Exception:
            current_page = None
'''
text = replace_once(text, old, new, "CPA page override behavior")

old = '''def run_registration_common(count, log_callback, cancel_callback, accounts_output_file, observer):
    from registration_flow import RegistrationCallbacks, RegistrationOperations, run_batch
    callbacks = RegistrationCallbacks(log=log_callback, cancelled=cancel_callback)
    operations = RegistrationOperations(
'''
new = '''def run_registration_common(count, log_callback, cancel_callback, accounts_output_file, observer):
    from registration_flow import RegistrationCallbacks, RegistrationOperations, run_batch
    callbacks = RegistrationCallbacks(log=log_callback, cancelled=cancel_callback)
    parallel_enabled = bool(config.get("multi_thread_enabled", False))
    parallel_workers = int(config.get("multi_thread_workers", 4) or 4)
    if parallel_enabled and parallel_workers > 1 and int(count) > 1:
        from registration_parallel import run_parallel_batch
        return run_parallel_batch(
            count=int(count),
            callbacks=callbacks,
            observer=observer,
            runtime_namespace=globals(),
            accounts_output_file=accounts_output_file,
            workers=parallel_workers,
            enable_nsfw=bool(config.get("enable_nsfw", True)),
            cleanup_interval=MEMORY_CLEANUP_INTERVAL,
            max_slot_retry=3,
            max_mail_retry=3,
        )
    operations = RegistrationOperations(
'''
text = replace_once(text, old, new, "parallel dispatch")

old = '''        add_label(13, 2, "CPA 输出目录:")
        self.cpa_auth_dir_var = tk.StringVar(value=str(config.get("cpa_auth_dir", "./cpa_auths")))
        self.cpa_auth_dir_entry = tk_entry(config_frame, textvariable=self.cpa_auth_dir_var, width=34)
        add_field(self.cpa_auth_dir_entry, 13, 3)

        btn_frame = tk.Frame(main_frame, bg=UI_BG)
'''
new = '''        add_label(13, 2, "CPA 输出目录:")
        self.cpa_auth_dir_var = tk.StringVar(value=str(config.get("cpa_auth_dir", "./cpa_auths")))
        self.cpa_auth_dir_entry = tk_entry(config_frame, textvariable=self.cpa_auth_dir_var, width=34)
        add_field(self.cpa_auth_dir_entry, 13, 3)

        add_label(14, 0, "并发注册:")
        self.multi_thread_var = tk.BooleanVar(value=bool(config.get("multi_thread_enabled", False)))
        self.multi_thread_check = tk_checkbutton(
            config_frame,
            text="启用多线程",
            variable=self.multi_thread_var,
            command=self._sync_multithread_controls,
        )
        add_field(self.multi_thread_check, 14, 1, sticky=tk.W)
        add_label(14, 2, "线程数:")
        self.multi_thread_workers_var = tk.StringVar(value=str(config.get("multi_thread_workers", 4)))
        self.multi_thread_workers_spinbox = tk.Spinbox(
            config_frame,
            from_=1,
            to=8,
            width=8,
            textvariable=self.multi_thread_workers_var,
            bg=UI_ENTRY_BG,
            fg=UI_FG,
            insertbackground=UI_FG,
            buttonbackground=UI_BUTTON_BG,
            disabledbackground="#2f2f2f",
            disabledforeground=UI_MUTED_FG,
            relief=tk.SOLID,
        )
        add_field(self.multi_thread_workers_spinbox, 14, 3, sticky=tk.W)
        self._sync_multithread_controls()

        btn_frame = tk.Frame(main_frame, bg=UI_BG)
'''
text = replace_once(text, old, new, "GUI multithread controls")

old = '''    def _reset_batch_counters(self):
        self.success_count = 0
        self.fail_count = 0
        self.registered_unsaved_count = 0
        self.postprocess_warning_count = 0

    def start_registration(self):
'''
new = '''    def _reset_batch_counters(self):
        self.success_count = 0
        self.fail_count = 0
        self.registered_unsaved_count = 0
        self.postprocess_warning_count = 0

    def _sync_multithread_controls(self):
        if not hasattr(self, "multi_thread_workers_spinbox"):
            return
        state = tk.NORMAL if bool(self.multi_thread_var.get()) else tk.DISABLED
        self.multi_thread_workers_spinbox.config(state=state)

    def start_registration(self):
'''
text = replace_once(text, old, new, "GUI multithread toggle method")

text = replace_once(
    text,
    '        config["cpa_auth_dir"] = self.cpa_auth_dir_var.get().strip() or "./cpa_auths"\n',
    '        config["cpa_auth_dir"] = self.cpa_auth_dir_var.get().strip() or "./cpa_auths"\n        config["multi_thread_enabled"] = bool(self.multi_thread_var.get())\n        config["multi_thread_workers"] = int(self.multi_thread_workers_var.get())\n',
    "GUI save multithread config",
)
text = replace_once(
    text,
    '        self.log(f"[*] 配置已保存，开始执行。目标数量: {count}")\n',
    '        self.log(f"[*] 配置已保存，开始执行。目标数量: {count}")\n        if config.get("multi_thread_enabled") and int(config.get("multi_thread_workers", 4)) > 1 and count > 1:\n            actual_workers = min(count, int(config.get("multi_thread_workers", 4)))\n            self.log(f"[*] 多线程注册已开启: 配置 {config.get(\'multi_thread_workers\')} | 实际 {actual_workers}")\n        else:\n            self.log("[*] 多线程注册关闭，使用原串行流程")\n',
    "GUI mode logging",
)
text = replace_once(
    text,
    '    cli_log(f"[*] 当前邮箱服务商: {config.get(\'email_provider\', \'duckmail\')} | 注册数量: {count}")\n',
    '    cli_log(f"[*] 当前邮箱服务商: {config.get(\'email_provider\', \'duckmail\')} | 注册数量: {count}")\n    if config.get("multi_thread_enabled") and int(config.get("multi_thread_workers", 4)) > 1 and count > 1:\n        cli_log(f"[*] 多线程注册: 开启 | 配置线程 {config.get(\'multi_thread_workers\')} | 实际线程 {min(count, int(config.get(\'multi_thread_workers\', 4)))}")\n    else:\n        cli_log("[*] 多线程注册: 关闭（串行）")\n',
    "CLI mode logging",
)
write(path, text)


# 6) New isolated worker coordinator. Existing registration_flow/browser/mail modules stay untouched.
parallel_module = r'''"""可选并发注册调度；每个 worker 使用独立的邮箱与注册浏览器模块实例。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import gc
import importlib.util
from pathlib import Path
import threading

from registration_flow import BatchResult, RegistrationCallbacks, RegistrationOperations, run_batch

_ROOT = Path(__file__).resolve().parent


def split_worker_counts(count, workers):
    total = max(int(count), 0)
    if total <= 0:
        return []
    actual = min(max(int(workers), 1), total)
    base, extra = divmod(total, actual)
    return [base + (1 if index < extra else 0) for index in range(actual)]


def load_isolated_module(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError("unable to load isolated module: %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summary_copy(batch):
    return {
        "success_count": int(batch.success_count),
        "fail_count": int(batch.fail_count),
        "processed_count": int(batch.processed_count),
        "registered_unsaved_count": int(batch.registered_unsaved_count),
        "postprocess_warning_count": int(batch.postprocess_warning_count),
        "cancelled": bool(batch.cancelled),
        "results": list(batch.results),
    }


def _aggregate(snapshots):
    total = BatchResult()
    for snapshot in snapshots.values():
        total.success_count += snapshot["success_count"]
        total.fail_count += snapshot["fail_count"]
        total.processed_count += snapshot["processed_count"]
        total.registered_unsaved_count += snapshot["registered_unsaved_count"]
        total.postprocess_warning_count += snapshot["postprocess_warning_count"]
        total.cancelled = total.cancelled or snapshot["cancelled"]
        total.results.extend(snapshot["results"])
    return total


def run_parallel_batch(count, callbacks, observer, runtime_namespace, accounts_output_file,
                       workers=4, enable_nsfw=True, cleanup_interval=5,
                       max_slot_retry=3, max_mail_retry=3):
    worker_counts = split_worker_counts(count, workers)
    if len(worker_counts) <= 1:
        raise ValueError("parallel batch requires at least two active workers")

    stats_lock = threading.Lock()
    log_lock = threading.Lock()
    io_lock = threading.Lock()
    abort_event = threading.Event()
    snapshots = {}
    fatal_errors = []

    def combined_cancelled():
        return abort_event.is_set() or callbacks.cancelled()

    callbacks.log(
        "[*] 多线程注册启动: %s 个 worker | 任务分配 %s"
        % (len(worker_counts), "/".join(str(value) for value in worker_counts))
    )

    def run_worker(worker_id, worker_count):
        def worker_log(message):
            with log_lock:
                callbacks.log("[T%s] %s" % (worker_id, message))

        mail_module = load_isolated_module(
            _ROOT / "mail_service.py",
            "_grok_mail_worker_%s_%s" % (worker_id, threading.get_ident()),
        )
        mail_module.bind_runtime(runtime_namespace)

        worker_namespace = dict(runtime_namespace)
        for name in getattr(mail_module, "_OWN_NAMES", set()):
            if hasattr(mail_module, name):
                worker_namespace[name] = getattr(mail_module, name)
        if hasattr(mail_module, "normalize_mail_body"):
            worker_namespace["normalize_mail_body"] = mail_module.normalize_mail_body

        browser_module = load_isolated_module(
            _ROOT / "registration_browser.py",
            "_grok_browser_worker_%s_%s" % (worker_id, threading.get_ident()),
        )
        browser_module.bind_runtime(worker_namespace)

        worker_callbacks = RegistrationCallbacks(log=worker_log, cancelled=combined_cancelled)

        def save_mail(email, token):
            with io_lock:
                return runtime_namespace["_save_mail_credential"](email, token, worker_log)

        def persist_line(email, password, sso):
            with io_lock:
                return runtime_namespace["_append_account_line"](
                    accounts_output_file, email, password, sso
                )

        def queue_unsaved(payload, error):
            with io_lock:
                return runtime_namespace["_queue_unsaved_account"](
                    accounts_output_file, payload, error, worker_log
                )

        def export_cpa(email, password, sso):
            return runtime_namespace["maybe_export_cpa_xai_after_success"](
                email=email,
                password=password,
                sso=sso,
                log_callback=worker_log,
                cancel_callback=combined_cancelled,
                page_override=browser_module.page,
            )

        def worker_cleanup(reason):
            worker_log("%s: 关闭本 worker 注册浏览器" % reason)
            browser_module.stop_browser()

        operations = RegistrationOperations(
            start_browser=lambda: browser_module.start_browser(log_callback=worker_log),
            restart_browser=lambda: browser_module.restart_browser(log_callback=worker_log),
            browser_missing=lambda: browser_module.browser is None,
            open_signup_page=lambda: browser_module.open_signup_page(
                log_callback=worker_log, cancel_callback=combined_cancelled
            ),
            fill_email_and_submit=lambda: browser_module.fill_email_and_submit(
                log_callback=worker_log, cancel_callback=combined_cancelled
            ),
            save_mail_credential=save_mail,
            fill_code_and_submit=lambda email, token: browser_module.fill_code_and_submit(
                email, token, log_callback=worker_log, cancel_callback=combined_cancelled
            ),
            fill_profile_and_submit=lambda: browser_module.fill_profile_and_submit(
                log_callback=worker_log, cancel_callback=combined_cancelled
            ),
            wait_for_sso_cookie=lambda: browser_module.wait_for_sso_cookie(
                log_callback=worker_log, cancel_callback=combined_cancelled
            ),
            enable_nsfw=lambda sso: browser_module.enable_nsfw_for_token(
                sso, log_callback=worker_log
            ),
            persist_account_line=persist_line,
            queue_unsaved_result=queue_unsaved,
            add_tokens=lambda sso, email: runtime_namespace["add_token_to_grok2api_pools"](
                sso, email=email, log_callback=worker_log
            ),
            export_cpa=export_cpa,
            cleanup=worker_cleanup,
            sleep=lambda seconds: runtime_namespace["sleep_with_cancel"](
                seconds, combined_cancelled
            ),
            cancelled_exception=runtime_namespace["RegistrationCancelled"],
            retry_exception=runtime_namespace["AccountRetryNeeded"],
        )

        def worker_observer(batch, account, output):
            with stats_lock:
                snapshots[worker_id] = _summary_copy(batch)
                total = _aggregate(snapshots)
            observer(total, account, output)

        try:
            batch = run_batch(
                count=worker_count,
                callbacks=worker_callbacks,
                observer=worker_observer,
                ops=operations,
                enable_nsfw=bool(enable_nsfw),
                cleanup_interval=int(cleanup_interval),
                max_slot_retry=int(max_slot_retry),
                max_mail_retry=int(max_mail_retry),
            )
            with stats_lock:
                snapshots[worker_id] = _summary_copy(batch)
            return batch
        finally:
            try:
                browser_module.stop_browser()
            except Exception as exc:
                worker_log("[Debug] worker 浏览器最终清理失败: %s" % exc)

    try:
        with ThreadPoolExecutor(
            max_workers=len(worker_counts),
            thread_name_prefix="grok-register",
        ) as executor:
            future_map = {
                executor.submit(run_worker, worker_id, worker_count): worker_id
                for worker_id, worker_count in enumerate(worker_counts, 1)
            }
            for future in as_completed(future_map):
                worker_id = future_map[future]
                try:
                    future.result()
                except Exception as exc:
                    fatal_errors.append((worker_id, exc))
                    abort_event.set()
                    callbacks.log("[!] T%s worker 级异常，正在停止其他 worker: %s" % (worker_id, exc))
    finally:
        try:
            from cpa_xai.browser_confirm import shutdown_mint_browsers
            shutdown_mint_browsers()
        except Exception as exc:
            callbacks.log("[Debug] 并发任务 CPA 浏览器统一清理失败: %s" % exc)
        collected = gc.collect()
        callbacks.log("[*] 并发任务统一清理完成，Python GC 已回收对象数: %s" % collected)

    if fatal_errors:
        worker_id, exc = fatal_errors[0]
        raise RuntimeError("parallel worker T%s failed: %s" % (worker_id, exc)) from exc

    with stats_lock:
        total = _aggregate(snapshots)
    total.cancelled = total.cancelled or callbacks.cancelled()
    return total
'''
write("registration_parallel.py", parallel_module)


# 7) Focused tests: defaults, distribution, isolation primitive, append integrity.
tests = r'''import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import app_config
import account_outputs
from registration_parallel import load_isolated_module, split_worker_counts


class OptionalMultithreadTests(unittest.TestCase):
    def test_defaults_keep_parallel_disabled_with_four_workers_configured(self):
        cfg = app_config.validate_config_structure({})
        self.assertFalse(cfg["multi_thread_enabled"])
        self.assertEqual(cfg["multi_thread_workers"], 4)

    def test_worker_count_validation(self):
        for workers in (1, 4, 8):
            cfg = app_config.validate_config_structure({"multi_thread_workers": workers})
            self.assertEqual(cfg["multi_thread_workers"], workers)
        for workers in (0, 9):
            with self.assertRaises(app_config.ConfigError):
                app_config.validate_config_structure({"multi_thread_workers": workers})

    def test_static_distribution_never_exceeds_target(self):
        self.assertEqual(split_worker_counts(10, 4), [3, 3, 2, 2])
        self.assertEqual(split_worker_counts(2, 4), [1, 1])
        self.assertEqual(split_worker_counts(1, 4), [1])
        self.assertEqual(sum(split_worker_counts(37, 8)), 37)

    def test_isolated_module_instances_do_not_share_globals(self):
        with tempfile.TemporaryDirectory() as directory:
            module_path = Path(directory) / "sample_runtime.py"
            module_path.write_text("value = None\\n", encoding="utf-8")
            first = load_isolated_module(module_path, "_parallel_test_first")
            second = load_isolated_module(module_path, "_parallel_test_second")
            first.value = "worker-1"
            second.value = "worker-2"
            self.assertEqual(first.value, "worker-1")
            self.assertEqual(second.value, "worker-2")

    def test_concurrent_account_appends_remain_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "accounts.txt")
            def write_one(index):
                account_outputs.append_account_line(
                    output,
                    "user%s@example.com" % index,
                    "password-%s" % index,
                    "sso-%s" % index,
                )
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(write_one, range(40)))
            lines = Path(output).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 40)
            self.assertEqual(len(set(lines)), 40)
            for line in lines:
                self.assertEqual(len(line.split("----", 2)), 3)

    def test_concurrent_pending_appends_are_valid_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "accounts.txt")
            def write_one(index):
                account_outputs.queue_unsaved_account(
                    output,
                    {"email": "user%s@example.com" % index, "password": "p", "sso": "sso-%s" % index},
                    "test",
                )
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(write_one, range(30)))
            pending = Path(output + ".pending.jsonl")
            rows = [json.loads(line) for line in pending.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 30)
            self.assertEqual(len({row["email"] for row in rows}), 30)


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_optional_multithread.py", tests)


# 8) README: concise behavior/safety boundary; do not rewrite existing docs.
path = "README.md"
text = read(path)
section = r'''

## 可选多线程注册（实验）

多线程默认**关闭**，因此升级后未修改配置的用户仍走原有串行注册流程。需要研究并发行为时可配置：

```json
{
  "multi_thread_enabled": true,
  "multi_thread_workers": 4
}
```

- `multi_thread_enabled`: 是否启用并发注册，默认 `false`。
- `multi_thread_workers`: 配置线程数，默认 `4`，允许 `1`–`8`；实际 worker 数不会超过本次注册数量。
- GUI 可通过“启用多线程”开关和线程数输入框设置；CLI 继续读取同一份 `config.json`。
- 并发模式为每个 worker 创建独立的邮箱模块实例与注册浏览器模块实例，避免共享 `browser/page` 状态；单账号注册、重试、grok2api、CPA 和结果统计仍复用原有逻辑。
- 账号、邮箱凭据、pending 和 CPA 失败日志的共享追加写入使用文件锁保护；CPA 浏览器在所有 worker 结束后统一清理。

该选项用于学习与并发行为研究。若未明确需要并发，请保持关闭以获得与旧版本一致的执行路径。
'''
if "## 可选多线程注册（实验）" in text:
    raise RuntimeError("README parallel section already exists")
text = text.rstrip() + section + "\n"
write(path, text)


# Remove one-shot scaffolding from final change set.
for relative in (
    "tools/apply_parallel_upgrade.py",
    ".github/workflows/apply-parallel-upgrade.yml",
):
    target = ROOT / relative
    if target.exists():
        target.unlink()

print("optional multithread upgrade applied")
