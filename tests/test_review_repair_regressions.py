"""Focused regression tests for cross-module reliability fixes."""
import os
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import account_outputs
import cpa_export
import proxy_pool_v3
import registration_flow
from cpa_xai import browser_confirm, browser_session, oauth_device, proxyutil
from registration_parallel import load_isolated_module


def test_runtime_startup_failure_cools_down_then_recovers():
    manager = proxy_pool_v3.ProxyPoolManager({
        "proxy_mode": "single", "proxy": "http://127.0.0.1:8888",
        "proxy_pool_probe_interval_sec": 0,
    })
    try:
        node = next(iter(manager._nodes.values()))
        node.inflight = 1
        manager._rollback_runtime_failure(node.id, RuntimeError("temporary backend start failure"))
        assert node.enabled
        assert node.inflight == 0
        assert node.cooldown_until > 0
        node.cooldown_until = 0
        assert node in manager._eligible_locked(proxy_pool_v3.time.time())
    finally:
        manager.shutdown()


def test_permanent_runtime_configuration_error_stays_disabled():
    manager = proxy_pool_v3.ProxyPoolManager({
        "proxy_mode": "single", "proxy": "http://127.0.0.1:8888",
        "proxy_pool_probe_interval_sec": 0,
    })
    try:
        node = next(iter(manager._nodes.values()))
        manager._rollback_runtime_failure(node.id, RuntimeError("unsupported proxy scheme"))
        assert not node.enabled
        assert node.configuration_failures == 1
    finally:
        manager.shutdown()


def test_pre_submit_navigation_failure_retries_without_aborting_batch():
    calls = {"open": 0}
    class Cancelled(Exception):
        pass
    class RetryNeeded(Exception):
        pass

    def open_page():
        calls["open"] += 1
        if calls["open"] == 1:
            raise registration_flow.RecoverablePreSubmitError("lost navigation context")

    ops = registration_flow.RegistrationOperations(
        start_browser=lambda: None, restart_browser=lambda: None,
        browser_missing=lambda: False, open_signup_page=open_page,
        fill_email_and_submit=lambda: ("user@example.com", "token"),
        save_mail_credential=lambda *_: True,
        fill_code_and_submit=lambda *_: "123456",
        fill_profile_and_submit=lambda: {"password": "pw"},
        wait_for_sso_cookie=lambda: "sso",
        enable_nsfw=lambda *_: (True, "ok"),
        persist_account_line=lambda *_: None,
        queue_unsaved_result=lambda *_: True,
        add_tokens=lambda *_: {},
        export_cpa=lambda *_: {"ok": True},
        cleanup=lambda *_: None, sleep=lambda *_: None,
        cancelled_exception=Cancelled, retry_exception=RetryNeeded,
        preflight_mail=lambda: True,
    )
    callbacks = registration_flow.RegistrationCallbacks(log=lambda _: None, cancelled=lambda: False)
    with patch.object(registration_flow, "begin_registration_slot", return_value=None), \
         patch.object(registration_flow, "end_registration_slot", return_value=None):
        batch = registration_flow.run_batch(1, callbacks, lambda *_: None, ops, enable_nsfw=False)
    assert calls["open"] == 2
    assert batch.success_count == 1
    assert batch.fail_count == 0


def test_cpa_default_direct_mode_ignores_configured_proxy():
    with patch.object(cpa_export, "current_proxy_url", return_value=None):
        settings = cpa_export.CpaExportSettings.from_config({
            "proxy_mode": "direct", "proxy": "http://stale-proxy:8080", "cpa_proxy": "",
        })
        assert settings.proxy == ""
        explicit = cpa_export.CpaExportSettings.from_config({
            "proxy_mode": "direct", "proxy": "http://stale-proxy:8080",
            "cpa_proxy": "http://intentional:8080",
        })
        assert explicit.proxy == "http://intentional:8080"


def test_cpa_explicit_empty_proxy_ignores_ambient_environment():
    with patch.dict(os.environ, {"HTTPS_PROXY": "http://environment:9000"}):
        assert proxyutil.resolve_proxy("") == ""
        with patch.object(oauth_device.urllib.request, "build_opener") as build:
            oauth_device._build_opener("")
        handler = build.call_args.args[0]
        assert isinstance(handler, oauth_device.urllib.request.ProxyHandler)
        assert handler.proxies == {}


def test_session_storage_is_cleared_for_all_authenticated_origins():
    calls = []
    class Cookies:
        def clear(self):
            calls.append(("cookies",))
    new_page = object()
    page = SimpleNamespace(
        run_cdp=lambda *args, **kwargs: calls.append(("cdp", args[0], kwargs["origin"])),
        close=lambda: calls.append(("close-old-tab",)),
    )
    browser = SimpleNamespace(
        set=SimpleNamespace(cookies=Cookies()),
        new_tab=lambda: calls.append(("new-tab",)) or new_page,
    )
    assert browser_session.clear_page_session(page, browser) is new_page
    origins = [entry[2] for entry in calls if entry[0] == "cdp"]
    assert origins == ["https://accounts.x.ai", "https://auth.x.ai", "https://grok.com"]
    assert calls[-3:] == [("cookies",), ("new-tab",), ("close-old-tab",)]


def test_session_cleanup_failure_recreates_cached_browser():
    old_browser = object()
    old_page = SimpleNamespace(run_cdp=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("CDP lost")))
    new_browser, new_page = object(), object()
    browser_session._mint_tls.state = {
        "browser": old_browser, "page": old_page, "served": 1,
        "proxy": "http://proxy:8080", "headless": False,
    }
    try:
        with patch.object(browser_session, "close_standalone") as close, \
             patch.object(browser_session, "create_standalone_page", return_value=(new_browser, new_page)) as create:
            browser, page, owned = browser_session.acquire_mint_browser(proxy="http://proxy:8080")
        assert (browser, page, owned) == (new_browser, new_page, False)
        close.assert_called_once_with(old_browser)
        create.assert_called_once()
    finally:
        del browser_session._mint_tls.state


def test_browser_reuse_keys_on_stable_upstream_not_ephemeral_bridge():
    browser, page = object(), object()
    browser_session._mint_tls.state = {
        "browser": browser, "page": page, "served": 1,
        "proxy": "socks5://proxy:1080", "headless": False,
    }
    try:
        with patch.object(browser_session, "clear_page_session", return_value=page) as clear, \
             patch.object(browser_session, "create_standalone_page") as create:
            result = browser_session.acquire_mint_browser(proxy="socks5://proxy:1080")
        assert result == (browser, page, False)
        clear.assert_called_once()
        create.assert_not_called()
    finally:
        del browser_session._mint_tls.state


def test_oauth_poll_thread_exits_on_unexpected_browser_exception():
    session = SimpleNamespace(
        user_code="ABC", expires_in=120, interval=5, device_code="device",
        token_endpoint="https://auth.x.ai/oauth/token",
        verification_uri_complete="https://accounts.x.ai/device",
    )
    before = {t.ident for t in threading.enumerate() if t.name == "oauth-poll"}
    with patch.object(oauth_device, "request_device_code", return_value=session), \
         patch.object(proxyutil, "prepare_http_compatible_proxy", return_value=("", None)), \
         patch.object(browser_confirm, "acquire_mint_browser", return_value=(object(), object(), True)), \
         patch.object(browser_confirm, "approve_device_code", side_effect=RuntimeError("browser disconnected")), \
         patch.object(browser_confirm, "close_standalone") as close:
        with pytest.raises(RuntimeError, match="browser disconnected"):
            browser_confirm.mint_with_browser("user@example.com", "pw", proxy="")
    close.assert_called_once()
    after = {t.ident for t in threading.enumerate() if t.name == "oauth-poll"}
    assert not (after - before)


def test_mail_credential_cache_not_stale_after_external_append():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "mail_credentials.txt"
        account_outputs.save_mail_credential(folder, "first@example.com", "secret1")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("external@example.com\tsecret2\n")
        account_outputs.save_mail_credential(folder, "first@example.com", "secret1")
        account_outputs.save_mail_credential(folder, "external@example.com", "secret2")
        account_outputs.save_mail_credential(folder, "third@example.com", "secret3")
        assert path.read_text(encoding="utf-8").splitlines() == [
            "first@example.com\tsecret1",
            "external@example.com\tsecret2",
            "third@example.com\tsecret3",
        ]


def test_worker_runtime_bindings_ignore_unrelated_globals():
    with tempfile.TemporaryDirectory() as folder:
        path = Path("mail_service.py").resolve()
        mail = load_isolated_module(path, "_test_review_mail_runtime")
        mail.bind_runtime({"config": {"email_provider": "duckmail"}, "unexpected_secret": "x"})
        assert not hasattr(mail, "unexpected_secret")
        browser = load_isolated_module(Path("registration_browser.py").resolve(), "_test_review_browser_runtime")
        browser.bind_runtime({"unexpected_secret": "x", "config": {}})
        assert not hasattr(browser, "unexpected_secret")
