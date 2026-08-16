from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    source = p.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, got {count}: {old[:120]!r}")
    p.write_text(source.replace(old, new, 1), encoding="utf-8")


# SSO recovery: only explicitly known transient browser-context errors are retried.
replace_once(
    "registration_browser.py",
    "from DrissionPage.errors import PageDisconnectedError\n",
    "from DrissionPage.errors import ContextLostError, JavaScriptError, PageDisconnectedError\n",
)
replace_once(
    "registration_browser.py",
    """        except ProxyTransportError:\n            raise\n        except Exception as exc:\n            last_wait_exception = exc\n            message = f\"{exc.__class__.__name__}: {exc}\"\n            if message == last_consecutive_error_message:\n                consecutive_wait_errors += 1\n            else:\n                consecutive_wait_errors = 1\n                last_consecutive_error_message = message\n            if log_callback:\n                now = time.time()\n                if message != last_wait_exception_message or now - last_wait_exception_at >= 10:\n                    log_callback(f\"[Debug] 等待 sso cookie 时出现异常 ({consecutive_wait_errors}/3): {message}\")\n                    last_wait_exception_message = message\n                    last_wait_exception_at = now\n            if consecutive_wait_errors >= 3:\n                raise RuntimeError(f\"等待 sso cookie 连续异常: {last_wait_exception.__class__.__name__}: {last_wait_exception}\") from last_wait_exception\n""",
    """        except ProxyTransportError:\n            raise\n        except (ContextLostError, JavaScriptError) as exc:\n            last_wait_exception = exc\n            message = f\"{exc.__class__.__name__}: {exc}\"\n            if message == last_consecutive_error_message:\n                consecutive_wait_errors += 1\n            else:\n                consecutive_wait_errors = 1\n                last_consecutive_error_message = message\n            if log_callback:\n                now = time.time()\n                if message != last_wait_exception_message or now - last_wait_exception_at >= 10:\n                    log_callback(f\"[Debug] 等待 sso cookie 时出现瞬时异常 ({consecutive_wait_errors}/3): {message}\")\n                    last_wait_exception_message = message\n                    last_wait_exception_at = now\n            if consecutive_wait_errors >= 3:\n                raise RuntimeError(f\"等待 sso cookie 连续瞬时异常: {last_wait_exception.__class__.__name__}: {last_wait_exception}\") from last_wait_exception\n        except Exception:\n            raise\n""",
)

# Verification-code inputs may auto-submit from input/change events. Probe readiness without side effects,
# then cross the commit boundary immediately before dispatching those events.
code_anchor = """    while time.time() < deadline:\n        raise_if_cancelled(cancel_callback)\n        filled = page.run_js(\n"""
code_probe = """    while time.time() < deadline:\n        raise_if_cancelled(cancel_callback)\n        ready = page.run_js(\n            \"\"\"\nconst code = String(arguments[0] || '').trim();\nif (!code) return 'not-ready';\nfunction isVisible(node) {\n    if (!node) return false;\n    const style = window.getComputedStyle(node);\n    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;\n    const rect = node.getBoundingClientRect();\n    return rect.width > 0 && rect.height > 0;\n}\nconst aggregate = Array.from(document.querySelectorAll(\n  'input[data-input-otp=\\\"true\\\"], input[name=\\\"code\\\"], input[autocomplete=\\\"one-time-code\\\"], input[inputmode=\\\"numeric\\\"], input[inputmode=\\\"text\\\"]'\n)).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 6) > 1);\nif (aggregate) return 'aggregate';\nconst otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {\n    if (!isVisible(node) || node.disabled || node.readOnly) return false;\n    const maxLength = Number(node.maxLength || 0);\n    const ac = String(node.autocomplete || '').toLowerCase();\n    return maxLength === 1 || ac === 'one-time-code';\n});\nreturn otpBoxes.length >= code.length ? 'boxes' : 'not-ready';\n            \"\"\",\n            clean_code,\n        )\n        if ready == \"not-ready\":\n            sleep_with_cancel(0.5, cancel_callback)\n            continue\n        _mark_registration_stage(\"code_submit\")\n        filled = page.run_js(\n"""
replace_once("registration_browser.py", code_anchor, code_probe)
replace_once(
    "registration_browser.py",
    """        _mark_registration_stage(\"code_submit\")\n        clicked = page.run_js(\n""",
    """        clicked = page.run_js(\n""",
)

# Profile submit: the readiness/Cloudflare probe is side-effect free. Only a ready submit control
# crosses the commit boundary; the following small JS call is the commit-capable action.
replace_once(
    "registration_browser.py",
    """        _mark_registration_stage(\"profile_submit\")\n        submit_state = page.run_js(\n""",
    """        submit_state = page.run_js(\n""",
)
replace_once(
    "registration_browser.py",
    """submitBtn.focus();\nsubmitBtn.click();\nreturn 'submitted';\n            \"\"\"\n        )\n\n        if isinstance(submit_state, str) and submit_state.startswith(\"wait-cloudflare\"):\n""",
    """return 'ready-to-submit';\n            \"\"\"\n        )\n\n        if isinstance(submit_state, str) and submit_state.startswith(\"wait-cloudflare\"):\n""",
)
replace_once(
    "registration_browser.py",
    """        if submit_state == \"submitted\":\n            if log_callback:\n                log_callback(f\"[*] 已填写注册资料并提交: {given_name} {family_name}\")\n            return {\"given_name\": given_name, \"family_name\": family_name, \"password\": password}\n        wait_cf_since = None\n""",
    """        if submit_state == \"ready-to-submit\":\n            _mark_registration_stage(\"profile_submit\")\n            submit_state = page.run_js(\n                r\"\"\"\nfunction isVisible(node) {\n    if (!node) return false;\n    const style = window.getComputedStyle(node);\n    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;\n    const rect = node.getBoundingClientRect();\n    return rect.width > 0 && rect.height > 0;\n}\nfunction buttonText(node) {\n    return [node.innerText, node.textContent, node.getAttribute('value'), node.getAttribute('aria-label'), node.getAttribute('title')]\n      .filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();\n}\nconst submitBtn = Array.from(document.querySelectorAll('button[type=\"submit\"], button, [role=\"button\"], input[type=\"submit\"]'))\n    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')\n    .find((node) => {\n        const t = buttonText(node).replace(/\\s+/g, '').toLowerCase();\n        return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');\n    });\nif (!submitBtn) return 'submit-disappeared';\nsubmitBtn.focus();\nsubmitBtn.click();\nreturn 'submitted';\n                \"\"\"\n            )\n            if submit_state == \"submitted\":\n                if log_callback:\n                    log_callback(f\"[*] 已填写注册资料并提交: {given_name} {family_name}\")\n                return {\"given_name\": given_name, \"family_name\": family_name, \"password\": password}\n        wait_cf_since = None\n""",
)

# Strengthen source-level regression checks for the exact commit boundaries and exception whitelist.
test_path = Path("tests/test_reliability_final_audit.py")
tests = test_path.read_text(encoding="utf-8")
addition = '''\n\ndef test_strict_commit_boundaries_and_sso_exception_whitelist():\n    source = pathlib.Path("registration_browser.py").read_text(encoding="utf-8")\n    code = source[source.index("def fill_code_and_submit("):source.index("def getTurnstileToken(")]\n    assert code.index("ready = page.run_js") < code.index('_mark_registration_stage("code_submit")') < code.index("filled = page.run_js")\n    assert code.count('_mark_registration_stage("code_submit")') == 1\n\n    profile = source[source.index("def fill_profile_and_submit("):source.index("def wait_for_sso_cookie(")]\n    assert profile.index('if submit_state == "ready-to-submit":') < profile.index('_mark_registration_stage("profile_submit")')\n    assert profile.count('_mark_registration_stage("profile_submit")') == 1\n\n    sso = source[source.index("def wait_for_sso_cookie("):]\n    assert "except (ContextLostError, JavaScriptError) as exc:" in sso\n    assert "except Exception:\\n            raise" in sso\n'''
if "test_strict_commit_boundaries_and_sso_exception_whitelist" in tests:
    raise SystemExit("strict finalization tests already present")
test_path.write_text(tests + addition, encoding="utf-8")
