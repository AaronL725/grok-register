#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FastAPI Control Plane for grok-register."""

from __future__ import annotations

import collections
import hashlib
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

# Project root = parent of web/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)

import grok_register_ttk as engine  # noqa: E402

ACCESS_PASSWORD = (os.getenv("GROK_REGISTER_ACCESS_PASSWORD") or "").strip()
HOST = (os.getenv("GROK_REGISTER_HOST") or "127.0.0.1").strip()
PORT = int(os.getenv("GROK_REGISTER_PORT") or "8092")

WEB_DIR = Path(__file__).resolve().parent
INDEX_HTML = WEB_DIR / "index.html"

SECRET_FIELDS = {
    "duckmail_api_key",
    "cloudflare_api_key",
    "cloudmail_public_token",
    "yyds_api_key",
    "yyds_jwt",
    "grok2api_remote_app_key",
    "grok2api_remote_admin_password",
    "proxy",
    "cpa_proxy",
}

_sessions: Dict[str, float] = {}
_SESSION_TTL = 86400 * 7

_job_lock = threading.Lock()
_job_thread: Optional[threading.Thread] = None
_controller: Optional[engine.CliStopController] = None
_log_buffer: Deque[str] = collections.deque(maxlen=2000)
_log_seq = 0
_log_cond = threading.Condition()
_job_state: Dict[str, Any] = {
    "running": False,
    "success": 0,
    "fail": 0,
    "target": 0,
    "last_accounts_file": "",
    "started_at": None,
    "finished_at": None,
    "error": "",
}

app = FastAPI(title="grok-register Web Control Plane", version="1.0.0")


def _hms() -> str:
    return time.strftime("%H:%M:%S", time.localtime())


def _append_log(message: str) -> None:
    global _log_seq
    ts = _hms()
    line = f"[{ts}] {message}"
    with _log_cond:
        _log_buffer.append(line)
        _log_seq += 1
        _log_cond.notify_all()


def _mask_value(key: str, value: Any) -> Any:
    if key not in SECRET_FIELDS:
        return value
    s = "" if value is None else str(value)
    if not s:
        return ""
    if len(s) <= 6:
        return "*" * len(s)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def _public_config() -> Dict[str, Any]:
    engine.load_config()
    cfg = dict(engine.config)
    masked = {k: _mask_value(k, v) for k, v in cfg.items()}
    for key in SECRET_FIELDS:
        raw = cfg.get(key, "")
        masked[f"has_{key}"] = bool(str(raw or "").strip())
    return masked


def _require_auth(x_access_key: Optional[str]) -> None:
    if not ACCESS_PASSWORD:
        return
    key = (x_access_key or "").strip()
    if not key:
        raise HTTPException(status_code=401, detail="access key required")
    if key == ACCESS_PASSWORD:
        return
    exp = _sessions.get(key)
    if exp and exp > time.time():
        return
    if exp:
        _sessions.pop(key, None)
    raise HTTPException(status_code=403, detail="invalid access key")


def _issue_token(password: str) -> str:
    raw = f"{password}:{secrets.token_hex(16)}:{time.time()}"
    token = hashlib.sha256(raw.encode()).hexdigest()
    _sessions[token] = time.time() + _SESSION_TTL
    return token


class AuthBody(BaseModel):
    password: str = ""


class StartBody(BaseModel):
    count: int = Field(default=1, ge=1, le=2500)


class ConfigBody(BaseModel):
    email_provider: Optional[str] = None
    register_count: Optional[int] = None
    multi_thread_enabled: Optional[bool] = None
    multi_thread_workers: Optional[int] = None
    enable_nsfw: Optional[bool] = None
    proxy: Optional[str] = None
    user_agent: Optional[str] = None
    defaultDomains: Optional[str] = None

    cloudflare_api_base: Optional[str] = None
    cloudflare_api_key: Optional[str] = None
    cloudflare_auth_mode: Optional[str] = None
    cloudflare_path_domains: Optional[str] = None
    cloudflare_path_accounts: Optional[str] = None
    cloudflare_path_token: Optional[str] = None
    cloudflare_path_messages: Optional[str] = None

    duckmail_api_key: Optional[str] = None

    cloudmail_api_base: Optional[str] = None
    cloudmail_public_token: Optional[str] = None
    cloudmail_domains: Optional[str] = None
    cloudmail_path_messages: Optional[str] = None

    yyds_api_key: Optional[str] = None
    yyds_jwt: Optional[str] = None

    grok2api_auto_add_local: Optional[bool] = None
    grok2api_local_token_file: Optional[str] = None
    grok2api_pool_name: Optional[str] = None
    grok2api_auto_add_remote: Optional[bool] = None
    grok2api_remote_base: Optional[str] = None
    grok2api_remote_app_key: Optional[str] = None
    grok2api_remote_admin_username: Optional[str] = None
    grok2api_remote_admin_password: Optional[str] = None

    cpa_export_enabled: Optional[bool] = None
    cpa_auth_dir: Optional[str] = None
    cpa_copy_to_hotload: Optional[bool] = None
    cpa_hotload_dir: Optional[str] = None
    cpa_base_url: Optional[str] = None
    cpa_proxy: Optional[str] = None
    cpa_headless: Optional[bool] = None
    cpa_force_standalone: Optional[bool] = None
    cpa_mint_timeout_sec: Optional[int] = None
    cpa_mint_cookie_inject: Optional[bool] = None


def _run_job(count: int) -> None:
    global _controller
    controller = engine.CliStopController()
    with _job_lock:
        _controller = controller
        _job_state["running"] = True
        _job_state["success"] = 0
        _job_state["fail"] = 0
        _job_state["target"] = count
        _job_state["error"] = ""
        _job_state["started_at"] = time.time()
        _job_state["finished_at"] = None

    def log_cb(msg: str) -> None:
        _append_log(str(msg))

    def progress_cb(succ: int, fail: int, acc_file: str) -> None:
        with _job_lock:
            _job_state["success"] = succ
            _job_state["fail"] = fail
            _job_state["last_accounts_file"] = acc_file

    try:
        engine.load_config()
        result = engine.run_registration_job(
            count, log_callback=log_cb, controller=controller, on_progress=progress_cb
        )
        with _job_lock:
            _job_state["success"] = int(result.get("success") or 0)
            _job_state["fail"] = int(result.get("fail") or 0)
            _job_state["last_accounts_file"] = str(result.get("accounts_file") or "")

    except Exception as exc:
        _append_log(f"[!] 任务异常: {exc}")
    finally:
        with _job_lock:
            _job_state["running"] = False
            _job_state["finished_at"] = time.time()
            _controller = None
        _append_log("[*] Web 任务线程已结束")


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(INDEX_HTML, headers={"Cache-Control": "no-store"})


@app.head("/", include_in_schema=False)
async def root_head():
    return Response(status_code=200, headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health():
    return {"ok": True, "service": "grok-register-web"}


@app.post("/api/auth")
async def api_auth(body: AuthBody):
    if not ACCESS_PASSWORD:
        return {"ok": True, "needs_auth": False, "token": ""}
    if (body.password or "").strip() != ACCESS_PASSWORD:
        return JSONResponse({"ok": False, "detail": "invalid password"}, status_code=403)
    token = _issue_token(body.password.strip())
    return {"ok": True, "needs_auth": True, "token": token}


@app.get("/api/config")
async def api_get_config(x_access_key: Optional[str] = Header(None)):
    _require_auth(x_access_key)
    return {"ok": True, "config": _public_config(), "needs_auth": bool(ACCESS_PASSWORD)}


@app.put("/api/config")
async def api_put_config(body: ConfigBody, x_access_key: Optional[str] = Header(None)):
    _require_auth(x_access_key)
    engine.load_config()
    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if key in SECRET_FIELDS and isinstance(value, str):
            stripped = value.strip()
            if stripped == "":
                engine.config[key] = ""
                continue
            if "*" in stripped:
                continue
        engine.config[key] = value
    engine.save_config()
    return {"ok": True, "config": _public_config()}


@app.get("/api/status")
async def api_status(x_access_key: Optional[str] = Header(None)):
    _require_auth(x_access_key)
    with _job_lock:
        state = dict(_job_state)
    return {"ok": True, **state}


@app.get("/api/logs/snapshot")
async def api_logs_snapshot(limit: int = 2000, x_access_key: Optional[str] = Header(None)):
    _require_auth(x_access_key)
    with _log_cond:
        lines = list(_log_buffer)
    if limit and len(lines) > limit:
        lines = lines[-limit:]
    return {"ok": True, "lines": lines}


@app.post("/api/proxy/test")
async def api_proxy_test(x_access_key: Optional[str] = Header(None)):
    _require_auth(x_access_key)
    engine.load_config()
    logs: List[str] = []

    def _log(msg: str) -> None:
        logs.append(str(msg))

    proxy = str(engine.config.get("proxy") or "").strip()
    if not proxy:
        return {"ok": True, "proxy": "", "logs": ["[*] 直连模式（未配置代理）"]}
    try:
        _log(f"[*] 正在测试代理连接: {proxy}")
        from curl_cffi import requests
        resp = requests.get("https://httpbin.org/ip", proxies={"http": proxy, "https": proxy}, timeout=10)
        resp.raise_for_status()
        ip = resp.json().get("origin", "")
        _log(f"[+] 代理连接成功！出口 IP: {ip}")
        return {"ok": True, "proxy": proxy, "ip": ip, "logs": logs}
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "detail": str(exc), "logs": logs},
            status_code=400,
        )


@app.post("/api/start")
async def api_start(body: StartBody, x_access_key: Optional[str] = Header(None)):
    global _job_thread
    _require_auth(x_access_key)
    with _job_lock:
        if _job_state["running"]:
            raise HTTPException(status_code=409, detail="job already running")
        _append_log(f"[*] 开始注册任务 数量={body.count}")
        t = threading.Thread(target=_run_job, args=(body.count,), daemon=True)
        _job_thread = t
        t.start()
    return {"ok": True, "started": True, "count": body.count}


@app.post("/api/stop")
async def api_stop(x_access_key: Optional[str] = Header(None)):
    _require_auth(x_access_key)
    with _job_lock:
        if not _job_state["running"] or not _controller:
            return {"ok": True, "stopped": False, "message": "no running job"}
        _controller.stop()
        _append_log("[!] 终止命令已发送")
    return {"ok": True, "stopped": True}


def main() -> None:
    import uvicorn
    uvicorn.run(
        "web.server:app",
        host=HOST,
        port=PORT,
        workers=1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
