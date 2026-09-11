"""Regression coverage for Cloud Mail public-token authentication (issues #84/#86)."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import grok_register_ttk as app
import mail_service
import registration_flow
import registration_parallel
from registration_flow import RegistrationCallbacks, RegistrationOperations


class DummyResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Cancelled(Exception):
    pass


class RetryNeeded(Exception):
    pass


class CloudMailAuthTests(unittest.TestCase):
    def setUp(self):
        self.prev_mail_config = mail_service.config
        self.prev_app_config = dict(app.config)
        mail_service.config = {
            "email_provider": "cloudmail",
            "cloudmail_api_base": "https://mail.example.test",
            "cloudmail_public_token": "public-token-123",
            "cloudmail_domains": "example.test",
            "cloudmail_path_messages": "/api/public/emailList",
            "proxy_mode": "direct",
        }

    def tearDown(self):
        mail_service.config = self.prev_mail_config
        app.config.clear()
        app.config.update(self.prev_app_config)

    def test_request_uses_raw_authorization_token_without_bearer(self):
        response = DummyResponse({"code": 200, "data": []})
        request = Mock(return_value=response)

        with patch.object(mail_service, "http_post", request, create=True):
            self.assertEqual(mail_service.cloudmail_get_messages("target@example.test"), [])

        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "public-token-123")
        self.assertNotEqual(kwargs["headers"]["Authorization"], "Bearer public-token-123")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")

    def test_json_401_becomes_explicit_auth_error(self):
        response = DummyResponse({"code": 401, "message": "token验证失败", "data": None})
        with patch.object(mail_service, "http_post", return_value=response, create=True):
            with self.assertRaises(mail_service.CloudMailAuthError):
                mail_service.cloudmail_get_messages("target@example.test")

    def test_http_401_becomes_explicit_auth_error(self):
        response = DummyResponse(ValueError("not json"), status_code=401, text="unauthorized")
        with patch.object(mail_service, "http_post", return_value=response, create=True):
            with self.assertRaises(mail_service.CloudMailAuthError):
                mail_service.cloudmail_get_messages("target@example.test")

    def test_kv_convergence_401_then_success(self):
        auth_error = mail_service.CloudMailAuthError("bad token")
        get_messages = Mock(side_effect=[auth_error, auth_error, []])
        sleeper = Mock()

        with patch.object(mail_service, "cloudmail_get_messages", get_messages), patch.object(
            mail_service, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(mail_service, "sleep_with_cancel", sleeper, create=True):
            messages = mail_service.cloudmail_wait_for_auth(
                "target@example.test",
                timeout=70,
            )

        self.assertEqual(messages, [])
        self.assertEqual(get_messages.call_count, 3)
        self.assertGreaterEqual(sleeper.call_count, 2)

    def test_persistent_401_fails_deterministically(self):
        auth_error = mail_service.CloudMailAuthError("bad token")
        get_messages = Mock(side_effect=auth_error)

        with patch.object(mail_service, "cloudmail_get_messages", get_messages), patch.object(
            mail_service, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(mail_service, "sleep_with_cancel", return_value=None, create=True):
            with self.assertRaisesRegex(
                mail_service.CloudMailAuthError,
                "持续验证失败",
            ) as caught:
                mail_service.cloudmail_wait_for_auth(
                    "target@example.test",
                    timeout=1,
                )

        self.assertEqual(get_messages.call_count, 2)
        message = str(caught.exception)
        self.assertIn("token_fp=", message)
        self.assertIn("host=mail.example.test", message)
        self.assertNotIn("public-token-123", message)

    def test_mail_polling_allows_one_auth_convergence_window(self):
        auth_error = mail_service.CloudMailAuthError("bad token")
        message = {
            "emailId": 1,
            "toEmail": "target@example.test",
            "subject": "123-ABC xAI",
            "text": "verification code: 123-ABC",
        }
        get_messages = Mock(side_effect=auth_error)
        recover = Mock(return_value=[message])

        with patch.object(mail_service, "cloudmail_get_messages", get_messages), patch.object(
            mail_service, "cloudmail_wait_for_auth", recover
        ), patch.object(mail_service, "raise_if_cancelled", return_value=None, create=True):
            code = mail_service.cloudmail_get_oai_code(
                "unused",
                "target@example.test",
                timeout=180,
                poll_interval=3,
            )

        self.assertEqual(code, "123-ABC")
        self.assertEqual(get_messages.call_count, 1)
        recover.assert_called_once()
        self.assertTrue(recover.call_args.kwargs["initial_failure"])
        self.assertLessEqual(
            recover.call_args.kwargs["timeout"],
            mail_service.CLOUDMAIL_AUTH_CONVERGENCE_SECONDS,
        )

    def test_persistent_auth_error_during_mail_polling_is_not_infinite(self):
        auth_error = mail_service.CloudMailAuthError("bad token")
        persistent = mail_service.CloudMailAuthError("持续验证失败")
        get_messages = Mock(side_effect=auth_error)
        recover = Mock(side_effect=persistent)
        sleeper = Mock()

        with patch.object(mail_service, "cloudmail_get_messages", get_messages), patch.object(
            mail_service, "cloudmail_wait_for_auth", recover
        ), patch.object(mail_service, "raise_if_cancelled", return_value=None, create=True), patch.object(
            mail_service, "sleep_with_cancel", sleeper, create=True
        ):
            with self.assertRaises(mail_service.CloudMailAuthError):
                mail_service.cloudmail_get_oai_code(
                    "unused",
                    "target@example.test",
                    timeout=180,
                    poll_interval=3,
                )

        self.assertEqual(get_messages.call_count, 1)
        recover.assert_called_once()
        sleeper.assert_not_called()

    def test_early_preflight_is_deferred_until_registration_slot(self):
        wait = Mock(return_value=[])
        logs = []
        with patch.object(mail_service, "cloudmail_wait_for_auth", wait):
            result = mail_service.cloudmail_preflight(log_callback=logs.append)

        self.assertIsNone(result)
        wait.assert_not_called()
        self.assertTrue(any("延后" in line for line in logs))

    def test_slot_preflight_uses_convergence_helper(self):
        wait = Mock(return_value=[])
        with patch.object(mail_service, "cloudmail_wait_for_auth", wait):
            self.assertTrue(
                mail_service.cloudmail_preflight(
                    defer_until_slot=False,
                    auth_timeout=70,
                )
            )

        wait.assert_called_once()
        self.assertEqual(wait.call_args.kwargs["timeout"], 70)

    def test_injected_preflight_happens_after_lease_and_before_browser(self):
        app.config["email_provider"] = "cloudmail"
        events = []

        def begin_slot(**_kwargs):
            events.append("lease")

        def injected_preflight():
            events.append("preflight")
            return True

        operations = RegistrationOperations(
            start_browser=lambda: events.append("browser"),
            restart_browser=lambda: events.append("restart"),
            browser_missing=lambda: True,
            open_signup_page=lambda: None,
            fill_email_and_submit=lambda: ("target@example.test", "mail-token"),
            save_mail_credential=lambda *_args: True,
            fill_code_and_submit=lambda *_args: "123-ABC",
            fill_profile_and_submit=lambda: {
                "given_name": "Test",
                "family_name": "User",
                "password": "pw",
            },
            wait_for_sso_cookie=lambda: "sso-token",
            enable_nsfw=lambda _sso: (True, "ok"),
            persist_account_line=lambda *_args: None,
            queue_unsaved_result=lambda *_args: True,
            add_tokens=lambda *_args: {},
            export_cpa=lambda *_args: {"ok": False, "skipped": True},
            cleanup=lambda _reason: None,
            sleep=lambda _seconds: None,
            cancelled_exception=Cancelled,
            retry_exception=RetryNeeded,
            preflight_mail=injected_preflight,
        )
        callbacks = RegistrationCallbacks(log=lambda _message: None, cancelled=lambda: False)
        global_preflight = Mock(side_effect=AssertionError("global mail preflight must not run"))

        with patch.object(registration_flow, "begin_registration_slot", side_effect=begin_slot), patch.object(
            registration_flow, "end_registration_slot", return_value=None
        ), patch.object(registration_flow, "current_proxy_lease", return_value=None), patch.object(
            mail_service, "cloudmail_preflight", global_preflight
        ):
            result = registration_flow.run_batch(
                1,
                callbacks,
                lambda *_args: None,
                operations,
                enable_nsfw=False,
            )

        self.assertEqual(result.success_count, 1)
        global_preflight.assert_not_called()
        self.assertLess(events.index("lease"), events.index("preflight"))
        self.assertLess(events.index("preflight"), events.index("browser"))

    def test_parallel_workers_bind_preflight_to_isolated_mail_module(self):
        source = Path(registration_parallel.__file__).read_text(encoding="utf-8")
        self.assertIn("mail_module.cloudmail_preflight(", source)
        self.assertIn("preflight_mail=preflight_mail", source)
        self.assertNotIn("preflight_mail=mail_service.cloudmail_preflight", source)

    def test_token_warning_does_not_rewrite_auth_scheme(self):
        mail_service.config["cloudmail_public_token"] = "Bearer public-token-123"
        warnings = mail_service._cloudmail_token_warnings()
        self.assertTrue(any("Bearer" in line for line in warnings))
        self.assertEqual(
            mail_service.cloudmail_build_headers()["Authorization"],
            "Bearer public-token-123",
        )


if __name__ == "__main__":
    unittest.main()
