#!/usr/bin/env python3
"""Local FastAPI control plane that reuses the existing registration engine."""
from __future__ import annotations

import collections
import datetime
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

import grok_register_ttk as engine

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = Path(__file__).resolve().parent / "index.html"
FRONTEND_DIST = ROOT / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"
PROXY_POOL_JS = Path(__file__).resolve().parent / "proxy-pool.js"
PROXY_POOL_CSS = Path(__file__).resolve().parent / "proxy-pool.css"
OUTLOOK_MAILBOX_JS = Path(__file__).resolve().parent / "outlook-mailbox.js"
LOG_LIMIT = 2000

app = FastAPI(title="grok-register WebUI", version="1.2")
# TestClient uses "testserver"; production entrypoints bind to 127.0.0.1.
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    www_redirect=False,
)

_job_lock = threading.Lock()
_job_thread: Optional[threading.Thread] = None
_controller: Any = None
_maintenance_state: Optional[str] = None
_job_state = {
    "running": False,
    "target": 0,
    "success": 0,
    "fail": 0,
    "pending": 0,
    "warnings": 0,
    "uncertain": 0,
    "cancelled": False,
    "started_at": None,
    "finished_at": None,
    "accounts_file": "",
    "error": "",
}

_log_lock = threading.Lock()
_log_seq = 0
_logs = collections.deque(maxlen=LOG_LIMIT)

_database_lock = threading.Lock()
_database_cache = {}
_reauth_manager = None


def _get_database():
    from storage.database import Database
    from storage.legacy_import import import_legacy_directory

    config = _load_config_if_idle()
    path = str(Path(config.get("database_path") or "./data/grok-register.sqlite3").expanduser().resolve())
    with _database_lock:
        database = _database_cache.get(path)
        if database is None:
            database = Database(path)
            _database_cache[path] = database
        imported = import_legacy_directory(database, ROOT, "")
    for source, summary in imported:
        _append_log(
            "[*] 读取账号列表时导入旧文件: %s | 新增 %s | 重复 %s | 格式错误 %s"
            % (Path(source).name, summary.imported, summary.duplicates, summary.malformed)
        )
    return database


def _normalize_date_filter(value: str, end_of_day: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        if len(raw) == 10:
            parsed = datetime.datetime.combine(
                datetime.date.fromisoformat(raw),
                datetime.time.max if end_of_day else datetime.time.min,
                tzinfo=datetime.timezone.utc,
            )
        else:
            parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            parsed = parsed.astimezone(datetime.timezone.utc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期必须使用 ISO 8601 格式") from exc
    return parsed.isoformat(timespec="microseconds")


def _append_log(message: str) -> None:
    global _log_seq
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), str(message))
    with _log_lock:
        _log_seq += 1
        _logs.append({"seq": _log_seq, "line": line})


def _state_snapshot() -> dict[str, Any]:
    with _job_lock:
        snapshot = dict(_job_state)
        snapshot["maintenance"] = _maintenance_state
        return snapshot


def _begin_maintenance(kind: str) -> None:
    global _maintenance_state
    with _job_lock:
        if _job_state["running"]:
            raise HTTPException(status_code=409, detail="注册任务运行期间不能执行维护操作")
        if _maintenance_state is not None:
            raise HTTPException(
                status_code=409,
                detail="已有维护操作正在执行: %s" % _maintenance_state,
            )
        _maintenance_state = str(kind)


def _end_maintenance(kind: str) -> None:
    global _maintenance_state
    with _job_lock:
        if _maintenance_state == str(kind):
            _maintenance_state = None


def _load_config_if_idle() -> dict[str, Any]:
    with _job_lock:
        if not _job_state["running"] and _maintenance_state is None:
            engine.load_config()
        return dict(engine.config)


def _new_accounts_file() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return str(ROOT / ("accounts_%s.txt" % stamp))


def _update_progress(batch: Any) -> None:
    with _job_lock:
        _job_state["success"] = int(batch.success_count)
        _job_state["fail"] = int(batch.fail_count)
        _job_state["pending"] = int(batch.registered_unsaved_count)
        _job_state["warnings"] = int(batch.postprocess_warning_count)
        _job_state["uncertain"] = int(getattr(batch, "uncertain_count", 0) or 0)
        _job_state["cancelled"] = bool(batch.cancelled)


def _run_job(count: int, controller: Any, accounts_file: str) -> None:
    global _controller
    try:
        batch = engine.run_registration_common(
            count=count,
            log_callback=_append_log,
            cancel_callback=controller.should_stop,
            accounts_output_file=accounts_file,
            observer=lambda batch, _account, _output: _update_progress(batch),
        )
        _update_progress(batch)
    except Exception as exc:
        with _job_lock:
            _job_state["error"] = str(exc)
        _append_log("[!] WebUI 任务异常: %s" % exc)
    finally:
        with _job_lock:
            _job_state["running"] = False
            _job_state["finished_at"] = time.time()
            _job_state["cancelled"] = bool(
                _job_state["cancelled"] or controller.should_stop()
            )
            _controller = None
        _append_log("[*] WebUI 任务结束")


@app.get("/", include_in_schema=False)
def index():
    react_index = FRONTEND_DIST / "index.html"
    if react_index.is_file():
        return FileResponse(react_index, headers={"Cache-Control": "no-store"})

    html = INDEX_HTML.read_text(encoding="utf-8")
    if PROXY_POOL_CSS.is_file():
        html = html.replace("</head>", '<link rel="stylesheet" href="/proxy-pool.css">\n</head>', 1)
    if PROXY_POOL_JS.is_file():
        html = html.replace("</body>", '<script src="/proxy-pool.js"></script>\n</body>', 1)
    if OUTLOOK_MAILBOX_JS.is_file():
        html = html.replace("</body>", '<script src="/outlook-mailbox.js"></script>\n</body>', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/assets/{asset_name}", include_in_schema=False)
def frontend_asset(asset_name: str):
    if Path(asset_name).name != asset_name:
        raise HTTPException(status_code=404, detail="资源不存在")
    asset = FRONTEND_ASSETS / asset_name
    if not asset.is_file():
        raise HTTPException(status_code=404, detail="资源不存在")
    return FileResponse(
        asset,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/proxy-pool.js", include_in_schema=False)
def proxy_pool_js():
    return FileResponse(PROXY_POOL_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/proxy-pool.css", include_in_schema=False)
def proxy_pool_css():
    return FileResponse(PROXY_POOL_CSS, media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/outlook-mailbox.js", include_in_schema=False)
def outlook_mailbox_js():
    return FileResponse(OUTLOOK_MAILBOX_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def protect_outlook_mailbox_api(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/mailboxes/outlook"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _require_local_origin(request: Request) -> None:
    origin = str(request.headers.get("origin") or "").strip()
    site = str(request.headers.get("sec-fetch-site") or "").lower()
    if site and site not in {"none", "same-origin"}:
        raise HTTPException(status_code=403, detail="仅允许同源 WebUI 请求")
    if origin and (
        request.url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or origin != "%s://%s" % (request.url.scheme, request.url.netloc)
    ):
        raise HTTPException(status_code=403, detail="仅允许同源 WebUI 请求")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/health-check")
async def dependency_health_check(request: Request):
    _require_local_origin(request)
    payload = await request.json()
    if not isinstance(payload, dict) or type(payload.get("connectivity", False)) is not bool:
        raise HTTPException(status_code=400, detail="connectivity 必须是布尔值")

    kind = "dependency_health_check"
    _begin_maintenance(kind)
    try:
        engine.load_config()
        cfg = engine.validate_config_structure(dict(engine.config))
        from preflight import run_health_checks
        result = await run_in_threadpool(
            run_health_checks, cfg, payload.get("connectivity", False), _append_log
        )
    except (engine.ConfigError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _end_maintenance(kind)

    _append_log(
        "[*] 依赖健康检查完成: Result: %s | errors=%s warnings=%s"
        % (result["result"], result["error_count"], result["warning_count"])
    )
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@app.get("/api/config")
def get_config():
    return {"ok": True, "config": _load_config_if_idle()}


@app.put("/api/config")
async def put_config(request: Request):
    _require_local_origin(request)
    updates = await request.json()
    if not isinstance(updates, dict):
        raise HTTPException(status_code=400, detail="配置更新必须是 JSON 对象")

    allowed = set(engine.DEFAULT_CONFIG)
    unknown = sorted(set(updates) - allowed)
    if unknown:
        raise HTTPException(status_code=400, detail="未知配置项: " + ", ".join(unknown))

    with _job_lock:
        if _job_state["running"]:
            raise HTTPException(status_code=409, detail="任务运行期间不能修改配置")
        if _maintenance_state is not None:
            raise HTTPException(status_code=409, detail="维护操作期间不能修改配置")
        engine.load_config()
        candidate = dict(engine.config)
        candidate.update(updates)
        try:
            validated = engine.validate_config_structure(candidate)
        except engine.ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        engine.config.clear()
        engine.config.update(validated)
        engine.save_config()
        result = dict(engine.config)
    return {"ok": True, "config": result}


@app.get("/api/mailboxes/outlook")
def get_outlook_mailboxes(request: Request):
    _require_local_origin(request)
    from outlook_mailbox_pool import load_outlook_mailbox_pool
    cfg = _load_config_if_idle()
    try:
        summary = load_outlook_mailbox_pool(cfg.get("outlook_accounts_file", ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({
        "ok": True,
        "path": summary["path"],
        "data": summary["data"],
        "count": summary["count"],
        "invalid": summary["invalid"],
        "duplicates": summary["duplicates"],
        "accounts": summary["accounts"],
    })


@app.put("/api/mailboxes/outlook")
async def put_outlook_mailboxes(request: Request):
    _require_local_origin(request)
    payload = await request.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), str):
        raise HTTPException(status_code=400, detail="请求必须包含字符串字段 data")
    from outlook_mailbox_pool import save_outlook_mailbox_pool
    with _job_lock:
        if _job_state["running"]:
            raise HTTPException(status_code=409, detail="任务运行期间不能修改 Outlook 邮箱池")
        if _maintenance_state is not None:
            raise HTTPException(status_code=409, detail="维护操作期间不能修改 Outlook 邮箱池")
        engine.load_config()
        previous = dict(engine.config)
        path = payload.get("path", previous.get("outlook_accounts_file", ""))
        try:
            candidate = engine.validate_config_structure({
                **previous, "outlook_accounts_file": path,
            })
            summary = save_outlook_mailbox_pool(candidate["outlook_accounts_file"], payload["data"])
            if candidate["outlook_accounts_file"] != previous["outlook_accounts_file"]:
                engine.config.clear()
                engine.config.update(candidate)
                try:
                    engine.save_config()
                except Exception:
                    engine.config.clear()
                    engine.config.update(previous)
                    raise
        except (ValueError, RuntimeError, OSError, engine.ConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    _append_log("[*] Outlook 邮箱池已保存: %s 个账号" % summary["count"])
    return JSONResponse({"ok": True, **summary})


@app.post("/api/mailboxes/outlook/test")
async def test_outlook_mailboxes(request: Request):
    _require_local_origin(request)
    payload = await request.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), str):
        raise HTTPException(status_code=400, detail="请求必须包含字符串字段 data")
    from outlook_mailbox_pool import probe_outlook_mailbox_pool_data

    kind = "outlook_mailbox_test"
    _begin_maintenance(kind)
    try:
        try:
            summary = await run_in_threadpool(probe_outlook_mailbox_pool_data, payload["data"])
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _end_maintenance(kind)
    _append_log(
        "[*] Outlook 邮箱池健康检查完成: %s/%s 个账号可用"
        % (summary["healthy"], summary["count"])
    )
    return JSONResponse({"ok": True, **summary})


@app.get("/api/proxy-pool/status")
def proxy_pool_status():
    from proxy_pool import manager_snapshot
    cfg = _load_config_if_idle()
    return {"ok": True, **manager_snapshot(config=cfg)}


@app.post("/api/proxy-pool/reload")
def proxy_pool_reload(request: Request):
    _require_local_origin(request)
    from proxy_pool import get_manager
    kind = "proxy_reload"
    _begin_maintenance(kind)
    try:
        engine.load_config()
        try:
            cfg = engine.validate_config_structure(dict(engine.config))
            manager = get_manager(config=cfg, log=_append_log)
            snapshot = manager.reload_sources(force=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _end_maintenance(kind)
    _append_log("[*] 代理池已重新加载")
    return {"ok": True, **snapshot}


@app.post("/api/proxy-pool/test")
def proxy_pool_test(request: Request):
    _require_local_origin(request)
    from proxy_pool import get_manager
    kind = "proxy_test"
    _begin_maintenance(kind)
    try:
        engine.load_config()
        try:
            cfg = engine.validate_config_structure(dict(engine.config))
            manager = get_manager(config=cfg, log=_append_log)
            manager.reload_sources(force=True)
            results = manager.probe_all(force=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _end_maintenance(kind)
    _append_log("[*] 代理池测试完成: %s 个节点" % len(results))
    return {"ok": True, "results": results, **manager.snapshot()}


@app.post("/api/proxy-pool/preflight")
def proxy_pool_preflight(request: Request, node_id: str = Query(..., min_length=1)):
    _require_local_origin(request)
    from proxy_pool import get_manager
    kind = "proxy_preflight"
    _begin_maintenance(kind)
    try:
        engine.load_config()
        try:
            cfg = engine.validate_config_structure(dict(engine.config))
            if not cfg.get("proxy_pool_preflight_enabled", True):
                raise HTTPException(status_code=409, detail="注册路径预检已在配置中关闭")
            manager = get_manager(config=cfg, log=_append_log)
            result = manager.preflight_node(node_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _end_maintenance(kind)
    _append_log("[*] 代理节点注册路径预检完成: %s" % node_id)
    return {"ok": True, "result": result, **manager.snapshot()}


@app.get("/api/status")
def status():
    return {"ok": True, **_state_snapshot()}


@app.get("/api/logs")
def logs(after: int = Query(default=0, ge=0)):
    with _log_lock:
        entries = [dict(item) for item in _logs if int(item["seq"]) > int(after)]
        latest = int(_log_seq)
    return {"ok": True, "latest": latest, "entries": entries}


@app.get("/api/accounts")
def list_accounts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default=""),
    registration_status: str = Query(default=""),
    authorization_status: str = Query(default=""),
    email_provider: str = Query(default=""),
    registration_run_id: Optional[int] = Query(default=None, ge=1),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    cpa_status: str = Query(default=""),
    grok2api_local_status: str = Query(default=""),
    grok2api_remote_status: str = Query(default=""),
):
    _require_local_origin(request)
    database = _get_database()
    result = database.list_accounts(
        limit=limit,
        offset=offset,
        search=search,
        registration_status=registration_status,
        authorization_status=authorization_status,
        email_provider=email_provider,
        registration_run_id=registration_run_id,
        date_from=_normalize_date_filter(date_from),
        date_to=_normalize_date_filter(date_to, end_of_day=True),
        cpa_status=cpa_status,
        grok2api_local_status=grok2api_local_status,
        grok2api_remote_status=grok2api_remote_status,
    )
    return JSONResponse(
        content={"ok": True, **result, "limit": limit, "offset": offset},
        headers={"Cache-Control": "no-store"},
    )


def _require_account_edits_idle():
    with _job_lock:
        if _job_state["running"] or _maintenance_state is not None:
            raise HTTPException(status_code=409, detail="任务运行期间不能删除账号")


@app.delete("/api/accounts")
async def delete_accounts(request: Request):
    _require_local_origin(request)
    _require_account_edits_idle()
    payload = await request.json()
    account_ids = payload.get("ids") if isinstance(payload, dict) else None
    if not isinstance(account_ids, list) or not account_ids:
        raise HTTPException(status_code=400, detail="请提供非空账号 ID 列表")
    if any(type(value) is not int or value < 1 for value in account_ids):
        raise HTTPException(status_code=400, detail="账号 ID 必须是正整数")
    ids = account_ids
    deleted = _get_database().delete_accounts(ids)
    return {"ok": True, "deleted_count": deleted, "legacy_files_unchanged": True}


@app.get("/api/accounts/{account_id}")
def get_account(account_id: int, request: Request):
    _require_local_origin(request)
    account = _get_database().get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return JSONResponse(
        content={"ok": True, "account": account},
        headers={"Cache-Control": "no-store"},
    )


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int, request: Request):
    _require_local_origin(request)
    _require_account_edits_idle()
    if not _get_database().delete_account(account_id):
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"ok": True, "deleted": True, "legacy_files_unchanged": True}


@app.post("/api/accounts/export")
async def export_accounts(request: Request):
    _require_local_origin(request)
    payload = await request.json()
    account_ids = payload.get("ids") if isinstance(payload, dict) else None
    if not isinstance(account_ids, list) or not account_ids:
        raise HTTPException(status_code=400, detail="请提供非空账号 ID 列表")
    if any(type(value) is not int or value < 1 for value in account_ids):
        raise HTTPException(status_code=400, detail="账号 ID 必须是正整数")
    ids = account_ids
    unique_ids = list(dict.fromkeys(ids))
    rows = _get_database().export_accounts(unique_ids)
    if len(rows) != len(unique_ids):
        raise HTTPException(status_code=404, detail="部分账号不存在")
    body = "".join(
        "%s----%s----%s\n" % (row["email"], row["password"], row["sso"])
        for row in rows
    )
    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="accounts-export.txt"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/stats")
def account_stats(request: Request):
    _require_local_origin(request)
    return JSONResponse(
        content={"ok": True, **_get_database().account_stats()},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/jobs/history")
def registration_history(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    _require_local_origin(request)
    return JSONResponse(
        content={
            "ok": True, **_get_database().list_registration_runs(limit, offset),
            "limit": limit, "offset": offset,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/jobs/history/{run_id}/attempts")
def registration_attempt_history(
    run_id: int,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    _require_local_origin(request)
    result = _get_database().list_registration_attempts(run_id, limit, offset)
    if result is None:
        raise HTTPException(status_code=404, detail="注册批次不存在")
    return JSONResponse(
        content={"ok": True, **result, "limit": limit, "offset": offset},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/jobs/history/{run_id}/attempts/{attempt_id}/screenshot")
def registration_attempt_screenshot(run_id: int, attempt_id: int, request: Request):
    _require_local_origin(request)
    database = _get_database()
    attempt = database.get_registration_attempt(run_id, attempt_id)
    if attempt is None or not attempt["screenshot_path"]:
        raise HTTPException(status_code=404, detail="失败截图不存在")

    diagnostics_dir = (
        Path(database.path).expanduser().resolve().parent / "diagnostics" / "registration"
    )
    screenshot = Path(attempt["screenshot_path"]).resolve()
    try:
        screenshot.relative_to(diagnostics_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="失败截图不存在")
    if not screenshot.is_file():
        raise HTTPException(status_code=404, detail="失败截图不存在")
    return FileResponse(
        screenshot,
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/api/reauth/start")
async def start_reauthentication(request: Request):
    _require_local_origin(request)
    payload = await request.json()
    ids = payload.get("ids") if isinstance(payload, dict) else None
    if not isinstance(ids, list) or not ids or len(ids) > 200:
        raise HTTPException(status_code=400, detail="请选择 1-200 个账号")
    if any(type(value) is not int or value < 1 for value in ids):
        raise HTTPException(status_code=400, detail="账号 ID 必须是正整数")

    global _reauth_manager
    kind = "account_reauthentication"
    _begin_maintenance(kind)
    try:
        engine.load_config()
        from reauth_jobs import ReauthManager, browser_login, refresh_integrations

        manager = ReauthManager(
            _get_database(), browser_login, refresh_integrations,
            ROOT, log=_append_log, on_finish=lambda: _end_maintenance(kind),
        )
        job_id = manager.start(ids)
        _reauth_manager = manager
        return {"ok": True, "job_id": job_id}
    except (ValueError, LookupError, engine.ConfigError) as exc:
        _end_maintenance(kind)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        _end_maintenance(kind)
        raise


@app.get("/api/reauth/status")
def reauthentication_status(request: Request):
    _require_local_origin(request)
    manager = _reauth_manager
    return JSONResponse(
        content={"ok": True, **(manager.status() if manager else {"running": False, "job": None})},
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/reauth/stop")
def stop_reauthentication(request: Request):
    _require_local_origin(request)
    manager = _reauth_manager
    return {"ok": True, "stopped": manager.stop() if manager else False}


@app.get("/api/reauth/history")
def reauthentication_history(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    _require_local_origin(request)
    return JSONResponse(
        content={"ok": True, **_get_database().list_jobs("reauthentication", limit, offset)},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/reauth/jobs/{job_id}")
def reauthentication_job(job_id: int, request: Request):
    _require_local_origin(request)
    job = _get_database().get_job(job_id)
    if job is None or job["job_type"] != "reauthentication":
        raise HTTPException(status_code=404, detail="重新登录任务不存在")
    return JSONResponse(content={"ok": True, "job": job}, headers={"Cache-Control": "no-store"})


@app.post("/api/start")
def start(request: Request):
    _require_local_origin(request)
    global _job_thread, _controller

    with _job_lock:
        if _job_state["running"]:
            raise HTTPException(status_code=409, detail="已有注册任务正在运行")
        if _maintenance_state is not None:
            raise HTTPException(status_code=409, detail="维护操作进行中，暂不能启动注册: %s" % _maintenance_state)

        engine.load_config()
        try:
            validated = engine.validate_run_requirements(dict(engine.config))
        except engine.ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        engine.config.clear()
        engine.config.update(validated)

        count = engine.resolve_registration_count(
            int(engine.config["register_count"]), log_callback=_append_log
        )
        controller = engine.CliStopController()
        accounts_file = _new_accounts_file()

        _job_state.update({
            "running": True,
            "target": count,
            "success": 0,
            "fail": 0,
            "pending": 0,
            "warnings": 0,
            "uncertain": 0,
            "cancelled": False,
            "started_at": time.time(),
            "finished_at": None,
            "accounts_file": accounts_file,
            "error": "",
        })
        _controller = controller
        thread = threading.Thread(
            target=_run_job,
            args=(count, controller, accounts_file),
            name="grok-register-web-job",
            daemon=True,
        )
        _job_thread = thread
        try:
            thread.start()
        except Exception:
            _job_state["running"] = False
            _job_state["finished_at"] = time.time()
            _controller = None
            _job_thread = None
            raise

    _append_log("[*] WebUI 启动注册任务，目标数量: %s" % count)
    return {"ok": True, "started": True, "target": count, "accounts_file": accounts_file}


@app.post("/api/stop")
def stop(request: Request):
    _require_local_origin(request)
    with _job_lock:
        controller = _controller
        running = bool(_job_state["running"])
    if not running or controller is None:
        return {"ok": True, "stopped": False}
    controller.stop()
    _append_log("[!] WebUI 已发送停止请求")
    return {"ok": True, "stopped": True}


def main() -> None:
    import uvicorn

    uvicorn.run("web.server:app", host="127.0.0.1", port=8092, workers=1)


if __name__ == "__main__":
    main()
