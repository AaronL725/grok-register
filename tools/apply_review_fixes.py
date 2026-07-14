#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "grok_register_ttk.py"
BROWSER = ROOT / "cpa_xai" / "browser_confirm.py"
OAUTH = ROOT / "cpa_xai" / "oauth_device.py"
GITIGNORE = ROOT / ".gitignore"


def read(path, encoding="utf-8"):
    return path.read_text(encoding=encoding)


def write(path, content, encoding="utf-8"):
    path.write_text(content, encoding=encoding)


def one(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


def between(text, start, end, new, label):
    i = text.find(start)
    if i < 0:
        raise RuntimeError(f"{label}: start not found")
    j = text.find(end, i + len(start))
    if j < 0:
        raise RuntimeError(f"{label}: end not found")
    return text[:i] + new + text[j:]


app = read(APP, "utf-8-sig")

if "import tempfile\n" not in app:
    app = one(app, "import urllib.parse\n", "import urllib.parse\nimport tempfile\n", "import tempfile")

if "class ConfigError(RuntimeError):" not in app:
    app = one(
        app,
        "class AccountRetryNeeded(Exception):\n    pass\n\n\ndef load_config():",
        "class AccountRetryNeeded(Exception):\n    pass\n\n\nclass ConfigError(RuntimeError):\n    pass\n\n\ndef load_config():",
        "ConfigError",
    )

app = between(app, "def load_config():\n", "def save_config():\n", '''def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("config root must be a JSON object")
            config = {**DEFAULT_CONFIG, **loaded}
        except Exception as exc:
            raise ConfigError(f"配置文件解析失败: {CONFIG_FILE}: {exc}") from exc
    else:
        config = DEFAULT_CONFIG.copy()
    return config


''', "load_config")

app = between(app, "def save_config():\n", "def ensure_stable_python_runtime():\n", '''def save_config():
    config_dir = os.path.dirname(os.path.abspath(CONFIG_FILE))
    os.makedirs(config_dir, exist_ok=True)
    fd = None
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix=".config-", suffix=".json.tmp", dir=config_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None
            json.dump(config, f, indent=4, ensure_ascii=False)
            f.write("\\n")
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


''', "save_config")

app = one(app, "    lock_path = token_file + \".lock\"\n    try:\n        from filelock import FileLock\n", "    lock_path = token_file + \".lock\"\n    try:\n        with open(lock_path, \"a\", encoding=\"utf-8\"):\n            pass\n        os.chmod(lock_path, 0o600)\n    except Exception:\n        pass\n    try:\n        from filelock import FileLock\n", "lock chmod")

app = one(app, "        pool = data.get(pool_name)\n        if not isinstance(pool, list):\n            pool = []\n", "        pool = data.get(pool_name)\n        if pool is None:\n            pool = []\n        elif not isinstance(pool, list):\n            raise RuntimeError(f\"本地 token 池 {pool_name} 不是列表，拒绝覆盖\")\n", "local pool guard")

app = one(app, "                    os.fsync(dst.fileno())\n            except Exception as exc:\n", "                    os.fsync(dst.fileno())\n                try:\n                    os.chmod(backup_path, 0o600)\n                except Exception:\n                    pass\n            except Exception as exc:\n", "backup chmod")

app = one(app, '''        temp_path = token_file + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, token_file)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
''', '''        fd, temp_path = tempfile.mkstemp(prefix=".token-", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\\n")
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
''', "token mkstemp")

remote_pool_new = "    pool = current.get(pool_name)\n    if pool is None:\n        pool = []\n    elif not isinstance(pool, list):\n        raise RuntimeError(f\"远端 token 池 {pool_name} 不是列表，拒绝全量覆盖\")\n"
if remote_pool_new not in app:
    app = one(app, "    pool = current.get(pool_name)\n    if not isinstance(pool, list):\n        pool = []\n", remote_pool_new, "remote pool guard")

app = between(app, "def cleanup_runtime_memory(log_callback=None, reason=\"定期清理\"):\n", "def refresh_active_page():\n", '''def cleanup_runtime_memory(log_callback=None, reason="定期清理"):
    if log_callback:
        log_callback(f"[*] {reason}: 关闭浏览器并清理内存")
    stop_browser()
    try:
        from cpa_xai.browser_confirm import shutdown_mint_browsers
        shutdown_mint_browsers()
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] CPA 浏览器清理失败: {exc}")
    collected = gc.collect()
    if log_callback:
        log_callback(f"[*] Python GC 已回收对象数: {collected}")


''', "cleanup")

app = one(app, "        self.ui_queue = queue.Queue()\n        self.accounts_output_file = \"\"\n", "        self.ui_queue = queue.Queue()\n        self._ui_thread_id = threading.get_ident()\n        self.accounts_output_file = \"\"\n", "ui thread id")

app = one(app, '''    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        self.log_text.insert(tk.END, f"{line}\n")
        self.log_text.see(tk.END)

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def update_stats(self):
        self.stats_var.set(f"成功: {self.success_count} | 失败: {self.fail_count}")

    def _set_running_ui(self, running):
        self.is_running = running
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        self.status_var.set("运行中..." if running else "就绪")
        self.status_label.config(foreground="blue" if running else "green")
''', '''    def _call_ui(self, func, *args):
        if threading.get_ident() == self._ui_thread_id:
            func(*args)
            return
        try:
            self.root.after(0, lambda: func(*args))
        except Exception:
            pass

    def _append_log_line(self, line):
        self.log_text.insert(tk.END, f"{line}\\n")
        self.log_text.see(tk.END)

    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        self._call_ui(self._append_log_line, line)

    def clear_log(self):
        self._call_ui(lambda: self.log_text.delete(1.0, tk.END))

    def update_stats(self):
        self._call_ui(lambda: self.stats_var.set(f"成功: {self.success_count} | 失败: {self.fail_count}"))

    def _set_running_ui(self, running):
        self.is_running = running
        def apply():
            self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
            self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
            self.status_var.set("运行中..." if running else "就绪")
            self.status_label.config(foreground="blue" if running else "green")
        self._call_ui(apply)
''', "GUI UI calls")

app = one(app, "        save_config()\n        if config[\"email_provider\"] == \"cloudflare\" and not config[\"cloudflare_api_base\"]:\n", "        try:\n            save_config()\n        except ConfigError as exc:\n            self.log(f\"[!] 配置保存失败: {exc}\")\n            return\n        if config[\"email_provider\"] == \"cloudflare\" and not config[\"cloudflare_api_base\"]:\n", "first save guard")
app = one(app, "        config[\"register_count\"] = count\n        save_config()\n        self.stop_requested = False\n", "        config[\"register_count\"] = count\n        try:\n            save_config()\n        except ConfigError as exc:\n            self.log(f\"[!] 配置保存失败: {exc}\")\n            return\n        self.stop_requested = False\n", "second save guard")
app = one(app, "        finally:\n            stop_browser()\n            self._set_running_ui(False)\n            self.log(\"[*] 任务结束\")\n", "        finally:\n            cleanup_runtime_memory(log_callback=self.log, reason=\"任务结束\")\n            self._set_running_ui(False)\n            self.log(\"[*] 任务结束\")\n", "GUI final cleanup")
app = one(app, "def main_cli():\n    load_config()\n    count = int(config.get(\"register_count\", 1) or 1)\n", "def main_cli():\n    try:\n        load_config()\n    except ConfigError as exc:\n        cli_log(f\"[!] {exc}\")\n        return\n    count = int(config.get(\"register_count\", 1) or 1)\n", "CLI config handling")
app = one(app, "    root = tk.Tk()\n    setup_light_theme(root)\n    app = GrokRegisterGUI(root)\n    root.mainloop()\n", "    root = tk.Tk()\n    setup_light_theme(root)\n    try:\n        app = GrokRegisterGUI(root)\n    except ConfigError as exc:\n        print(f\"[!] {exc}\", file=sys.stderr)\n        try:\n            messagebox.showerror(\"配置错误\", str(exc))\n        except Exception:\n            pass\n        root.destroy()\n        return\n    root.mainloop()\n", "GUI config handling")

ast.parse(app)
write(APP, app, "utf-8")

browser = read(BROWSER)
browser = one(browser, '''    resolved = resolve_proxy(proxy)
    proxy_bridge = None
    chrome_proxy, proxy_bridge = prepare_chromium_proxy(resolved, log=logger)
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
''', '''    resolved = resolve_proxy(proxy)
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
''', "CPA bridge cleanup")
ast.parse(browser)
write(BROWSER, browser)

oauth = read(OAUTH)
oauth = one(oauth, "                timeout=timeout,\n                proxy=proxy,\n                retries=2,\n                retry_sleep=1.0,\n            )\n            net_streak = 0\n", "                timeout=min(float(timeout), 5.0),\n                proxy=proxy,\n                retries=0,\n                retry_sleep=1.0,\n            )\n            net_streak = 0\n", "oauth poll timeout")
ast.parse(oauth)
write(OAUTH, oauth)

gitignore = read(GITIGNORE)
write(GITIGNORE, "\n".join(line for line in gitignore.splitlines() if line.strip() != "*.png").rstrip() + "\n")

for path in (APP, BROWSER, OAUTH):
    ast.parse(read(path, "utf-8"))

print("review fixes applied")
