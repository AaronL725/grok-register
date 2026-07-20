#!/usr/bin/env python3
from pathlib import Path

path = Path("cpa_xai/browser_confirm.py")
source = path.read_text(encoding="utf-8")
old = '''    for label in (
        "全部允许",
        "Allow all",
        "Accept All Cookies",
        "Accept all cookies",
        "接受",
        "Accept",
    ):
'''
new = '''    for label in (
        "接受所有 Cookie",
        "接受所有Cookie",
        "全部允许",
        "Allow all",
        "Allow All Cookies",
        "Allow all cookies",
        "Accept All Cookies",
        "Accept all cookies",
        "接受",
        "Accept",
    ):
'''
if source.count(old) != 1:
    raise RuntimeError("expected cookie label tuple not found exactly once")
updated = source.replace(old, new, 1)
if updated.count("接受所有 Cookie") != 1 or updated.count("接受所有Cookie") != 1:
    raise RuntimeError("Chinese cookie labels were not added exactly once")
if updated.count("Allow All Cookies") != 1 or updated.count("Allow all cookies") != 1:
    raise RuntimeError("Allow-all cookie labels were not added exactly once")
path.write_text(updated, encoding="utf-8")
