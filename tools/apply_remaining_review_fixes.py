#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ast
from pathlib import Path

import review_fix_payloads as payloads


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "grok_register_ttk.py"
OAUTH = ROOT / "cpa_xai" / "oauth_device.py"
BROWSER = ROOT / "cpa_xai" / "browser_confirm.py"


def read(path):
    return path.read_text(encoding="utf-8-sig")


def write(path, content):
    ast.parse(content)
    path.write_text(content, encoding="utf-8")


def lines_replace(source, start_line, end_line, replacement):
    lines = source.splitlines(keepends=True)
    text = replacement
    if text and not text.endswith("\n"):
        text += "\n"
    lines[start_line - 1 : end_line] = [text]
    return "".join(lines)


def module_node(source, name, node_types=(ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
    tree = ast.parse(source)
    matches = [
        node
        for node in tree.body
        if isinstance(node, node_types) and getattr(node, "name", None) == name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"module node {name}: expected 1, got {len(matches)}")
    return matches[0]


def replace_module_def(source, name, replacement):
    node = module_node(source, name)
    return lines_replace(source, node.lineno, node.end_lineno, replacement.rstrip() + "\n\n")


def remove_module_def(source, name):
    node = module_node(source, name)
    return lines_replace(source, node.lineno, node.end_lineno, "")


def replace_module_region(source, start_name, end_name, replacement):
    start = module_node(source, start_name)
    end = module_node(source, end_name)
    if start.lineno >= end.lineno:
        raise RuntimeError(f"invalid module region {start_name}..{end_name}")
    return lines_replace(source, start.lineno, end.lineno - 1, replacement.rstrip() + "\n\n")


def class_node(source, class_name):
    return module_node(source, class_name, (ast.ClassDef,))


def class_method(source, class_name, method_name):
    cls = class_node(source, class_name)
    matches = [
        node
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"class method {class_name}.{method_name}: expected 1, got {len(matches)}"
        )
    return matches[0]


def replace_class_method(source, class_name, method_name, replacement):
    node = class_method(source, class_name, method_name)
    return lines_replace(source, node.lineno, node.end_lineno, replacement.rstrip() + "\n\n")


def replace_class_region(source, class_name, start_method, end_method, replacement):
    start = class_method(source, class_name, start_method)
    end = class_method(source, class_name, end_method)
    if start.lineno >= end.lineno:
        raise RuntimeError(
            f"invalid class region {class_name}.{start_method}..{end_method}"
        )
    return lines_replace(source, start.lineno, end.lineno - 1, replacement.rstrip() + "\n\n")


def insert_before_module_def(source, name, insertion):
    node = module_node(source, name)
    return lines_replace(source, node.lineno, node.lineno - 1, insertion.rstrip() + "\n\n")


def remove_top_level_call(source, function_name):
    tree = ast.parse(source)
    matches = []
    for node in tree.body:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == function_name
        ):
            matches.append(node)
    if len(matches) != 1:
        raise RuntimeError(
            f"top-level call {function_name}: expected 1, got {len(matches)}"
        )
    node = matches[0]
    return lines_replace(source, node.lineno, node.end_lineno, "")


def replace_once(source, old, new, label):
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return source.replace(old, new, 1)


main = read(MAIN)

registration_import = '''from registration_core import (
    AccountRetryNeeded,
    OutputContext,
    OutputResult,
    RegistrationCallbacks,
    RegistrationCancelled,
    RegistrationHooks,
    RegistrationResult,
    run_batch,
)
'''
if "from registration_core import (" not in main:
    main = replace_once(
        main,
        "from curl_cffi import requests\n",
        "from curl_cffi import requests\n\n" + registration_import,
        "registration_core import",
    )

main = remove_module_def(main, "RegistrationCancelled")
main = remove_module_def(main, "AccountRetryNeeded")

default_block = main.split("DEFAULT_CONFIG = {", 1)[1].split("}\n", 1)[0]
if '"email_provider":' not in default_block:
    main = replace_once(
        main,
        '    "duckmail_api_key": "",\n',
        '    "duckmail_api_key": "",\n'
        '    "email_provider": "duckmail",\n'
        '    "yyds_api_key": "",\n'
        '    "yyds_jwt": "",\n'
        '    "defaultDomains": "",\n',
        "default provider fields",
    )
if '"grok2api_allow_legacy_full_replace":' not in main:
    main = replace_once(
        main,
        '    "grok2api_remote_app_key": "",\n',
        '    "grok2api_remote_app_key": "",\n'
        '    "grok2api_allow_legacy_full_replace": False,\n',
        "legacy fallback default",
    )
if '"cpa_oidc_initial_timeout_sec":' not in main:
    main = replace_once(
        main,
        '    "cpa_mint_timeout_sec": 300,\n',
        '    "cpa_mint_timeout_sec": 300,\n'
        '    "cpa_oidc_initial_timeout_sec": 15,\n'
        '    "cpa_oidc_poll_timeout_sec": 12,\n',
        "oidc timeout defaults",
    )

main = replace_module_region(
    main,
    "load_config",
    "ensure_stable_python_runtime",
    payloads.CONFIG_BLOCK,
)
main = remove_top_level_call(main, "load_config")
main = replace_module_region(
    main,
    "add_token_to_grok2api_remote_pool",
    "apply_browser_proxy_option",
    payloads.REMOTE_BLOCK,
)
main = insert_before_module_def(
    main,
    "maybe_export_cpa_xai_after_success",
    payloads.SHARED_MAIN,
)
main = replace_class_method(
    main,
    "GrokRegisterGUI",
    "__init__",
    payloads.GUI_INIT,
)
main = replace_class_region(
    main,
    "GrokRegisterGUI",
    "_call_ui",
    "should_stop",
    payloads.GUI_METHODS,
)
main = replace_once(
    main,
    'self.stats_var = tk.StringVar(value="成功: 0 | 失败: 0")',
    'self.stats_var = tk.StringVar(value="成功: 0 | 失败: 0 | 待重试: 0")',
    "initial GUI stats",
)
main = replace_once(
    main,
    "        self.success_count = 0\n"
    "        self.fail_count = 0\n"
    "        self.results = []\n"
    "        now = datetime.datetime.now().strftime",
    "        self.success_count = 0\n"
    "        self.fail_count = 0\n"
    "        self.pending_count = 0\n"
    "        self.results = []\n"
    "        now = datetime.datetime.now().strftime",
    "start registration counters",
)
main = replace_class_method(
    main,
    "GrokRegisterGUI",
    "run_registration",
    payloads.GUI_RUN,
)
main = replace_module_def(
    main,
    "run_registration_cli",
    payloads.CLI_RUN,
)
write(MAIN, main)


oauth = read(OAUTH)
oauth = remove_module_def(oauth, "_sleep_with_cancel")
oauth = replace_module_region(
    oauth,
    "discover",
    "_is_transient_net_error",
    payloads.DISCOVER_BLOCK,
)
oauth = replace_module_def(oauth, "_post_form", payloads.POST_BLOCK)
oauth = replace_module_def(oauth, "request_device_code", payloads.REQUEST_BLOCK)
oauth = replace_module_def(oauth, "poll_device_token", payloads.POLL_BLOCK)
oauth = oauth.replace(
    "isinstance(reason, BaseException)",
    "isinstance(reason, Exception)",
)
write(OAUTH, oauth)


browser = read(BROWSER)
browser = replace_once(
    browser,
    payloads.BROWSER_TAIL_OLD,
    payloads.BROWSER_TAIL_NEW,
    "CPA browser startup lifecycle",
)
browser = replace_once(
    browser,
    "    browser_timeout_sec: float = 240.0,\n"
    "    poll_log: Optional[LogFn] = None,\n",
    "    browser_timeout_sec: float = 240.0,\n"
    "    initial_request_timeout_sec: float = 15.0,\n"
    "    poll_request_timeout_sec: float = 12.0,\n"
    "    poll_log: Optional[LogFn] = None,\n",
    "mint timeout signature",
)
browser = replace_once(
    browser,
    '''        last_error = None
        session = None
        for attempt in range(1, 4):
            try:
                session = request_device_code(proxy=resolved or None)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                logger("request_device_code attempt %s/3 failed: %s" % (attempt, exc))
                _sleep(1.5 * attempt)
        if session is None:
            raise last_error or RuntimeError("request_device_code failed")
''',
    '''        session = request_device_code(
            proxy=resolved or None,
            timeout=float(initial_request_timeout_sec),
            cancel=cancel,
            retries=2,
        )
''',
    "single OIDC initial retry policy",
)
browser = replace_once(
    browser,
    "                    expires_in=min(session.expires_in, int(browser_timeout_sec) + 60),\n"
    "                    log=logger,\n",
    "                    expires_in=min(session.expires_in, int(browser_timeout_sec) + 60),\n"
    "                    timeout=float(poll_request_timeout_sec),\n"
    "                    log=logger,\n",
    "poll timeout propagation",
)
write(BROWSER, browser)

print("remaining review fixes applied")
