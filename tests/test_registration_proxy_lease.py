import unittest
from unittest.mock import patch

from registration_flow import RegistrationCallbacks, RegistrationOperations, run_batch


class Cancelled(Exception):
    pass


class RetryNeeded(Exception):
    pass


class RegistrationProxyLeaseTests(unittest.TestCase):
    def _ops(self, state):
        def fill_code(_email, _token):
            state["code_calls"] += 1
            if state["code_calls"] == 1:
                raise RuntimeError("未收到验证码")
            return "123456"

        return RegistrationOperations(
            start_browser=lambda: state.__setitem__("browser", True),
            restart_browser=lambda: state.__setitem__("restarts", state["restarts"] + 1),
            browser_missing=lambda: not state["browser"],
            open_signup_page=lambda: None,
            fill_email_and_submit=lambda: ("a@example.com", "mail-token"),
            save_mail_credential=lambda _email, _token: True,
            fill_code_and_submit=fill_code,
            fill_profile_and_submit=lambda: {"given_name": "A", "family_name": "B", "password": "pw"},
            wait_for_sso_cookie=lambda: "sso-token",
            enable_nsfw=lambda _sso: (True, "ok"),
            persist_account_line=lambda _email, _password, _sso: None,
            queue_unsaved_result=lambda _payload, _error: True,
            add_tokens=lambda _sso, _email: {},
            export_cpa=lambda _email, _password, _sso: {"ok": True, "skipped": False},
            cleanup=lambda _reason: state.__setitem__("browser", False),
            sleep=lambda _seconds: None,
            cancelled_exception=Cancelled,
            retry_exception=RetryNeeded,
        )

    def test_mail_retry_keeps_one_slot_lease(self):
        state = {"browser": False, "restarts": 0, "code_calls": 0}
        callbacks = RegistrationCallbacks(log=lambda _message: None, cancelled=lambda: False)
        observer = lambda _batch, _account, _output: None
        begin_calls = []
        end_calls = []

        with patch("registration_flow.begin_registration_slot", side_effect=lambda **kw: begin_calls.append(kw)), patch(
            "registration_flow.end_registration_slot", side_effect=lambda **kw: end_calls.append(kw)
        ):
            result = run_batch(
                count=1,
                callbacks=callbacks,
                observer=observer,
                ops=self._ops(state),
                enable_nsfw=True,
                max_mail_retry=2,
            )

        self.assertEqual(result.success_count, 1)
        self.assertEqual(len(begin_calls), 1)
        self.assertEqual(len(end_calls), 1)
        self.assertTrue(end_calls[0]["success"])
        self.assertEqual(state["code_calls"], 2)
        self.assertEqual(state["restarts"], 1, "mail retry should restart browser without starting a new slot")

    def test_each_processed_account_gets_its_own_slot(self):
        state = {"browser": False, "restarts": 0, "code_calls": 99}
        callbacks = RegistrationCallbacks(log=lambda _message: None, cancelled=lambda: False)
        begin_calls = []
        end_calls = []
        ops = self._ops(state)
        ops.fill_code_and_submit = lambda _email, _token: "123456"
        with patch("registration_flow.begin_registration_slot", side_effect=lambda **kw: begin_calls.append(kw)), patch(
            "registration_flow.end_registration_slot", side_effect=lambda **kw: end_calls.append(kw)
        ):
            result = run_batch(2, callbacks, lambda *_args: None, ops, enable_nsfw=False)
        self.assertEqual(result.processed_count, 2)
        self.assertEqual([call["slot_index"] for call in begin_calls], [1, 2])
        self.assertEqual(len(end_calls), 2)


if __name__ == "__main__":
    unittest.main()
