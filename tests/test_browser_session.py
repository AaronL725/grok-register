"""验证 CPA 浏览器会话的复用、取消、代理和清理行为。"""

import unittest
from cpa_xai import browser_session


class Bridge:
    def __init__(self):
        self.stops = 0
    def stop(self):
        self.stops += 1


class Browser:
    def __init__(self, bridge=None):
        self._cpa_proxy_bridge = bridge
        self.quits = 0
    def quit(self):
        self.quits += 1


class BrowserSessionTests(unittest.TestCase):
    def test_close_standalone_closes_browser_and_bridge(self):
        bridge = Bridge()
        browser = Browser(bridge)
        browser_session._register_mint_browser(browser)
        browser_session.close_standalone(browser)
        self.assertEqual(browser.quits, 1)
        self.assertEqual(bridge.stops, 1)

    def test_normalize_cookies_rejects_invalid_items(self):
        value = browser_session.normalize_cookies([None, {"name": "a", "value": "b"}, "bad"])
        self.assertEqual(len(value), 1)
        self.assertEqual(value[0]["name"], "a")


if __name__ == "__main__":
    unittest.main()
