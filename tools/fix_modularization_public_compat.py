#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().with_name("apply_full_safe_modularization.py")
text = path.read_text(encoding="utf-8")


def replace_once(source, old, new, label):
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return source.replace(old, new, 1)


# Keep monkeypatch compatibility for generate_username after moving mail code.
text = replace_once(
    text,
    '''def _bind_mail_service():
    _mail_service.bind_runtime(globals())
''',
    '''def _bind_mail_service():
    _mail_service.bind_runtime(globals())
    _current = globals().get("generate_username")
    _standard = _MAIL_COMPAT_PROXIES.get("generate_username")
    if _current is not None and _current is not _standard:
        _mail_service.generate_username = _current
    elif _standard is not None:
        _mail_service.generate_username = _MAIL_ORIGINALS["generate_username"]
''',
    "mail binder",
)

text = replace_once(
    text,
    '''for _name in {MAIL_NAMES!r}:
    globals()[_name] = _make_compat_proxy(_mail_service, _name, _bind_mail_service)
for _name in {REGISTRATION_NAMES!r}:
''',
    '''_MAIL_ORIGINALS = dict((name, getattr(_mail_service, name)) for name in {MAIL_NAMES!r})
_MAIL_COMPAT_PROXIES = dict()
for _name in {MAIL_NAMES!r}:
    _proxy = _make_compat_proxy(_mail_service, _name, _bind_mail_service)
    _MAIL_COMPAT_PROXIES[_name] = _proxy
    globals()[_name] = _proxy
for _name in {REGISTRATION_NAMES!r}:
''',
    "mail proxy registry",
)

# Preserve cf_mail_debug.requests and create_address for tests/external callers.
text = replace_once(
    text,
    '''import argparse
import time

from mail_service import CloudflareMailClient, extract_verification_code


def main():
''',
    '''import argparse
import time

from curl_cffi import requests
from mail_service import CloudflareMailClient, extract_verification_code


def create_address(api_base, auth_mode="none", api_key="", create_path="/api/new_address",
                   domain="", name="", timeout=20):
    import mail_service as _mail_service
    client = CloudflareMailClient(
        api_base, auth_mode=auth_mode, api_key=api_key,
        create_path=create_path, timeout=timeout,
    )
    original_requests = _mail_service.requests
    _mail_service.requests = requests
    try:
        return client.create_address(domain=domain, name=name)
    finally:
        _mail_service.requests = original_requests


def main():
''',
    "debug public API",
)

text = replace_once(
    text,
    '''        address, credential = client.create_address(domain=args.domain, name=args.name)
''',
    '''        address, credential = create_address(
            args.api_base,
            auth_mode=args.auth_mode,
            api_key=args.api_key,
            create_path=args.create_path,
            domain=args.domain,
            name=args.name,
        )
''',
    "debug create call",
)

# External cpa_xai packages may use relative imports; register before execution.
text = replace_once(
    text,
    "import shutil\nimport time\nfrom pathlib import Path\n",
    "import shutil\nimport sys\nimport time\nfrom pathlib import Path\n",
    "cpa sys import",
)
text = replace_once(
    text,
    '''    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.mint_and_export
''',
    '''    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module.mint_and_export
''',
    "cpa external package load",
)

path.write_text(text, encoding="utf-8")
print("public compatibility patch applied")
