import inspect
import unittest

import grok_register_ttk as app
from cpa_xai import browser_confirm


class GuiQueueTests(unittest.TestCase):
    def test_worker_facing_log_only_enqueues(self):
        source = inspect.getsource(app.GrokRegisterGUI.log)
        self.assertIn("ui_queue.put", source)
        self.assertNotIn("root.after", source)
        self.assertNotIn("log_text.insert", source)

    def test_main_thread_queue_consumer_schedules_itself(self):
        source = inspect.getsource(app.GrokRegisterGUI.process_ui_queue)
        self.assertIn("get_nowait", source)
        self.assertIn("root.after(50", source)
        self.assertIn("log_text.insert", source)

    def test_cross_thread_call_ui_helper_is_removed(self):
        self.assertFalse(hasattr(app.GrokRegisterGUI, "_call_ui"))


class BrowserLifecycleTests(unittest.TestCase):
    def test_page_is_initialized_before_browser_is_registered(self):
        source = inspect.getsource(browser_confirm.create_standalone_page)
        page_position = source.index("page = browser.latest_tab")
        register_position = source.index("_register_mint_browser(browser)")
        self.assertLess(page_position, register_position)

    def test_failure_path_closes_created_browser(self):
        source = inspect.getsource(browser_confirm.create_standalone_page)
        self.assertIn("if browser is not None:", source)
        self.assertIn("close_standalone(browser)", source)


if __name__ == "__main__":
    unittest.main()
