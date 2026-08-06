"""Run one interactive xAI Build Authorization Code + PKCE probe."""

import argparse
import base64
import hashlib
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from curl_cffi import requests

from cpa_xai.schema import build_cpa_xai_auth, jwt_payload
from cpa_xai.writer import write_cpa_xai_auth


CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
AUTHORIZE_URL = "https://auth.x.ai/oauth2/authorize"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
REDIRECT_URI = "http://127.0.0.1:56121/callback"
SCOPE = (
    "openid profile email offline_access grok-cli:access api:access "
    "conversations:read conversations:write"
)


def _b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def build_authorize_url(state, nonce, challenge):
    return AUTHORIZE_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "plan": "generic",
        "referrer": "grok-build",
    })


class CallbackState:
    def __init__(self):
        self.event = threading.Event()
        self.params = {}


def make_handler(callback_state):
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            callback_state.params = {
                key: values[0] if values else ""
                for key, values in parse_qs(parsed.query).items()
            }
            callback_state.event.set()
            body = "xAI Build authorization received. You can return to Codex."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, _format, *_args):
            return

    return CallbackHandler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    proxy = str(config.get("proxy", "") or "").strip()
    auth_dir = str(config.get("cpa_auth_dir", "./cpa_auths") or "./cpa_auths")
    base_url = str(config.get("cpa_base_url", "") or "").strip()

    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_hex(32)
    nonce = secrets.token_hex(16)
    callback_state = CallbackState()
    server = HTTPServer(("127.0.0.1", 56121), make_handler(callback_state))
    server.timeout = 0.5

    def serve_until_callback():
        deadline = time.time() + args.timeout
        while not callback_state.event.is_set() and time.time() < deadline:
            server.handle_request()

    thread = threading.Thread(target=serve_until_callback, daemon=True)
    thread.start()
    print("AUTH_URL=" + build_authorize_url(state, nonce, challenge), flush=True)

    thread.join(args.timeout + 2)
    server.server_close()
    if not callback_state.event.is_set():
        print("PKCE_OK=False", flush=True)
        print("PKCE_REASON=callback_timeout", flush=True)
        return 2

    params = callback_state.params
    if params.get("state") != state:
        print("PKCE_OK=False", flush=True)
        print("PKCE_REASON=state_mismatch", flush=True)
        return 3
    if params.get("error"):
        print("PKCE_OK=False", flush=True)
        print("PKCE_REASON=" + str(params.get("error")), flush=True)
        return 4
    code = str(params.get("code", "") or "").strip()
    if not code:
        print("PKCE_OK=False", flush=True)
        print("PKCE_REASON=missing_code", flush=True)
        return 5

    request_options = {"timeout": 30, "impersonate": "chrome"}
    if proxy:
        request_options["proxies"] = {"http": proxy, "https": proxy}
    response = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    }, headers={
        "Accept": "*/*",
        "User-Agent": "grok-pager/0.2.93 grok-shell/0.2.93 (windows; x86_64)",
        "X-Grok-Client-Version": "0.2.93",
    }, **request_options)
    if response.status_code != 200:
        print("PKCE_OK=False", flush=True)
        print("PKCE_REASON=token_http_" + str(response.status_code), flush=True)
        return 6

    tokens = response.json()
    access_token = str(tokens.get("access_token", "") or "")
    refresh_token = str(tokens.get("refresh_token", "") or "")
    claims = jwt_payload(access_token) if access_token else {}
    referrer = str(claims.get("referrer", "") or "")
    email = str(claims.get("email", "") or "")
    ok = bool(access_token and refresh_token and referrer == "grok-build")
    print("PKCE_OK=" + str(ok), flush=True)
    print("PKCE_HAS_REFRESH=" + str(bool(refresh_token)), flush=True)
    print("PKCE_REFERRER_OK=" + str(referrer == "grok-build"), flush=True)
    if not ok:
        return 7

    payload = build_cpa_xai_auth(
        email=email,
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=tokens.get("id_token"),
        expires_in=tokens.get("expires_in"),
        base_url=base_url,
        token_endpoint=TOKEN_URL,
        redirect_uri=REDIRECT_URI,
    )
    destination = write_cpa_xai_auth(auth_dir, payload)
    print("PKCE_SAVED=" + str(bool(destination and os.path.exists(destination))), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
