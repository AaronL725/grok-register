"""自动完成 xAI 登录、设备授权确认和相关页面交互。"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse


LogFn = Callable[[str], None]

from .browser_session import (
    BrowserConfirmError, _noop_log, _sleep, create_standalone_page, close_standalone,
    clear_page_session, normalize_cookies, inject_cookies, acquire_mint_browser,
    release_mint_browser, shutdown_mint_browsers,
)









def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _debug_shot_dir() -> Path:
    folder = _project_root() / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _safe_tag(value: str) -> str:
    text = (value or "na").strip()
    out = []
    for char in text:
        if char.isalnum() or char in ("@", ".", "-", "_"):
            out.append(char)
        else:
            out.append("_")
    return ("".join(out)[:80] or "na")


def _save_debug_shot(page: Any, tag: str, email: str = "", log: Optional[LogFn] = None) -> Optional[str]:
    logger = log or _noop_log
    try:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        name = "%s_%s_%s.png" % (timestamp, _safe_tag(email), _safe_tag(tag))
        path = _debug_shot_dir() / name
        saved = None
        for kwargs in (
            {"path": str(path), "full_page": True},
            {"path": str(path)},
            {"name": str(path)},
        ):
            try:
                if hasattr(page, "get_screenshot"):
                    page.get_screenshot(**kwargs)
                    saved = path
                    break
            except TypeError:
                continue
            except Exception:
                continue
        if saved is None and hasattr(page, "get_screenshot"):
            try:
                page.get_screenshot(path=str(path))
                saved = path
            except Exception:
                pass
        if saved is None:
            logger("screenshot failed tag=%s" % tag)
            return None
        try:
            meta = path.with_suffix(".txt")
            meta.write_text(
                "url=%s\nemail=%s\ntag=%s\nvisible=%s\n" % (_page_url(page), email, tag, _norm(_visible_text(page))[:800]),
                encoding="utf-8",
            )
        except Exception:
            pass
        logger("debug shot saved: %s" % saved)
        return str(saved)
    except Exception as exc:  # noqa: BLE001
        logger("screenshot error: %s" % exc)
        return None


def _is_turnstile_challenge(text: str) -> bool:
    source = text or ""
    lower = source.lower()
    needles = (
        "确认您是真人",
        "确认你是真人",
        "verify you are human",
        "confirm you are human",
        "just a moment",
        "checking your browser",
        "cf-turnstile",
        "进行人机验证",
        "人机验证",
    )
    return any(needle in source or needle in lower for needle in needles)


























def _page_url(page: Any) -> str:
    try:
        return str(getattr(page, "url", "") or "")
    except Exception:
        return ""


def _visible_text(page: Any) -> str:
    try:
        return str(page.run_js("return (document.body && (document.body.innerText || document.body.textContent)) || '';"))
    except Exception:
        return ""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _find_button_exact(page: Any, label: str) -> Optional[Any]:
    try:
        return page.ele(
            "xpath://button[normalize-space()='%s'] | //*[@role='button' and normalize-space()='%s'] | //a[normalize-space()='%s'] | //input[@type='submit' and @value='%s']"
            % (label, label, label, label),
            timeout=0.4,
        )
    except Exception:
        return None


def _cookie_banner_visible(text: str) -> bool:
    source = text or ""
    lower = source.lower()
    return any(
        needle in source or needle in lower
        for needle in ("全部允许", "隐私偏好", "cookie", "privacy preference", "allow all")
    )


def _dismiss_cookie_banner(page: Any, log: LogFn) -> bool:
    for label in ("全部允许", "Allow all", "接受", "Accept"):
        if _click_exact(page, [label], log, real=True):
            log("cookie banner dismissed: %s" % label)
            return True
    return False


def _click_exact(page: Any, labels, log: LogFn, real: bool = False) -> Optional[str]:
    for label in labels:
        target = _find_button_exact(page, label)
        if not target:
            continue
        try:
            if real:
                target.click()
            else:
                try:
                    target.click(by_js=True)
                except Exception:
                    target.click()
            log("clicked exact button: %s" % label)
            return label
        except Exception as exc:
            log("button click failed %s: %s" % (label, exc))
    return None


def _wait_turnstile(page: Any, log: LogFn, timeout_sec: float, email: str = "", raise_on_timeout: bool = False) -> bool:
    deadline = time.time() + float(timeout_sec)
    while time.time() < deadline:
        text = _visible_text(page)
        if not _is_turnstile_challenge(text):
            try:
                token_length = page.run_js(
                    """
                    const input = document.querySelector('input[name="cf-turnstile-response"]');
                    return String((input && input.value) || '').trim().length;
                    """
                )
                if int(token_length or 0) >= 80:
                    return True
            except Exception:
                pass
            if not _is_turnstile_challenge(_visible_text(page)):
                return True
        _sleep(1.0)
    if raise_on_timeout:
        shot = _save_debug_shot(page, tag="turnstile-timeout", email=email, log=log)
        message = "turnstile timeout"
        if shot:
            message = "%s shot=%s" % (message, shot)
        raise BrowserConfirmError(message)
    return False


def _fill(page: Any, selector: str, value: str, log: LogFn, label: str = "") -> bool:
    try:
        target = page.ele(selector, timeout=0.8)
    except Exception:
        target = None
    if not target:
        return False
    try:
        target.clear()
    except Exception:
        pass
    try:
        target.input(value)
    except Exception:
        try:
            target.click()
            page.run_js(
                """
                const selector = arguments[0];
                const value = arguments[1];
                const node = document.querySelector(selector);
                if (!node) return false;
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                if (setter) setter.call(node, value);
                else node.value = value;
                node.dispatchEvent(new Event('input', { bubbles: true }));
                node.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
                """,
                selector,
                value,
            )
        except Exception:
            return False
    if label:
        log("filled %s" % label)
    return True


def _fill_input(page: Any, selector: str, value: str, label: str, log: LogFn) -> bool:
    return _fill(page, selector, value, log, label)


def _detect_auth_error(text: str, url: str = "") -> Optional[str]:
    source = text or ""
    lower = source.lower()
    resolved_url = (url or "").lower()
    needles = [
        ("wrong password", "wrong password"),
        ("invalid password", "wrong password"),
        ("incorrect password", "wrong password"),
        ("密码错误", "wrong password"),
        ("wrong email", "wrong email"),
        ("invalid email", "wrong email"),
        ("attention required", "cloudflare challenge/block"),
        ("access denied", "cloudflare blocked / access denied"),
        ("unable to access", "cloudflare blocked / unable to access"),
        ("rate limited", "cloudflare rate limited"),
        ("try again later", "cloudflare rate limited"),
    ]
    for needle, message in needles:
        if needle in lower or needle in source:
            return message
    if "auth.grok.com/set-cookie" in resolved_url and (
        "blocked" in lower or "unable to access" in lower or "cloudflare" in lower
    ):
        return "cloudflare blocked on set-cookie"
    return None


def approve_device_code(
    page: Any,
    verification_uri_complete: str,
    email: str,
    password: str,
    user_code: str = "",
    timeout_sec: float = 240.0,
    stop_event: Optional[threading.Event] = None,
    log: Optional[LogFn] = None,
) -> None:
    logger = log or _noop_log
    if page is None:
        raise BrowserConfirmError("page is None")
    email = (email or "").strip()
    password = password or ""
    if not email or not password:
        raise BrowserConfirmError("email/password required")
    if not user_code and "user_code=" in (verification_uri_complete or ""):
        try:
            user_code = verification_uri_complete.split("user_code=", 1)[1].split("&", 1)[0]
        except Exception:
            user_code = ""
    logger("open device url: %s" % verification_uri_complete)
    try:
        page.get(verification_uri_complete, timeout=60)
    except TypeError:
        page.get(verification_uri_complete)
    _sleep(2.0)

    deadline = time.time() + float(timeout_sec)
    phase = "device"
    login_attempts = 0
    last_url = ""

    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            logger("stop_event set — leave browser loop")
            return
        url = _page_url(page)
        text = _visible_text(page)
        if url != last_url:
            logger("url: %s" % url[:180])
            last_url = url
            snippet = _norm(text)[:160]
            if snippet:
                logger("visible: %s" % snippet)
        auth_error = _detect_auth_error(text, url)
        if auth_error:
            shot = None
            if "block" in auth_error or "cloudflare" in auth_error or "access denied" in auth_error:
                shot = _save_debug_shot(page, tag="cf-block", email=email, log=logger)
            message = auth_error
            if shot:
                message = "%s shot=%s" % (auth_error, shot)
            raise BrowserConfirmError("auth failed: %s" % message)
        if "device/done" in url or "设备已授权" in text or "device authorized" in text.lower():
            logger("device done page — waiting for token poll")
            _sleep(1.5)
            continue
        if "Invalid action" in text:
            page.get(verification_uri_complete)
            _sleep(2.0)
            phase = "device"
            continue
        if _cookie_banner_visible(text):
            if _dismiss_cookie_banner(page, logger):
                _sleep(0.6)
                continue
        if "/consent" in url or "授权 Grok Build" in text or "Authorize Grok Build" in text:
            phase = "consent"
            if _cookie_banner_visible(_visible_text(page)):
                _dismiss_cookie_banner(page, logger)
                _sleep(0.6)
                continue
            if _click_exact(page, ["允许", "Allow", "Authorize", "Approve"], logger, real=True):
                _sleep(2.5)
                continue
            try:
                page.run_js(
                    """
                    const forms = Array.from(document.querySelectorAll('form'));
                    const form = forms.find((x) => {
                      const text = (x.innerText || '');
                      return text.includes('Grok Build') || text.includes('允许') || text.includes('Allow');
                    }) || document.querySelector('form');
                    if (!form) return;
                    const formText = (form.innerText || '');
                    if (formText.includes('隐私偏好') || formText.includes('全部允许') || /cookie/i.test(formText)) return;
                    let actionInput = form.querySelector('input[name=action]');
                    if (!actionInput) {
                      actionInput = document.createElement('input');
                      actionInput.type = 'hidden';
                      actionInput.name = 'action';
                      form.appendChild(actionInput);
                    }
                    actionInput.value = 'allow';
                    const button = [...form.querySelectorAll('button')].find((b) => {
                      const text = (b.innerText || '').trim();
                      return text === '允许' || text === 'Allow' || text === 'Authorize' || text === 'Approve';
                    });
                    if (button) button.click();
                    else form.submit();
                    """
                )
                _sleep(2.5)
            except Exception as exc:
                logger("consent fallback failed: %s" % exc)
            continue
        if page.ele("css:input[name='user_code']", timeout=0.3) and "consent" not in url:
            phase = "device"
            if user_code:
                try:
                    user_code_input = page.ele("css:input[name='user_code']")
                    current = (user_code_input.value or "") if user_code_input else ""
                    if user_code.replace("-", "") not in current.replace("-", ""):
                        user_code_input.clear()
                        user_code_input.input(user_code)
                        logger("filled user_code")
                except Exception:
                    pass
            if _click_exact(page, ["继续", "Continue"], logger, real=False):
                _sleep(2.0)
                continue
            try:
                submit = page.ele("css:button[type='submit']", timeout=0.5)
                if submit:
                    submit.click(by_js=True)
                    _sleep(2.0)
                    continue
            except Exception:
                pass
        if "正在重定向" in text or ("/account" in url and "sign-in" not in url):
            if _click_exact(page, ["继续", "Continue"], logger, real=False):
                _sleep(2.0)
                continue
        if _cookie_banner_visible(text):
            _dismiss_cookie_banner(page, logger)
            _sleep(0.4)
        if "使用邮箱登录" in text or "Continue with email" in text:
            if _click_exact(page, ["使用邮箱登录", "Continue with email", "Sign in with email"], logger, real=False):
                _sleep(1.5)
                phase = "email"
                continue
        if page.ele("css:input[type='email']", timeout=0.3) and not page.ele("css:input[type='password']", timeout=0.2):
            phase = "email"
            _fill(page, "css:input[type='email']", email, logger, "email")
            if _click_exact(page, ["下一步", "Next", "Continue", "继续"], logger, real=False):
                _sleep(1.8)
                continue
        if page.ele("css:input[type='password']", timeout=0.3):
            phase = "password"
            if login_attempts >= 3:
                auth_error = _detect_auth_error(text, url) or "login failed after retries (still on password page)"
                raise BrowserConfirmError("auth failed: %s" % auth_error)
            login_attempts += 1
            _fill(page, "css:input[type='email']", email, logger, "email")
            _wait_turnstile(page, logger, 25, email=email, raise_on_timeout=True)
            _fill(page, "css:input[type='password']", password, logger, "password")
            _wait_turnstile(page, logger, 12, email=email, raise_on_timeout=False)
            if not _click_exact(page, ["登录", "Sign in", "Log in"], logger, real=True):
                try:
                    submit = page.ele("css:button[type='submit']", timeout=0.5) or page.ele("css:button[data-testid='sign-in-submit']", timeout=0.5)
                    if submit:
                        submit.click()
                except Exception as exc:
                    logger("login submit fail: %s" % exc)
            for _ in range(20):
                if stop_event is not None and stop_event.is_set():
                    return
                _sleep(0.5)
                post = _visible_text(page)
                auth_error = _detect_auth_error(post, _page_url(page))
                if auth_error:
                    raise BrowserConfirmError("auth failed: %s" % auth_error)
                if not page.ele("css:input[type='password']", timeout=0.2):
                    break
                if "sign-in" not in _page_url(page):
                    break
            post = _visible_text(page)
            auth_error = _detect_auth_error(post, _page_url(page))
            if auth_error:
                raise BrowserConfirmError("auth failed: %s" % auth_error)
            if page.ele("css:input[type='password']", timeout=0.2) and (_is_turnstile_challenge(post) or login_attempts >= 2):
                shot = _save_debug_shot(page, tag="login-stuck-turnstile", email=email, log=logger)
                message = "turnstile/login stuck after submit"
                if shot:
                    message = "%s shot=%s" % (message, shot)
                raise BrowserConfirmError("auth failed: %s" % message)
            continue
        _sleep(1.0)

    if stop_event is not None and stop_event.is_set():
        return
    shot = _save_debug_shot(page, tag="timeout-phase-%s" % phase, email=email, log=logger)
    message = "browser confirm timeout phase=%s login_attempts=%s" % (phase, login_attempts)
    if shot:
        message = "%s shot=%s" % (message, shot)
    if phase in ("password", "email") or _is_turnstile_challenge(_visible_text(page)):
        raise BrowserConfirmError("auth failed: %s" % message)
    raise BrowserConfirmError(message)


def mint_with_browser(
    email: str,
    password: str,
    page: Optional[Any] = None,
    proxy: Optional[str] = None,
    headless: bool = False,
    browser_timeout_sec: float = 240.0,
    poll_log: Optional[LogFn] = None,
    cancel: Optional[Callable[[], bool]] = None,
    force_standalone: bool = True,
    cookies: Any = None,
    reuse_browser: bool = True,
    recycle_every: int = 15,
    request_timeout_sec: float = 15.0,
    poll_timeout_sec: float = 15.0,
):
    from .oauth_device import OAuthDeviceError, poll_device_token, request_device_code
    from .proxyutil import proxy_log_label, resolve_proxy, set_runtime_proxy

    logger = poll_log or _noop_log
    own_browser = None
    owned = False
    work_page = None if force_standalone else page
    resolved = resolve_proxy(proxy)
    set_runtime_proxy(resolved or None)
    success = False
    try:
        session = request_device_code(
            proxy=resolved or None,
            timeout=float(request_timeout_sec),
            cancel=cancel,
            retries=2,
        )
        logger("device user_code=%s expires_in=%s proxy=%s" % (session.user_code, session.expires_in, proxy_log_label(resolved) or "(none)"))
        if work_page is None:
            own_browser, work_page, owned = acquire_mint_browser(
                proxy=resolved or None,
                headless=headless,
                reuse=reuse_browser,
                recycle_every=recycle_every,
                log=logger,
            )
        if cookies:
            injected = inject_cookies(work_page, cookies, log=logger)
            logger("cookie inject count=%s" % injected)
            try:
                work_page.get("https://accounts.x.ai/")
                _sleep(1.0)
                logger("post-inject session url=%s visible=%s" % (_page_url(work_page)[:120], _norm(_visible_text(work_page))[:120]))
            except Exception as exc:
                logger("post-inject check: %s" % exc)
        stop_event = threading.Event()
        token_box = {}
        error_box = {}

        def combined_cancel():
            return stop_event.is_set() or bool(cancel and cancel())

        def _poll() -> None:
            try:
                for _ in range(20):
                    if combined_cancel():
                        raise OAuthDeviceError("cancelled")
                    time.sleep(0.1)
                result = poll_device_token(
                    session.device_code,
                    token_endpoint=session.token_endpoint,
                    interval=max(session.interval, 5),
                    expires_in=min(session.expires_in, int(browser_timeout_sec) + 60),
                    log=logger,
                    cancel=combined_cancel,
                    proxy=resolved or None,
                    timeout=float(poll_timeout_sec),
                )
                token_box["token"] = result
                stop_event.set()
                logger("token poll SUCCESS — stop_event set")
            except Exception as exc:
                error_box["err"] = exc
                stop_event.set()

        thread = threading.Thread(target=_poll, name="oauth-poll", daemon=True)
        thread.start()
        try:
            approve_device_code(
                work_page,
                verification_uri_complete=session.verification_uri_complete,
                email=email,
                password=password,
                user_code=session.user_code,
                timeout_sec=browser_timeout_sec,
                stop_event=stop_event,
                log=logger,
            )
        except BrowserConfirmError as exc:
            lower = str(exc).lower()
            hard = (
                "auth failed" in lower
                or "turnstile" in lower
                or "cloudflare" in lower
                or "blocked" in lower
                or "access denied" in lower
                or "password" in lower
                or "browser confirm timeout" in lower
            )
            if hard:
                stop_event.set()
                thread.join(timeout=5)
                if thread.is_alive():
                    logger("token poll thread did not stop within 5s after browser failure")
                raise
        thread.join(timeout=max(browser_timeout_sec, 60) + 30)
        if thread.is_alive():
            stop_event.set()
            thread.join(timeout=5)
            if thread.is_alive():
                raise OAuthDeviceError("token poll thread did not stop after timeout")
        if "token" in token_box:
            token_result = token_box["token"]
            success = True
            return {
                "access_token": token_result.access_token,
                "refresh_token": token_result.refresh_token,
                "id_token": token_result.id_token,
                "token_type": token_result.token_type,
                "expires_in": token_result.expires_in,
                "user_code": session.user_code,
                "token_endpoint": session.token_endpoint,
            }
        if "err" in error_box:
            raise error_box["err"]
        raise OAuthDeviceError("token poll thread ended without result")
    finally:
        if own_browser is not None:
            if owned:
                close_standalone(own_browser)
            else:
                release_mint_browser(owned=False, success=success, log=logger)
