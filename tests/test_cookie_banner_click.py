import unittest
from unittest.mock import patch

from cpa_xai import browser_confirm


class Button:
    def __init__(self, real_failures=0, js_fails=False):
        self.real_failures = real_failures
        self.js_fails = js_fails
        self.real_calls = 0
        self.js_calls = 0

    def click(self, by_js=False):
        if by_js:
            self.js_calls += 1
            if self.js_fails:
                raise RuntimeError("js failed")
            return
        self.real_calls += 1
        if self.real_calls <= self.real_failures:
            raise RuntimeError("no position or size")


class Page:
    def __init__(self, button, visible_texts=None):
        self.button = button
        self.visible_texts = list(visible_texts or [""])

    def ele(self, *_args, **_kwargs):
        return self.button

    def run_js(self, *_args, **_kwargs):
        if len(self.visible_texts) > 1:
            return self.visible_texts.pop(0)
        return self.visible_texts[0]


class CookieBannerClickTests(unittest.TestCase):
    @patch.object(browser_confirm, "_sleep", lambda _seconds: None)
    def test_retries_real_click_before_success(self):
        button = Button(real_failures=1)
        self.assertTrue(
            browser_confirm._click_cookie_button(Page(button), "Accept", lambda _m: None)
        )
        self.assertEqual(button.real_calls, 2)
        self.assertEqual(button.js_calls, 0)

    @patch.object(browser_confirm, "_sleep", lambda _seconds: None)
    def test_uses_js_only_after_real_click_retries(self):
        button = Button(real_failures=3)
        self.assertTrue(
            browser_confirm._click_cookie_button(Page(button), "Accept", lambda _m: None)
        )
        self.assertEqual(button.real_calls, 3)
        self.assertEqual(button.js_calls, 1)

    @patch.object(browser_confirm, "_sleep", lambda _seconds: None)
    def test_dismiss_requires_banner_to_disappear(self):
        button = Button()
        page = Page(button, ["cookie", ""])
        self.assertTrue(browser_confirm._dismiss_cookie_banner(page, lambda _m: None))

    def test_general_real_click_does_not_gain_js_fallback(self):
        button = Button(real_failures=1)
        result = browser_confirm._click_exact(
            Page(button), ["Allow"], lambda _m: None, real=True
        )
        self.assertIsNone(result)
        self.assertEqual(button.js_calls, 0)


if __name__ == "__main__":
    unittest.main()
