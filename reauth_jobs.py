"""Sequential reauthentication job coordinator and authorization recovery."""

import os
from pathlib import Path
import threading

from account_outputs import replace_account_session
from reauthentication import LoginCancelled
from storage.account_service import classify_integration_status
from proxy_pool import (
    ProxyAcquireCancelled,
    begin_registration_slot,
    current_proxy_lease,
    end_registration_slot,
    is_proxy_transport_exception,
)


def browser_login(email, password, cancelled, log):
    """Run a normal login in a browser dedicated to this account."""
    import grok_register_ttk as engine
    from reauthentication import login_with_password

    try:
        _browser, page = engine.start_browser(log_callback=log)
        return login_with_password(
            email, password, page, cancelled=cancelled, log=log
        )
    finally:
        engine.stop_browser()


def refresh_integrations(email, password, sso, cancelled, log):
    """Refresh downstream authorization artifacts after a successful login."""
    import grok_register_ttk as engine

    pools = engine.add_token_to_grok2api_pools(sso, email=email, log_callback=log)
    cpa = engine.maybe_export_cpa_xai_after_success(
        email, password, sso, log_callback=log, cancel_callback=cancelled
    )
    return classify_integration_status(pools, cpa, password, sso)


class ReauthManager:
    def __init__(self, database, login, postprocess, output_dir, log=lambda _text: None, on_finish=None):
        self.database = database
        self.login = login
        self.postprocess = postprocess
        self.output_dir = Path(output_dir)
        self.log = log
        self.on_finish = on_finish
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._job_id = None

    def start(self, account_ids):
        ids = list(dict.fromkeys(int(item) for item in account_ids))
        if not ids or any(item < 1 for item in ids):
            raise ValueError("请选择有效的账号")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("已有重新登录任务正在运行")
            accounts = [self.database.get_account(item) for item in ids]
            if any(record is None for record in accounts):
                raise LookupError("所选账号存在已删除的记录")
            job_id = self.database.create_job("reauthentication", accounts)
            self._stop.clear()
            self._job_id = job_id
            thread = threading.Thread(
                target=self._run, args=(job_id, accounts),
                name="grok-register-reauthentication", daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except Exception:
                self.database.finish_job(job_id, "failed", error_message="ThreadStartError")
                self._thread = None
                raise
            return job_id

    def status(self):
        with self._lock:
            job_id = self._job_id
            running = self._thread is not None and self._thread.is_alive()
        if job_id is None:
            return {"running": False, "job": None}
        return {"running": running, "job": self.database.get_job(job_id)}

    def stop(self):
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            if running:
                self._stop.set()
            return running

    def _restore_legacy_exports(self, account, old_sso, new_sso):
        paths = {
            item["path"] for item in account["artifacts"]
            if item["artifact_type"] == "legacy_txt"
        }
        if not paths:
            paths = {str(self.output_dir / "accounts_reauthenticated.txt")}

        errors = []
        for path in sorted(paths):
            try:
                replace_account_session(
                    path, account["email"], account["password"], old_sso, new_sso
                )
                self.database.add_artifact(account["id"], "legacy_txt", os.path.realpath(path), "txt")
            except Exception as exc:
                errors.append("TXT 同步失败: " + type(exc).__name__)
        return errors

    def _run(self, job_id, accounts):
        try:
            for account in accounts:
                account_id = account["id"]
                if self._stop.is_set():
                    self.database.update_job_item(job_id, account_id, "skipped")
                    continue
                if not account["password"]:
                    self.database.update_job_item(job_id, account_id, "failed", "没有保存密码")
                    continue

                self.database.update_job_item(job_id, account_id, "running")
                lease_started = False
                login_succeeded = False
                transport_error = None
                try:
                    lease_started = True
                    try:
                        begin_registration_slot(
                            slot_index=account_id,
                            attempt_index=1,
                            worker_key=threading.current_thread().name,
                            log=self.log,
                            cancel_callback=self._stop.is_set,
                        )
                    except ProxyAcquireCancelled as exc:
                        raise LoginCancelled("重新登录已停止") from exc

                    new_sso = self.login(
                        account["email"], account["password"],
                        cancelled=self._stop.is_set, log=self.log,
                    )
                    if self._stop.is_set():
                        raise LoginCancelled("重新登录已停止")
                    login_succeeded = True
                    previous = self.database.update_account_session(account_id, new_sso)
                    warnings = self._restore_legacy_exports(account, previous["old_sso"], new_sso)
                    try:
                        integrations = self.postprocess(
                            account["email"], account["password"], new_sso,
                            cancelled=self._stop.is_set, log=self.log,
                        )
                        self.database.update_account_integration_status(
                            account_id, *integrations
                        )
                    except Exception as exc:
                        if current_proxy_lease() is not None and is_proxy_transport_exception(exc):
                            transport_error = exc
                        warnings.append("授权后处理失败: " + type(exc).__name__)
                    self.database.update_job_item(
                        job_id, account_id, "succeeded", "; ".join(warnings)
                    )
                except LoginCancelled:
                    self.database.update_job_item(job_id, account_id, "skipped")
                    self._stop.set()
                except Exception as exc:
                    if current_proxy_lease() is not None and is_proxy_transport_exception(exc):
                        transport_error = exc
                    message = str(exc)
                    for secret in (account["password"], account["sso"]):
                        if secret:
                            message = message.replace(secret, "[REDACTED]")
                    self.database.update_job_item(
                        job_id, account_id, "failed", type(exc).__name__ + ": " + message[:200]
                    )
                finally:
                    if lease_started:
                        try:
                            end_registration_slot(
                                success=login_succeeded and transport_error is None,
                                transport_error=transport_error,
                            )
                        except Exception as exc:
                            self.log("[!] 重新登录代理租约释放失败: %s" % type(exc).__name__)
            self.database.finish_job(
                job_id, "cancelled" if self._stop.is_set() else "completed",
                cancelled=self._stop.is_set(),
            )
        except Exception as exc:
            self.database.finish_job(job_id, "failed", error_message=type(exc).__name__)
        finally:
            if self.on_finish is not None:
                self.on_finish()
