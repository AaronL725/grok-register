"""管理 CPA 授权浏览器会话、代理、Cookie 注入和资源清理。"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

LogFn = Callable[[str], None]
_mint_tls = threading.local()
_mint_registry_lock = threading.Lock()
_mint_registry = set()

def _noop_log(_: str) -> None:
    return None

class BrowserConfirmError(RuntimeError):
    pass

def _sleep(sec: float) -> None:
    time.sleep(sec)

def create_standalone_page(proxy: Optional[str] = None, headless: bool = False, log: Optional[LogFn] = None):
    logger = log or _noop_log
    try:
        from DrissionPage import Chromium, ChromiumOptions
    except ImportError as exc:
        raise BrowserConfirmError("DrissionPage not installed") from exc

    options = None
    package_root = Path(__file__).resolve().parents[1]
    try:
        from browser_runtime import create_browser_options

        options = create_browser_options(
            extension_path=package_root / "turnstilePatch"
        )
        logger("using shared browser_runtime.create_browser_options")
    except Exception as exc:  # noqa: BLE001
        logger("shared browser options unavailable: %s" % exc)
        options = None

    if options is None:
        options = ChromiumOptions()
        options.auto_port()
        options.set_timeouts(base=2)
        for flag in (
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--mute-audio",
            "--no-first-run",
            "--disable-background-networking",
            "--window-size=1280,900",
        ):
            options.set_argument(flag)
        extension = str(package_root / "turnstilePatch")
        if os.path.isdir(extension):
            try:
                options.add_extension(extension)
                logger("added extension %s" % extension)
            except Exception as exc:  # noqa: BLE001
                logger("extension add failed: %s" % exc)

    if headless:
        try:
            options.headless(True)
        except Exception:
            options.set_argument("--headless=new")
        logger("headless=True (may hit Cloudflare / break real clicks)")
    else:
        try:
            options.headless(False)
        except Exception:
            pass

    for candidate in (
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ):
        if os.path.isfile(candidate):
            try:
                options.set_browser_path(candidate)
            except Exception:
                pass
            break

    from .proxyutil import prepare_chromium_proxy, proxy_log_label, resolve_proxy

    resolved = resolve_proxy(proxy)
    proxy_bridge = None
    browser = None
    chrome_proxy, proxy_bridge = prepare_chromium_proxy(resolved, log=logger)
    try:
        if chrome_proxy:
            options.set_argument("--proxy-server=%s" % chrome_proxy)
            logger("browser proxy=%s (chromium %s)" % (proxy_log_label(resolved), chrome_proxy))
        else:
            logger("browser proxy=(none)")
        browser = Chromium(options)
        if proxy_bridge is not None:
            setattr(browser, "_cpa_proxy_bridge", proxy_bridge)
        page = browser.latest_tab
        _register_mint_browser(browser)
        logger("standalone chromium started")
        return browser, page
    except Exception:
        if browser is not None:
            close_standalone(browser)
        if proxy_bridge is not None:
            try:
                proxy_bridge.stop()
            except Exception:
                pass
        raise

def close_standalone(browser: Any) -> None:
    if browser is None:
        return
    _unregister_mint_browser(browser)
    bridge = getattr(browser, "_cpa_proxy_bridge", None)
    try:
        browser.quit()
    except Exception:
        pass
    if bridge is not None:
        try:
            bridge.stop()
        except Exception:
            pass

def _register_mint_browser(browser: Any) -> None:
    if browser is None:
        return
    with _mint_registry_lock:
        _mint_registry.add(browser)

def _unregister_mint_browser(browser: Any) -> None:
    if browser is None:
        return
    with _mint_registry_lock:
        _mint_registry.discard(browser)

def _mint_tls_get():
    state = getattr(_mint_tls, "state", None)
    if state is None:
        state = {"browser": None, "page": None, "served": 0, "proxy": None, "headless": None}
        _mint_tls.state = state
    return state

def clear_page_session(page: Any, browser: Optional[Any] = None, log: Optional[LogFn] = None) -> None:
    logger = log or _noop_log
    try:
        if page is not None:
            try:
                page.get("about:blank")
            except Exception:
                pass
            for javascript in (
                "try{localStorage.clear()}catch(e){}",
                "try{sessionStorage.clear()}catch(e){}",
            ):
                try:
                    page.run_js(javascript)
                except Exception:
                    pass
        for target in (page, browser):
            if target is None:
                continue
            try:
                target.set.cookies.clear()  # type: ignore[attr-defined]
                logger("mint session cookies cleared")
                break
            except Exception:
                try:
                    cookies = target.cookies()
                    if isinstance(cookies, list):
                        for cookie in cookies:
                            try:
                                target.set.cookies.remove(cookie)  # type: ignore[attr-defined]
                            except Exception:
                                pass
                except Exception:
                    pass
    except Exception as exc:
        logger("clear_page_session: %s" % exc)

def normalize_cookies(cookies: Any):
    output = []
    if not cookies:
        return output
    if isinstance(cookies, dict):
        for name, value in cookies.items():
            if name and value is not None:
                output.append({"name": str(name), "value": str(value), "domain": ".x.ai", "path": "/"})
        cookies = output
        output = []
    if not isinstance(cookies, (list, tuple)):
        return output
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name") or cookie.get("Name")
        value = cookie.get("value") or cookie.get("Value")
        if not name or value is None:
            continue
        domain = str(cookie.get("domain") or cookie.get("Domain") or ".x.ai")
        path = str(cookie.get("path") or cookie.get("Path") or "/")
        item = {"name": str(name), "value": str(value), "domain": domain, "path": path}
        for source, target in (
            ("expiry", "expiry"),
            ("expires", "expiry"),
            ("secure", "secure"),
            ("httpOnly", "httpOnly"),
            ("sameSite", "sameSite"),
        ):
            if source in cookie and cookie[source] is not None:
                item[target] = cookie[source]
        output.append(item)
    sso_names = {"sso", "sso-rw", "cf_clearance", "sso_jwt", "__cf_bm"}
    extras = []
    seen = {(item["name"], item["domain"], item["path"]) for item in output}
    for item in list(output):
        name = item["name"]
        if name not in sso_names and not name.startswith("sso"):
            continue
        for domain in (".x.ai", "accounts.x.ai", ".accounts.x.ai", "auth.x.ai", ".auth.x.ai"):
            key = (name, domain, item["path"])
            if key in seen:
                continue
            clone = dict(item)
            clone["domain"] = domain
            extras.append(clone)
            seen.add(key)
    output.extend(extras)
    return output

def inject_cookies(page: Any, cookies: Any, log: Optional[LogFn] = None) -> int:
    logger = log or _noop_log
    items = normalize_cookies(cookies)
    if not items or page is None:
        return 0
    for url in ("https://accounts.x.ai/", "https://auth.x.ai/", "https://grok.com/"):
        try:
            page.get(url)
            _sleep(0.4)
        except Exception:
            continue
    count = 0
    for target_name, target in (("page", page), ("browser", getattr(page, "browser", None))):
        if target is None:
            continue
        try:
            target.set.cookies(items)  # type: ignore[attr-defined]
            count = len(items)
            logger("injected cookies bulk via %s=%s" % (target_name, count))
            break
        except Exception as exc:
            logger("bulk set via %s failed: %s" % (target_name, exc))
    if count == 0:
        for item in items:
            ok = False
            for target in (page, getattr(page, "browser", None)):
                if target is None:
                    continue
                try:
                    target.set.cookies(item)  # type: ignore[attr-defined]
                    ok = True
                    break
                except Exception:
                    continue
            if ok:
                count += 1
        logger("injected cookies one-by-one=%s/%s" % (count, len(items)))
    try:
        javascript_items = [
            cookie
            for cookie in items
            if not bool(cookie.get("httpOnly")) and str(cookie.get("name") or "").startswith("sso")
        ]
        if javascript_items:
            page.run_js(
                """
                const items = arguments[0] || [];
                for (const c of items) {
                  let cookie = `${c.name}=${c.value}; path=${c.path || '/'}; domain=${c.domain || '.x.ai'}`;
                  if (c.secure !== false) cookie += '; Secure';
                  document.cookie = cookie;
                }
                return document.cookie;
                """,
                javascript_items,
            )
            logger("injected non-httpOnly sso cookies via document.cookie")
    except Exception as exc:
        logger("document.cookie injection failed: %s" % exc)
    return count

def acquire_mint_browser(proxy: Optional[str] = None, headless: bool = False, reuse: bool = True, recycle_every: int = 15, log: Optional[LogFn] = None):
    logger = log or _noop_log
    state = _mint_tls_get()
    if not reuse:
        browser, page = create_standalone_page(proxy=proxy, headless=headless, log=logger)
        return browser, page, True
    proxy_key = str(proxy or "")
    if state["browser"] is None:
        browser, page = create_standalone_page(proxy=proxy, headless=headless, log=logger)
        state.update({"browser": browser, "page": page, "served": 0, "proxy": proxy_key, "headless": bool(headless)})
        return browser, page, False
    if state["proxy"] != proxy_key or bool(state["headless"]) != bool(headless):
        try:
            close_standalone(state["browser"])
        except Exception:
            pass
        browser, page = create_standalone_page(proxy=proxy, headless=headless, log=logger)
        state.update({"browser": browser, "page": page, "served": 0, "proxy": proxy_key, "headless": bool(headless)})
        return browser, page, False
    if recycle_every and state["served"] and state["served"] % max(int(recycle_every), 1) == 0:
        try:
            close_standalone(state["browser"])
        except Exception:
            pass
        browser, page = create_standalone_page(proxy=proxy, headless=headless, log=logger)
        state.update({"browser": browser, "page": page, "served": 0, "proxy": proxy_key, "headless": bool(headless)})
        return browser, page, False
    clear_page_session(state["page"], browser=state["browser"], log=logger)
    return state["browser"], state["page"], False

def release_mint_browser(owned: bool, success: bool, log: Optional[LogFn] = None) -> None:
    logger = log or _noop_log
    if owned:
        return
    state = _mint_tls_get()
    if success:
        state["served"] = int(state.get("served", 0) or 0) + 1
        logger("mint browser served=%s" % state["served"])

def shutdown_mint_browsers() -> None:
    state = _mint_tls_get()
    with _mint_registry_lock:
        browsers = list(_mint_registry)
    for browser in browsers:
        try:
            close_standalone(browser)
        except Exception:
            pass
    state.update({"browser": None, "page": None, "served": 0, "proxy": None, "headless": None})

