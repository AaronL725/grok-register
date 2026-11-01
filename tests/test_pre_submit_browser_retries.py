"""Regression tests for safe retries before the email signup submission boundary."""

import unittest
from unittest.mock import patch

import registration_browser
from app_config import ConfigError
from registration_flow import RecoverablePreSubmitError


class PreSubmitBrowserRetryTests(unittest.TestCase):
    def test_missing_email_signup_button_is_recoverable(self):
        class Page:
            url = "https://accounts.x.ai/sign-up"
            html = "<html>signup page</html>"

        clock = iter((0.0, 0.0, 2.0))
        with patch.object(registration_browser, "page", Page()), \
             patch.object(registration_browser.time, "time", side_effect=lambda: next(clock)), \
             patch.object(registration_browser, "_run_pre_submit_js", return_value=False), \
             patch.object(registration_browser, "_raise_if_email_signup_unavailable"), \
             patch.object(registration_browser, "raise_if_cancelled", return_value=None, create=True), \
             patch.object(registration_browser, "sleep_with_cancel", return_value=None, create=True):
            with self.assertRaisesRegex(RecoverablePreSubmitError, "未找到"):
                registration_browser.click_email_signup_button(timeout=1)

    def test_mailbox_configuration_error_is_not_wrapped_as_recoverable(self):
        with patch.object(
            registration_browser, "get_email_and_token",
            side_effect=ConfigError("mail provider configuration is invalid"),
            create=True,
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ):
            with self.assertRaisesRegex(ConfigError, "mail provider configuration"):
                registration_browser.fill_email_and_submit()

    def test_transient_mailbox_error_remains_recoverable(self):
        with patch.object(
            registration_browser, "get_email_and_token",
            side_effect=RuntimeError("temporary mailbox outage"),
            create=True,
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ):
            with self.assertRaisesRegex(RecoverablePreSubmitError, "temporary mailbox outage"):
                registration_browser.fill_email_and_submit()

    def test_browser_start_failure_is_recoverable_after_local_retries(self):
        chromium = patch.object(
            registration_browser, "Chromium",
            side_effect=RuntimeError("temporary chromium startup failure"),
        )
        with patch.object(registration_browser, "browser", None), \
             patch.object(registration_browser, "page", None), \
             patch.object(registration_browser, "browser_proxy_bridge", None), \
             patch.object(registration_browser, "browser_started_with_proxy", False), \
             patch.object(registration_browser, "prepare_browser_proxy", return_value=("", None), create=True), \
             patch.object(registration_browser, "create_browser_options", return_value=object(), create=True), \
             patch.object(registration_browser, "get_configured_proxy", return_value="", create=True), \
             patch.object(registration_browser, "_managed_proxy_mode", return_value=False), \
             patch.object(registration_browser, "is_proxy_connection_error", return_value=False, create=True), \
             patch.object(registration_browser.time, "sleep", return_value=None), \
             chromium as start:
            with self.assertRaisesRegex(RecoverablePreSubmitError, "浏览器启动失败"):
                registration_browser.start_browser()

        self.assertEqual(start.call_count, 4)

    def test_page_navigation_failure_is_recoverable_and_marks_page_open(self):
        class Page:
            def get(self, _url):
                raise RuntimeError("navigation context lost")

        class Browser:
            def get_tab(self, _index):
                return Page()

            def new_tab(self, _url):
                raise RuntimeError("could not create fallback tab")

        stages = []
        with patch.object(registration_browser, "browser", Browser()), \
             patch.object(registration_browser, "page", None), \
             patch.object(registration_browser, "browser_started_with_proxy", False), \
             patch.object(registration_browser, "get_configured_proxy", return_value="", create=True), \
             patch.object(registration_browser, "raise_if_cancelled", return_value=None, create=True), \
             patch.object(registration_browser, "_mark_registration_stage", side_effect=stages.append):
            with self.assertRaisesRegex(RecoverablePreSubmitError, "注册页导航暂时失败"):
                registration_browser.open_signup_page()

        self.assertEqual(stages, ["page_open"])

    def test_missing_browser_executable_is_not_retried(self):
        chromium = patch.object(registration_browser, "Chromium")
        with patch.object(registration_browser, "browser", None), \
             patch.object(registration_browser, "page", None), \
             patch.object(registration_browser, "browser_proxy_bridge", None), \
             patch.object(registration_browser, "browser_started_with_proxy", False), \
             patch.object(registration_browser, "prepare_browser_proxy", return_value=("", None), create=True), \
             patch.object(registration_browser, "create_browser_options", side_effect=FileNotFoundError("CHROME_PATH missing"), create=True), \
             patch.object(registration_browser, "get_configured_proxy", return_value="", create=True), \
             patch.object(registration_browser, "_managed_proxy_mode", return_value=False), \
             patch.object(registration_browser.time, "sleep", return_value=None), \
             chromium as start:
            with self.assertRaisesRegex(FileNotFoundError, "CHROME_PATH missing"):
                registration_browser.start_browser()

        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
