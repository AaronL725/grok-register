import unittest

from registration_core import (
    AccountRetryNeeded,
    OutputResult,
    RegistrationCallbacks,
    RegistrationHooks,
    RegistrationResult,
    run_batch,
)


class RegistrationCoreTests(unittest.TestCase):
    def _hooks(self, register_one, persist, events):
        return RegistrationHooks(
            register_one=register_one,
            persist=persist,
            start_browser=lambda log: events.append("start"),
            restart_browser=lambda log: events.append("restart"),
            browser_is_available=lambda: True,
            cleanup=lambda log, reason: events.append(("cleanup", reason)),
            sleep=lambda seconds, cancel: events.append(("sleep", seconds)),
        )

    def test_shared_batch_preserves_registration_then_persistence_order(self):
        events = []
        callbacks = RegistrationCallbacks(
            log=lambda message: events.append(("log", message)),
            cancelled=lambda: False,
        )

        def register_one(_callbacks):
            events.append("register")
            return RegistrationResult(
                ok=True,
                registered=True,
                email="a@example.com",
                password="pw",
                sso="token",
            )

        def persist(result, _callbacks):
            events.append(("persist", result.email))
            return OutputResult(saved=True)

        result = run_batch(1, callbacks, self._hooks(register_one, persist, events))

        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.fail_count, 0)
        self.assertEqual(result.pending_count, 0)
        self.assertLess(events.index("register"), events.index(("persist", "a@example.com")))
        self.assertEqual(events[-1], ("cleanup", "任务结束"))

    def test_retryable_account_keeps_same_slot_until_success(self):
        events = []
        calls = {"count": 0}
        callbacks = RegistrationCallbacks(log=lambda message: None, cancelled=lambda: False)

        def register_one(_callbacks):
            calls["count"] += 1
            if calls["count"] == 1:
                raise AccountRetryNeeded("retry")
            return RegistrationResult(
                ok=True,
                registered=True,
                email="a@example.com",
                password="pw",
                sso="token",
            )

        result = run_batch(
            1,
            callbacks,
            self._hooks(register_one, lambda *_: OutputResult(saved=True), events),
        )

        self.assertEqual(calls["count"], 2)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.fail_count, 0)

    def test_fourth_retryable_failure_consumes_slot(self):
        events = []
        calls = {"count": 0}
        callbacks = RegistrationCallbacks(log=lambda message: None, cancelled=lambda: False)

        def register_one(_callbacks):
            calls["count"] += 1
            raise AccountRetryNeeded("still stuck")

        result = run_batch(
            1,
            callbacks,
            self._hooks(register_one, lambda *_: OutputResult(saved=True), events),
            max_slot_retry=3,
        )

        self.assertEqual(calls["count"], 4)
        self.assertEqual(result.success_count, 0)
        self.assertEqual(result.fail_count, 1)

    def test_registered_but_unsaved_is_pending_not_success(self):
        events = []
        callbacks = RegistrationCallbacks(log=lambda message: None, cancelled=lambda: False)
        registration = RegistrationResult(
            ok=True,
            registered=True,
            email="a@example.com",
            password="pw",
            sso="token",
        )

        result = run_batch(
            1,
            callbacks,
            self._hooks(
                lambda _: registration,
                lambda *_: OutputResult(
                    saved=False,
                    pending_retry=True,
                    pending_actions=["account_result"],
                ),
                events,
            ),
        )

        self.assertEqual(result.registered_count, 1)
        self.assertEqual(result.success_count, 0)
        self.assertEqual(result.pending_count, 1)
        self.assertEqual(result.fail_count, 0)

    def test_cancel_before_browser_start(self):
        events = []
        callbacks = RegistrationCallbacks(log=lambda message: None, cancelled=lambda: True)

        result = run_batch(
            1,
            callbacks,
            self._hooks(
                lambda _: self.fail("register_one should not run"),
                lambda *_: self.fail("persist should not run"),
                events,
            ),
        )

        self.assertTrue(result.cancelled)
        self.assertNotIn("start", events)
        self.assertEqual(events[-1], ("cleanup", "任务结束"))


if __name__ == "__main__":
    unittest.main()
