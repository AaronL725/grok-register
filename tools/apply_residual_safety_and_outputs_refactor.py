#!/usr/bin/env python3
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


def replace_between(text, start, end, replacement, label):
    i = text.find(start)
    if i < 0:
        raise RuntimeError(f"{label}: start not found")
    j = text.find(end, i)
    if j < 0:
        raise RuntimeError(f"{label}: end not found")
    return text[:i] + replacement + text[j:]


# 1) Harden the shared orchestration boundaries without changing registration steps.
flow_path = ROOT / "registration_flow.py"
flow = flow_path.read_text(encoding="utf-8")
new_persist = r'''def persist_account_result(result, callbacks, ops):
    try:
        ops.persist_account_line(result.email, result.password, result.sso)
        saved = True
        save_error = ""
        pending_saved = False
    except Exception as exc:
        saved = False
        save_error = str(exc)
        try:
            pending_saved = bool(
                ops.queue_unsaved_result(
                    {
                        "email": result.email,
                        "password": result.password,
                        "sso": result.sso,
                        "profile": result.profile,
                    },
                    save_error,
                )
            )
        except Exception as pending_exc:
            pending_saved = False
            callbacks.log(f"[!] pending 队列写入异常: {pending_exc}")
        callbacks.log(f"[!] 账号已注册但主结果文件保存失败: {save_error}")
        if pending_saved:
            callbacks.log("[!] 未保存账号已写入 pending 队列，等待人工重试")
        else:
            callbacks.log("[!] pending 队列也写入失败，请立即复制当前账号信息")

    try:
        pools = ops.add_tokens(result.sso, result.email)
        if not isinstance(pools, dict):
            raise TypeError("token pool result must be a dict")
    except Exception as exc:
        callbacks.log(f"[!] token 入池后处理异常，账号结果已保留: {exc}")
        pools = {
            "internal": {
                "enabled": True,
                "ok": False,
                "error": str(exc),
            }
        }
    for name, state in pools.items():
        if isinstance(state, dict) and state.get("enabled") and not state.get("ok"):
            callbacks.log(f"[!] grok2api {name} 入池失败: {state.get('error')}")

    try:
        cpa = ops.export_cpa(result.email, result.password, result.sso)
        if not isinstance(cpa, dict):
            raise TypeError("CPA result must be a dict")
    except Exception as exc:
        callbacks.log(f"[!] CPA 导出后处理异常，账号结果已保留: {exc}")
        cpa = {"ok": False, "skipped": False, "error": str(exc)}

    return OutputResult(
        registered=True,
        saved=saved,
        pending_saved=pending_saved,
        save_error=save_error,
        pools=pools,
        cpa=cpa,
    )


'''
flow = replace_between(flow, "def persist_account_result(", "def _notify_observer", new_persist, "persist_account_result")
new_tail = r'''def _notify_observer(observer, result, account, output, callbacks):
    try:
        observer(result, account, output)
    except Exception as exc:
        callbacks.log(f"[Debug] observer 执行失败: {exc}")


def _run_cleanup_safely(ops, callbacks, reason):
    try:
        ops.cleanup(reason)
        return True
    except Exception as exc:
        callbacks.log(f"[!] 清理失败，已忽略且不影响账号统计: {reason}: {exc}")
        return False


def _prepare_next_account(result, settings, callbacks, ops):
    if result.processed_count >= settings.count:
        return False
    if callbacks.cancelled():
        result.cancelled = True
        return False
    try:
        if ops.browser_missing():
            ops.start_browser()
        else:
            ops.restart_browser()
        ops.sleep(1)
        return True
    except ops.cancelled_exception:
        result.cancelled = True
        callbacks.log("[!] 已在账号间准备阶段停止")
        return False


def run_batch(count, callbacks, observer, ops, enable_nsfw=True, cleanup_interval=5,
              max_slot_retry=3, max_mail_retry=3, settings=None):
    if settings is None:
        settings = RegistrationSettings(
            count=int(count),
            enable_nsfw=bool(enable_nsfw),
            cleanup_interval=int(cleanup_interval),
            max_slot_retry=int(max_slot_retry),
            max_mail_retry=int(max_mail_retry),
        )
    result = BatchResult()
    retry_count_for_slot = 0
    last_cleanup_success_count = 0
    try:
        ops.start_browser()
        callbacks.log("[*] 浏览器已启动")
        while result.processed_count < settings.count:
            if callbacks.cancelled():
                result.cancelled = True
                break
            callbacks.log(f"--- 开始第 {result.processed_count + 1}/{settings.count} 个账号 ---")
            account = None
            output = None
            continue_batch = True
            try:
                account = register_one_account(
                    callbacks,
                    ops,
                    enable_nsfw=settings.enable_nsfw,
                    max_mail_retry=settings.max_mail_retry,
                )
                output = persist_account_result(account, callbacks, ops)
                result.results.append({"registration": account, "output": output})
                retry_count_for_slot = 0
                result.processed_count += 1
                if output.saved:
                    result.success_count += 1
                    callbacks.log(f"[+] 注册并保存成功: {account.email}")
                    if (
                        settings.cleanup_interval > 0
                        and result.success_count % settings.cleanup_interval == 0
                        and result.success_count != last_cleanup_success_count
                        and result.processed_count < settings.count
                    ):
                        _run_cleanup_safely(
                            ops,
                            callbacks,
                            f"已成功 {result.success_count} 个账号，执行定期清理",
                        )
                        last_cleanup_success_count = result.success_count
                else:
                    result.fail_count += 1
                    result.registered_unsaved_count += 1
                    callbacks.log(f"[-] 注册成功但持久化未完成: {account.email}")
                pool_warning = any(
                    isinstance(state, dict) and state.get("enabled") and not state.get("ok")
                    for state in output.pools.values()
                )
                cpa_warning = bool(output.cpa and not output.cpa.get("ok") and not output.cpa.get("skipped"))
                if pool_warning or cpa_warning:
                    result.postprocess_warning_count += 1
            except ops.cancelled_exception:
                result.cancelled = True
                callbacks.log("[!] 注册被停止")
                continue_batch = False
            except ops.retry_exception as exc:
                retry_count_for_slot += 1
                if retry_count_for_slot <= settings.max_slot_retry:
                    callbacks.log(
                        f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/{settings.max_slot_retry} 次: {exc}"
                    )
                else:
                    result.fail_count += 1
                    result.processed_count += 1
                    retry_count_for_slot = 0
                    callbacks.log(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
            except Exception as exc:
                result.fail_count += 1
                result.processed_count += 1
                retry_count_for_slot = 0
                callbacks.log(f"[-] 注册失败: {exc}")
            finally:
                _notify_observer(observer, result, account, output, callbacks)

            if not continue_batch or result.cancelled:
                break
            if not _prepare_next_account(result, settings, callbacks, ops):
                break
    finally:
        _run_cleanup_safely(ops, callbacks, "任务结束")
    return result
'''
flow = replace_between(flow, "def _notify_observer", "    return result\n", new_tail, "flow tail")
# Remove a possible duplicate return left by the marker replacement.
flow = flow.replace("\n    return result\n    return result\n", "\n    return result\n")
ast.parse(flow)
flow_path.write_text(flow, encoding="utf-8")


# 2) Move account file and pending recovery operations to a focused module.
outputs = r'''"""Account result persistence and pending recovery helpers."""
import json
import os
import tempfile
from datetime import datetime, timezone

from filelock import FileLock


def append_account_line(path, email, password, sso):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{email}----{password}----{sso}\n")
        handle.flush()
        os.fsync(handle.fileno())


def save_mail_credential(base_dir, email, credential):
    path = os.path.join(base_dir, "mail_credentials.txt")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{email}\t{credential}\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def queue_unsaved_account(path, payload, error):
    pending_path = path + ".pending.jsonl"
    record = dict(payload)
    record["save_error"] = str(error)
    record["queued_at"] = datetime.now(timezone.utc).isoformat()
    with open(pending_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(pending_path, 0o600)
    except Exception:
        pass
    return True


def _existing_account_keys(target_path):
    keys = set()
    if not os.path.isfile(target_path):
        return keys
    with open(target_path, "r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            parts = raw_line.rstrip("\n").split("----", 2)
            if len(parts) == 3:
                keys.add((parts[0].strip(), parts[2].strip()))
    return keys


def retry_pending_file(pending_path, output_path=None, log_callback=None):
    logger = log_callback or (lambda message: None)
    pending_path = os.path.realpath(os.path.abspath(os.path.expanduser(str(pending_path))))
    if not os.path.isfile(pending_path):
        raise FileNotFoundError(f"pending 文件不存在: {pending_path}")
    suffix = ".pending.jsonl"
    if output_path:
        target_path = os.path.realpath(os.path.abspath(os.path.expanduser(str(output_path))))
    elif pending_path.endswith(suffix):
        target_path = os.path.realpath(pending_path[:-len(suffix)])
    else:
        target_path = os.path.realpath(pending_path + ".recovered.txt")
    if os.path.normcase(pending_path) == os.path.normcase(target_path):
        raise ValueError("pending 输入文件与输出文件不能是同一个文件")

    lock_path = pending_path + ".lock"
    with FileLock(lock_path, timeout=30):
        if not os.path.isfile(pending_path):
            return {"restored": 0, "remaining": 0, "output_path": target_path}
        with open(pending_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
        existing = _existing_account_keys(target_path)
        unresolved = []
        restored = 0
        for line_number, raw_line in enumerate(lines, 1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
                if not isinstance(record, dict):
                    raise ValueError("record must be a JSON object")
                email = str(record.get("email") or "").strip()
                password = str(record.get("password") or "")
                sso = str(record.get("sso") or "").strip()
                if not email or not sso:
                    raise ValueError("record missing email or sso")
                key = (email, sso)
                if key not in existing:
                    append_account_line(target_path, email, password, sso)
                    existing.add(key)
                restored += 1
                logger(f"[+] 已恢复 pending 账号: {email}")
            except Exception as exc:
                unresolved.append(raw_line if raw_line.endswith("\n") else raw_line + "\n")
                logger(f"[!] pending 第 {line_number} 行恢复失败: {exc}")

        directory = os.path.dirname(pending_path) or "."
        fd, temp_path = tempfile.mkstemp(prefix=".pending-retry-", suffix=".jsonl.tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.writelines(unresolved)
                handle.flush()
                os.fsync(handle.fileno())
            if unresolved:
                os.replace(temp_path, pending_path)
                temp_path = None
                try:
                    os.chmod(pending_path, 0o600)
                except Exception:
                    pass
            else:
                os.unlink(temp_path)
                temp_path = None
                try:
                    os.unlink(pending_path)
                except FileNotFoundError:
                    pass
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
        return {"restored": restored, "remaining": len(unresolved), "output_path": target_path}
'''
outputs_path = ROOT / "account_outputs.py"
ast.parse(outputs)
outputs_path.write_text(outputs, encoding="utf-8")


# 3) Split structural and runtime config validation; keep existing public validate_config.
main_path = ROOT / "grok_register_ttk.py"
main = main_path.read_text(encoding="utf-8")
main = replace_once(main, "def validate_config(raw):", "def validate_config_structure(raw):", "rename structural validation")
runtime_block = r'''def validate_run_requirements(cfg):
    cfg = validate_config_structure(cfg)
    provider = cfg["email_provider"]
    if provider == "cloudflare" and not cfg["cloudflare_api_base"]:
        raise ConfigError("Cloudflare 模式需要配置 cloudflare_api_base")
    if provider == "cloudmail":
        missing = [
            key for key in ("cloudmail_api_base", "cloudmail_public_token", "cloudmail_domains")
            if not cfg[key]
        ]
        if missing:
            raise ConfigError("Cloud Mail 模式缺少必需配置: " + ", ".join(missing))
    if provider == "yyds" and not (cfg["yyds_api_key"] or cfg["yyds_jwt"]):
        raise ConfigError("YYDS 模式需要至少配置 yyds_api_key 或 yyds_jwt")
    if cfg["grok2api_auto_add_remote"]:
        missing = [
            key for key in ("grok2api_remote_base", "grok2api_remote_app_key")
            if not cfg[key]
        ]
        if missing:
            raise ConfigError("远端 token 入池缺少必需配置: " + ", ".join(missing))
    if cfg["cpa_copy_to_hotload"] and not cfg["cpa_hotload_dir"]:
        raise ConfigError("启用 CPA 热加载复制时必须配置 cpa_hotload_dir")
    return cfg


def validate_config(raw):
    """Backward-compatible full validation used before a run or save."""
    return validate_run_requirements(raw)


'''
# Remove runtime dependency checks from structural validator.
old_runtime = '''    provider = cfg["email_provider"]
    if provider == "cloudflare" and not cfg["cloudflare_api_base"]:
        raise ConfigError("Cloudflare 模式需要配置 cloudflare_api_base")
    if provider == "cloudmail":
        missing = [
            key for key in ("cloudmail_api_base", "cloudmail_public_token", "cloudmail_domains")
            if not cfg[key]
        ]
        if missing:
            raise ConfigError("Cloud Mail 模式缺少必需配置: " + ", ".join(missing))
    if cfg["grok2api_auto_add_remote"]:
        missing = [
            key for key in ("grok2api_remote_base", "grok2api_remote_app_key")
            if not cfg[key]
        ]
        if missing:
            raise ConfigError("远端 token 入池缺少必需配置: " + ", ".join(missing))
    if cfg["cpa_copy_to_hotload"] and not cfg["cpa_hotload_dir"]:
        raise ConfigError("启用 CPA 热加载复制时必须配置 cpa_hotload_dir")

'''
main = replace_once(main, old_runtime, "", "remove runtime checks from structure")
main = replace_once(main, "    return cfg\n\n\ndef load_config():", "    return cfg\n\n\n" + runtime_block + "def load_config():", "insert runtime validator")
main = main.replace("config = validate_config(loaded)", "config = validate_config_structure(loaded)", 1)
main = main.replace("config = validate_config(DEFAULT_CONFIG.copy())", "config = validate_config_structure(DEFAULT_CONFIG.copy())", 1)
# Save permits incomplete editable GUI config but still enforces structure.
main = main.replace("config = validate_config(config)\n    config_dir", "config = validate_config_structure(config)\n    config_dir", 1)

# Replace local output implementations with compatibility wrappers.
start = main.find("def _save_mail_credential(")
end = main.find("def run_registration_common(", start)
if start < 0 or end < 0:
    raise RuntimeError("account output helper block not found")
wrappers = r'''def _save_mail_credential(email, credential, log_callback=None):
    from account_outputs import save_mail_credential
    try:
        return save_mail_credential(os.path.dirname(__file__), email, credential)
    except Exception as exc:
        log_exception("保存邮箱凭据失败", exc, log_callback)
        return False


def _append_account_line(path, email, password, sso):
    from account_outputs import append_account_line
    return append_account_line(path, email, password, sso)


def _queue_unsaved_account(path, payload, error, log_callback=None):
    from account_outputs import queue_unsaved_account
    try:
        return queue_unsaved_account(path, payload, error)
    except Exception as exc:
        log_exception("写入账号 pending 队列失败", exc, log_callback)
        return False


def retry_pending_file(pending_path, output_path=None, log_callback=None):
    from account_outputs import retry_pending_file as _retry_pending_file
    return _retry_pending_file(pending_path, output_path=output_path, log_callback=log_callback)


'''
main = main[:start] + wrappers + main[end:]

# GUI stats include all batch states.
main = main.replace('self.stats_var = tk.StringVar(value="成功: 0 | 失败: 0")', 'self.stats_var = tk.StringVar(value="成功: 0 | 失败: 0 | 待恢复: 0 | 后处理警告: 0")', 1)
main = main.replace('self.stats_var.set(f"成功: {event[1]} | 失败: {event[2]}")', 'self.stats_var.set(f"成功: {event[1]} | 失败: {event[2]} | 待恢复: {event[3]} | 后处理警告: {event[4]}")', 1)
main = main.replace('self.ui_queue.put(("stats", self.success_count, self.fail_count))', 'self.ui_queue.put(("stats", self.success_count, self.fail_count, self.registered_unsaved_count, self.postprocess_warning_count))', 1)
main = main.replace('        self.fail_count = 0\n        self.results = []', '        self.fail_count = 0\n        self.registered_unsaved_count = 0\n        self.postprocess_warning_count = 0\n        self.results = []', 1)
# Initial GUI object counters.
main = main.replace('        self.fail_count = 0\n        self.results = []\n        self.stop_requested', '        self.fail_count = 0\n        self.registered_unsaved_count = 0\n        self.postprocess_warning_count = 0\n        self.results = []\n        self.stop_requested', 1)

# Replace start_registration validation/save section to gather all values, validate once, save once.
old_start_fragment = '''        try:
            save_config()
        except ConfigError as exc:
            self.log(f"[!] 配置保存失败: {exc}")
            return
        if config["email_provider"] == "cloudflare" and not config["cloudflare_api_base"]:
            self.log("[!] Cloudflare 模式需要先填写 Cloudflare API Base")
            return
        if config["email_provider"] == "cloudmail":
            missing = []
            if not config["cloudmail_api_base"]:
                missing.append("API Base")
            if not config["cloudmail_public_token"]:
                missing.append("Public Token")
            if not config["cloudmail_domains"]:
                missing.append("域名")
            if missing:
                self.log(f"[!] Cloud Mail 模式缺少配置: {', '.join(missing)}")
                return
        try:
            count = int(self.count_var.get())
        except Exception:
            self.log("[!] 注册数量无效")
            return
        config["register_count"] = count
        try:
            save_config()
        except ConfigError as exc:
            self.log(f"[!] 配置保存失败: {exc}")
            return
'''
new_start_fragment = '''        try:
            count = int(self.count_var.get())
            config["register_count"] = count
            validated = validate_run_requirements(config)
            config.clear()
            config.update(validated)
            save_config()
        except (ValueError, ConfigError) as exc:
            self.log(f"[!] 配置无效或保存失败: {exc}")
            return
'''
main = replace_once(main, old_start_fragment, new_start_fragment, "GUI single validation/save")

# Observer and final GUI state expose new counters.
main = main.replace('            self.fail_count = batch.fail_count\n            if account is not None:', '            self.fail_count = batch.fail_count\n            self.registered_unsaved_count = batch.registered_unsaved_count\n            self.postprocess_warning_count = batch.postprocess_warning_count\n            if account is not None:', 1)
main = main.replace('            self.fail_count = batch.fail_count\n        except Exception as exc:', '            self.fail_count = batch.fail_count\n            self.registered_unsaved_count = batch.registered_unsaved_count\n            self.postprocess_warning_count = batch.postprocess_warning_count\n            self.update_stats()\n        except Exception as exc:', 1)

# CLI stats include all states.
main = main.replace('last_stats = {"success": 0, "fail": 0}', 'last_stats = {"success": 0, "fail": 0, "pending": 0, "warnings": 0}', 1)
main = main.replace('        last_stats["fail"] = batch.fail_count\n        cli_log(f"[*] 当前统计: 成功 {batch.success_count} | 失败 {batch.fail_count}")', '        last_stats["fail"] = batch.fail_count\n        last_stats["pending"] = batch.registered_unsaved_count\n        last_stats["warnings"] = batch.postprocess_warning_count\n        cli_log(f"[*] 当前统计: 成功 {batch.success_count} | 失败 {batch.fail_count} | 待恢复 {batch.registered_unsaved_count} | 后处理警告 {batch.postprocess_warning_count}")', 1)
main = main.replace('        last_stats["fail"] = batch.fail_count\n    except KeyboardInterrupt:', '        last_stats["fail"] = batch.fail_count\n        last_stats["pending"] = batch.registered_unsaved_count\n        last_stats["warnings"] = batch.postprocess_warning_count\n    except KeyboardInterrupt:', 1)
main = main.replace("cli_log(f\"[*] 任务结束。成功 {last_stats['success']} | 失败 {last_stats['fail']}\")", "cli_log(f\"[*] 任务结束。成功 {last_stats['success']} | 失败 {last_stats['fail']} | 待恢复 {last_stats['pending']} | 后处理警告 {last_stats['warnings']}\")", 1)
# CLI performs runtime validation after structural loading.
main = main.replace('    count = int(config.get("register_count", 1) or 1)\n    cli_log("[*] CLI 已加载配置")', '    try:\n        validated = validate_run_requirements(config)\n        config.clear()\n        config.update(validated)\n    except ConfigError as exc:\n        cli_log(f"[!] {exc}")\n        return\n    count = int(config.get("register_count", 1) or 1)\n    cli_log("[*] CLI 已加载配置")', 1)

ast.parse(main)
main_path.write_text(main, encoding="utf-8")


# 4) Focused regression tests for each new safety boundary.
flow_tests_path = ROOT / "tests" / "test_registration_flow.py"
flow_tests = flow_tests_path.read_text(encoding="utf-8")
insert_tests = r'''
    def test_cleanup_failure_does_not_change_success_statistics(self):
        fake = FakeOps()
        ops = fake.operations()
        def cleanup(reason):
            if "已成功" in reason:
                raise RuntimeError("cleanup failed")
            fake.events.append(("cleanup", reason))
        ops.cleanup = cleanup
        batch = run_batch(2, self.callbacks(), lambda *args: None, ops, cleanup_interval=1)
        self.assertEqual(batch.success_count, 2)
        self.assertEqual(batch.fail_count, 0)
        self.assertEqual(batch.processed_count, 2)

    def test_cancel_during_between_account_sleep_ends_normally(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.sleep = lambda seconds: (_ for _ in ()).throw(Cancelled())
        batch = run_batch(2, self.callbacks(), lambda *args: None, ops)
        self.assertTrue(batch.cancelled)
        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.processed_count, 1)

    def test_final_cleanup_failure_does_not_hide_original_error(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.start_browser = lambda: (_ for _ in ()).throw(RuntimeError("original start error"))
        ops.cleanup = lambda reason: (_ for _ in ()).throw(RuntimeError("cleanup error"))
        logs = []
        with self.assertRaisesRegex(RuntimeError, "original start error"):
            run_batch(1, self.callbacks(logs), lambda *args: None, ops)
        self.assertTrue(any("清理失败" in line for line in logs))

    def test_postprocessing_exceptions_become_warnings(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.add_tokens = lambda sso, email: (_ for _ in ()).throw(RuntimeError("pool down"))
        ops.export_cpa = lambda email, password, sso: (_ for _ in ()).throw(RuntimeError("cpa down"))
        batch = run_batch(1, self.callbacks(), lambda *args: None, ops)
        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.fail_count, 0)
        self.assertEqual(batch.postprocess_warning_count, 1)
'''
flow_tests = flow_tests.replace('\n\nif __name__ == "__main__":', insert_tests + '\n\nif __name__ == "__main__":')
ast.parse(flow_tests)
flow_tests_path.write_text(flow_tests, encoding="utf-8")

pending_tests = r'''import json
import os
import tempfile
import unittest

from account_outputs import retry_pending_file


class PendingRecoveryTests(unittest.TestCase):
    def test_retry_is_idempotent_after_target_was_already_written(self):
        with tempfile.TemporaryDirectory() as directory:
            pending = os.path.join(directory, "accounts.txt.pending.jsonl")
            target = os.path.join(directory, "accounts.txt")
            record = {"email": "a@example.com", "password": "pw", "sso": "token"}
            with open(pending, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("a@example.com----pw----token\n")
            summary = retry_pending_file(pending)
            self.assertEqual(summary["restored"], 1)
            with open(target, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.readlines(), ["a@example.com----pw----token\n"])
            self.assertFalse(os.path.exists(pending))

    def test_rejects_same_input_and_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            pending = os.path.join(directory, "pending.jsonl")
            with open(pending, "w", encoding="utf-8") as handle:
                handle.write("{}\n")
            with self.assertRaises(ValueError):
                retry_pending_file(pending, output_path=pending)


if __name__ == "__main__":
    unittest.main()
'''
pending_path = ROOT / "tests" / "test_pending_recovery.py"
ast.parse(pending_tests)
pending_path.write_text(pending_tests, encoding="utf-8")

for path in (flow_path, outputs_path, main_path, flow_tests_path, pending_path):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print("residual safety and account outputs refactor applied")
