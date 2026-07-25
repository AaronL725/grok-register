"""负责账号结果、pending 恢复以及 grok2api token 池的安全持久化。"""
import hashlib
import json
import os
import tempfile
import threading
import time
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone

from filelock import FileLock


def append_account_line(path, email, password, sso):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{email}----{password}----{sso}\n")
        handle.flush()
        os.fsync(handle.fileno())


def save_mail_credential(base_dir, email, credential):
    path = os.path.join(base_dir, "mail_credentials.txt")
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
                    append_account_line(target_path, email, password, sso)
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
_grok2api_v3_auth_lock = threading.Lock()
_grok2api_v3_access_token = ""
_grok2api_v3_access_token_expires_at = None
_grok2api_v3_session_key = ""


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


def get_grok2api_v3_api_base(base):
    """Normalize a Go-version grok2api site URL to its admin API root."""
    normalized = str(base or "").strip().rstrip("/")
    if not normalized:
        return ""
    suffix = "/api/admin/v1"
    lower = normalized.lower()
    if lower.endswith(suffix):
        return normalized
    for legacy_suffix in ("/admin/api", "/admin"):
        if lower.endswith(legacy_suffix):
            normalized = normalized[:-len(legacy_suffix)].rstrip("/")
            break
    return normalized + suffix


def _grok2api_v3_cache_key(api_base, username, password):
    digest = hashlib.sha256(str(password).encode("utf-8")).hexdigest()
    return f"{api_base}\n{username}\n{digest}"


def _clear_grok2api_v3_auth_cache():
    global _grok2api_v3_access_token
    global _grok2api_v3_access_token_expires_at
    global _grok2api_v3_session_key
    with _grok2api_v3_auth_lock:
        _grok2api_v3_access_token = ""
        _grok2api_v3_access_token_expires_at = None
        _grok2api_v3_session_key = ""


def _pick_nested_value(payload, *paths):
    for path in paths:
        current = payload
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if current not in (None, ""):
            return current
    return None


def _parse_grok2api_v3_expiry(payload):
    value = _pick_nested_value(
        payload,
        ("data", "tokens", "accessTokenExpiresAt"),
        ("tokens", "accessTokenExpiresAt"),
        ("data", "accessTokenExpiresAt"),
        ("accessTokenExpiresAt",),
    )
    if not value:
        return datetime.now(timezone.utc) + timedelta(minutes=5)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc) + timedelta(minutes=5)


def _extract_grok2api_v3_access_token(payload):
    value = _pick_nested_value(
        payload,
        ("data", "tokens", "accessToken"),
        ("tokens", "accessToken"),
        ("data", "accessToken"),
        ("accessToken",),
    )
    return str(value or "").strip()


def _grok2api_v3_login(api_base, username, password, verify_tls, timeout):
    endpoint = f"{api_base}/auth/login"
    try:
        response = http_post(
            endpoint,
            headers={"Content-Type": "application/json"},
            json={"username": username, "password": password},
            timeout=timeout,
            verify=verify_tls,
        )
    except Exception as exc:
        raise RemoteTokenRequestError(f"新版 grok2api 管理员登录请求失败: {endpoint}: {exc}") from exc
    status = int(getattr(response, "status_code", 0) or 0)
    if not 200 <= status < 300:
        raise RemoteTokenRequestError(f"新版 grok2api 管理员登录失败: {endpoint}: HTTP {status}")
    try:
        payload = response.json()
    except Exception as exc:
        raise RemoteTokenCompatibilityError("新版 grok2api 登录响应不是有效 JSON") from exc
    access_token = _extract_grok2api_v3_access_token(payload)
    if not access_token:
        raise RemoteTokenCompatibilityError("新版 grok2api 登录响应缺少 accessToken")
    return access_token, _parse_grok2api_v3_expiry(payload)


def _get_grok2api_v3_access_token(api_base, username, password, verify_tls, timeout, force=False):
    global _grok2api_v3_access_token
    global _grok2api_v3_access_token_expires_at
    global _grok2api_v3_session_key
    session_key = _grok2api_v3_cache_key(api_base, username, password)
    with _grok2api_v3_auth_lock:
        now = datetime.now(timezone.utc)
        cache_valid = (
            not force
            and _grok2api_v3_session_key == session_key
            and bool(_grok2api_v3_access_token)
            and isinstance(_grok2api_v3_access_token_expires_at, datetime)
            and _grok2api_v3_access_token_expires_at > now + timedelta(seconds=30)
        )
        if cache_valid:
            return _grok2api_v3_access_token
        token, expires_at = _grok2api_v3_login(
            api_base, username, password, verify_tls, timeout
        )
        _grok2api_v3_access_token = token
        _grok2api_v3_access_token_expires_at = expires_at
        _grok2api_v3_session_key = session_key
        return token


def _redact_sensitive_text(value, sensitive_values):
    text = str(value or "")
    for sensitive in sensitive_values:
        secret = str(sensitive or "")
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def _parse_grok2api_v3_import_response(response, sensitive_values=()):
    text = str(getattr(response, "text", "") or "")
    content_type = str((getattr(response, "headers", {}) or {}).get("Content-Type", "") or "").lower()
    if "text/event-stream" not in content_type:
        if not text.strip():
            return {}
        try:
            payload = response.json()
        except Exception:
            return {}
        if isinstance(payload, dict) and payload.get("success") is False:
            raise RemoteTokenRequestError("新版 grok2api 返回导入失败")
        return payload if isinstance(payload, dict) else {}

    current_event = ""
    completed = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("event:"):
            current_event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        data_text = line[5:].strip()
        try:
            data = json.loads(data_text)
        except Exception:
            data = {"message": data_text}
        if current_event == "error":
            message = ""
            if isinstance(data, dict):
                message = str(data.get("message") or data.get("error") or "").strip()
            message = _redact_sensitive_text(message, sensitive_values)
            raise RemoteTokenRequestError(
                "新版 grok2api 导入失败" + (f": {message[:200]}" if message else "")
            )
        if current_event == "complete":
            completed = data if isinstance(data, dict) else {}
    if completed is None:
        raise RemoteTokenCompatibilityError("新版 grok2api 导入响应缺少 complete 事件")
    return completed


def _build_grok2api_v3_multipart(token):
    try:
        from curl_cffi import CurlMime
    except Exception as exc:
        raise RemoteTokenRequestError(f"无法创建新版 grok2api 导入表单: {exc}") from exc
    multipart = CurlMime()
    multipart.addpart(
        name="file",
        filename="grok-web-sso.txt",
        content_type="text/plain; charset=utf-8",
        data=(token + "\n").encode("utf-8"),
    )
    return multipart


def add_token_to_grok2api_v3(raw_token, email="", log_callback=None):
    """Import one SSO token into the Go-version grok2api Grok Web provider."""
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    api_base = get_grok2api_v3_api_base(config.get("grok2api_v3_base_url", ""))
    username = str(config.get("grok2api_v3_admin_username", "") or "").strip()
    password = str(config.get("grok2api_v3_admin_password", "") or "")
    verify_tls = bool(config.get("grok2api_v3_verify_tls", True))
    timeout = int(config.get("grok2api_v3_request_timeout_sec", 60) or 60)
    if not api_base or not username or not password:
        raise RemoteTokenRequestError("新版 grok2api 未配置 base_url/admin_username/admin_password")

    endpoint = f"{api_base}/accounts/web/import"
    for attempt in range(2):
        access_token = _get_grok2api_v3_access_token(
            api_base, username, password, verify_tls, timeout, force=attempt > 0
        )
        multipart = _build_grok2api_v3_multipart(token)
        try:
            response = http_post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "text/event-stream, application/json",
                },
                multipart=multipart,
                timeout=timeout,
                verify=verify_tls,
            )
        except Exception as exc:
            raise RemoteTokenRequestError(f"新版 grok2api SSO 导入请求失败: {endpoint}: {exc}") from exc
        finally:
            multipart.close()
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 401 and attempt == 0:
            _clear_grok2api_v3_auth_cache()
            continue
        if not 200 <= status < 300:
            raise RemoteTokenRequestError(f"新版 grok2api SSO 导入失败: {endpoint}: HTTP {status}")
        result = _parse_grok2api_v3_import_response(
            response, sensitive_values=(token, access_token)
        )
        if log_callback:
            created = result.get("created") if isinstance(result, dict) else None
            updated = result.get("updated") if isinstance(result, dict) else None
            synced = result.get("synced") if isinstance(result, dict) else None
            summary = ", ".join(
                f"{name}={value}"
                for name, value in (("created", created), ("updated", updated), ("synced", synced))
                if value is not None
            )
            log_callback(
                "[+] 已导入新版 grok2api Grok Web"
                + (f": {summary}" if summary else "")
                + (f" ({email})" if email else "")
            )
        return True
    raise RemoteTokenRequestError("新版 grok2api 管理员认证已失效")

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

def add_token_to_grok2api_remote_pool(raw_token, email="", log_callback=None):
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
        "v3": {"enabled": bool(config.get("grok2api_v3_auto_import", False)), "ok": None, "error": None},
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
    if result["v3"]["enabled"]:
        try:
            result["v3"]["ok"] = bool(add_token_to_grok2api_v3(raw_token, email=email, log_callback=log_callback))
        except Exception as exc:
            result["v3"]["ok"] = False
            result["v3"]["error"] = log_exception("导入新版 grok2api Grok Web 失败", exc, log_callback)
    return result
