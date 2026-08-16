"""SSO 注册风控早停：读取 grok.com botFlagSource / policy，命中后隔离并跳过入库。"""
import json
import os
import re
import time

from filelock import FileLock


GROK_HOME_URL = "https://grok.com/"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)

config = {}
_http_get = None


class RegistrationRiskDenied(RuntimeError):
    """SSO 被 grok.com 风控标记，不应写入正常账号池或后处理。"""


class RegistrationRiskPersistenceError(RuntimeError):
    """风控已命中，但隔离文件和风险 pending 均无法持久化。"""


def configure_risk_runtime(config_ref, http_get):
    global config, _http_get
    config = config_ref
    _http_get = http_get


def normalize_sso_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token


def _parse_detail_fields(details):
    fields = {}
    for item in str(details or "").split(","):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            fields[key.strip().lower()] = value.strip()
    risk = None
    try:
        if fields.get("risk"):
            risk = float(fields["risk"])
    except (TypeError, ValueError):
        risk = None
    return {
        "details": str(details or ""),
        "policy": str(fields.get("policy") or "").strip().lower(),
        "event": str(fields.get("event") or "").strip(),
        "risk": risk,
    }


def parse_grok_account_state(page_html):
    """从 grok.com 首页 RSC / HTML 解析全部账号注册风控状态。"""
    raw = str(page_html or "")
    # Next.js 会把对象嵌入字符串，字段名通常表现为 \"botFlagSource\"。
    normalized = raw.replace('\\"', '"')

    source_matches = list(
        re.finditer(r'botFlagSource"\s*:\s*(null|-?\d+)', normalized)
    )
    details_matches = list(
        re.finditer(r'botFlagDetails"\s*:\s*(?:null|"([^"]*)")', normalized)
    )

    sources = []
    for match in source_matches:
        raw_value = match.group(1)
        if raw_value == "null":
            sources.append(None)
            continue
        try:
            sources.append(int(raw_value))
        except (TypeError, ValueError):
            sources.append(None)

    detail_states = []
    for match in details_matches:
        detail_states.append(_parse_detail_fields(match.group(1) or ""))

    # 兼容旧 scalar 字段：优先展示会触发 block 的值，否则展示第一个可解析值。
    source = next((value for value in sources if value in (1, 2)), None)
    if source is None:
        source = next((value for value in sources if value is not None), None)

    blocking_detail = next(
        (item for item in detail_states if item.get("policy") == "deny"),
        None,
    )
    selected_detail = blocking_detail or next(
        (item for item in detail_states if item.get("details")),
        None,
    )
    if selected_detail is None:
        selected_detail = {"details": "", "policy": "", "event": "", "risk": None}

    denied = any(
        item.get("policy") == "deny" and item.get("event") == "$registration"
        for item in detail_states
    )

    return {
        "found": bool(source_matches or details_matches),
        "bot_flag_source": source,
        "bot_flag_details": selected_detail.get("details", ""),
        "policy": selected_detail.get("policy", ""),
        "risk": selected_detail.get("risk"),
        "event": selected_detail.get("event", ""),
        "denied": denied,
        "bot_flag_sources": sources,
        "bot_flag_detail_states": detail_states,
    }


def _mark_proxy_transport_failure(result, exc):
    """在 fail-open 前保留当前 lease 的可疑传输失败信号。"""
    try:
        from proxy_pool import current_proxy_lease, get_manager, is_proxy_transport_exception

        if not is_proxy_transport_exception(exc):
            return
        result["transport_error"] = True
        lease = current_proxy_lease()
        if lease is not None:
            try:
                get_manager().report_suspected_transport_failure(lease, str(exc))
            except Exception:
                # health feedback 不能改变 risk gate 的 fail-open 产品语义。
                pass
    except Exception:
        pass


def inspect_sso_account_state(sso_cookie, proxy="", user_agent="", timeout=20, http_get=None):
    """读取 grok.com 当前账号状态；诊断失败时返回 unknown，不阻断入库。"""
    result = parse_grok_account_state("")
    result.update({"status_code": 0, "url": "", "error": "", "transport_error": False})
    token = normalize_sso_token(sso_cookie)
    if not token:
        result["error"] = "sso 为空"
        return result

    getter = http_get or _http_get
    request_kwargs = {
        "headers": {
            "User-Agent": user_agent or str((config or {}).get("user_agent") or DEFAULT_UA),
            "Accept": "text/html,application/xhtml+xml",
        },
        "cookies": {"sso": token, "sso-rw": token},
        "timeout": timeout,
        "allow_redirects": True,
        "impersonate": "chrome",
    }
    if proxy:
        request_kwargs["proxies"] = {"http": proxy, "https": proxy}

    try:
        if getter is None:
            from curl_cffi import requests
            response = requests.get(GROK_HOME_URL, **request_kwargs)
        else:
            response = getter(GROK_HOME_URL, **request_kwargs)
        result["status_code"] = int(getattr(response, "status_code", 0) or 0)
        result["url"] = str(getattr(response, "url", "") or "")
        if result["status_code"] != 200:
            suffix = "（可能是 Cloudflare/出口限制）" if result["status_code"] in (403, 429, 503) else ""
            result["error"] = "grok.com HTTP %s%s" % (result["status_code"], suffix)
            return result
        parsed = parse_grok_account_state(getattr(response, "text", "") or "")
        result.update(parsed)
        if not parsed["found"]:
            result["error"] = "grok.com 未发现 botFlag 字段"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        _mark_proxy_transport_failure(result, exc)
        return result


def registration_risk_should_block(state):
    """是否隔离当前 SSO，阻止其进入正常账号池和后续 grok2api / CPA。

    命中条件：
      - 任一 botFlagSource in (1, 2)
      - 任一 policy=deny（含 $registration / $login）
    读不到风控字段或请求失败时不硬拦。
    """
    if not isinstance(state, dict):
        return False, ""

    sources = state.get("bot_flag_sources")
    if not isinstance(sources, (list, tuple)):
        sources = [state.get("bot_flag_source")]

    detail_states = state.get("bot_flag_detail_states")
    if not isinstance(detail_states, (list, tuple)):
        detail_states = [{
            "details": state.get("bot_flag_details") or "",
            "policy": state.get("policy") or "",
            "event": state.get("event") or "",
        }]

    for item in detail_states:
        if not isinstance(item, dict):
            continue
        policy = str(item.get("policy") or "").strip().lower()
        if policy == "deny":
            details = str(item.get("details") or "").strip()
            event = str(item.get("event") or "").strip()
            return True, details or ("policy=deny,event=%s" % (event or "unknown"))

    for source in sources:
        if source in (1, 2):
            details = str(state.get("bot_flag_details") or "").strip()
            return True, details or ("botFlagSource=%s" % source)

    if state.get("denied"):
        details = str(state.get("bot_flag_details") or "").strip()
        return True, details or "policy=deny,event=$registration"
    return False, ""


def resolve_rejected_file():
    configured = str((config or {}).get("sso_risk_rejected_file", "") or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "sso_risk_rejected.txt")


def resolve_rejected_pending_file():
    return resolve_rejected_file() + ".pending.jsonl"


def _safe_details(details):
    return re.sub(r"[\r\n\t]+", " ", str(details or "")).strip()


def _append_rejected_line(path, email, sso, details):
    safe_details = _safe_details(details)
    line = "%s----%s----%s\n" % (email or "", sso, safe_details)
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    with FileLock(path + ".lock", timeout=30):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    return path


def queue_sso_risk_rejected_pending(email, sso, details, primary_error="", state=None, log_callback=None):
    """主隔离文件不可写时，把风险 SSO 写入独立 JSONL pending，绝不进入正常账号 pending。"""
    path = resolve_rejected_pending_file()
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    payload = {
        "email": str(email or ""),
        "sso": normalize_sso_token(sso),
        "details": _safe_details(details),
        "primary_error": str(primary_error or ""),
        "created_at": int(time.time()),
    }
    if isinstance(state, dict):
        payload.update({
            "bot_flag_source": state.get("bot_flag_source"),
            "policy": state.get("policy"),
            "event": state.get("event"),
            "risk": state.get("risk"),
        })
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    with FileLock(path + ".lock", timeout=30):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    if log_callback:
        log_callback("[风控] 主隔离文件写入失败，已保存到风险 pending: %s" % path)
    return path


def append_sso_risk_rejected(email, sso, details, log_callback=None, state=None):
    """把风险 SSO 持久化；主文件失败时自动退避到独立 risk pending。"""
    path = resolve_rejected_file()
    try:
        written = _append_rejected_line(path, email, sso, details)
    except Exception as primary_exc:
        try:
            queue_sso_risk_rejected_pending(
                email,
                sso,
                details,
                primary_error=primary_exc,
                state=state,
                log_callback=log_callback,
            )
        except Exception as pending_exc:
            raise RegistrationRiskPersistenceError(
                "风控账号隔离与风险 pending 均写入失败: primary=%s; pending=%s"
                % (primary_exc, pending_exc)
            ) from pending_exc
        return resolve_rejected_pending_file()

    if log_callback:
        log_callback("[风控] 已隔离到 %s" % written)
    return written


def _risk_identity(email, sso):
    return "%s----%s----" % (str(email or ""), normalize_sso_token(sso))


def retry_sso_risk_pending_file(pending_path=None, rejected_path=None, log_callback=None):
    """幂等恢复风险 pending；成功条目进入 rejected，失败条目原子保留。"""
    pending_path = os.path.abspath(
        os.path.expanduser(str(pending_path or resolve_rejected_pending_file()))
    )
    rejected_path = os.path.abspath(
        os.path.expanduser(str(rejected_path or resolve_rejected_file()))
    )
    if not os.path.exists(pending_path):
        return {"processed": 0, "recovered": 0, "remaining": 0, "errors": []}

    parent = os.path.dirname(pending_path) or "."
    os.makedirs(parent, exist_ok=True)
    errors = []
    recovered = 0
    processed = 0

    with FileLock(pending_path + ".lock", timeout=30):
        try:
            with open(pending_path, "r", encoding="utf-8") as handle:
                rows = [line.rstrip("\n") for line in handle if line.strip()]
        except FileNotFoundError:
            return {"processed": 0, "recovered": 0, "remaining": 0, "errors": []}

        existing = set()
        if os.path.exists(rejected_path):
            try:
                with open(rejected_path, "r", encoding="utf-8") as handle:
                    for line in handle:
                        parts = line.rstrip("\n").split("----", 2)
                        if len(parts) >= 2:
                            existing.add(_risk_identity(parts[0], parts[1]))
            except Exception:
                existing = set()

        remaining_rows = []
        for row in rows:
            processed += 1
            try:
                payload = json.loads(row)
                email = str(payload.get("email") or "")
                sso = normalize_sso_token(payload.get("sso"))
                details = str(payload.get("details") or "registration_risk")
                if not sso:
                    raise ValueError("risk pending row missing sso")
                identity = _risk_identity(email, sso)
                if identity not in existing:
                    _append_rejected_line(rejected_path, email, sso, details)
                    existing.add(identity)
                recovered += 1
            except Exception as exc:
                remaining_rows.append(row)
                errors.append(str(exc))

        tmp_path = pending_path + ".tmp"
        if remaining_rows:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                for row in remaining_rows:
                    handle.write(row + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, pending_path)
        else:
            try:
                os.remove(pending_path)
            except FileNotFoundError:
                pass
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass

    if log_callback:
        log_callback(
            "[风控] risk pending 恢复完成: processed=%s recovered=%s remaining=%s"
            % (processed, recovered, len(remaining_rows))
        )
    return {
        "processed": processed,
        "recovered": recovered,
        "remaining": len(remaining_rows),
        "errors": errors,
    }


def ensure_sso_eligible(raw_token, email="", proxy="", user_agent="", log_callback=None, http_get=None):
    """检查新账号风控状态；命中时持久化隔离并拒绝正常入库。"""
    if not bool((config or {}).get("sso_risk_gate_enabled", True)):
        return {"found": False, "skipped": True}

    sso = normalize_sso_token(raw_token)
    if not sso:
        raise RegistrationRiskDenied("注册风控检查失败: sso 为空")

    def _risk_log(message):
        if log_callback:
            log_callback("[风控] %s" % str(message).strip())

    _risk_log("检查新账号注册风控状态 ...")
    state = inspect_sso_account_state(
        sso,
        proxy=proxy,
        user_agent=user_agent,
        http_get=http_get,
    )
    block, details = registration_risk_should_block(state)
    if block:
        details = str(details or state.get("bot_flag_details") or "registration_risk")
        append_sso_risk_rejected(
            email,
            sso,
            details,
            log_callback=log_callback,
            state=state,
        )
        raise RegistrationRiskDenied(
            "注册风控拒绝，已跳过入库: botFlagSource=%s %s"
            % (state.get("bot_flag_source"), details)
        )
    if not state.get("found"):
        _risk_log("未读取到注册风控字段，继续入库: %s" % (state.get("error") or "unknown"))
    elif state.get("bot_flag_source") == 0:
        _risk_log("注册风控状态可用: botFlagSource=0")
    return state
