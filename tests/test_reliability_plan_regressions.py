import pathlib

import pytest

import app_config
import browser_runtime
import mail_service
import registration_parallel
from registration_flow import BatchResult


def test_parallel_aggregate_preserves_uncertain_count():
    one = BatchResult(uncertain_count=2, fail_count=2, processed_count=2)
    two = BatchResult(uncertain_count=1, fail_count=1, processed_count=1)
    total = registration_parallel._aggregate({1: registration_parallel._summary_copy(one), 2: registration_parallel._summary_copy(two)})
    assert total.uncertain_count == 3


def test_domain_allocator_is_process_shared_and_provider_scoped():
    allocator = registration_parallel.DomainAllocator()
    assert [allocator.next("cf", ["a", "b", "c"]) for _ in range(4)] == ["a", "b", "c", "a"]
    assert allocator.next("cloudmail", ["x", "y"]) == "x"


def test_query_key_has_params_but_no_bearer(monkeypatch):
    mail_service.config = {"cloudflare_api_key": "secret", "cloudflare_auth_mode": "query-key"}
    assert mail_service.cloudflare_apply_auth_params() == {"key": "secret"}
    assert "Authorization" not in mail_service.cloudflare_build_headers()
    client = mail_service.CloudflareMailClient("https://example.test", auth_mode="query-key", api_key="secret")
    assert client.build_auth_params() == {"key": "secret"}
    assert "Authorization" not in client.build_auth_headers()


def test_unknown_config_key_rejected():
    with pytest.raises(app_config.ConfigError):
        app_config.validate_config_structure({"multi_thread_worker": 8})


def test_http_post_is_not_replayed_by_default(monkeypatch):
    browser_runtime._config = {"proxy_mode": "auto", "proxy": "http://proxy.invalid:1"}
    calls = []
    def fail(url, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("proxy connection refused")
    monkeypatch.setattr(browser_runtime.requests, "post", fail)
    monkeypatch.setattr(browser_runtime, "is_proxy_connection_error", lambda exc: True)
    with pytest.raises(Exception):
        browser_runtime.http_post("https://example.test", json={"x": 1})
    assert len(calls) == 1


def test_preflight_source_contains_usable_semantics():
    source = pathlib.Path("proxy_pool_v3.py").read_text(encoding="utf-8")
    assert '"usable": usable' in source
    assert 'all(item["usable"] for item in results)' in source


def test_web_ui_renders_uncertain():
    source = pathlib.Path("web/index.html").read_text(encoding="utf-8")
    assert 'id="sUncertain"' in source
    assert 's.uncertain' in source


def test_all_modes_use_stage_aware_batch_engine():
    source = pathlib.Path("registration_flow.py").read_text(encoding="utf-8")
    tail = source[source.index("def run_batch("):]
    assert "return _run_batch_managed" in tail
    assert "return _run_batch_legacy" not in tail
