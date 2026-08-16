"""SSO 注册风控早停：读取 grok.com botFlagSource / policy，命中后隔离并跳过入库。"""
import os
import re

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


def configure_risk_runtime(config_ref, http_get):
    global config, _http_get
    config = config_ref
    _http_get = http_get


def normalize_sso_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token


def parse_grok_account_state(page_html):
    """从 grok.com 首页 RSC / HTML 解析账号注册风控状态。"""
    raw = str(page_html or "")
    # Next.js 会把对象嵌入字符串，字段名通常表现为 \"botFlagSource\"。
    normalized = raw.replace('\\"', '"')
    source_match = re.search(r'botFlagSource"\s*:\s*(null|-?\d+)', normalized)
    details_match = re.search(
        r'botFlagDetails"\s*:\s*(?:null|"([^"]*)")',
        normalized,
    )

    source = None
    if source_match and source_match.group(1) != "null":
        try:
            source = int(source_match.group(1))
        except (TypeError, ValueError):
            source = None
    details = details_match.group(1) if details_match and details_match.group(1) else ""

    detail_fields = {}
    for item in details.split(","):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            detail_fields[key.strip().lower()] = value.strip()
    risk = None
    try:
        if detail_fields.get("risk"):
            risk = float(detail_fields["risk"])
    except (TypeError, ValueError):
        risk = None
    policy = detail_fields.get("policy", "").lower()
    event = detail_fields.get("event", "")
    denied = policy == "deny" and event == "$registration"

    return {
        "found": bool(source_match or details_match),
        "bot_flag_source": source,
        "bot_flag_details": details,
        "policy": policy,
        "risk": risk,
        "event": event,
        "denied": denied,
    }


def inspect_sso_account_state(sso_cookie, proxy="", user_agent="", timeout=20, http_get=None):
    """读取 grok.com 当前账号状态；诊断失败时返回 unknown，不阻断入库。"""
    result = parse_grok_account_state("")
    result.update({"status_code": 0, "url": "", "error": ""})
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
        return result


def registration_risk_should_block(state):
    """是否隔离当前 SSO，阻止其进入正常账号池和后续 grok2api / CPA。

    命中条件：
      - botFlagSource in (1, 2)
      - policy=deny（含 $registration / $login）
    读不到风控字段时不硬拦。
    """
    if not isinstance(state, dict):
        return False, ""
    details = str(state.get("bot_flag_details") or "").strip()
    bf = state.get("bot_flag_source")
    policy = str(state.get("policy") or "").strip().lower()
    event = str(state.get("event") or "").strip()

    if state.get("denied"):
        return True, details or "policy=deny,event=$registration"
    if bf in (1, 2):
        return True, details or ("botFlagSource=%s" % bf)
    if policy == "deny":
        return True, details or ("policy=deny,event=%s" % (event or "unknown"))
    return False, ""


def resolve_rejected_file():
    configured = str((config or {}).get("sso_risk_rejected_file", "") or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "sso_risk_rejected.txt")


def append_sso_risk_rejected(email, sso, details, log_callback=None):
    path = resolve_rejected_file()
    safe_details = re.sub(r"[\r\n\t]+", " ", str(details or "")).strip()
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
    if log_callback:
        log_callback("[风控] 已隔离到 %s" % path)
    return path


def ensure_sso_eligible(raw_token, email="", proxy="", user_agent="", log_callback=None, http_get=None):
    """检查新账号风控状态；命中时写入隔离文件并拒绝正常入库。"""
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
        append_sso_risk_rejected(email, sso, details, log_callback=log_callback)
        raise RegistrationRiskDenied(
            "注册风控拒绝，已跳过入库: botFlagSource=%s %s"
            % (state.get("bot_flag_source"), details)
        )
    if not state.get("found"):
        _risk_log("未读取到注册风控字段，继续入库: %s" % (state.get("error") or "unknown"))
    elif state.get("bot_flag_source") == 0:
        _risk_log("注册风控状态可用: botFlagSource=0")
    return state
