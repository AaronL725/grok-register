#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "cpa_xai" / "browser_confirm.py"
text = path.read_text(encoding="utf-8")
old = '''def _dismiss_cookie_banner(page: Any, log: LogFn) -> bool:\n    for label in ("全部允许", "Allow all", "接受", "Accept"):\n        if _click_exact(page, [label], log, real=True):\n            log("cookie banner dismissed: %s" % label)\n            return True\n    return False\n'''
new = '''def _dismiss_cookie_banner(page: Any, log: LogFn) -> bool:\n    for label in (\n        "全部允许",\n        "Allow all",\n        "Accept All Cookies",\n        "Accept all cookies",\n        "接受",\n        "Accept",\n    ):\n        if _click_exact(page, [label], log, real=True):\n            log("cookie banner dismissed: %s" % label)\n            return True\n    return False\n'''
if text.count(old) != 1:
    raise RuntimeError(f"expected exactly one target function, found {text.count(old)}")
updated = text.replace(old, new, 1)
if updated.count("Accept All Cookies") != 1 or updated.count("Accept all cookies") != 1:
    raise RuntimeError("new cookie labels were not applied exactly once")
path.write_text(updated, encoding="utf-8")
print("issue 21 cookie labels applied")
