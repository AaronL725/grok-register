"""编排 GUI 与 CLI 共用的单账号注册和批量执行流程。"""
from dataclasses import dataclass, field
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from app_config import config as app_config
from sso_risk import RegistrationRiskDenied
from proxy_pool import (
    ProxyAcquireCancelled,
    ProxyPoolError,
    ProxyTransportError,
    begin_registration_slot,
    classify_proxy_network_error,
    current_proxy_lease,
    end_registration_slot,
    get_manager,
    is_proxy_transport_exception,
)

_STAGE_TLS = threading.local()

STAGE_LEASE_ACQUIRE = "lease_acquire"
STAGE_BROWSER_START = "browser_start"
STAGE_PAGE_OPEN = "page_open"
STAGE_EMAIL_SUBMIT = "email_submit"
STAGE_CODE_SUBMIT = "code_submit"
STAGE_PROFILE_SUBMIT = "profile_submit"
STAGE_SSO_WAIT = "sso_wait"
STAGE_ACCOUNT_CONFIRMED = "account_confirmed"
STAGE_POSTPROCESS = "postprocess"

SAFE_NEW_LEASE = "safe_new_lease"
SAME_LEASE_RECOVERY = "same_lease_recovery"
OUTCOME_UNCERTAIN = "outcome_uncertain"
NO_RETRY = "no_retry"


def _set_registration_stage(value):
    _STAGE_TLS.stage = value
    return value


def current_registration_stage():
    return getattr(_STAGE_TLS, "stage", STAGE_LEASE_ACQUIRE)


def registration_retry_disposition(stage, error=None):
    """Classify whether a failed attempt may safely be replayed with a new lease."""
    stage = str(stage or STAGE_LEASE_ACQUIRE)
    if stage in (STAGE_LEASE_ACQUIRE, STAGE_BROWSER_START, STAGE_PAGE_OPEN):
        return SAFE_NEW_LEASE
    if stage in (STAGE_EMAIL_SUBMIT, STAGE_CODE_SUBMIT, STAGE_PROFILE_SUBMIT, STAGE_SSO_WAIT):
        return OUTCOME_UNCERTAIN
    if stage in (STAGE_ACCOUNT_CONFIRMED, STAGE_POSTPROCESS):
        return NO_RETRY
    return OUTCOME_UNCERTAIN


@dataclass
class RegistrationCallbacks:
    log: Callable[[str], None]
    cancelled: Callable[[], bool]


@dataclass
class RegistrationOperations:
    start_browser: Callable[[], None]
    restart_browser: Callable[[], None]
    browser_missing: Callable[[], bool]
    open_signup_page: Callable[[], None]
    fill_email_and_submit: Callable[[], Tuple[str, str]]
    save_mail_credential: Callable[[str, str], bool]
    fill_code_and_submit: Callable[[str, str], str]
    fill_profile_and_submit: Callable[[], Dict[str, Any]]
    wait_for_sso_cookie: Callable[[], str]
    enable_nsfw: Callable[[str], Tuple[bool, str]]
    persist_account_line: Callable[[str, str, str], None]
    queue_unsaved_result: Callable[[Dict[str, Any], str], bool]
    add_tokens: Callable[[str, str], Dict[str, Dict[str, Any]]]
    export_cpa: Callable[[str, str, str], Dict[str, Any]]
    cleanup: Callable[[str], None]
    sleep: Callable[[float], None]
    cancelled_exception: type
    retry_exception: type
    internal_stage_markers: bool = False
    screen_sso: Optional[Callable[[str, str], Any]] = None


@dataclass
class RegistrationResult:
    ok: bool
    email: str = ""
    password: str = ""
    sso: str = ""
    profile: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    retryable: bool = False
    proxy_feedback_kind: str = "application"
    proxy_feedback_error: str = ""


@dataclass
class OutputResult:
    registered: bool
    saved: bool
    pending_saved: bool = False
    save_error: str = ""
    pools: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    cpa: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RegistrationSettings:
    count: int
    enable_nsfw: bool = True
    max_mail_retry: int = 3
    max_slot_retry: int = 3
    cleanup_interval: int = 5


@dataclass
class BatchResult:
    success_count: int = 0
    fail_count: int = 0
    processed_count: int = 0
    registered_unsaved_count: int = 0
    postprocess_warning_count: int = 0
    uncertain_count: int = 0
    cancelled: bool = False
    results: list = field(default_factory=list)


def _stronger_feedback(current_kind, current_error, candidate_error):
    candidate_kind = classify_proxy_network_error(candidate_error)
    priority = {
        "application": 0,
        "compatibility": 0,
        "configuration": 0,
        "suspected_transport": 1,
        "hard_transport": 2,
    }
    if priority.get(candidate_kind, 0) > priority.get(current_kind, 0):
        return candidate_kind, str(candidate_error or "")
    return current_kind, current_error


def _feedback_from_output(account, output):
    kind = str(getattr(account, "proxy_feedback_kind", "application") or "application")
    error = str(getattr(account, "proxy_feedback_error", "") or "")
    cpa = output.cpa if output is not None and isinstance(output.cpa, dict) else {}
    if cpa and not cpa.get("skipped") and not cpa.get("ok"):
        kind, error = _stronger_feedback(kind, error, cpa.get("error") or "")
    return kind, error


def register_one_account(callbacks, ops, enable_nsfw=True, max_mail_retry=3):
    email = ""
    dev_token = ""
    code = ""
    mail_ok = False
    proxy_feedback_kind = "application"
    proxy_feedback_error = ""
    for mail_try in range(1, max_mail_retry + 1):
        if callbacks.cancelled():
            raise ops.cancelled_exception()
        callbacks.log(f"[*] 1. 打开注册页 (尝试 {mail_try}/{max_mail_retry})")
        _set_registration_stage(STAGE_PAGE_OPEN)
        ops.open_signup_page()
        callbacks.log("[*] 2. 创建邮箱并提交")
        if not ops.internal_stage_markers:
            _set_registration_stage(STAGE_EMAIL_SUBMIT)
        email, dev_token = ops.fill_email_and_submit()
        callbacks.log(f"[*] 邮箱: {email}")
        callbacks.log(f"[Debug] 邮箱credential(jwt): {dev_token}")
        if not ops.save_mail_credential(email, dev_token):
            callbacks.log("[!] 邮箱凭据保存失败，注册继续，但已明确记录该异常")
        callbacks.log("[*] 3. 拉取验证码")
        try:
            if not ops.internal_stage_markers:
                _set_registration_stage(STAGE_CODE_SUBMIT)
            code = ops.fill_code_and_submit(email, dev_token)
            mail_ok = True
            break
        except Exception as exc:
            message = str(exc)
            if ("未收到验证码" in message or "验证码" in message) and mail_try < max_mail_retry and not is_proxy_transport_exception(exc):
                callbacks.log(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {message}")
                _set_registration_stage(STAGE_BROWSER_START)
                ops.restart_browser()
                ops.sleep(1)
                continue
            raise
    if not mail_ok:
        raise RuntimeError("验证码阶段失败，已达到最大重试次数")
    callbacks.log(f"[*] 验证码: {code}")
    callbacks.log("[*] 4. 填写资料")
    if not ops.internal_stage_markers:
        _set_registration_stage(STAGE_PROFILE_SUBMIT)
    profile = ops.fill_profile_and_submit()
    callbacks.log(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
    callbacks.log("[*] 5. 等待 sso cookie")
    _set_registration_stage(STAGE_SSO_WAIT)
    sso = ops.wait_for_sso_cookie()
    _set_registration_stage(STAGE_ACCOUNT_CONFIRMED)
    if enable_nsfw:
        callbacks.log("[*] 6. 开启 NSFW")
        try:
            nsfw_ok, nsfw_msg = ops.enable_nsfw(sso)
            if nsfw_ok:
                callbacks.log(f"[+] NSFW 开启成功: {nsfw_msg}")
            else:
                callbacks.log(f"[!] NSFW 未开启，继续保存账号: {nsfw_msg}")
                proxy_feedback_kind, proxy_feedback_error = _stronger_feedback(proxy_feedback_kind, proxy_feedback_error, nsfw_msg)
        except Exception as exc:
            callbacks.log(f"[!] NSFW 开启异常，继续保存账号: {exc}")
            proxy_feedback_kind, proxy_feedback_error = _stronger_feedback(proxy_feedback_kind, proxy_feedback_error, exc)
    return RegistrationResult(
        ok=True, email=email, password=str(profile.get("password") or ""), sso=sso, profile=profile,
        proxy_feedback_kind=proxy_feedback_kind, proxy_feedback_error=proxy_feedback_error,
    )


def persist_account_result(result, callbacks, ops):
    _set_registration_stage(STAGE_POSTPROCESS)
    screen = getattr(ops, "screen_sso", None)
    if callable(screen):
        screen(result.sso, result.email)
    try:
        ops.persist_account_line(result.email, result.password, result.sso)
        saved = True; save_error = ""; pending_saved = False
    except Exception as exc:
        saved = False; save_error = str(exc)
        try:
            pending_saved = bool(ops.queue_unsaved_result({"email": result.email, "password": result.password, "sso": result.sso, "profile": result.profile}, save_error))
        except Exception as pending_exc:
            pending_saved = False; callbacks.log(f"[!] pending 队列写入异常: {pending_exc}")
        callbacks.log(f"[!] 账号已注册但主结果文件保存失败: {save_error}")
        callbacks.log("[!] 未保存账号已写入 pending 队列，等待人工重试" if pending_saved else "[!] pending 队列也写入失败，请立即复制当前账号信息")
    try:
        pools = ops.add_tokens(result.sso, result.email)
        if not isinstance(pools, dict):
            raise TypeError("token pool result must be a dict")
    except Exception as exc:
        callbacks.log(f"[!] token 入池后处理异常，账号结果已保留: {exc}")
        pools = {"internal": {"enabled": True, "ok": False, "error": str(exc)}}
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
    return OutputResult(registered=True, saved=saved, pending_saved=pending_saved, save_error=save_error, pools=pools, cpa=cpa)


def _notify_observer(observer, result, account, output, callbacks):
    try:
        observer(result, account, output)
    except Exception as exc:
        callbacks.log(f"[Debug] observer 执行失败: {exc}")


def _run_cleanup_safely(ops, callbacks, reason):
    try:
        ops.cleanup(reason); return True
    except Exception as exc:
        callbacks.log(f"[!] 清理失败，已忽略且不影响账号统计: {reason}: {exc}"); return False


def _prepare_next_account(result, settings, callbacks, ops):
    if result.processed_count >= settings.count:
        return False
    if callbacks.cancelled():
        result.cancelled = True; return False
    try:
        if ops.browser_missing(): ops.start_browser()
        else: ops.restart_browser()
        ops.sleep(1); return True
    except ops.cancelled_exception:
        result.cancelled = True; callbacks.log("[!] 已在账号间准备阶段停止"); return False


def _record_success(result, settings, callbacks, ops, account, output, last_cleanup_success_count):
    result.results.append({"registration": account, "output": output}); result.processed_count += 1
    if output.saved:
        result.success_count += 1; callbacks.log(f"[+] 注册并保存成功: {account.email}")
        if settings.cleanup_interval > 0 and result.success_count % settings.cleanup_interval == 0 and result.success_count != last_cleanup_success_count and result.processed_count < settings.count:
            _run_cleanup_safely(ops, callbacks, f"已成功 {result.success_count} 个账号，执行定期清理")
            last_cleanup_success_count = result.success_count
    else:
        result.fail_count += 1; result.registered_unsaved_count += 1; callbacks.log(f"[-] 注册成功但持久化未完成: {account.email}")
    pool_warning = any(isinstance(state, dict) and state.get("enabled") and not state.get("ok") for state in output.pools.values())
    cpa_warning = bool(output.cpa and not output.cpa.get("skipped") and (not output.cpa.get("ok") or output.cpa.get("warning") or output.cpa.get("cpa_copy_error")))
    if pool_warning or cpa_warning: result.postprocess_warning_count += 1
    return last_cleanup_success_count


def _record_uncertain(result, callbacks, stage, exc):
    result.fail_count += 1; result.processed_count += 1; result.uncertain_count += 1
    callbacks.log(f"[-] 当前账号在 {stage} 阶段出现结果不确定的失败；为避免重复提交，不自动更换代理重放整个注册流程: {exc}")


def _run_batch_legacy(settings, callbacks, observer, ops):
    result = BatchResult(); retry_count_for_slot = 0; last_cleanup_success_count = 0
    try:
        ops.start_browser(); callbacks.log("[*] 浏览器已启动")
        while result.processed_count < settings.count:
            if callbacks.cancelled(): result.cancelled = True; break
            callbacks.log(f"--- 开始第 {result.processed_count + 1}/{settings.count} 个账号 ---")
            account = None; output = None; continue_batch = True
            try:
                account = register_one_account(callbacks, ops, enable_nsfw=settings.enable_nsfw, max_mail_retry=settings.max_mail_retry)
                output = persist_account_result(account, callbacks, ops); retry_count_for_slot = 0
                last_cleanup_success_count = _record_success(result, settings, callbacks, ops, account, output, last_cleanup_success_count)
            except ops.cancelled_exception:
                result.cancelled = True; callbacks.log("[!] 注册被停止"); continue_batch = False
            except ops.retry_exception as exc:
                retry_count_for_slot += 1
                if retry_count_for_slot <= settings.max_slot_retry: callbacks.log(f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/{settings.max_slot_retry} 次: {exc}")
                else:
                    result.fail_count += 1; result.processed_count += 1; retry_count_for_slot = 0; callbacks.log(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
            except Exception as exc:
                result.fail_count += 1; result.processed_count += 1; retry_count_for_slot = 0; callbacks.log(f"[-] 注册失败: {exc}")
            finally:
                _notify_observer(observer, result, account, output, callbacks)
            if not continue_batch or result.cancelled: break
            if not _prepare_next_account(result, settings, callbacks, ops): break
    finally:
        _run_cleanup_safely(ops, callbacks, "任务结束")
    return result


def _run_batch_managed(settings, callbacks, observer, ops):
    result = BatchResult(); retry_count_for_slot = 0; last_cleanup_success_count = 0; first_browser_start = True
    try:
        while result.processed_count < settings.count:
            if callbacks.cancelled(): result.cancelled = True; break
            slot_index = result.processed_count + 1; attempt_index = retry_count_for_slot + 1
            account = None; output = None; continue_batch = True; transport_error = None; slot_success = False
            _set_registration_stage(STAGE_LEASE_ACQUIRE)
            try:
                begin_registration_slot(slot_index=slot_index, attempt_index=attempt_index, worker_key=threading.current_thread().name, log=callbacks.log, cancel_callback=callbacks.cancelled)
                _set_registration_stage(STAGE_BROWSER_START)
                if first_browser_start or ops.browser_missing():
                    ops.start_browser(); callbacks.log("[*] 浏览器已启动"); first_browser_start = False
                else:
                    ops.restart_browser(); ops.sleep(1)
                callbacks.log(f"--- 开始第 {slot_index}/{settings.count} 个账号 ---")
                account = register_one_account(callbacks, ops, enable_nsfw=settings.enable_nsfw, max_mail_retry=settings.max_mail_retry)
                output = persist_account_result(account, callbacks, ops)
                feedback_kind, feedback_error = _feedback_from_output(account, output); lease = current_proxy_lease()
                if lease is not None and feedback_kind == "hard_transport":
                    transport_error = ProxyTransportError(feedback_error or "postprocess proxy failure")
                    callbacks.log("[!] 后处理确认代理传输失败，节点将进入失败反馈/冷却")
                elif lease is not None and feedback_kind == "suspected_transport":
                    get_manager().report_suspected_transport_failure(lease, feedback_error)
                    callbacks.log("[*] 后处理出现可疑网络错误，已安排当前节点立即复测")
                elif feedback_kind in ("compatibility", "configuration"):
                    callbacks.log("[Debug] 后处理属于本地兼容/配置错误，不计入代理传输健康度")
                slot_success = bool(account and account.ok); retry_count_for_slot = 0
                last_cleanup_success_count = _record_success(result, settings, callbacks, ops, account, output, last_cleanup_success_count)
            except ProxyAcquireCancelled:
                result.cancelled = True; callbacks.log("[!] 已在等待代理租约时停止"); continue_batch = False
            except ops.cancelled_exception:
                result.cancelled = True; callbacks.log("[!] 注册被停止"); continue_batch = False
            except ops.retry_exception as exc:
                stage = current_registration_stage(); disposition = registration_retry_disposition(stage, exc)
                if disposition != SAFE_NEW_LEASE:
                    _record_uncertain(result, callbacks, stage, exc); retry_count_for_slot = 0
                else:
                    retry_count_for_slot += 1
                    if retry_count_for_slot <= settings.max_slot_retry: callbacks.log(f"[!] 当前账号流程卡住，安全重试第 {retry_count_for_slot}/{settings.max_slot_retry} 次: {exc}")
                    else:
                        result.fail_count += 1; result.processed_count += 1; retry_count_for_slot = 0; callbacks.log(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
            except RegistrationRiskDenied as exc:
                result.fail_count += 1; result.processed_count += 1; retry_count_for_slot = 0
                callbacks.log(f"[-] 注册风控拒绝，已跳过入库: {exc}")
            except Exception as exc:
                has_lease = current_proxy_lease() is not None
                proxy_failure = isinstance(exc, ProxyPoolError) or (has_lease and is_proxy_transport_exception(exc))
                stage = current_registration_stage(); disposition = registration_retry_disposition(stage, exc)
                if proxy_failure and has_lease and is_proxy_transport_exception(exc): transport_error = exc
                if disposition == OUTCOME_UNCERTAIN:
                    _record_uncertain(result, callbacks, stage, exc); retry_count_for_slot = 0
                elif proxy_failure and disposition == SAFE_NEW_LEASE:
                    retry_count_for_slot += 1
                    if retry_count_for_slot <= settings.max_slot_retry:
                        callbacks.log(f"[!] 当前账号代理在安全阶段不可用，释放租约并重试 {retry_count_for_slot}/{settings.max_slot_retry}: {exc}")
                    else:
                        result.fail_count += 1; result.processed_count += 1; retry_count_for_slot = 0; callbacks.log(f"[-] 当前账号代理重试达到上限，跳过: {exc}")
                elif disposition == SAFE_NEW_LEASE:
                    raise
                else:
                    result.fail_count += 1; result.processed_count += 1; retry_count_for_slot = 0; callbacks.log(f"[-] 注册失败: {exc}")
            finally:
                try:
                    end_registration_slot(success=slot_success, transport_error=transport_error)
                except Exception as exc:
                    callbacks.log(f"[Debug] 代理租约释放失败: {exc}")
                _notify_observer(observer, result, account, output, callbacks)
            if not continue_batch or result.cancelled: break
    finally:
        _run_cleanup_safely(ops, callbacks, "任务结束")
    return result


def run_batch(count, callbacks, observer, ops, enable_nsfw=True, cleanup_interval=5, max_slot_retry=3, max_mail_retry=3, settings=None):
    if settings is None:
        settings = RegistrationSettings(count=int(count), enable_nsfw=bool(enable_nsfw), cleanup_interval=int(cleanup_interval), max_slot_retry=int(max_slot_retry), max_mail_retry=int(max_mail_retry))
    # All modes share the same stage-aware retry engine. begin_registration_slot() is a no-op when the proxy manager is unmanaged.
    return _run_batch_managed(settings, callbacks, observer, ops)
