"""负责应用配置的默认值、加载保存、规范化和运行前校验。"""
import json
import os
import tempfile
import urllib.parse

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "duckmail_api_key": "",
    "cloudflare_api_base": "",
    "cloudflare_api_key": "",
    "cloudflare_auth_mode": "none",
    "cloudflare_path_domains": "/api/domains",
    "cloudflare_path_accounts": "/api/new_address",
    "cloudflare_path_token": "/api/token",
    "cloudflare_path_messages": "/api/mails",
    "cloudmail_api_base": "",
    "cloudmail_public_token": "",
    "cloudmail_domains": "",
    "cloudmail_path_messages": "/api/public/emailList",
    "proxy_mode": "auto",
    "proxy": "",
    "proxy_fallback": "none",
    "proxy_pool_file": "",
    "proxy_pool_subscription_url": "",
    "proxy_pool_subscription_proxy": "",
    "proxy_pool_endpoint_mode": "auto",
    "proxy_pool_refresh_interval_sec": 900,
    "proxy_pool_probe_interval_sec": 900,
    "proxy_pool_probe_timeout_sec": 15,
    "proxy_pool_probe_provider": "cloudflare",
    "proxy_pool_probe_dual_stack": True,
    "proxy_pool_max_concurrent_per_node": 1,
    "proxy_pool_acquire_timeout_sec": 30,
    "proxy_protocol_backend": "auto",
    "proxy_singbox_path": "",
    "proxy_protocol_start_timeout_sec": 10,
    "proxy_runtime_idle_ttl_sec": 120,
    "proxy_runtime_cache_max": 32,
    "proxy_pool_persist_health": False,
    "proxy_pool_state_file": "./proxy_pool_state.json",
    "proxy_pool_subscription_public_only": False,
    "proxy_pool_preflight_enabled": True,
    "enable_nsfw": True,
    "sso_risk_gate_enabled": True,
    "sso_risk_rejected_file": "./sso_risk_rejected.txt",
    "register_count": 1,
    "multi_thread_enabled": False,
    "multi_thread_workers": 4,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "grok2api_auto_add_local": False,
    "grok2api_local_token_file": "",
    "grok2api_pool_name": "ssoBasic",
    "grok2api_auto_add_remote": False,
    "grok2api_remote_base": "",
    "grok2api_remote_app_key": "",
    "grok2api_remote_admin_username": "",
    "grok2api_remote_admin_password": "",
    "api_reverse_tools": "",
    "cpa_export_enabled": True,
    "cpa_auth_dir": "./cpa_auths",
    "cpa_copy_to_hotload": False,
    "cpa_hotload_dir": "",
    "cpa_base_url": "https://cli-chat-proxy.grok.com/v1",
    "cpa_proxy": "",
    "cpa_headless": False,
    "cpa_force_standalone": True,
    "cpa_mint_timeout_sec": 300,
    "cpa_mint_cookie_inject": True,
    "cpa_oidc_request_timeout_sec": 15,
    "cpa_oidc_poll_timeout_sec": 15,
    "grok2api_allow_legacy_full_save": False,
    "email_provider": "duckmail",
    "yyds_api_key": "",
    "yyds_jwt": "",
    "defaultDomains": "",
}


config = DEFAULT_CONFIG.copy()


class ConfigError(RuntimeError):
    pass


def _require_bool(cfg, key):
    value = cfg.get(key)
    if type(value) is not bool:
        raise ConfigError(f"配置项 {key} 必须是布尔值 true/false")
    return value


def _require_int(cfg, key, minimum, maximum):
    value = cfg.get(key)
    if type(value) is not int:
        raise ConfigError(f"配置项 {key} 必须是整数")
    if not minimum <= value <= maximum:
        raise ConfigError(f"配置项 {key} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _require_string(cfg, key, path=False):
    value = cfg.get(key)
    if not isinstance(value, str):
        raise ConfigError(f"配置项 {key} 必须是字符串")
    value = value.strip() if key not in ("user_agent",) else value
    if "\x00" in value:
        raise ConfigError(f"配置项 {key} 包含非法空字符")
    if path and value:
        os.path.expanduser(value)
    return value


def validate_config_structure(raw):
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a JSON object")
    unknown = sorted(set(raw) - set(DEFAULT_CONFIG))
    if unknown:
        raise ConfigError("未知配置项: " + ", ".join(unknown))
    cfg = {**DEFAULT_CONFIG, **raw}
    bool_keys = (
        "enable_nsfw", "sso_risk_gate_enabled", "grok2api_auto_add_local", "grok2api_auto_add_remote",
        "grok2api_allow_legacy_full_save", "cpa_export_enabled",
        "cpa_copy_to_hotload", "cpa_headless", "cpa_force_standalone",
        "cpa_mint_cookie_inject", "multi_thread_enabled",
        "proxy_pool_probe_dual_stack", "proxy_pool_persist_health",
        "proxy_pool_subscription_public_only", "proxy_pool_preflight_enabled",
    )
    for key in bool_keys:
        cfg[key] = _require_bool(cfg, key)
    cfg["register_count"] = _require_int(cfg, "register_count", 1, 2500)
    cfg["multi_thread_workers"] = _require_int(cfg, "multi_thread_workers", 1, 8)
    cfg["proxy_pool_refresh_interval_sec"] = _require_int(cfg, "proxy_pool_refresh_interval_sec", 0, 86400)
    cfg["proxy_pool_probe_interval_sec"] = _require_int(cfg, "proxy_pool_probe_interval_sec", 0, 86400)
    cfg["proxy_pool_probe_timeout_sec"] = _require_int(cfg, "proxy_pool_probe_timeout_sec", 3, 120)
    cfg["proxy_pool_max_concurrent_per_node"] = _require_int(cfg, "proxy_pool_max_concurrent_per_node", 1, 64)
    cfg["proxy_pool_acquire_timeout_sec"] = _require_int(cfg, "proxy_pool_acquire_timeout_sec", 1, 600)
    cfg["proxy_protocol_start_timeout_sec"] = _require_int(cfg, "proxy_protocol_start_timeout_sec", 3, 60)
    cfg["proxy_runtime_idle_ttl_sec"] = _require_int(cfg, "proxy_runtime_idle_ttl_sec", 0, 3600)
    cfg["proxy_runtime_cache_max"] = _require_int(cfg, "proxy_runtime_cache_max", 1, 256)
    cfg["cpa_mint_timeout_sec"] = _require_int(cfg, "cpa_mint_timeout_sec", 30, 1800)
    cfg["cpa_oidc_request_timeout_sec"] = _require_int(cfg, "cpa_oidc_request_timeout_sec", 3, 120)
    cfg["cpa_oidc_poll_timeout_sec"] = _require_int(cfg, "cpa_oidc_poll_timeout_sec", 3, 120)
    string_keys = tuple(key for key, value in DEFAULT_CONFIG.items() if isinstance(value, str))
    path_keys = {
        "grok2api_local_token_file", "api_reverse_tools", "cpa_auth_dir", "cpa_hotload_dir",
        "proxy_pool_file", "proxy_singbox_path", "proxy_pool_state_file",
        "sso_risk_rejected_file",
    }
    for key in string_keys:
        cfg[key] = _require_string(cfg, key, path=key in path_keys)
    enums = {
        "email_provider": {"duckmail", "yyds", "cloudflare", "cloudmail"},
        "cloudflare_auth_mode": {"query-key", "bearer", "x-api-key", "x-admin-auth", "none"},
        "grok2api_pool_name": {"ssoBasic", "ssoSuper"},
        "proxy_mode": {"auto", "direct", "single", "pool"},
        "proxy_fallback": {"none", "direct", "single"},
        "proxy_pool_endpoint_mode": {"auto", "fixed", "rotating"},
        "proxy_pool_probe_provider": {"cloudflare", "ipinfo"},
        "proxy_protocol_backend": {"auto", "sing-box", "native-only"},
    }
    for key, allowed in enums.items():
        value = cfg.get(key, DEFAULT_CONFIG.get(key, ""))
        if value not in allowed:
            raise ConfigError(f"配置项 {key} 的值无效: {value!r}; 允许值: {sorted(allowed)}")
        cfg[key] = value

    api_path_keys = {
        "cloudflare_path_domains", "cloudflare_path_accounts",
        "cloudflare_path_token", "cloudflare_path_messages",
        "cloudmail_path_messages",
    }
    for key in api_path_keys:
        value = cfg[key]
        if value and not value.startswith("/"):
            value = "/" + value
        cfg[key] = value

    url_keys = {
        "cloudflare_api_base", "cloudmail_api_base",
        "grok2api_remote_base", "cpa_base_url",
        "proxy_pool_subscription_url",
    }
    for key in url_keys:
        value = cfg[key]
        if not value:
            continue
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ConfigError(f"配置项 {key} 必须是有效的 http/https URL")

    for key in path_keys:
        value = cfg[key]
        if value.startswith("~"):
            cfg[key] = os.path.expanduser(value)
    return cfg


def validate_run_requirements(cfg):
    cfg = validate_config_structure(cfg)
    provider = cfg["email_provider"]
    if provider == "cloudflare" and not cfg["cloudflare_api_base"]:
        raise ConfigError("Cloudflare 模式需要配置 cloudflare_api_base")
    if provider == "cloudmail":
        missing = [
            key for key in ("cloudmail_api_base", "cloudmail_public_token", "cloudmail_domains")
            if not cfg[key]
        ]
        if missing:
            raise ConfigError("Cloud Mail 模式缺少必需配置: " + ", ".join(missing))
    if provider == "yyds" and not (cfg["yyds_api_key"] or cfg["yyds_jwt"]):
        raise ConfigError("YYDS 模式需要至少配置 yyds_api_key 或 yyds_jwt")

    if cfg["proxy_mode"] == "single" and not cfg["proxy"]:
        raise ConfigError("single 代理模式必须配置 proxy")
    if cfg["proxy_mode"] == "pool" and not (cfg["proxy_pool_file"] or cfg["proxy_pool_subscription_url"]):
        raise ConfigError("pool 代理模式至少需要 proxy_pool_file 或 proxy_pool_subscription_url")
    if cfg["proxy_fallback"] == "single" and not cfg["proxy"]:
        raise ConfigError("proxy_fallback=single 时必须配置 proxy")
    if cfg["proxy_pool_persist_health"] and not cfg["proxy_pool_state_file"]:
        raise ConfigError("启用代理健康状态持久化时必须配置 proxy_pool_state_file")

    if cfg["grok2api_auto_add_remote"]:
        if not cfg["grok2api_remote_base"]:
            raise ConfigError("远端 token 入池缺少必需配置: grok2api_remote_base")
        has_legacy = bool(cfg["grok2api_remote_app_key"])
        has_go = bool(cfg["grok2api_remote_admin_username"] and cfg["grok2api_remote_admin_password"])
        has_partial_go = bool(cfg["grok2api_remote_admin_username"] or cfg["grok2api_remote_admin_password"])
        if has_legacy and has_partial_go:
            raise ConfigError("旧版 app_key 与新版管理员账号密码不能同时配置")
        if has_partial_go and not has_go:
            raise ConfigError("新版 grok2api 必须同时配置管理员账号和密码")
        if not has_legacy and not has_go:
            raise ConfigError("远端 token 入池需要旧版 app_key 或新版管理员账号密码")
    if cfg["cpa_export_enabled"] and cfg["cpa_copy_to_hotload"] and not cfg["cpa_hotload_dir"]:
        raise ConfigError("启用 CPA 热加载复制时必须配置 cpa_hotload_dir")
    return cfg


def validate_config(raw):
    """Backward-compatible full validation used before a run or save."""
    return validate_run_requirements(raw)


def _replace_config(value):
    config.clear()
    config.update(value)
    return config


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            return _replace_config(validate_config_structure(loaded))
        except ConfigError:
            raise
        except Exception as exc:
            raise ConfigError(f"配置文件解析失败: {CONFIG_FILE}: {exc}") from exc
    return _replace_config(validate_config_structure(DEFAULT_CONFIG.copy()))


def save_config():
    normalized = validate_config_structure(config)
    _replace_config(normalized)
    config_dir = os.path.dirname(os.path.abspath(CONFIG_FILE))
    os.makedirs(config_dir, exist_ok=True)
    fd = None
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix=".config-", suffix=".json.tmp", dir=config_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            json.dump(config, handle, indent=4, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_path, 0o600)
        except Exception:
            pass
        os.replace(temp_path, CONFIG_FILE)
        temp_path = None
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except Exception:
            pass
    except Exception as exc:
        raise ConfigError(f"保存配置失败: {exc}") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
    return config
