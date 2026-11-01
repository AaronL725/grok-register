"""编排 GUI 与 CLI 共用的单账号注册和批量执行流程。"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import traceback
from typing import Any, Callable, Dict, Optional, Tuple

from app_config import ConfigError, config as app_config
from proxy_protocols import ProxyProtocolError
from sso_risk import RegistrationRiskDenied
from proxy_pool import (
    ProxyAcquireCancelled,
    ProxyPoolError,
    ProxyConfigurationError,
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
STAGE_CODE_WAIT = "code_wait"
STAGE_CODE_SUBMIT = "code_submit"
STAGE_PROFILE_SUBMIT = "profile_submit"
STAGE_SSO_WAIT = "sso_wait"
STAGE_ACCOUNT_CONFIRMED = "account_confirmed"
STAGE_POSTPROCESS = "postprocess"

SAFE_NEW_LEASE = "safe_new_lease"
SAME_LEASE_RECOVERY = "same_lease_recovery"
OUTCOME_UNCERTAIN = "outcome_uncertain"
NO_RETRY = "no_retry"
FATAL = "fatal"


def _set_registration_stage(value):
    _STAGE_TLS.stage = value
    return value


def current_registration_stage():
    return getattr(_STAGE_TLS, "stage", STAGE_LEASE_ACQUIRE)


def _is_non_retryable_pre_submit_error(error):
    while error is not None:
        if isinstance(error, (
            ConfigError, ProxyConfigurationError, ProxyProtocolError,
            FileNotFoundError, PermissionError, ImportError, AttributeError,
        )):
            return True
        if isinstance(error, ProxyPoolError) and classify_proxy_network_error(error) in (
            "configuration", "compatibility",
        ):
            return True
        error = error.__cause__ or error.__context__
    return False


def registration_retry_disposition(stage, error=None):
    """Classify retry safety from the current registration stage."""
    stage = str(stage or STAGE_LEASE_ACQUIRE)
    if stage in (STAGE_LEASE_ACQUIRE, STAGE_BROWSER_START, STAGE_PAGE_OPEN):
        return FATAL if _is_non_retryable_pre_submit_error(error) else SAFE_NEW_LEASE
    if stage == STAGE_CODE_WAIT:
        return SAME_LEASE_RECOVERY
    if stage in (STAGE_EMAIL_SUBMIT, STAGE_CODE_SUBMIT, STAGE_PROFILE_SUBMIT, STAGE_SSO_WAIT):
        return OUTCOME_UNCERTAIN
    if stage in (STAGE_ACCOUNT_CONFIRMED, STAGE_POSTPROCESS):
        return NO_RETRY
    return OUTCOME_UNCERTAIN


class EmailSignupUnavailable(RuntimeError):
    """The xAI signup page explicitly declined the email signup flow."""


class RecoverablePreSubmitError(RuntimeError):
    """Transient browser navigation failure before any account data was submitted."""


class VerificationCodeUnavailable(RuntimeError):
    """Mailbox polling timed out before any verification-code submission began."""
    pass


class VerificationSubmissionUnconfirmed(RuntimeError):
    """A verification code was entered, but the browser could not confirm the next stage."""
    pass


@dataclass
class RegistrationCallbacks:
    log: Callable[[str], None]
    cancelled: Callable[[], bool]
    stop_all: Optional[Callable[[], None]] = None
    record_attempt: Optional[Callable[[Dict[str, Any]], None]] = None


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
    preflight_mail: Optional[Callable[[], Any]] = None
    persist_account_record: Optional[Callable[[Any], Dict[str, Any]]] = None
    persist_account_status: Optional[Callable[[int, Dict[str, Any], Dict[str, Any], str, str], Any]] = None
    capture_failure_diagnostics: Optional[Callable[[Dict[str, Any]], str]] = None


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
    legacy_export_error: str = ""
    account_id: Optional[int] = None
    status_persist_error: str = ""


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
        callbacks.log("[Debug] 邮箱服务凭据已创建（内容已隐藏）")
        if not ops.save_mail_credential(email, dev_token):
            callbacks.log("[!] 邮箱凭据保存失败，注册继续，但已明确记录该异常")
        callbacks.log("[*] 3. 拉取验证码")
        try:
            if not ops.internal_stage_markers:
                _set_registration_stage(STAGE_CODE_WAIT)
            code = ops.fill_code_and_submit(email, dev_token)
            mail_ok = True
            break
        except VerificationCodeUnavailable as exc:
            if mail_try < max_mail_retry:
                callbacks.log(f"[!] 本邮箱在等待阶段未取到验证码，保持当前代理租约并更换邮箱重试: {exc}")
                # Keep the code_wait disposition while recovering. If browser
                # restart itself fails, the outer retry engine must not treat
                # this as a safe point for acquiring a different proxy lease.
                _set_registration_stage(STAGE_CODE_WAIT)
                ops.restart_browser()
                ops.sleep(1)
                continue
            raise
    if not mail_ok:
        raise RuntimeError("验证码阶段失败，已达到最大重试次数")
    callbacks.log("[*] 验证码已获取并提交（内容已隐藏）")
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
    legacy_export_error = ""
    account_id = None
    try:
        persist_record = getattr(ops, "persist_account_record", None)
        if callable(persist_record):
            persistence = persist_record(result)
            if not isinstance(persistence, dict):
                raise TypeError("persist_account_record must return a mapping")
            account_id = persistence.get("account_id")
            if type(account_id) is not int or account_id < 1:
                raise ValueError("persist_account_record must return a positive account_id")
            legacy_export_error = str(persistence.get("legacy_export_error") or "")
        else:
            ops.persist_account_line(result.email, result.password, result.sso)
        saved = True
        save_error = ""
        pending_saved = False
        if legacy_export_error:
            callbacks.log("[!] SQLite 已保存账号，但旧版 TXT 兼容导出失败: %s" % legacy_export_error)
    except Exception as exc:
        saved = False
        save_error = str(exc)
        try:
            pending_saved = bool(ops.queue_unsaved_result(
                {"email": result.email, "password": result.password, "sso": result.sso, "profile": result.profile},
                save_error,
            ))
        except Exception as pending_exc:
            pending_saved = False
            callbacks.log("[!] pending 队列写入异常: %s" % pending_exc)
        callbacks.log("[!] 账号已注册但主结果文件保存失败: %s" % save_error)
        callbacks.log(
            "[!] 未保存账号已写入 pending 队列，等待人工重试"
            if pending_saved else "[!] pending 队列也写入失败；已停止向下游发布此账号"
        )
        if not pending_saved:
            return OutputResult(
                registered=True, saved=False, pending_saved=False, save_error=save_error,
                cpa={"ok": False, "skipped": True, "reason": "account_not_persisted"},
            )

    try:
        pools = ops.add_tokens(result.sso, result.email)
        if not isinstance(pools, dict):
            raise TypeError("token pool result must be a dict")
    except Exception as exc:
        callbacks.log("[!] token 入池后处理异常，账号结果已保留: %s" % exc)
        pools = {"internal": {"enabled": True, "ok": False, "error": str(exc)}}
    for name, state in pools.items():
        if isinstance(state, dict) and state.get("enabled") and not state.get("ok"):
            callbacks.log("[!] grok2api %s 入池失败: %s" % (name, state.get("error")))

    try:
        cpa = ops.export_cpa(result.email, result.password, result.sso)
        if not isinstance(cpa, dict):
            raise TypeError("CPA result must be a dict")
    except Exception as exc:
        callbacks.log("[!] CPA 导出后处理异常，账号结果已保留: %s" % exc)
        cpa = {"ok": False, "skipped": False, "error": str(exc)}

    status_persist_error = ""
    persist_status = getattr(ops, "persist_account_status", None)
    if callable(persist_status) and account_id is not None:
        try:
            if persist_status(account_id, pools, cpa, result.password, result.sso) is False:
                raise RuntimeError("account row was not found while saving integration status")
        except Exception as exc:
            status_persist_error = str(exc)
            callbacks.log("[!] CPA/Grok2API 状态写入 SQLite 失败: %s" % exc)

    return OutputResult(
        registered=True, saved=saved, pending_saved=pending_saved, save_error=save_error,
        pools=pools, cpa=cpa, legacy_export_error=legacy_export_error,
        account_id=account_id, status_persist_error=status_persist_error,
    )


def _notify_observer(observer, result, account, output, callbacks):
    try:
        observer(result, account, output)
    except Exception as exc:
        callbacks.log(f"[Debug] observer 执行失败: {exc}")


def _attempt_failure_type(status, error, stage, output):
    if status == "cancelled":
        return "cancelled"
    if status == "uncertain":
        return "outcome_uncertain"
    if status == "rejected":
        if isinstance(error, EmailSignupUnavailable):
            return "signup_rejected"
        if isinstance(error, RegistrationRiskDenied):
            return "risk_denied"
        return "rejected"
    if status == "registered_unsaved":
        return "persistence"
    if status in ("retrying", "failed"):
        if error is not None and (
            isinstance(error, ProxyPoolError) or is_proxy_transport_exception(error)
        ):
            return "proxy_transport"
        if stage in (STAGE_BROWSER_START, STAGE_PAGE_OPEN):
            return "browser"
        if stage in (STAGE_EMAIL_SUBMIT, STAGE_CODE_WAIT, STAGE_CODE_SUBMIT):
            return "mail_or_verification"
        return "registration"
    if output is not None:
        pool_warning = any(
            isinstance(state, dict) and state.get("enabled") and not state.get("ok")
            for state in output.pools.values()
        )
        cpa_warning = bool(
            output.cpa and not output.cpa.get("skipped")
            and (
                not output.cpa.get("ok") or output.cpa.get("warning")
                or output.cpa.get("partial") or output.cpa.get("cpa_copy_error")
            )
        )
        if (
            pool_warning or cpa_warning or output.legacy_export_error
            or output.status_persist_error
        ):
            return "postprocess"
    return ""


def _notify_attempt(callbacks, slot_index, attempt_number, started_at, account, output, status, error, ops):
    callback = getattr(callbacks, "record_attempt", None)
    if not callable(callback):
        return

    stage = current_registration_stage()
    message = str(error or "")
    error_type = type(error).__name__ if error is not None else ""
    if error is None and output is not None:
        warnings = [
            (output.save_error, "AccountPersistenceError"),
            (output.legacy_export_error, "LegacyExportError"),
            (output.status_persist_error, "IntegrationStatusPersistenceError"),
        ]
        for detail, kind in warnings:
            if detail:
                error_type = kind
                message = str(detail)
                break

    trace = ""
    if error is not None:
        trace = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )

    if account is not None:
        for secret in (getattr(account, "password", ""), getattr(account, "sso", "")):
            if secret:
                message = message.replace(str(secret), "[REDACTED]")
                trace = trace.replace(str(secret), "[REDACTED]")

    failure_type = _attempt_failure_type(status, error, stage, output)
    screenshot_path = ""
    capture = getattr(ops, "capture_failure_diagnostics", None)
    if status in ("retrying", "failed", "uncertain", "rejected") and callable(capture):
        try:
            screenshot_path = str(capture({
                "slot_index": slot_index,
                "attempt_number": attempt_number,
                "worker_key": threading.current_thread().name,
                "stage": stage,
                "status": status,
                "error_type": error_type,
            }) or "")
        except Exception as exc:
            callbacks.log("[Debug] 失败截图捕获异常: %s" % type(exc).__name__)

    try:
        callback({
            "slot_index": slot_index,
            "attempt_number": attempt_number,
            "worker_key": threading.current_thread().name,
            "stage": stage,
            "status": status,
            "account_id": getattr(output, "account_id", None),
            "error_type": error_type,
            "error_message": message[:500],
            "traceback": trace[:8000],
            "failure_type": failure_type,
            "screenshot_path": screenshot_path,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    except Exception as exc:
        callbacks.log("[!] 注册尝试状态写入 SQLite 失败: %s" % exc)


def _run_cleanup_safely(ops, callbacks, reason):
    try:
        ops.cleanup(reason); return True
    except Exception as exc:
        callbacks.log(f"[!] 清理失败，已忽略且不影响账号统计: {reason}: {exc}"); return False


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
    cpa_warning = bool(
        output.cpa and not output.cpa.get("skipped")
        and (
            not output.cpa.get("ok") or output.cpa.get("warning")
            or output.cpa.get("partial") or output.cpa.get("cpa_copy_error")
        )
    ) or bool(output.legacy_export_error) or bool(output.status_persist_error)
    if pool_warning or cpa_warning: result.postprocess_warning_count += 1
    return last_cleanup_success_count


def _record_uncertain(result, callbacks, stage, exc):
    result.fail_count += 1; result.processed_count += 1; result.uncertain_count += 1
    callbacks.log(f"[-] 当前账号在 {stage} 阶段出现结果不确定的失败；为避免重复提交，不自动更换代理重放整个注册流程: {exc}")


def _preflight_mail_after_lease(callbacks, ops):
    preflight = getattr(ops, "preflight_mail", None)
    if callable(preflight):
        return preflight()
    if str(app_config.get("email_provider", "") or "").strip().lower() != "cloudmail":
        return True
    # Backward-compatible fallback for callers that have not injected the
    # mail operation yet. Parallel workers always inject their isolated module.
    import mail_service
    return mail_service.cloudmail_preflight(
        log_callback=callbacks.log,
        cancel_callback=callbacks.cancelled,
        defer_until_slot=False,
    )


def _run_batch_managed(settings, callbacks, observer, ops):
    result = BatchResult(); retry_count_for_slot = 0; last_cleanup_success_count = 0; first_browser_start = True
    try:
        while result.processed_count < settings.count:
            if callbacks.cancelled(): result.cancelled = True; break
            slot_index = result.processed_count + 1
            attempt_index = retry_count_for_slot + 1
            attempt_started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            attempt_status = "failed"
            attempt_error = None
            account = None
            output = None
            continue_batch = True
            transport_error = None
            slot_success = False
            _set_registration_stage(STAGE_LEASE_ACQUIRE)
            try:
                begin_registration_slot(slot_index=slot_index, attempt_index=attempt_index, worker_key=threading.current_thread().name, log=callbacks.log, cancel_callback=callbacks.cancelled)
                _preflight_mail_after_lease(callbacks, ops)
                _set_registration_stage(STAGE_BROWSER_START)
                if first_browser_start or ops.browser_missing():
                    ops.start_browser(); callbacks.log("[*] 浏览器已启动"); first_browser_start = False
                else:
                    ops.restart_browser(); ops.sleep(1)
                callbacks.log(f"--- 开始第 {slot_index}/{settings.count} 个账号 ---")
                account = register_one_account(callbacks, ops, enable_nsfw=settings.enable_nsfw, max_mail_retry=settings.max_mail_retry)
                output = persist_account_result(account, callbacks, ops)
                attempt_status = "succeeded" if output.saved else "registered_unsaved"
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
            except ProxyAcquireCancelled as exc:
                attempt_status = "cancelled"; attempt_error = exc
                result.cancelled = True; callbacks.log("[!] 已在等待代理租约时停止"); continue_batch = False
            except ops.cancelled_exception as exc:
                attempt_status = "cancelled"; attempt_error = exc
                result.cancelled = True; callbacks.log("[!] 注册被停止"); continue_batch = False
            except ops.retry_exception as exc:
                attempt_error = exc
                stage = current_registration_stage()
                disposition = registration_retry_disposition(stage, exc)
                if disposition == FATAL:
                    attempt_status = "failed"
                    raise
                if disposition != SAFE_NEW_LEASE:
                    attempt_status = "uncertain"
                    _record_uncertain(result, callbacks, stage, exc)
                    retry_count_for_slot = 0
                else:
                    attempt_status = "retrying"
                    retry_count_for_slot += 1
                    if retry_count_for_slot <= settings.max_slot_retry:
                        callbacks.log(
                            f"[!] 当前账号流程卡住，安全重试第 "
                            f"{retry_count_for_slot}/{settings.max_slot_retry} 次: {exc}"
                        )
                    else:
                        attempt_status = "failed"
                        result.fail_count += 1
                        result.processed_count += 1
                        retry_count_for_slot = 0
                        callbacks.log(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
            except EmailSignupUnavailable as exc:
                attempt_status = "rejected"; attempt_error = exc
                result.fail_count += 1; result.processed_count += 1; retry_count_for_slot = 0
                callbacks.log(f"[-] 邮箱注册被 xAI 页面明确拒绝，停止本批次以免反复提交: {exc}")
                if callbacks.stop_all is not None:
                    callbacks.stop_all()
                continue_batch = False
            except RegistrationRiskDenied as exc:
                attempt_status = "rejected"; attempt_error = exc
                result.fail_count += 1; result.processed_count += 1; retry_count_for_slot = 0
                callbacks.log(f"[-] 注册风控拒绝，已跳过入库: {exc}")
            except Exception as exc:
                attempt_error = exc
                has_lease = current_proxy_lease() is not None
                proxy_failure = isinstance(exc, ProxyPoolError) or (
                    has_lease and is_proxy_transport_exception(exc)
                )
                stage = current_registration_stage()
                disposition = registration_retry_disposition(stage, exc)
                if proxy_failure and has_lease and is_proxy_transport_exception(exc):
                    transport_error = exc

                if disposition == OUTCOME_UNCERTAIN:
                    attempt_status = "uncertain"
                    _record_uncertain(result, callbacks, stage, exc)
                    retry_count_for_slot = 0
                elif disposition == SAFE_NEW_LEASE:
                    attempt_status = "retrying"
                    retry_count_for_slot += 1
                    if retry_count_for_slot <= settings.max_slot_retry:
                        if proxy_failure:
                            callbacks.log(
                                f"[!] 当前账号代理在安全阶段不可用，释放租约并重试 "
                                f"{retry_count_for_slot}/{settings.max_slot_retry}: {exc}"
                            )
                        else:
                            callbacks.log(
                                f"[!] {stage} 阶段在注册信息提交前失败，安全重试 "
                                f"{retry_count_for_slot}/{settings.max_slot_retry}: {exc}"
                            )
                    else:
                        attempt_status = "failed"
                        result.fail_count += 1
                        result.processed_count += 1
                        retry_count_for_slot = 0
                        callbacks.log(f"[-] 提交前安全重试达到上限，跳过当前账号: {exc}")
                elif disposition == FATAL:
                    attempt_status = "failed"
                    raise
                else:
                    attempt_status = "failed"
                    result.fail_count += 1
                    result.processed_count += 1
                    retry_count_for_slot = 0
                    callbacks.log(f"[-] 注册失败: {exc}")
            finally:
                try:
                    end_registration_slot(success=slot_success, transport_error=transport_error)
                except Exception as exc:
                    callbacks.log(f"[Debug] 代理租约释放失败: {exc}")
                if output is not None:
                    attempt_status = "succeeded" if output.saved else "registered_unsaved"
                _notify_attempt(
                    callbacks, slot_index, attempt_index, attempt_started_at,
                    account, output, attempt_status, attempt_error, ops,
                )
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