CONFIG_BLOCK = r'''def _config_error(field, message):
    raise ConfigError(f"配置项 {field!r} {message}")


def _require_config_bool(values, field):
    value = values.get(field)
    if type(value) is not bool:
        _config_error(field, "必须是 JSON boolean（true/false）")
    return value


def _require_config_int(values, field, minimum, maximum):
    value = values.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        _config_error(field, "必须是整数")
    if value < minimum or value > maximum:
        _config_error(field, f"必须在 {minimum} 到 {maximum} 之间")
    return value


def _require_config_string(values, field, allow_empty=True):
    value = values.get(field)
    if not isinstance(value, str):
        _config_error(field, "必须是字符串")
    if "\x00" in value:
        _config_error(field, "不能包含 NUL 字符")
    value = value.strip()
    if not allow_empty and not value:
        _config_error(field, "不能为空")
    return value


def validate_config(raw_config):
    if not isinstance(raw_config, dict):
        raise ConfigError("配置文件根节点必须是 JSON object")

    values = {**DEFAULT_CONFIG, **raw_config}

    bool_fields = (
        "enable_nsfw",
        "grok2api_auto_add_local",
        "grok2api_auto_add_remote",
        "grok2api_allow_legacy_full_replace",
        "cpa_export_enabled",
        "cpa_copy_to_hotload",
        "cpa_headless",
        "cpa_force_standalone",
        "cpa_mint_cookie_inject",
    )
    for field in bool_fields:
        values[field] = _require_config_bool(values, field)

    values["register_count"] = _require_config_int(values, "register_count", 1, 2500)
    values["cpa_mint_timeout_sec"] = _require_config_int(values, "cpa_mint_timeout_sec", 30, 1800)
    values["cpa_oidc_initial_timeout_sec"] = _require_config_int(
        values, "cpa_oidc_initial_timeout_sec", 3, 60
    )
    values["cpa_oidc_poll_timeout_sec"] = _require_config_int(
        values, "cpa_oidc_poll_timeout_sec", 3, 60
    )

    string_fields = (
        "duckmail_api_key",
        "cloudflare_api_base",
        "cloudflare_api_key",
        "cloudflare_path_domains",
        "cloudflare_path_accounts",
        "cloudflare_path_token",
        "cloudflare_path_messages",
        "cloudmail_api_base",
        "cloudmail_public_token",
        "cloudmail_domains",
        "cloudmail_path_messages",
        "proxy",
        "user_agent",
        "grok2api_local_token_file",
        "grok2api_remote_base",
        "grok2api_remote_app_key",
        "api_reverse_tools",
        "cpa_auth_dir",
        "cpa_hotload_dir",
        "cpa_base_url",
        "cpa_proxy",
        "defaultDomains",
        "yyds_api_key",
        "yyds_jwt",
    )
    for field in string_fields:
        values[field] = _require_config_string(values, field)

    path_fields = (
        "cloudflare_path_domains",
        "cloudflare_path_accounts",
        "cloudflare_path_token",
        "cloudflare_path_messages",
        "cloudmail_path_messages",
    )
    for field in path_fields:
        if not values[field].startswith("/"):
            _config_error(field, "必须以 / 开头")

    enum_fields = {
        "email_provider": {"duckmail", "yyds", "cloudflare", "cloudmail"},
        "cloudflare_auth_mode": {"query-key", "bearer", "x-api-key", "x-admin-auth", "none"},
        "grok2api_pool_name": {"ssoBasic", "ssoSuper"},
    }
    for field, allowed in enum_fields.items():
        values[field] = _require_config_string(values, field, allow_empty=False)
        if values[field] not in allowed:
            _config_error(field, "必须是以下值之一: " + ", ".join(sorted(allowed)))

    return values


def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            config = validate_config(loaded)
        except ConfigError:
            raise
        except Exception as exc:
            raise ConfigError(f"配置文件解析失败: {CONFIG_FILE}: {exc}") from exc
    else:
        config = validate_config(DEFAULT_CONFIG.copy())
    return config


def save_config():
    global config
    normalized = validate_config(config)
    config_dir = os.path.dirname(os.path.abspath(CONFIG_FILE))
    os.makedirs(config_dir, exist_ok=True)
    fd = None
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix=".config-", suffix=".json.tmp", dir=config_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None
            json.dump(normalized, f, indent=4, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
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
        config = normalized
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
'''

REMOTE_BLOCK = r'''class RemoteTokenPoolError(RuntimeError):
    pass


class RemoteTokenPoolIncompatibleError(RemoteTokenPoolError):
    pass


class RemoteTokenPoolRequestError(RemoteTokenPoolError):
    pass


def _response_status(response):
    try:
        return int(getattr(response, "status_code", 0) or 0)
    except Exception:
        return 0


def _response_preview_text(response):
    text = str(getattr(response, "text", "") or "").strip().replace("\n", " ")
    return text[:300]


def _response_etag(response):
    headers = getattr(response, "headers", None) or {}
    try:
        return str(headers.get("ETag") or headers.get("etag") or "").strip()
    except Exception:
        return ""


def _remote_request_error(endpoint, response):
    status = _response_status(response)
    preview = _response_preview_text(response)
    suffix = f": {preview}" if preview else ""
    return RemoteTokenPoolRequestError(f"grok2api 请求失败 HTTP {status}: {endpoint}{suffix}")


def _legacy_remote_pool_replace(api_bases, base, headers, query, pool_name, token, email, log_callback=None):
    if not bool(config.get("grok2api_allow_legacy_full_replace", False)):
        raise RemoteTokenPoolIncompatibleError(
            "远端不支持原子 /tokens/add；为避免并发覆盖，旧版全量回退默认禁用"
        )

    load_errors = []
    for api_base in api_bases or [base]:
        endpoint = f"{api_base}/tokens"
        try:
            response = http_get(endpoint, headers=headers, params=query, timeout=20)
        except Exception as exc:
            raise RemoteTokenPoolRequestError(f"读取远端 token 池网络失败: {endpoint}: {exc}") from exc

        status = _response_status(response)
        if status in (404, 405):
            load_errors.append(f"{endpoint}: HTTP {status}")
            continue
        if status != 200:
            raise _remote_request_error(endpoint, response)

        try:
            payload = response.json()
        except Exception as exc:
            raise RemoteTokenPoolRequestError(f"读取远端 token 池返回非 JSON: {endpoint}: {exc}") from exc
        candidate = payload.get("tokens") if isinstance(payload, dict) and "tokens" in payload else payload
        if not isinstance(candidate, dict):
            raise RemoteTokenPoolRequestError(f"读取远端 token 池返回结构异常: {endpoint}")

        etag = _response_etag(response)
        if not etag:
            raise RemoteTokenPoolIncompatibleError(
                "旧版 /tokens 接口未提供 ETag，无法使用 If-Match 保证并发安全，已拒绝全量覆盖"
            )

        pool = candidate.get(pool_name)
        if pool is None:
            pool = []
        elif not isinstance(pool, list):
            raise RemoteTokenPoolRequestError(f"远端 token 池 {pool_name} 不是列表，拒绝全量覆盖")

        existing = set()
        for item in pool:
            if isinstance(item, str):
                existing.add(_normalize_sso_token(item))
            elif isinstance(item, dict):
                existing.add(_normalize_sso_token(item.get("token", "")))
        if token not in existing:
            pool.append({"token": token, "tags": ["auto-register"], "note": email})
        candidate[pool_name] = pool

        save_headers = dict(headers)
        save_headers["If-Match"] = etag
        try:
            saved = http_post(endpoint, headers=save_headers, params=query, json=candidate, timeout=30)
        except Exception as exc:
            raise RemoteTokenPoolRequestError(f"保存远端 token 池网络失败: {endpoint}: {exc}") from exc

        save_status = _response_status(saved)
        if 200 <= save_status < 300:
            if log_callback:
                log_callback(f"[+] 已通过 ETag/If-Match 写入 grok2api 旧版远端池: {pool_name} ({endpoint})")
            return True
        if save_status in (409, 412):
            raise RemoteTokenPoolRequestError(f"远端 token 池在写入前已发生变化，If-Match 被拒绝 HTTP {save_status}")
        raise _remote_request_error(endpoint, saved)

    raise RemoteTokenPoolIncompatibleError("远端未提供可用的 /tokens 兼容接口: " + "; ".join(load_errors))


def add_token_to_grok2api_remote_pool(raw_token, email="", log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False

    base = str(config.get("grok2api_remote_base", "") or "").strip().rstrip("/")
    app_key = str(config.get("grok2api_remote_app_key", "") or "").strip()
    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "").strip()
    if not base or not app_key:
        raise RemoteTokenPoolRequestError("grok2api 远端已启用，但 base/app_key 配置不完整")

    headers = {"Content-Type": "application/json"}
    query = {"app_key": app_key}
    pool_map = {"ssoBasic": "basic", "ssoSuper": "super"}
    remote_pool = pool_map[pool_name]
    api_bases = get_grok2api_remote_api_bases(base)
    add_payload = {"tokens": [token], "pool": remote_pool, "tags": ["auto-register"]}
    incompatible = []

    for api_base in api_bases:
        endpoint = f"{api_base}/tokens/add"
        try:
            response = http_post(endpoint, headers=headers, params=query, json=add_payload, timeout=30)
        except Exception as exc:
            raise RemoteTokenPoolRequestError(
                f"grok2api 原子入池网络失败，不执行全量回退: {endpoint}: {exc}"
            ) from exc

        status = _response_status(response)
        if 200 <= status < 300:
            if log_callback:
                log_callback(f"[+] 已写入 grok2api 远端池: {pool_name} ({endpoint})")
            return True
        if status in (404, 405):
            incompatible.append(f"{endpoint}: HTTP {status}")
            continue
        raise _remote_request_error(endpoint, response)

    if log_callback:
        log_callback("[Debug] 所有 /tokens/add 候选接口均返回 404/405: " + "; ".join(incompatible))
    return _legacy_remote_pool_replace(
        api_bases=api_bases,
        base=base,
        headers=headers,
        query=query,
        pool_name=pool_name,
        token=token,
        email=email,
        log_callback=log_callback,
    )


def log_exception(log_callback, context, exc, level="!"):
    message = f"{context}: {exc.__class__.__name__}: {exc}"
    if log_callback:
        log_callback(f"[{level}] {message}")
    else:
        print(f"[{level}] {message}", file=sys.stderr)
    return message


def add_token_to_grok2api_pools(raw_token, email="", log_callback=None):
    results = {
        "local": {"enabled": bool(config.get("grok2api_auto_add_local", False)), "ok": None, "skipped": True, "error": None},
        "remote": {"enabled": bool(config.get("grok2api_auto_add_remote", False)), "ok": None, "skipped": True, "error": None},
    }

    if results["local"]["enabled"]:
        results["local"]["skipped"] = False
        try:
            results["local"]["ok"] = bool(add_token_to_grok2api_local_pool(raw_token, email=email, log_callback=log_callback))
            if not results["local"]["ok"]:
                results["local"]["error"] = "本地 token 入池返回 False"
        except Exception as exc:
            results["local"]["ok"] = False
            results["local"]["error"] = log_exception(log_callback, "写入 grok2api 本地池失败", exc)

    if results["remote"]["enabled"]:
        results["remote"]["skipped"] = False
        try:
            results["remote"]["ok"] = bool(add_token_to_grok2api_remote_pool(raw_token, email=email, log_callback=log_callback))
            if not results["remote"]["ok"]:
                results["remote"]["error"] = "远端 token 入池返回 False"
        except Exception as exc:
            results["remote"]["ok"] = False
            results["remote"]["error"] = log_exception(log_callback, "写入 grok2api 远端池失败", exc)

    return results
'''

SHARED_MAIN = r'''def append_line_durable(path, line):
    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    lock_path = target + ".lock"
    try:
        with open(lock_path, "a", encoding="utf-8"):
            pass
        os.chmod(lock_path, 0o600)
    except Exception:
        pass
    try:
        from filelock import FileLock
    except Exception as exc:
        raise RuntimeError(f"filelock 依赖不可用，拒绝非持久化写入: {exc}")
    with FileLock(lock_path, timeout=30):
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(target, 0o600)
        except Exception:
            pass
    return target


def _save_mail_credential(email, dev_token, log_callback=None):
    path = os.path.join(os.path.dirname(__file__), "mail_credentials.txt")
    try:
        append_line_durable(path, f"{email}\t{dev_token}\n")
        return True
    except Exception as exc:
        log_exception(log_callback, "保存邮箱凭据失败", exc)
        return False


def register_one_account(callbacks):
    callbacks.check_cancelled()
    email = ""
    dev_token = ""
    code = ""
    mail_ok = False
    max_mail_retry = 3

    for mail_try in range(1, max_mail_retry + 1):
        callbacks.check_cancelled()
        callbacks.log(f"[*] 1. 打开注册页 (尝试 {mail_try}/{max_mail_retry})")
        open_signup_page(log_callback=callbacks.log, cancel_callback=callbacks.cancelled)
        callbacks.log("[*] 2. 创建邮箱并提交")
        email, dev_token = fill_email_and_submit(log_callback=callbacks.log, cancel_callback=callbacks.cancelled)
        callbacks.log(f"[*] 邮箱: {email}")
        callbacks.log(f"[Debug] 邮箱credential(jwt): {dev_token}")
        _save_mail_credential(email, dev_token, log_callback=callbacks.log)

        callbacks.log("[*] 3. 拉取验证码")
        try:
            code = fill_code_and_submit(email, dev_token, log_callback=callbacks.log, cancel_callback=callbacks.cancelled)
            mail_ok = True
            break
        except RegistrationCancelled:
            raise
        except Exception as mail_exc:
            message = str(mail_exc)
            if ("未收到验证码" in message or "验证码" in message) and mail_try < max_mail_retry:
                callbacks.log(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {message}")
                restart_browser(log_callback=callbacks.log)
                sleep_with_cancel(1, callbacks.cancelled)
                continue
            raise

    if not mail_ok:
        raise RuntimeError("验证码阶段失败，已达到最大重试次数")

    callbacks.log(f"[*] 验证码: {code}")
    callbacks.log("[*] 4. 填写资料")
    profile = fill_profile_and_submit(log_callback=callbacks.log, cancel_callback=callbacks.cancelled)
    callbacks.log(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
    callbacks.log("[*] 5. 等待 sso cookie")
    sso = wait_for_sso_cookie(log_callback=callbacks.log, cancel_callback=callbacks.cancelled)

    if config.get("enable_nsfw", True):
        callbacks.log("[*] 6. 开启 NSFW")
        nsfw_ok, nsfw_message = enable_nsfw_for_token(sso, log_callback=callbacks.log)
        if nsfw_ok:
            callbacks.log(f"[+] NSFW 开启成功: {nsfw_message}")
        else:
            callbacks.log(f"[!] NSFW 未开启，继续保存账号: {nsfw_message}")

    return RegistrationResult(
        ok=True,
        registered=True,
        email=email,
        password=str(profile.get("password", "") or ""),
        sso=sso,
        profile=profile,
    )


def persist_account_result(result, output_context, callbacks):
    output = OutputResult()
    record_line = f"{result.email}----{result.password}----{result.sso}\n"

    try:
        append_line_durable(output_context.accounts_output_file, record_line)
        output.saved = True
    except Exception as exc:
        output.pending_retry = True
        output.pending_actions.append("account_result")
        output.errors.append(log_exception(callbacks.log, "保存账号结果失败", exc))
        pending_path = output_context.pending_output_file or os.path.join(
            os.path.dirname(os.path.abspath(output_context.accounts_output_file)),
            "pending_account_results.jsonl",
        )
        pending_payload = {
            "email": result.email,
            "password": result.password,
            "sso": result.sso,
            "target_file": os.path.abspath(output_context.accounts_output_file),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        try:
            append_line_durable(pending_path, json.dumps(pending_payload, ensure_ascii=False) + "\n")
            output.pending_file = os.path.abspath(pending_path)
            callbacks.log(f"[!] 账号结果已写入待重试队列: {output.pending_file}")
        except Exception as pending_exc:
            output.errors.append(log_exception(callbacks.log, "写入账号待重试队列失败", pending_exc))

    output.pool_results = add_token_to_grok2api_pools(result.sso, email=result.email, log_callback=callbacks.log)
    for pool_name, pool_state in output.pool_results.items():
        if pool_state.get("enabled") and pool_state.get("ok") is False:
            action = f"{pool_name}_token_pool"
            if action not in output.pending_actions:
                output.pending_actions.append(action)

    output.cpa_result = maybe_export_cpa_xai_after_success(
        email=result.email,
        password=result.password,
        sso=result.sso,
        log_callback=callbacks.log,
        cancel_callback=callbacks.cancelled,
    )
    if config.get("cpa_export_enabled", False) and not output.cpa_result.get("ok") and not output.cpa_result.get("skipped"):
        output.pending_actions.append("cpa_export")

    return output


def build_registration_hooks(accounts_output_file):
    output_context = OutputContext(
        accounts_output_file=accounts_output_file,
        pending_output_file=os.path.join(
            os.path.dirname(os.path.abspath(accounts_output_file)),
            "pending_account_results.jsonl",
        ),
    )
    return RegistrationHooks(
        register_one=register_one_account,
        persist=lambda result, callbacks: persist_account_result(result, output_context, callbacks),
        start_browser=lambda log: start_browser(log_callback=log),
        restart_browser=lambda log: restart_browser(log_callback=log),
        browser_is_available=lambda: browser is not None,
        cleanup=lambda log, reason: cleanup_runtime_memory(log_callback=log, reason=reason),
        sleep=sleep_with_cancel,
    )
'''

GUI_INIT = r'''    def __init__(self, root):
        self.root = root
        self.root.title("Grok 注册机")
        self.root.geometry("1120x900")
        self.root.minsize(960, 700)
        self.is_running = False
        self.batch_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.pending_count = 0
        self.results = []
        self.stop_requested = False
        self.ui_queue = queue.Queue()
        self.accounts_output_file = ""
        self.setup_ui()
        self.root.after(50, self.process_ui_queue)
'''

GUI_METHODS = r'''    def process_ui_queue(self):
        while True:
            try:
                event, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if event == "log":
                    self.log_text.insert(tk.END, payload + "\n")
                    self.log_text.see(tk.END)
                elif event == "clear_log":
                    self.log_text.delete(1.0, tk.END)
                elif event == "stats":
                    self.stats_var.set(
                        "成功: {success} | 失败: {failed} | 待重试: {pending}".format(**payload)
                    )
                elif event == "running":
                    running = bool(payload)
                    self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
                    self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
                    self.status_var.set("运行中..." if running else "就绪")
                    self.status_label.config(foreground="blue" if running else "green")
                elif event == "error_dialog":
                    messagebox.showerror(payload.get("title", "错误"), payload.get("message", ""))
                else:
                    print(f"[Debug] 未知 UI 事件: {event}", file=sys.stderr)
            except Exception as exc:
                print(f"[!] 处理 UI 事件失败 ({event}): {exc}", file=sys.stderr)
        try:
            self.root.after(50, self.process_ui_queue)
        except Exception as exc:
            print(f"[Debug] UI 队列调度已停止: {exc}", file=sys.stderr)

    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        self.ui_queue.put(("log", line))

    def clear_log(self):
        self.ui_queue.put(("clear_log", None))

    def update_stats(self):
        self.ui_queue.put(("stats", {"success": self.success_count, "failed": self.fail_count, "pending": self.pending_count}))

    def _set_running_ui(self, running):
        self.is_running = bool(running)
        self.ui_queue.put(("running", self.is_running))

    def show_error(self, title, message):
        self.ui_queue.put(("error_dialog", {"title": str(title), "message": str(message)}))

'''

GUI_RUN = r'''    def run_registration(self, count):
        callbacks = RegistrationCallbacks(log=self.log, cancelled=self.should_stop)

        def observer(event, payload):
            stats = payload.get("stats") or {}
            self.success_count = int(stats.get("success", self.success_count))
            self.fail_count = int(stats.get("failed", self.fail_count))
            self.pending_count = int(stats.get("pending", self.pending_count))
            if event == "account":
                registration = payload.get("registration")
                output = payload.get("output")
                self.results.append({
                    "email": getattr(registration, "email", ""),
                    "sso": getattr(registration, "sso", ""),
                    "profile": getattr(registration, "profile", {}),
                    "output": output,
                })
            self.update_stats()

        try:
            batch = run_batch(
                count=count,
                callbacks=callbacks,
                hooks=build_registration_hooks(self.accounts_output_file),
                observer=observer,
                max_slot_retry=3,
                cleanup_interval=MEMORY_CLEANUP_INTERVAL,
            )
            self.success_count = batch.success_count
            self.fail_count = batch.fail_count
            self.pending_count = batch.pending_count
            self.update_stats()
            self.log("[*] 任务结束。成功 {0} | 失败 {1} | 待重试 {2}".format(
                batch.success_count, batch.fail_count, batch.pending_count
            ))
        except Exception as exc:
            log_exception(self.log, "GUI 批量任务异常", exc)
        finally:
            self._set_running_ui(False)
'''

CLI_RUN = r'''def run_registration_cli(count):
    controller = CliStopController()
    accounts_output_file = os.path.join(
        os.path.dirname(__file__),
        f"accounts_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
    )
    cli_log(f"[*] 终端模式启动，目标数量: {count}")
    cli_log(f"[*] 成功账号将实时保存到: {accounts_output_file}")
    callbacks = RegistrationCallbacks(log=cli_log, cancelled=controller.should_stop)
    batch = None

    def observer(event, payload):
        if event == "account":
            stats = payload.get("stats") or {}
            cli_log("[*] 当前统计: 成功 {success} | 失败 {failed} | 待重试 {pending}".format(**stats))

    try:
        batch = run_batch(
            count=count,
            callbacks=callbacks,
            hooks=build_registration_hooks(accounts_output_file),
            observer=observer,
            max_slot_retry=3,
            cleanup_interval=MEMORY_CLEANUP_INTERVAL,
        )
    except KeyboardInterrupt:
        controller.stop()
        cli_log("[!] 收到 Ctrl+C，任务已停止并清理")
    finally:
        if batch is not None:
            cli_log("[*] 任务结束。成功 {0} | 失败 {1} | 待重试 {2}".format(
                batch.success_count, batch.fail_count, batch.pending_count
            ))
'''

DISCOVER_BLOCK = r'''def _check_cancel(cancel):
    if cancel and cancel():
        raise OAuthDeviceError("cancelled")


def _sleep_with_cancel(seconds, cancel=None):
    deadline = time.time() + max(float(seconds), 0.0)
    while time.time() < deadline:
        _check_cancel(cancel)
        time.sleep(min(0.2, max(deadline - time.time(), 0.0)))
    _check_cancel(cancel)


def discover(proxy=None, timeout=15.0, cancel=None, retries=2, retry_sleep=1.0):
    request = urllib.request.Request(
        DISCOVERY_URL,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "grok-register-cpa/1.0"},
    )
    last_error = None
    for attempt in range(max(int(retries), 0) + 1):
        _check_cancel(cancel)
        opener = _build_opener(proxy)
        try:
            with opener.open(request, timeout=float(timeout)) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = int(getattr(response, "status", 200) or 200)
            _check_cancel(cancel)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            status = int(exc.code)
            if status >= 500 and attempt < int(retries):
                _sleep_with_cancel(float(retry_sleep) * (attempt + 1), cancel)
                continue
            raise OAuthDeviceError("xAI discovery failed HTTP %s: %s" % (status, body)) from exc
        except Exception as exc:
            last_error = exc
            if not _is_transient_net_error(exc) or attempt >= int(retries):
                raise OAuthDeviceError("xAI discovery request failed: %s" % exc) from exc
            _sleep_with_cancel(float(retry_sleep) * (attempt + 1), cancel)
            continue

        if status != 200:
            raise OAuthDeviceError("xAI discovery failed HTTP %s: %s" % (status, body))
        try:
            payload = json.loads(body)
        except Exception as exc:
            raise OAuthDeviceError("xAI discovery parse failed: %s" % exc) from exc
        _check_cancel(cancel)
        return {
            "device_authorization_endpoint": _validate_endpoint(
                payload.get("device_authorization_endpoint"), "device_authorization_endpoint"
            ),
            "token_endpoint": _validate_endpoint(payload.get("token_endpoint"), "token_endpoint"),
        }

    if last_error is not None:
        raise OAuthDeviceError("xAI discovery request failed: %s" % last_error)
    raise OAuthDeviceError("xAI discovery failed without response")
'''

POST_BLOCK = r'''def _post_form(url, form, timeout=30.0, proxy=None, retries=0, retry_sleep=1.5, cancel=None):
    data = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "grok-register-cpa/1.0",
        },
    )
    last_error = None
    for attempt in range(max(int(retries), 0) + 1):
        _check_cancel(cancel)
        opener = _build_opener(proxy)
        try:
            with opener.open(request, timeout=float(timeout)) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = int(getattr(response, "status", 200) or 200)
            _check_cancel(cancel)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            status = int(exc.code)
        except Exception as exc:
            last_error = exc
            if not _is_transient_net_error(exc) or attempt >= int(retries):
                raise
            _sleep_with_cancel(float(retry_sleep) * (attempt + 1), cancel)
            continue
        try:
            payload = json.loads(body)
        except Exception:
            payload = body
        _check_cancel(cancel)
        return status, payload
    if last_error is not None:
        raise last_error
    raise OAuthDeviceError("form request failed without response")
'''

REQUEST_BLOCK = r'''def request_device_code(client_id=CLIENT_ID, scope=SCOPE, timeout=15.0, proxy=None, cancel=None, retries=2):
    _check_cancel(cancel)
    discovery = discover(proxy=proxy, timeout=timeout, cancel=cancel, retries=retries, retry_sleep=1.0)
    device_endpoint = discovery["device_authorization_endpoint"]
    token_endpoint = discovery["token_endpoint"]
    status, payload = _post_form(
        device_endpoint,
        {"client_id": client_id, "scope": scope},
        timeout=timeout,
        proxy=proxy,
        retries=retries,
        retry_sleep=1.0,
        cancel=cancel,
    )
    _check_cancel(cancel)
    if status != 200 or not isinstance(payload, dict):
        raise OAuthDeviceError("device code request failed HTTP %s: %r" % (status, payload))
    device_code = str(payload.get("device_code") or "").strip()
    user_code = str(payload.get("user_code") or "").strip()
    if not device_code or not user_code:
        raise OAuthDeviceError("device code response missing fields: %r" % payload)
    verification_uri = str(payload.get("verification_uri") or "https://accounts.x.ai/oauth2/device").strip()
    verification_uri_complete = str(
        payload.get("verification_uri_complete") or ("%s?user_code=%s" % (verification_uri, user_code))
    ).strip()
    return DeviceCodeSession(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=verification_uri_complete,
        expires_in=int(payload.get("expires_in") or 1800),
        interval=max(int(payload.get("interval") or 5), 1),
        token_endpoint=token_endpoint,
        raw=payload,
    )
'''

POLL_BLOCK = r'''def poll_device_token(
    device_code,
    token_endpoint,
    client_id=CLIENT_ID,
    interval=5,
    expires_in=1800,
    timeout=12.0,
    log=None,
    cancel=None,
    proxy=None,
):
    logger = log or (lambda message: None)
    deadline = time.time() + max(int(expires_in) - 5, 30)
    sleep_seconds = max(int(interval), 1)
    net_streak = 0
    max_net_streak = 20
    while time.time() < deadline:
        _check_cancel(cancel)
        try:
            status, payload = _post_form(
                token_endpoint,
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": str(device_code).strip(),
                    "client_id": client_id,
                },
                timeout=float(timeout),
                proxy=proxy,
                retries=0,
                retry_sleep=1.0,
                cancel=cancel,
            )
            net_streak = 0
        except OAuthDeviceError:
            raise
        except Exception as exc:
            if not _is_transient_net_error(exc):
                raise
            net_streak += 1
            wait_seconds = min(sleep_seconds + min(net_streak, 5), 20)
            logger("oauth poll network blip (%s/%s): %s — retry in %ss" % (net_streak, max_net_streak, exc, wait_seconds))
            if net_streak >= max_net_streak:
                raise OAuthDeviceError("device auth aborted after %s network errors: %s" % (net_streak, exc))
            _sleep_with_cancel(wait_seconds, cancel)
            continue

        if status == 200 and isinstance(payload, dict) and payload.get("access_token"):
            access_token = str(payload.get("access_token") or "").strip()
            refresh_token = str(payload.get("refresh_token") or "").strip()
            if not refresh_token:
                raise OAuthDeviceError("token response missing refresh_token")
            return TokenResult(
                access_token=access_token,
                refresh_token=refresh_token,
                id_token=(str(payload.get("id_token") or "").strip() or None),
                token_type=str(payload.get("token_type") or "Bearer"),
                expires_in=int(payload.get("expires_in") or 21600),
                raw=payload,
            )

        error_code = ""
        error_description = ""
        if isinstance(payload, dict):
            error_code = str(payload.get("error") or "")
            error_description = str(payload.get("error_description") or "")
        if error_code in ("authorization_pending", "slow_down"):
            if error_code == "slow_down":
                sleep_seconds = min(sleep_seconds + 5, 30)
            logger("oauth poll: %s (sleep %ss)" % (error_code, sleep_seconds))
            _sleep_with_cancel(sleep_seconds, cancel)
            continue
        if error_code in ("expired_token", "access_denied"):
            raise OAuthDeviceError("device auth failed: %s: %s" % (error_code, error_description))
        if status == 400 and error_code:
            raise OAuthDeviceError("device auth token error: %s: %s" % (error_code, error_description or payload))
        if status >= 500 or not isinstance(payload, dict):
            net_streak += 1
            wait_seconds = min(sleep_seconds + 2, 20)
            logger("oauth poll soft HTTP %s: %r — retry in %ss" % (status, payload, wait_seconds))
            if net_streak >= max_net_streak:
                raise OAuthDeviceError("device auth aborted after repeated soft HTTP failures status=%s" % status)
            _sleep_with_cancel(wait_seconds, cancel)
            continue
        logger("oauth poll unexpected HTTP %s: %r" % (status, payload))
        _sleep_with_cancel(sleep_seconds, cancel)
    raise OAuthDeviceError("device auth timed out waiting for user approval")
'''

BROWSER_TAIL_OLD = r'''    resolved = resolve_proxy(proxy)
    proxy_bridge = None
    chrome_proxy, proxy_bridge = prepare_chromium_proxy(resolved, log=logger)
    try:
        if chrome_proxy:
            options.set_argument("--proxy-server=%s" % chrome_proxy)
            logger("browser proxy=%s (chromium %s)" % (proxy_log_label(resolved), chrome_proxy))
        else:
            logger("browser proxy=(none)")

        browser = Chromium(options)
        if proxy_bridge is not None:
            try:
                setattr(browser, "_cpa_proxy_bridge", proxy_bridge)
            except Exception:
                pass
        _register_mint_browser(browser)
        page = browser.latest_tab
        logger("standalone chromium started")
        return browser, page
    except Exception:
        if proxy_bridge is not None:
            try:
                proxy_bridge.stop()
            except Exception:
                pass
        raise
'''

BROWSER_TAIL_NEW = r'''    resolved = resolve_proxy(proxy)
    proxy_bridge = None
    browser = None
    try:
        chrome_proxy, proxy_bridge = prepare_chromium_proxy(resolved, log=logger)
        if chrome_proxy:
            options.set_argument("--proxy-server=%s" % chrome_proxy)
            logger("browser proxy=%s (chromium %s)" % (proxy_log_label(resolved), chrome_proxy))
        else:
            logger("browser proxy=(none)")

        browser = Chromium(options)
        if proxy_bridge is not None:
            setattr(browser, "_cpa_proxy_bridge", proxy_bridge)
        page = browser.latest_tab
        _register_mint_browser(browser)
        logger("standalone chromium started")
        return browser, page
    except Exception:
        if browser is not None:
            close_standalone(browser)
        elif proxy_bridge is not None:
            try:
                proxy_bridge.stop()
            except Exception:
                pass
        raise
'''
