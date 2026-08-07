"""负责账号结果、pending 恢复以及 grok2api token 池的安全持久化。"""
import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.parse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone

from filelock import FileLock


def _append_account_line_unlocked(path, email, password, sso):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{email}----{password}----{sso}\n")
        handle.flush()
        os.fsync(handle.fileno())


def append_account_line(path, email, password, sso):
    with FileLock(path + ".lock", timeout=30):
        _append_account_line_unlocked(path, email, password, sso)


def save_mail_credential(base_dir, email, credential):
    path = os.path.join(base_dir, "mail_credentials.txt")
    with FileLock(path + ".lock", timeout=30):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{email}\t{credential}\n")
            handle.flush()
            os.fsync(handle.fileno())
    return True


def queue_unsaved_account(path, payload, error):
    pending_path = path + ".pending.jsonl"
    record = dict(payload)
    record["save_error"] = str(error)
    record["queued_at"] = datetime.now(timezone.utc).isoformat()
    with FileLock(pending_path + ".lock", timeout=30):
        with open(pending_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(pending_path, 0o600)
        except Exception:
            pass
    return True


def _existing_account_keys(target_path):
    keys = set()
    if not os.path.isfile(target_path):
        return keys
    with open(target_path, "r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            parts = raw_line.rstrip("\n").split("----", 2)
            if len(parts) == 3:
                keys.add((parts[0].strip(), parts[2].strip()))
    return keys


def retry_pending_file(pending_path, output_path=None, log_callback=None):
    logger = log_callback or (lambda message: None)
    pending_path = os.path.realpath(os.path.abspath(os.path.expanduser(str(pending_path))))
    if not os.path.isfile(pending_path):
        raise FileNotFoundError(f"pending 文件不存在: {pending_path}")
    suffix = ".pending.jsonl"
    if output_path:
        target_path = os.path.realpath(os.path.abspath(os.path.expanduser(str(output_path))))
    elif pending_path.endswith(suffix):
        target_path = os.path.realpath(pending_path[:-len(suffix)])
    else:
        target_path = os.path.realpath(pending_path + ".recovered.txt")
    if os.path.normcase(pending_path) == os.path.normcase(target_path):
        raise ValueError("pending 输入文件与输出文件不能是同一个文件")

    lock_paths = sorted(
        {pending_path + ".lock", target_path + ".lock"},
        key=lambda value: os.path.normcase(os.path.abspath(value)),
    )
    with ExitStack() as stack:
        for lock_path in lock_paths:
            stack.enter_context(FileLock(lock_path, timeout=30))
        if not os.path.isfile(pending_path):
            return {"restored": 0, "remaining": 0, "output_path": target_path}
        with open(pending_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
        existing = _existing_account_keys(target_path)
        unresolved = []
        restored = 0
        for line_number, raw_line in enumerate(lines, 1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
                if not isinstance(record, dict):
                    raise ValueError("record must be a JSON object")
                email = str(record.get("email") or "").strip()
                password = str(record.get("password") or "")
                sso = str(record.get("sso") or "").strip()
                if not email or not sso:
                    raise ValueError("record missing email or sso")
                key = (email, sso)
                if key not in existing:
                    _append_account_line_unlocked(target_path, email, password, sso)
                    existing.add(key)
                restored += 1
                logger(f"[+] 已恢复 pending 账号: {email}")
            except Exception as exc:
                unresolved.append(raw_line if raw_line.endswith("\n") else raw_line + "\n")
                logger(f"[!] pending 第 {line_number} 行恢复失败: {exc}")

        directory = os.path.dirname(pending_path) or "."
        fd, temp_path = tempfile.mkstemp(prefix=".pending-retry-", suffix=".jsonl.tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.writelines(unresolved)
                handle.flush()
                os.fsync(handle.fileno())
            if unresolved:
                os.replace(temp_path, pending_path)
                temp_path = None
                try:
                    os.chmod(pending_path, 0o600)
                except Exception:
                    pass
            else:
                os.unlink(temp_path)
                temp_path = None
                try:
                    os.unlink(pending_path)
                except FileNotFoundError:
                    pass
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
        return {"restored": restored, "remaining": len(unresolved), "output_path": target_path}


# Token-pool runtime dependencies are injected by the application adapter.
config = {}
_http_get = None
_http_post = None
_log_exception = None
_remote_compat_error = RuntimeError
_remote_request_error = RuntimeError
_grok2api_admin_lock = threading.Lock()
_grok2api_admin_token = ""
_grok2api_admin_expires_at = None
_grok2api_admin_session_key = ""


def configure_token_runtime(config_ref, http_get, http_post, log_exception,
                            compatibility_error=RuntimeError, request_error=RuntimeError):
    global config, _http_get, _http_post, _log_exception
    global _remote_compat_error, _remote_request_error
    config = config_ref
    _http_get = http_get
    _http_post = http_post
    _log_exception = log_exception
    _remote_compat_error = compatibility_error
    _remote_request_error = request_error
    globals()["http_get"] = http_get
    globals()["http_post"] = http_post
    globals()["log_exception"] = log_exception
    globals()["RemoteTokenCompatibilityError"] = compatibility_error
    globals()["RemoteTokenRequestError"] = request_error


def resolve_grok2api_local_token_file():
    configured = str(config.get("grok2api_local_token_file", "") or "").strip()
    if configured:
        return configured
    return os.path.join(os.path.dirname(__file__), "token.json")

def _normalize_sso_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token

def add_token_to_grok2api_local_pool(raw_token, email="", log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    token_file = os.path.abspath(resolve_grok2api_local_token_file())
    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip() or "ssoBasic"
    parent = os.path.dirname(token_file)
    os.makedirs(parent, exist_ok=True)
    lock_path = token_file + ".lock"
    try:
        with open(lock_path, "a", encoding="utf-8"):
            pass
        os.chmod(lock_path, 0o600)
    except Exception:
        pass
    try:
        from filelock import FileLock
    except Exception as exc:
        raise RuntimeError(f"filelock 依赖不可用，拒绝非原子写入 token 池: {exc}")
    with FileLock(lock_path, timeout=30):
        data = {}
        if os.path.exists(token_file):
            try:
                with open(token_file, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception as exc:
                broken_path = token_file + f".broken-{int(time.time())}"
                try:
                    os.replace(token_file, broken_path)
                except Exception:
                    broken_path = token_file
                raise RuntimeError(f"本地 token 文件 JSON 解析失败，已停止写入以避免覆盖: {broken_path}: {exc}")
        if not isinstance(data, dict):
            raise RuntimeError("本地 token 文件根节点不是 JSON object，拒绝覆盖")
        pool = data.get(pool_name)
        if pool is None:
            pool = []
        elif not isinstance(pool, list):
            raise RuntimeError(f"本地 token 池 {pool_name} 不是列表，拒绝覆盖")
        existing = set()
        for item in pool:
            if isinstance(item, str):
                existing.add(_normalize_sso_token(item))
            elif isinstance(item, dict):
                existing.add(_normalize_sso_token(item.get("token", "")))
        if token in existing:
            if log_callback:
                log_callback(f"[*] grok2api 本地池已存在 token: {pool_name}")
            return True
        pool.append({"token": token, "tags": ["auto-register"], "note": email})
        data[pool_name] = pool
        if os.path.exists(token_file):
            backup_path = token_file + ".bak"
            try:
                with open(token_file, "rb") as src, open(backup_path, "wb") as dst:
                    dst.write(src.read())
                    dst.flush()
                    os.fsync(dst.fileno())
                try:
                    os.chmod(backup_path, 0o600)
                except Exception:
                    pass
            except Exception as exc:
                raise RuntimeError(f"创建本地 token 备份失败，拒绝继续写入: {exc}")
        fd, temp_path = tempfile.mkstemp(prefix=".token-", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(temp_path, 0o600)
            except Exception:
                pass
            os.replace(temp_path, token_file)
            temp_path = None
            try:
                os.chmod(token_file, 0o600)
            except Exception:
                pass
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
    if log_callback:
        log_callback(f"[+] 已写入 grok2api 本地池: {pool_name} ({token_file})")
    return True

def get_grok2api_remote_api_bases(base):
    """生成 grok2api 管理 API 候选根路径。

    参数:
      - base str: 用户配置的 grok2api 远端地址

    返回:
      - list[str]: 依次尝试的管理 API 根路径
    """
    normalized = str(base or "").strip().rstrip("/")
    if not normalized:
        return []
    lower = normalized.lower()
    candidates = [normalized]
    if lower.endswith("/admin/api"):
        return candidates
    if lower.endswith("/admin"):
        candidates.append(f"{normalized}/api")
    else:
        candidates.append(f"{normalized}/admin/api")
    seen = set()
    unique = []
    for item in candidates:
        if item not in seen:
            unique.append(item)
            seen.add(item)
    return unique

def _grok2api_go_api_base(base):
    normalized = str(base or "").strip().rstrip("/")
    if not normalized:
        return ""
    for suffix in ("/api/admin/v1", "/admin/api", "/admin"):
        if normalized.lower().endswith(suffix):
            normalized = normalized[:-len(suffix)].rstrip("/")
            break
    parsed = urllib.parse.urlsplit(normalized)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise RemoteTokenRequestError("新版 grok2api 远端管理接口必须使用 HTTPS")
    return normalized + "/api/admin/v1"


def _clear_grok2api_admin_cache():
    global _grok2api_admin_token, _grok2api_admin_expires_at, _grok2api_admin_session_key
    with _grok2api_admin_lock:
        _grok2api_admin_token = ""
        _grok2api_admin_expires_at = None
        _grok2api_admin_session_key = ""


def _get_grok2api_admin_token(api_base, username, password, force=False):
    global _grok2api_admin_token, _grok2api_admin_expires_at, _grok2api_admin_session_key
    session_key = "%s\n%s\n%s" % (
        api_base, username, hashlib.sha256(password.encode("utf-8")).hexdigest()
    )
    with _grok2api_admin_lock:
        now = datetime.now(timezone.utc)
        if (
            not force
            and _grok2api_admin_session_key == session_key
            and _grok2api_admin_token
            and isinstance(_grok2api_admin_expires_at, datetime)
            and _grok2api_admin_expires_at > now + timedelta(seconds=30)
        ):
            return _grok2api_admin_token
        endpoint = api_base + "/auth/login"
        try:
            response = http_post(
                endpoint,
                headers={"Content-Type": "application/json"},
                json={"username": username, "password": password},
                timeout=60,
                proxies={},
                verify=True,
            )
        except Exception as exc:
            raise RemoteTokenRequestError(f"新版 grok2api 管理员登录请求失败: {endpoint}: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if not 200 <= status < 300:
            raise RemoteTokenRequestError(f"新版 grok2api 管理员登录失败: {endpoint}: HTTP {status}")
        try:
            tokens = response.json().get("data", {}).get("tokens", {})
            token = str(tokens.get("accessToken") or "").strip()
            expiry = str(tokens.get("accessTokenExpiresAt") or "").strip()
        except Exception as exc:
            raise RemoteTokenCompatibilityError("新版 grok2api 登录响应格式不兼容") from exc
        if not token:
            raise RemoteTokenCompatibilityError("新版 grok2api 登录响应缺少 accessToken")
        try:
            expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            expires_at = now + timedelta(minutes=5)
        _grok2api_admin_token = token
        _grok2api_admin_expires_at = expires_at.astimezone(timezone.utc)
        _grok2api_admin_session_key = session_key
        return token


def _parse_grok2api_go_import(response, secrets=()):
    current_event = ""
    completed = None
    for raw_line in str(getattr(response, "text", "") or "").splitlines():
        line = raw_line.strip()
        if line.startswith("event:"):
            current_event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except Exception:
            data = {"message": line[5:].strip()}
        if current_event == "error":
            message = str(data.get("message") or data.get("error") or "") if isinstance(data, dict) else ""
            for secret in secrets:
                if secret:
                    message = message.replace(str(secret), "[REDACTED]")
            raise RemoteTokenRequestError("新版 grok2api 导入失败" + (f": {message[:200]}" if message else ""))
        if current_event == "complete":
            completed = data if isinstance(data, dict) else {}
    if completed is None:
        raise RemoteTokenCompatibilityError("新版 grok2api 导入响应缺少 complete 事件")
    return completed


def _add_token_to_grok2api_go_remote_pool(token, email="", log_callback=None):
    base = str(config.get("grok2api_remote_base", "") or "").strip()
    username = str(config.get("grok2api_remote_admin_username", "") or "").strip()
    password = str(config.get("grok2api_remote_admin_password", "") or "")
    api_base = _grok2api_go_api_base(base)
    endpoint = api_base + "/accounts/web/import"
    try:
        from curl_cffi import CurlMime
    except Exception as exc:
        raise RemoteTokenRequestError(f"无法创建新版 grok2api 导入表单: {exc}") from exc
    for attempt in range(2):
        access_token = _get_grok2api_admin_token(api_base, username, password, force=attempt > 0)
        multipart = CurlMime()
        multipart.addpart(
            name="file",
            filename="grok-web-sso.txt",
            content_type="text/plain; charset=utf-8",
            data=(token + "\n").encode("utf-8"),
        )
        try:
            response = http_post(
                endpoint,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "text/event-stream"},
                multipart=multipart,
                timeout=60,
                proxies={},
                verify=True,
            )
        except Exception as exc:
            raise RemoteTokenRequestError(f"新版 grok2api SSO 导入请求失败: {endpoint}: {exc}") from exc
        finally:
            multipart.close()
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 401 and attempt == 0:
            _clear_grok2api_admin_cache()
            continue
        if not 200 <= status < 300:
            raise RemoteTokenRequestError(f"新版 grok2api SSO 导入失败: {endpoint}: HTTP {status}")
        result = _parse_grok2api_go_import(response, (token, access_token))
        summary = ", ".join(
            f"{key}={result.get(key)}"
            for key in ("created", "updated", "skipped", "synced", "syncFailed")
            if result.get(key) is not None
        )
        if int(result.get("syncFailed") or 0):
            raise RemoteTokenRequestError("SSO 已导入，但初始同步失败" + (f": {summary}" if summary else ""))
        if log_callback:
            log_callback(
                "[+] 已导入新版 grok2api Grok Web"
                + (f": {summary}" if summary else "")
                + (f" ({email})" if email else "")
            )
        return True
    raise RemoteTokenRequestError("新版 grok2api 管理员认证已失效")


def add_token_to_grok2api_remote_pool(raw_token, email="", log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    username = str(config.get("grok2api_remote_admin_username", "") or "").strip()
    password = str(config.get("grok2api_remote_admin_password", "") or "")
    if username and password:
        return _add_token_to_grok2api_go_remote_pool(token, email=email, log_callback=log_callback)
    return _add_token_to_grok2api_legacy_remote_pool(token, email=email, log_callback=log_callback)


def _add_token_to_grok2api_legacy_remote_pool(raw_token, email="", log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    base = str(config.get("grok2api_remote_base", "") or "").strip().rstrip("/")
    app_key = str(config.get("grok2api_remote_app_key", "") or "").strip()
    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip()
    if not base or not app_key:
        raise RemoteTokenRequestError("grok2api 远端未配置 base/app_key")
    headers = {"Content-Type": "application/json"}
    query = {"app_key": app_key}
    remote_pool = {"ssoBasic": "basic", "ssoSuper": "super"}[pool_name]
    api_bases = get_grok2api_remote_api_bases(base)
    incompatible = []
    add_payload = {"tokens": [token], "pool": remote_pool, "tags": ["auto-register"]}
    for api_base in api_bases:
        endpoint = f"{api_base}/tokens/add"
        try:
            response = http_post(endpoint, headers=headers, params=query, json=add_payload, timeout=30)
        except Exception as exc:
            raise RemoteTokenRequestError(f"远端 /tokens/add 网络请求失败: {endpoint}: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if 200 <= status < 300:
            if log_callback:
                log_callback(f"[+] 已写入 grok2api 远端池: {pool_name} ({endpoint})")
            return True
        if status in (404, 405):
            incompatible.append(f"{endpoint}: HTTP {status}")
            continue
        body = str(getattr(response, "text", "") or "")[:300]
        raise RemoteTokenRequestError(f"远端 /tokens/add 请求失败，不允许全量回退: {endpoint}: HTTP {status}: {body}")
    if not bool(config.get("grok2api_allow_legacy_full_save", False)):
        raise RemoteTokenCompatibilityError(
            "/tokens/add 不受支持，旧版全量保存默认禁用以避免并发覆盖: " + "; ".join(incompatible)
        )
    current = None
    fallback_base = None
    etag = None
    load_errors = []
    for api_base in api_bases or [base]:
        endpoint = f"{api_base}/tokens"
        try:
            response = http_get(endpoint, headers=headers, params=query, timeout=20)
        except Exception as exc:
            raise RemoteTokenRequestError(f"旧版远端池读取网络失败: {endpoint}: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            load_errors.append(f"{endpoint}: HTTP {status}")
            continue
        payload = response.json()
        candidate = payload.get("tokens") if isinstance(payload, dict) and "tokens" in payload else payload
        if not isinstance(candidate, dict):
            load_errors.append(f"{endpoint}: unexpected payload")
            continue
        current = candidate
        fallback_base = api_base
        response_headers = getattr(response, "headers", {}) or {}
        etag = response_headers.get("ETag") or response_headers.get("etag")
        break
    if current is None or fallback_base is None:
        raise RemoteTokenRequestError("无法安全读取旧版远端 token 池: " + "; ".join(load_errors))
    pool = current.get(pool_name)
    if pool is None:
        pool = []
    elif not isinstance(pool, list):
        raise RemoteTokenRequestError(f"远端 token 池 {pool_name} 不是列表，拒绝全量覆盖")
    existing = {
        _normalize_sso_token(item if isinstance(item, str) else item.get("token", ""))
        for item in pool if isinstance(item, (str, dict))
    }
    if token not in existing:
        pool.append({"token": token, "tags": ["auto-register"], "note": email})
    current[pool_name] = pool
    if not etag:
        raise RemoteTokenCompatibilityError(
            "旧版远端接口未提供 ETag，无法保证并发安全，已拒绝全量保存"
        )
    save_headers = dict(headers)
    save_headers["If-Match"] = etag
    endpoint = f"{fallback_base}/tokens"
    try:
        response = http_post(endpoint, headers=save_headers, params=query, json=current, timeout=30)
    except Exception as exc:
        raise RemoteTokenRequestError(f"旧版远端池保存网络失败: {endpoint}: {exc}") from exc
    status = int(getattr(response, "status_code", 0) or 0)
    if not 200 <= status < 300:
        raise RemoteTokenRequestError(f"旧版远端池保存失败: {endpoint}: HTTP {status}")
    if log_callback:
        log_callback(f"[+] 已写入 grok2api 远端池（旧版兼容）: {pool_name} ({endpoint})")
    return True

def add_token_to_grok2api_pools(raw_token, email="", log_callback=None):
    result = {
        "local": {"enabled": bool(config.get("grok2api_auto_add_local", False)), "ok": None, "error": None},
        "remote": {"enabled": bool(config.get("grok2api_auto_add_remote", False)), "ok": None, "error": None},
    }
    if result["local"]["enabled"]:
        try:
            result["local"]["ok"] = bool(add_token_to_grok2api_local_pool(raw_token, email=email, log_callback=log_callback))
        except Exception as exc:
            result["local"]["ok"] = False
            result["local"]["error"] = log_exception("写入 grok2api 本地池失败", exc, log_callback)
    if result["remote"]["enabled"]:
        try:
            result["remote"]["ok"] = bool(add_token_to_grok2api_remote_pool(raw_token, email=email, log_callback=log_callback))
        except Exception as exc:
            result["remote"]["ok"] = False
            result["remote"]["error"] = log_exception("写入 grok2api 远端池失败", exc, log_callback)
    return result

