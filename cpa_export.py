"""Optional post-registration CPA/OIDC export hook."""

import os
import shutil
import sys
import time
from pathlib import Path


_ROOT = Path(__file__).resolve().parent
_DEFAULT_AUTH_DIR = _ROOT / "cpa_auths"


def _ensure_cpa_xai_on_path(tools_dir=None):
    if tools_dir:
        tools = Path(tools_dir).expanduser().resolve()
    else:
        env_value = str(os.environ.get("API_REVERSE_TOOLS") or "").strip()
        tools = Path(env_value).expanduser().resolve() if env_value else _ROOT
    if tools.name == "cpa_xai" and (tools / "__init__.py").is_file():
        tools = tools.parent
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    return tools


def export_cookies_from_page(page):
    if page is None:
        return []
    cookies = None
    for getter in (
        lambda: page.cookies(all_domains=True, all_info=True),
        lambda: page.cookies(all_domains=True),
        lambda: page.cookies(),
    ):
        try:
            cookies = getter()
            if cookies:
                break
        except TypeError:
            continue
        except Exception:
            continue
    if not cookies:
        try:
            browser = getattr(page, "browser", None)
            if browser is not None:
                cookies = browser.cookies()
        except Exception:
            cookies = None
    if isinstance(cookies, list):
        return [item for item in cookies if isinstance(item, dict)]
    return []


def export_cpa_xai_for_account(
    email,
    password,
    page=None,
    cookies=None,
    sso=None,
    config=None,
    log_callback=None,
    cancel_callback=None,
):
    cfg = dict(config or {})
    log = log_callback or (lambda message: None)
    if not bool(cfg.get("cpa_export_enabled", False)):
        return {"ok": False, "skipped": True, "reason": "disabled"}
    tools_dir = cfg.get("api_reverse_tools") or None
    _ensure_cpa_xai_on_path(tools_dir)
    try:
        from cpa_xai import mint_and_export
    except Exception as exc:
        log("[cpa] import cpa_xai failed: %s" % exc)
        return {"ok": False, "error": "import: %s" % exc}

    auth_dir = Path(cfg.get("cpa_auth_dir") or _DEFAULT_AUTH_DIR).expanduser()
    if not auth_dir.is_absolute():
        auth_dir = (_ROOT / auth_dir).resolve()
    hotload_dir_value = str(cfg.get("cpa_hotload_dir") or "").strip()
    hotload_dir = Path(hotload_dir_value).expanduser() if hotload_dir_value else None
    if hotload_dir is not None and not hotload_dir.is_absolute():
        hotload_dir = (_ROOT / hotload_dir).resolve()

    proxy = str(cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip()
    headless = bool(cfg.get("cpa_headless", False))
    timeout = float(cfg.get("cpa_mint_timeout_sec") or 300)
    initial_request_timeout = float(cfg.get("cpa_oidc_initial_timeout_sec") or 15)
    poll_request_timeout = float(cfg.get("cpa_oidc_poll_timeout_sec") or 12)
    base_url = str(cfg.get("cpa_base_url") or "https://cli-chat-proxy.grok.com/v1").strip()
    force_standalone = bool(cfg.get("cpa_force_standalone", True))
    cookie_inject = bool(cfg.get("cpa_mint_cookie_inject", True))

    use_cookies = cookies
    if use_cookies is None and cookie_inject and page is not None:
        use_cookies = export_cookies_from_page(page)
    if not cookie_inject:
        use_cookies = None
    elif sso:
        base = list(use_cookies) if isinstance(use_cookies, list) else []
        sso_value = str(sso).strip()
        for cookie_name in ("sso", "sso-rw"):
            for domain in (".x.ai", "accounts.x.ai", ".accounts.x.ai", "auth.x.ai", ".auth.x.ai", "grok.com", ".grok.com"):
                base.append(
                    {
                        "name": cookie_name,
                        "value": sso_value,
                        "domain": domain,
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                    }
                )
        use_cookies = base

    auth_dir.mkdir(parents=True, exist_ok=True)
    log("[cpa] mint OIDC for %s -> %s" % (email, auth_dir))

    def _log(message):
        log("[cpa] %s" % message)

    result = mint_and_export(
        email=email,
        password=password,
        auth_dir=auth_dir,
        page=None if force_standalone else page,
        proxy=proxy or None,
        headless=headless,
        base_url=base_url,
        browser_timeout_sec=timeout,
        initial_request_timeout_sec=initial_request_timeout,
        poll_request_timeout_sec=poll_request_timeout,
        force_standalone=force_standalone,
        cookies=use_cookies,
        reuse_browser=True,
        recycle_every=15,
        log=_log,
        cancel=cancel_callback,
    )
    if result.get("ok") and result.get("path") and bool(cfg.get("cpa_copy_to_hotload", False)) and hotload_dir:
        try:
            hotload_dir.mkdir(parents=True, exist_ok=True)
            source = Path(result["path"])
            target = hotload_dir / source.name
            shutil.copy2(str(source), str(target))
            try:
                os.chmod(str(target), 0o600)
            except Exception:
                pass
            result["hotload_path"] = str(target)
            log("[cpa] hotload copy -> %s" % target)
        except Exception as exc:
            result["cpa_copy_error"] = str(exc)
            log("[cpa] hotload copy failed: %s" % exc)
    if not result.get("ok"):
        fail_path = auth_dir / "cpa_auth_failed.txt"
        try:
            with open(str(fail_path), "a", encoding="utf-8") as handle:
                handle.write("%s----%s----%s\n" % (email, result.get("error") or "unknown", int(time.time())))
                handle.flush()
                os.fsync(handle.fileno())
        except Exception as exc:
            log("[cpa] failed to persist failure marker %s: %s" % (fail_path, exc))
    return result
