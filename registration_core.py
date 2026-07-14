"""Shared registration orchestration for GUI and CLI adapters."""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]
ObserverFn = Callable[[str, Dict[str, Any]], None]


class RegistrationCancelled(RuntimeError):
    """Raised when the active registration task is cancelled."""


class AccountRetryNeeded(RuntimeError):
    """Raised when the current account slot should be retried."""


@dataclass(frozen=True)
class RegistrationCallbacks:
    log: LogFn
    cancelled: CancelFn

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise RegistrationCancelled("cancelled")


@dataclass
class RegistrationResult:
    ok: bool
    registered: bool = False
    email: str = ""
    password: str = ""
    sso: str = ""
    profile: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    retryable: bool = False


@dataclass
class OutputContext:
    accounts_output_file: str
    pending_output_file: str = ""


@dataclass
class OutputResult:
    saved: bool = False
    pending_retry: bool = False
    pending_file: str = ""
    pool_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    cpa_result: Dict[str, Any] = field(default_factory=dict)
    pending_actions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class BatchItem:
    registration: RegistrationResult
    output: OutputResult


@dataclass
class BatchResult:
    requested_count: int
    registered_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    pending_count: int = 0
    degraded_count: int = 0
    cancelled: bool = False
    fatal_error: str = ""
    items: List[BatchItem] = field(default_factory=list)

    def snapshot(self) -> Dict[str, int]:
        return {
            "requested": self.requested_count,
            "registered": self.registered_count,
            "success": self.success_count,
            "failed": self.fail_count,
            "pending": self.pending_count,
            "degraded": self.degraded_count,
        }


@dataclass(frozen=True)
class RegistrationHooks:
    register_one: Callable[[RegistrationCallbacks], RegistrationResult]
    persist: Callable[[RegistrationResult, RegistrationCallbacks], OutputResult]
    start_browser: Callable[[LogFn], None]
    restart_browser: Callable[[LogFn], None]
    browser_is_available: Callable[[], bool]
    cleanup: Callable[[LogFn, str], None]
    sleep: Callable[[float, CancelFn], None]


def _notify(observer: Optional[ObserverFn], event: str, payload: Dict[str, Any], callbacks: RegistrationCallbacks) -> None:
    if observer is None:
        return
    try:
        observer(event, payload)
    except Exception as exc:
        callbacks.log("[Debug] batch observer failed during %s: %s" % (event, exc))


def run_batch(
    count: int,
    callbacks: RegistrationCallbacks,
    hooks: RegistrationHooks,
    observer: Optional[ObserverFn] = None,
    max_slot_retry: int = 3,
    cleanup_interval: int = 5,
) -> BatchResult:
    """Run the shared GUI/CLI batch loop while preserving existing retry semantics."""

    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("count must be a positive integer")

    result = BatchResult(requested_count=count)
    slot_index = 0
    retry_count_for_slot = 0

    try:
        callbacks.check_cancelled()
        hooks.start_browser(callbacks.log)
        callbacks.log("[*] 浏览器已启动")

        while slot_index < count:
            if callbacks.cancelled():
                result.cancelled = True
                callbacks.log("[!] 注册被停止")
                break

            callbacks.log("--- 开始第 %s/%s 个账号 ---" % (slot_index + 1, count))
            should_continue = True
            try:
                registration = hooks.register_one(callbacks)
                if not registration.ok or not registration.registered:
                    if registration.retryable:
                        raise AccountRetryNeeded(registration.error or "registration retry requested")
                    raise RuntimeError(registration.error or "registration failed")

                output = hooks.persist(registration, callbacks)
                result.registered_count += 1
                result.items.append(BatchItem(registration=registration, output=output))
                retry_count_for_slot = 0
                slot_index += 1

                if output.saved:
                    result.success_count += 1
                    callbacks.log("[+] 注册并保存成功: %s" % registration.email)
                else:
                    result.pending_count += 1
                    callbacks.log("[!] 注册成功但账号结果未完成持久化，已加入待重试: %s" % registration.email)

                if output.pending_actions:
                    result.degraded_count += 1
                    callbacks.log(
                        "[!] 账号后处理存在待重试项: %s"
                        % ", ".join(output.pending_actions)
                    )

                _notify(
                    observer,
                    "account",
                    {
                        "registration": registration,
                        "output": output,
                        "stats": result.snapshot(),
                    },
                    callbacks,
                )

                if (
                    cleanup_interval > 0
                    and result.success_count > 0
                    and result.success_count % cleanup_interval == 0
                    and slot_index < count
                ):
                    hooks.cleanup(
                        callbacks.log,
                        "已成功 %s 个账号，执行定期清理" % result.success_count,
                    )

            except RegistrationCancelled:
                result.cancelled = True
                callbacks.log("[!] 注册被停止")
                should_continue = False
            except AccountRetryNeeded as exc:
                retry_count_for_slot += 1
                if retry_count_for_slot <= max_slot_retry:
                    callbacks.log(
                        "[!] 当前账号流程卡住，重试第 %s/%s 次: %s"
                        % (retry_count_for_slot, max_slot_retry, exc)
                    )
                else:
                    result.fail_count += 1
                    retry_count_for_slot = 0
                    slot_index += 1
                    callbacks.log("[-] 当前账号已达到最大重试次数，跳过: %s" % exc)
            except Exception as exc:
                result.fail_count += 1
                retry_count_for_slot = 0
                slot_index += 1
                callbacks.log("[-] 注册失败: %s" % exc)

            _notify(observer, "stats", {"stats": result.snapshot()}, callbacks)

            if not should_continue or callbacks.cancelled():
                result.cancelled = True
                break

            if slot_index < count:
                if hooks.browser_is_available():
                    hooks.restart_browser(callbacks.log)
                else:
                    hooks.start_browser(callbacks.log)
                hooks.sleep(1, callbacks.cancelled)

    except RegistrationCancelled:
        result.cancelled = True
        callbacks.log("[!] 注册被停止")
    except Exception as exc:
        result.fatal_error = str(exc)
        callbacks.log("[!] 任务异常: %s" % exc)
    finally:
        try:
            hooks.cleanup(callbacks.log, "任务结束")
        except Exception as exc:
            callbacks.log("[Debug] 任务结束清理失败: %s" % exc)
        _notify(
            observer,
            "complete",
            {"result": result, "stats": result.snapshot()},
            callbacks,
        )

    return result
