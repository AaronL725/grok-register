"""验证共享注册流程的重试、统计、取消、清理和后处理边界。"""

import unittest

from app_config import ConfigError
from registration_flow import (
    RecoverablePreSubmitError,
    RegistrationCallbacks,
    RegistrationOperations,
    RegistrationResult,
    _notify_attempt,
    persist_account_result,
    run_batch,
)


class Cancelled(Exception):
    pass


class Retryable(Exception):
    pass


class FakeOps:
    def __init__(self, save_ok=True, observer_events=None):
        self.events = []
        self.save_ok = save_ok
        self.observer_events = observer_events if observer_events is not None else []
        self.account_no = 0

    def operations(self):
        return RegistrationOperations(
            start_browser=lambda: self.events.append("start"),
            restart_browser=lambda: self.events.append("restart"),
            browser_missing=lambda: False,
            open_signup_page=lambda: self.events.append("open"),
            fill_email_and_submit=self._email,
            save_mail_credential=lambda email, token: True,
            fill_code_and_submit=lambda email, token: "123456",
            fill_profile_and_submit=lambda: {"given_name": "A", "family_name": "B", "password": "pw"},
            wait_for_sso_cookie=lambda: "sso-token",
            enable_nsfw=lambda sso: (True, "ok"),
            persist_account_line=self._persist,
            queue_unsaved_result=lambda payload, error: True,
            add_tokens=lambda sso, email: {
                "local": {"enabled": False, "ok": None, "error": None},
                "remote": {"enabled": False, "ok": None, "error": None},
            },
            export_cpa=lambda email, password, sso: {"ok": False, "skipped": True},
            cleanup=lambda reason: self.events.append(("cleanup", reason)),
            sleep=lambda seconds: self.events.append(("sleep", seconds)),
            cancelled_exception=Cancelled,
            retry_exception=Retryable,
        )

    def _email(self):
        self.account_no += 1
        return f"user{self.account_no}@example.com", "mail-token"

    def _persist(self, email, password, sso):
        if not self.save_ok:
            raise OSError("disk full")
        self.events.append(("persist", email))


class RegistrationFlowTests(unittest.TestCase):
    def callbacks(self, logs=None):
        logs = logs if logs is not None else []
        return RegistrationCallbacks(log=logs.append, cancelled=lambda: False)

    def test_transient_browser_start_failure_retries_and_still_runs_cleanup(self):
        fake = FakeOps()
        ops = fake.operations()
        starts = {"count": 0}

        def start_browser():
            starts["count"] += 1
            if starts["count"] == 1:
                raise RuntimeError("temporary start failure")
            fake.events.append("start")

        ops.start_browser = start_browser
        batch = run_batch(1, self.callbacks(), lambda *args: None, ops)

        self.assertEqual(starts["count"], 2)
        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.fail_count, 0)
        self.assertEqual(fake.events[-1], ("cleanup", "任务结束"))

    def test_last_account_does_not_restart_browser(self):
        fake = FakeOps()
        batch = run_batch(1, self.callbacks(), lambda *args: None, fake.operations())
        self.assertEqual(batch.success_count, 1)
        self.assertNotIn("restart", fake.events)
        self.assertEqual(fake.events[-1], ("cleanup", "任务结束"))

    def test_cleanup_interval_does_not_repeat_after_unsaved_result(self):
        fake = FakeOps(save_ok=True)
        ops = fake.operations()
        original_persist = ops.persist_account_line
        calls = {"count": 0}

        def persist(email, password, sso):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("disk full")
            original_persist(email, password, sso)

        ops.persist_account_line = persist
        batch = run_batch(2, self.callbacks(), lambda *args: None, ops, cleanup_interval=1)
        interval_cleanups = [
            event for event in fake.events
            if isinstance(event, tuple)
            and len(event) > 1
            and isinstance(event[1], str)
            and "已成功" in event[1]
        ]
        self.assertEqual(len(interval_cleanups), 1)
        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.registered_unsaved_count, 1)

    def test_observer_failure_is_logged_and_batch_continues(self):
        fake = FakeOps()
        logs = []

        def broken_observer(*args):
            raise RuntimeError("ui broke")

        batch = run_batch(1, self.callbacks(logs), broken_observer, fake.operations())
        self.assertEqual(batch.success_count, 1)
        self.assertTrue(any("observer 执行失败" in line for line in logs))

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
        ops.start_browser = lambda: (_ for _ in ()).throw(ConfigError("original configuration error"))
        ops.cleanup = lambda reason: (_ for _ in ()).throw(RuntimeError("cleanup error"))
        logs = []
        with self.assertRaisesRegex(ConfigError, "original configuration error"):
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


    def test_attempt_callback_records_retry_and_success(self):
        fake = FakeOps()
        ops = fake.operations()
        starts = {"count": 0}

        def start_browser():
            starts["count"] += 1
            if starts["count"] == 1:
                raise RecoverablePreSubmitError("temporary navigation failure")

        ops.start_browser = start_browser
        attempts = []
        callbacks = RegistrationCallbacks(
            log=lambda _message: None,
            cancelled=lambda: False,
            record_attempt=attempts.append,
        )
        batch = run_batch(1, callbacks, lambda *args: None, ops)

        self.assertEqual(batch.success_count, 1)
        self.assertEqual([item["status"] for item in attempts], ["retrying", "succeeded"])
        self.assertEqual([item["attempt_number"] for item in attempts], [1, 2])
        self.assertEqual([item["slot_index"] for item in attempts], [1, 1])
        self.assertEqual(attempts[0]["error_type"], "RecoverablePreSubmitError")
        self.assertEqual(attempts[1]["error_type"], "")

    def test_attempt_callback_records_failed_stage_without_credentials(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.start_browser = lambda: (_ for _ in ()).throw(ConfigError("invalid browser configuration"))
        attempts = []
        callbacks = RegistrationCallbacks(
            log=lambda _message: None,
            cancelled=lambda: False,
            record_attempt=attempts.append,
        )
        with self.assertRaisesRegex(ConfigError, "invalid browser configuration"):
            run_batch(1, callbacks, lambda *args: None, ops)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "failed")
        self.assertEqual(attempts[0]["stage"], "browser_start")

    def test_persist_account_status_receives_independent_results(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.persist_account_record = lambda account: {
            "account_id": 42,
            "legacy_export_error": "",
        }
        recorded = []
        ops.persist_account_status = lambda *args: recorded.append(args)
        ops.add_tokens = lambda sso, email: {
            "local": {"enabled": True, "ok": True, "error": ""},
            "remote": {"enabled": True, "ok": False, "error": "remote failed"},
        }
        ops.export_cpa = lambda email, password, sso: {
            "ok": True, "skipped": False, "warning": True,
        }

        output = persist_account_result(
            RegistrationResult(ok=True, email="user@example.com", password="pw", sso="token"),
            self.callbacks(),
            ops,
        )

        self.assertEqual(output.account_id, 42)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0][0], 42)
        self.assertEqual(recorded[0][1]["remote"]["ok"], False)
        self.assertTrue(recorded[0][2]["warning"])

    def test_attempt_history_includes_account_persistence_failure(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.persist_account_record = lambda _account: (_ for _ in ()).throw(OSError("disk full"))
        attempts = []
        callbacks = RegistrationCallbacks(
            log=lambda _message: None,
            cancelled=lambda: False,
            record_attempt=attempts.append,
        )

        batch = run_batch(1, callbacks, lambda *_args: None, ops)

        self.assertEqual(batch.registered_unsaved_count, 1)
        self.assertEqual(attempts[0]["status"], "registered_unsaved")
        self.assertEqual(attempts[0]["error_type"], "AccountPersistenceError")
        self.assertEqual(attempts[0]["error_message"], "disk full")

    def test_partial_cpa_export_counts_as_postprocess_warning(self):
        fake = FakeOps()
        ops = fake.operations()
        ops.persist_account_record = lambda _account: {
            "account_id": 7,
            "legacy_export_error": "",
        }
        ops.persist_account_status = lambda *_args: True
        ops.export_cpa = lambda *_args: {
            "ok": True, "skipped": False, "partial": True,
        }

        batch = run_batch(1, self.callbacks(), lambda *_args: None, ops)

        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.postprocess_warning_count, 1)

    def test_failure_screenshot_is_captured_before_final_cleanup(self):
        fake = FakeOps()
        ops = fake.operations()
        events = []
        ops.start_browser = lambda: (_ for _ in ()).throw(ConfigError("browser start configuration failed"))
        ops.capture_failure_diagnostics = lambda details: (
            events.append(("screenshot", details["stage"], details["status"])) or "/tmp/failure.png"
        )
        ops.cleanup = lambda reason: events.append(("cleanup", reason))
        attempts = []
        callbacks = RegistrationCallbacks(
            log=lambda _message: None,
            cancelled=lambda: False,
            record_attempt=attempts.append,
        )

        with self.assertRaisesRegex(ConfigError, "browser start configuration failed"):
            run_batch(1, callbacks, lambda *_args: None, ops)

        self.assertEqual(events[0], ("screenshot", "browser_start", "failed"))
        self.assertEqual(events[1], ("cleanup", "任务结束"))
        self.assertEqual(attempts[0]["failure_type"], "browser")
        self.assertEqual(attempts[0]["screenshot_path"], "/tmp/failure.png")
        self.assertIn("browser start failed", attempts[0]["traceback"])

    def test_screenshot_failure_does_not_replace_registration_error_or_skip_cleanup(self):
        fake = FakeOps()
        ops = fake.operations()
        events = []
        ops.start_browser = lambda: (_ for _ in ()).throw(ConfigError("original configuration failure"))
        ops.capture_failure_diagnostics = lambda _details: (
            _ for _ in ()
        ).throw(OSError("screenshot disk full"))
        ops.cleanup = lambda reason: events.append(reason)
        attempts = []
        logs = []
        callbacks = RegistrationCallbacks(
            log=logs.append,
            cancelled=lambda: False,
            record_attempt=attempts.append,
        )

        with self.assertRaisesRegex(ConfigError, "original configuration failure"):
            run_batch(1, callbacks, lambda *_args: None, ops)

        self.assertEqual(events, ["任务结束"])
        self.assertEqual(attempts[0]["screenshot_path"], "")
        self.assertEqual(attempts[0]["error_message"], "original failure")
        self.assertTrue(any("失败截图捕获异常" in message for message in logs))

    def test_attempt_error_and_traceback_redact_account_credentials(self):
        fake = FakeOps()
        attempts = []
        callbacks = RegistrationCallbacks(
            log=lambda _message: None,
            cancelled=lambda: False,
            record_attempt=attempts.append,
        )
        account = RegistrationResult(
            ok=True, email="user@example.com",
            password="account-password-secret", sso="sso-token-secret",
        )
        try:
            raise RuntimeError("failed with account-password-secret and sso-token-secret")
        except RuntimeError as error:
            _notify_attempt(
                callbacks, 1, 1, "2026-10-31T12:00:00+00:00",
                account, None, "failed", error, fake.operations(),
            )

        attempt = attempts[0]
        self.assertNotIn("account-password-secret", attempt["error_message"])
        self.assertNotIn("sso-token-secret", attempt["error_message"])
        self.assertNotIn("account-password-secret", attempt["traceback"])
        self.assertNotIn("sso-token-secret", attempt["traceback"])
        self.assertIn("[REDACTED]", attempt["traceback"])

    def test_retrying_attempt_captures_screenshot_without_changing_retry(self):
        fake = FakeOps()
        ops = fake.operations()
        starts = {"count": 0}
        screenshots = []

        def start_browser():
            starts["count"] += 1
            if starts["count"] == 1:
                raise RecoverablePreSubmitError("temporary browser failure")

        ops.start_browser = start_browser
        ops.capture_failure_diagnostics = lambda details: (
            screenshots.append(details) or "/tmp/retry.png"
        )
        attempts = []
        callbacks = RegistrationCallbacks(
            log=lambda _message: None,
            cancelled=lambda: False,
            record_attempt=attempts.append,
        )

        batch = run_batch(1, callbacks, lambda *_args: None, ops)

        self.assertEqual(batch.success_count, 1)
        self.assertEqual([item["status"] for item in attempts], ["retrying", "succeeded"])
        self.assertEqual(len(screenshots), 1)
        self.assertEqual(attempts[0]["failure_type"], "browser")
        self.assertEqual(attempts[0]["screenshot_path"], "/tmp/retry.png")


    def test_generic_page_open_exception_retries_with_a_new_attempt(self):
        fake = FakeOps()
        ops = fake.operations()
        calls = {"count": 0}
        attempts = []

        def open_signup_page():
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("email signup button not ready")

        ops.open_signup_page = open_signup_page
        callbacks = RegistrationCallbacks(
            log=lambda _message: None,
            cancelled=lambda: False,
            record_attempt=attempts.append,
        )

        batch = run_batch(1, callbacks, lambda *_args: None, ops)

        self.assertEqual(calls["count"], 2)
        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.fail_count, 0)
        self.assertEqual([item["status"] for item in attempts], ["retrying", "succeeded"])

    def test_post_submit_exception_is_uncertain_and_never_replayed(self):
        fake = FakeOps()
        ops = fake.operations()
        calls = {"count": 0}

        def submit_email():
            calls["count"] += 1
            raise RuntimeError("browser disconnected during email submit")

        ops.fill_email_and_submit = submit_email
        batch = run_batch(1, self.callbacks(), lambda *_args: None, ops)

        self.assertEqual(calls["count"], 1)
        self.assertEqual(batch.processed_count, 1)
        self.assertEqual(batch.fail_count, 1)
        self.assertEqual(batch.uncertain_count, 1)
        self.assertEqual(batch.success_count, 0)

    def test_configuration_error_is_not_retried(self):
        fake = FakeOps()
        ops = fake.operations()
        calls = {"count": 0}

        def start_browser():
            calls["count"] += 1
            raise ConfigError("invalid configuration")

        ops.start_browser = start_browser
        with self.assertRaisesRegex(ConfigError, "invalid configuration"):
            run_batch(1, self.callbacks(), lambda *_args: None, ops)

        self.assertEqual(calls["count"], 1)
        self.assertEqual(fake.events[-1], ("cleanup", "任务结束"))

if __name__ == "__main__":
    unittest.main()
