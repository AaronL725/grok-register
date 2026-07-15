"""协调浏览器授权、OAuth 轮询和 CPA 凭证导出流程。"""

from .browser_confirm import mint_with_browser
from .schema import DEFAULT_BASE_URL, build_cpa_xai_auth
from .writer import write_cpa_xai_auth


def mint_and_export(
    email,
    password,
    auth_dir,
    page=None,
    proxy=None,
    headless=False,
    base_url=DEFAULT_BASE_URL,
    browser_timeout_sec=240.0,
    force_standalone=True,
    cookies=None,
    reuse_browser=True,
    recycle_every=15,
    log=None,
    cancel=None,
    request_timeout_sec=15.0,
    poll_timeout_sec=15.0,
):
    logger = log or (lambda message: None)
    email = str(email or "").strip()
    password = str(password or "")
    if not email or not password:
        return {"ok": False, "email": email, "error": "missing email/password"}
    try:
        tokens = mint_with_browser(
            email=email,
            password=password,
            page=None if force_standalone else page,
            proxy=proxy,
            headless=bool(headless),
            browser_timeout_sec=float(browser_timeout_sec),
            poll_log=logger,
            cancel=cancel,
            force_standalone=bool(force_standalone),
            cookies=cookies,
            reuse_browser=bool(reuse_browser),
            recycle_every=int(recycle_every or 0),
            request_timeout_sec=float(request_timeout_sec),
            poll_timeout_sec=float(poll_timeout_sec),
        )
    except Exception as exc:
        logger("mint failed: %s" % exc)
        return {"ok": False, "email": email, "error": str(exc)}
    payload = build_cpa_xai_auth(
        email=email,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        id_token=tokens.get("id_token"),
        expires_in=tokens.get("expires_in"),
        base_url=base_url,
        token_endpoint=tokens.get("token_endpoint") or "",
    )
    path = write_cpa_xai_auth(auth_dir, payload)
    logger("wrote %s" % path)
    return {
        "ok": True,
        "email": email,
        "path": str(path),
        "user_code": tokens.get("user_code"),
        "base_url": str(base_url or DEFAULT_BASE_URL),
    }
