"""可选并发注册调度；每个 worker 使用独立的邮箱与注册浏览器模块实例。"""
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
    observer_lock = threading.Lock()
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
            with observer_lock:
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
