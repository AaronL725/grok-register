import pathlib

import registration_parallel


def test_isolated_mail_module_receives_shared_domain_allocator():
    allocator = registration_parallel.DomainAllocator()
    runtime = {
        "config": {
            "defaultDomains": "a.example,b.example,c.example",
            "cloudmail_domains": "x.example,y.example",
        },
        "domain_allocator": allocator,
    }
    first = registration_parallel.load_isolated_module(
        pathlib.Path("mail_service.py"), "_test_mail_allocator_one"
    )
    second = registration_parallel.load_isolated_module(
        pathlib.Path("mail_service.py"), "_test_mail_allocator_two"
    )
    first.bind_runtime(runtime)
    second.bind_runtime(runtime)
    assert first.cloudflare_next_default_domain() == "a.example"
    assert second.cloudflare_next_default_domain() == "b.example"
    assert first.cloudmail_next_domain() == "x.example"
    assert second.cloudmail_next_domain() == "y.example"


def test_sso_wait_source_resets_consecutive_error_streak_after_successful_poll():
    source = pathlib.Path("registration_browser.py").read_text(encoding="utf-8")
    start = source.index("def wait_for_sso_cookie(")
    block = source[start:]
    assert 'last_consecutive_error_message = ""' in block
    assert "# A completed polling iteration breaks any previous exception streak." in block
    assert "if message == last_consecutive_error_message:" in block


def test_sing_box_start_retries_after_early_exit_and_cleans_failed_config(monkeypatch, tmp_path):
    import proxy_protocol_runtime as runtime_mod
    from types import SimpleNamespace

    manager = runtime_mod.ProtocolRuntimeManager({"proxy_protocol_start_timeout_sec": 3})
    ports = iter([21001, 21002])
    paths = []

    class FakeProcess:
        def __init__(self, exit_code):
            self.exit_code = exit_code
        def poll(self):
            return self.exit_code
        def terminate(self):
            pass
        def wait(self, timeout=None):
            return self.exit_code
        def kill(self):
            self.exit_code = -9

    processes = iter([FakeProcess(1), FakeProcess(None)])
    monkeypatch.setattr(manager, "_find_executable", lambda: "/fake/sing-box")
    monkeypatch.setattr(manager, "_free_port", lambda: next(ports))
    monkeypatch.setattr(manager, "_build_config", lambda descriptor, port: {"port": port})

    def write_config(value):
        path = tmp_path / f"proxy-{value['port']}.json"
        path.write_text("{}", encoding="utf-8")
        paths.append(path)
        return str(path)

    monkeypatch.setattr(manager, "_write_config", write_config)
    monkeypatch.setattr(manager, "_check_config", lambda executable, path: None)
    monkeypatch.setattr(runtime_mod.subprocess, "Popen", lambda *args, **kwargs: next(processes))
    monkeypatch.setattr(manager, "_port_ready", lambda port: port == 21002)

    descriptor = SimpleNamespace(node_id="node-1", protocol="vmess")
    entry = manager._start_entry(descriptor)
    try:
        assert entry.port == 21002
        assert len(paths) == 2
        assert not paths[0].exists()
        assert paths[1].exists()
    finally:
        manager._stop_entry(entry)
    assert not paths[1].exists()


def test_strict_commit_boundaries_and_sso_exception_whitelist():
    source = pathlib.Path("registration_browser.py").read_text(encoding="utf-8")
    email = source[source.index("def fill_email_and_submit("):source.index("def fill_code_and_submit(")]
    email_ready = email.index("ready_to_submit = page.run_js")
    email_stage = email.index('_mark_registration_stage("email_submit")', email_ready)
    email_commit = email.index("clicked = page.run_js", email_stage)
    assert email_ready < email_stage < email_commit
    assert email.count('_mark_registration_stage("email_submit")') == 1

    code = source[source.index("def fill_code_and_submit("):source.index("def getTurnstileToken(")]
    assert code.index("ready = page.run_js") < code.index('_mark_registration_stage("code_submit")') < code.index("filled = page.run_js")
    assert code.count('_mark_registration_stage("code_submit")') == 1

    profile = source[source.index("def fill_profile_and_submit("):source.index("def wait_for_sso_cookie(")]
    assert profile.index('if submit_state == "ready-to-submit":') < profile.index('_mark_registration_stage("profile_submit")')
    assert profile.count('_mark_registration_stage("profile_submit")') == 1

    sso = source[source.index("def wait_for_sso_cookie("):]
    assert "except (ContextLostError, JavaScriptError) as exc:" in sso
    assert "except Exception:\n            raise" in sso
