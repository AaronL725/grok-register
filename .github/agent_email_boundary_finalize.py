from pathlib import Path

p = Path("registration_browser.py")
source = p.read_text(encoding="utf-8")
start = source.index("def fill_email_and_submit(")
end = source.index("def fill_code_and_submit(", start)
segment = source[start:end]
old = '''        sleep_with_cancel(0.8, cancel_callback)\n        _mark_registration_stage("email_submit")\n        clicked = page.run_js(\n'''
new = '''        sleep_with_cancel(0.8, cancel_callback)\n        ready_to_submit = page.run_js(\n            r"""\nfunction isVisible(node) {\n    if (!node) return false;\n    const style = window.getComputedStyle(node);\n    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;\n    const rect = node.getBoundingClientRect();\n    return rect.width > 0 && rect.height > 0;\n}\nfunction textOf(node) {\n    return [node.getAttribute('placeholder'), node.getAttribute('data-testid'), node.getAttribute('name'),\n            node.getAttribute('id'), node.getAttribute('autocomplete'), node.getAttribute('aria-label')]\n        .filter(Boolean).join(' ').toLowerCase();\n}\nconst direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));\nfor (const node of Array.from(document.querySelectorAll('input, textarea'))) {\n    const type = (node.getAttribute('type') || '').toLowerCase();\n    if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;\n    const meta = textOf(node);\n    if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) direct.push(node);\n}\nconst input = Array.from(new Set(direct)).find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;\nif (!input || !(input.value || '').trim()) return false;\nreturn (input.getAttribute('type') || '').toLowerCase() !== 'email' || input.checkValidity();\n            """\n        )\n        if not ready_to_submit:\n            sleep_with_cancel(0.5, cancel_callback)\n            continue\n        _mark_registration_stage("email_submit")\n        clicked = page.run_js(\n'''
if segment.count(old) != 1:
    raise SystemExit(f"email boundary anchor mismatch: {segment.count(old)}")
segment = segment.replace(old, new, 1)
p.write_text(source[:start] + segment + source[end:], encoding="utf-8")

test_path = Path("tests/test_reliability_final_audit.py")
tests = test_path.read_text(encoding="utf-8")
old_test = '''    source = pathlib.Path("registration_browser.py").read_text(encoding="utf-8")\n    code = source[source.index("def fill_code_and_submit("):source.index("def getTurnstileToken(")]\n'''
new_test = '''    source = pathlib.Path("registration_browser.py").read_text(encoding="utf-8")\n    email = source[source.index("def fill_email_and_submit("):source.index("def fill_code_and_submit(")]\n    email_ready = email.index("ready_to_submit = page.run_js")\n    email_stage = email.index('_mark_registration_stage("email_submit")', email_ready)\n    email_commit = email.index("clicked = page.run_js", email_stage)\n    assert email_ready < email_stage < email_commit\n    assert email.count('_mark_registration_stage("email_submit")') == 1\n\n    code = source[source.index("def fill_code_and_submit("):source.index("def getTurnstileToken(")]\n'''
if tests.count(old_test) != 1:
    raise SystemExit(f"strict audit test anchor mismatch: {tests.count(old_test)}")
test_path.write_text(tests.replace(old_test, new_test, 1), encoding="utf-8")
