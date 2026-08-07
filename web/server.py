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
from fastapi.responses import FileResponse

import grok_register_ttk as engine

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = Path(__file__).resolve().parent / "index.html"
LOG_LIMIT = 2000

app = FastAPI(title="grok-register WebUI", version="1.0")

_job_lock = threading.Lock()
_job_thread: Optional[threading.Thread] = None
_controller: Any = None
_job_state = {
    "running": False,
    "target": 0,
    "success": 0,
    "fail": 0,
    "pending": 0,
    "warnings": 0,
    "cancelled": False,
    "started_at": None,
    "finished_at": None,
    "accounts_file": "",
    "error": "",
}

_log_lock = threading.Lock()
_log_seq = 0
_logs = collections.deque(maxlen=LOG_LIMIT)


def _append_log(message: str) -> None:
    global _log_seq
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), str(message))
    with _log_lock:
        _log_seq += 1
        _logs.append({"seq": _log_seq, "line": line})


def _state_snapshot() -> dict[str, Any]:
    with _job_lock:
        return dict(_job_state)


def _load_config_if_idle() -> dict[str, Any]:
    with _job_lock:
        if not _job_state["running"]:
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
    return FileResponse(INDEX_HTML, headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def get_config():
    return {"ok": True, "config": _load_config_if_idle()}


@app.put("/api/config")
async def put_config(request: Request):
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
        engine.load_config()
        candidate = dict(engine.config)
        candidate.update(updates)
        try:
            validated = engine.validate_run_requirements(candidate)
        except engine.ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        engine.config.clear()
        engine.config.update(validated)
        engine.save_config()
        result = dict(engine.config)
    return {"ok": True, "config": result}


@app.get("/api/status")
def status():
    return {"ok": True, **_state_snapshot()}


@app.get("/api/logs")
def logs(after: int = Query(default=0, ge=0)):
    with _log_lock:
        entries = [dict(item) for item in _logs if int(item["seq"]) > int(after)]
        latest = int(_log_seq)
    return {"ok": True, "latest": latest, "entries": entries}


@app.post("/api/start")
def start():
    global _job_thread, _controller

    with _job_lock:
        if _job_state["running"]:
            raise HTTPException(status_code=409, detail="已有注册任务正在运行")

        engine.load_config()
        try:
            validated = engine.validate_run_requirements(dict(engine.config))
        except engine.ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        engine.config.clear()
        engine.config.update(validated)

        count = int(engine.config["register_count"])
        controller = engine.CliStopController()
        accounts_file = _new_accounts_file()

        _job_state.update({
            "running": True,
            "target": count,
            "success": 0,
            "fail": 0,
            "pending": 0,
            "warnings": 0,
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
def stop():
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
