<div align="center">

[![Grok Register — GUI, CLI and WebUI registration automation toolkit](assets/banner.png)](https://github.com/AaronL725/grok-register)

Grok Register 是一个面向自动化流程研究、测试环境验证和个人学习的 Python 工具。项目提供 GUI / CLI / WebUI、四种临时邮箱、可选 1–8 线程并发与账号级代理池，并集成 Chromium 页面自动化、账号安全落盘、pending 恢复、grok2api token 入池和可选 CPA xAI OIDC 凭证导出。

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/Interface-GUI%20%2B%20CLI%20%2B%20WebUI-success.svg" alt="GUI + CLI + WebUI">
  <img src="https://img.shields.io/badge/Parallel-1--8%20Workers-6f42c1.svg" alt="1-8 Workers">
  <img src="https://img.shields.io/badge/Proxy-direct%20%2F%20single%20%2F%20pool-orange.svg" alt="Proxy: direct / single / pool">
  <img src="https://img.shields.io/badge/Browser-Chromium%2FChrome-4285F4.svg" alt="Chromium/Chrome">
  <a href="http://makeapullrequest.com"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome"></a>
  <a href="https://linux.do"><img src="https://img.shields.io/badge/Join-linux.do-orange" alt="linux.do"></a>
</p>

<p align="center">
 <a href="https://www.star-history.com/aaronl725/grok-register">
  <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/badge?repo=AaronL725/grok-register&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/badge?repo=AaronL725/grok-register" />
   <img alt="Star History Rank" src="https://api.star-history.com/badge?repo=AaronL725/grok-register" />
  </picture>
 </a>
</p>

</div>

---

> [!IMPORTANT]
> 本项目仅用于自动化流程研究、测试环境验证和个人学习。使用者应自行遵守目标网站服务条款、当地法律法规和第三方服务限制。请勿将本项目用于滥用、绕过平台限制或未经授权的商业用途。

## 目录

- [项目功能](#项目功能)
- [快速开始](#快速开始)
- [运行方式](#运行方式)
- [配置说明](#配置说明)
- [代理与代理池](#代理与代理池)
- [可选多线程注册](#可选多线程注册)
- [grok2api token 入池](#grok2api-token-入池)
- [CPA / xAI OIDC 导出](#cpa--xai-oidc-导出)
- [输出与 pending 恢复](#输出与-pending-恢复)
- [项目结构](#项目结构)
- [常见问题](#常见问题)
- [License](#license)
- [Acknowledgments](#acknowledgments)
- [Star History](#star-history)

## 项目功能

Grok Register 使用真实 Chromium / Chrome 完成注册流程，并把 GUI、CLI 和 WebUI 都接到同一套注册核心上。

主要功能：

- 自动打开注册页、提交邮箱、轮询验证码、填写资料并获取 SSO cookie。
- 支持 **DuckMail / YYDS / Cloudflare 临时邮箱 / Cloud Mail** 四种邮箱来源。
- 支持 **GUI / CLI / WebUI** 三种操作入口。
- 支持可选 **1–8 线程并发注册**；默认关闭。
- 支持 `direct / single / pool` 代理模式、健康检查、冷却、订阅、固定/旋转节点和账号级稳定 Proxy Lease。
- 代理池可混合解析 **HTTP / HTTPS / SOCKS / VLESS / VMess / Trojan / Hysteria2 / TUIC / Shadowsocks** 节点。
- 支持注册后尝试开启 NSFW；失败不会丢失已经注册成功的账号。
- 支持 SSO 入库前筛查 `botFlagSource` / `policy=deny`；明确命中后隔离并跳过 grok2api / CPA。风控检查采用 fail-open：网络请求失败、HTTP 异常或未解析到风控字段时会记录诊断并继续入库。
- 支持把 SSO token 写入 grok2api 本地池或远端池。
- 支持可选 CPA xAI OIDC 凭证导出与 CLIProxyAPI hotload。
- 成功账号实时落盘；主账号结果写入失败时会进入对应的 `accounts_*.txt.pending.jsonl`，可稍后幂等恢复。风控隔离写入失败使用独立的 risk pending，不与普通账号 pending 混用。
- 支持停止任务、浏览器重启、邮箱重试、运行时清理和后处理错误隔离。

单个账号的主要流程：

```text
打开注册页
  → 创建邮箱并提交
  → 获取并填写验证码
  → 填写资料
  → 获取 SSO cookie
  → 可选开启 NSFW
  → SSO 风控筛查（botFlagSource / policy）
  → 保存账号
  → 可选写入 grok2api
  → 可选导出 CPA/OIDC
```

> grok2api 入池和 CPA/OIDC 都属于注册后的附加后处理。后处理失败会记录警告，但不会把已经保存成功的账号重新算作注册失败。SSO 风控命中时不会写入主账号文件，也不会进入 grok2api / CPA。

## 快速开始

### 1. 环境要求

- Python **3.9+**
- Google Chrome 或 Chromium
- 可访问注册页面和所选邮箱 API 的网络环境
- GUI 需要 Tkinter；没有 Tkinter 时可以使用 CLI 或 WebUI
- **仅当使用 VLESS / VMess / Trojan / Hysteria2 / TUIC / Shadowsocks 节点时需要 sing-box**；HTTP/HTTPS/SOCKS 继续使用项目原生代理实现

### 2. 安装

```bash
git clone https://github.com/AaronL725/grok-register.git
cd grok-register

python -m venv .venv
```

激活虚拟环境：

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

安装核心依赖：

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

复制配置文件：

```bash
# macOS / Linux
cp config.example.json config.json

# Windows CMD
copy config.example.json config.json
```

### 3. 先完成最小配置

如果先使用 DuckMail，可以从下面这组最小运行配置开始：

```json
{
  "email_provider": "duckmail",
  "duckmail_api_key": "",
  "register_count": 1,
  "proxy_mode": "auto",
  "proxy": "",
  "multi_thread_enabled": false,
  "cpa_export_enabled": false
}
```

然后根据 `email_provider` 和需要启用的后处理功能继续填写对应配置。完整字段见 [`config.example.json`](config.example.json)。

> `config.example.json` 是完整字段模板，其中的 `example.com`、`temp-mail.example.com` 等均为占位值，并不是可直接使用的服务地址。若使用 Cloudflare / Cloud Mail / YYDS，请先填写对应服务参数。

### 4. 启动

GUI：

```bash
python grok_register_ttk.py
```

WebUI：

```bash
python -m pip install -r requirements-web.txt
python -m web.server
```

访问：

```text
http://127.0.0.1:8092
```

> GUI、CLI 和 WebUI 共用同一个 `config.json` 和同一套注册逻辑。建议同一时间只使用一个入口启动任务。

## 运行方式

### WebUI（可选）

```bash
python -m pip install -r requirements-web.txt
python -m web.server
```

WebUI 默认监听 `127.0.0.1:8092`，提供中英双语配置、开始/停止、批次统计、实时日志、代理池节点状态、订阅解析统计、重新加载和手动测试。

### GUI

```bash
python grok_register_ttk.py
```

GUI 可以直接配置主要邮箱、代理、代理池、多线程和注册参数，然后点击“开始注册”。

### CLI

以下三种写法等价：

```bash
python grok_register_ttk.py cli
python grok_register_ttk.py start
python grok_register_ttk.py --cli
```

CLI 读取 `config.json`，通过校验后提示：

```text
> start
```

输入 `start` 才正式运行；按 `Ctrl+C` 可请求停止。

> CLI 只是省略 Tk GUI，注册页面仍然会使用真实 Chromium / Chrome。

## 配置说明

项目启动时做结构校验，真正开始任务时再检查当前启用功能所需字段，因此可以先打开 GUI / WebUI 再逐步配置。

### 基础配置

| 配置项 | 说明 |
| --- | --- |
| `email_provider` | `duckmail` / `yyds` / `cloudflare` / `cloudmail` |
| `register_count` | 本批次注册数量 |
| `enable_nsfw` | 注册后是否尝试开启 NSFW |
| `sso_risk_gate_enabled` | 入库前是否检查 grok.com `botFlagSource` / `policy=deny`，默认 `true` |
| `sso_risk_rejected_file` | 被风控隔离的 SSO 记录文件，默认 `./sso_risk_rejected.txt` |
| `user_agent` | Chromium 和请求使用的 User-Agent |
| `proxy_mode` | `auto` / `direct` / `single` / `pool` |
| `proxy` | 单代理地址；`auto` 模式下留空即直连 |
| `multi_thread_enabled` | 是否启用并发注册，默认 `false` |
| `multi_thread_workers` | 并发 worker 数，范围 `1–8` |

### 邮箱服务

#### DuckMail

```json
{
  "email_provider": "duckmail",
  "duckmail_api_key": ""
}
```

#### YYDS

```json
{
  "email_provider": "yyds",
  "yyds_api_key": "",
  "yyds_jwt": ""
}
```

`yyds_api_key` 和 `yyds_jwt` 至少填写一个。

#### Cloudflare 临时邮箱

常用字段：

| 配置项 | 说明 |
| --- | --- |
| `cloudflare_api_base` | 邮箱 API 根地址 |
| `cloudflare_api_key` | 与 `cloudflare_auth_mode` 配套使用的认证凭据：`none` 时可留空；`bearer` 时作为 Bearer Token；`x-api-key` 时作为 `X-API-Key`；`x-admin-auth` 时作为 Admin Password；`query-key` 时作为 URL `key` 参数 |
| `cloudflare_auth_mode` | `none` / `bearer` / `x-api-key` / `x-admin-auth` / `query-key` |
| `cloudflare_path_accounts` | 创建邮箱接口 |
| `cloudflare_path_messages` | 邮件列表接口 |
| `defaultDomains` | 默认收信域名；多个域名用英文逗号分隔 |

匿名创建示例：

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://你的-worker-api-域名",
  "cloudflare_api_key": "",
  "cloudflare_auth_mode": "none",
  "cloudflare_path_accounts": "/api/new_address",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "example.com"
}
```

Admin 创建示例：

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://你的-worker-api-域名",
  "cloudflare_api_key": "你的 ADMIN_PASSWORD",
  "cloudflare_auth_mode": "x-admin-auth",
  "cloudflare_path_accounts": "/admin/new_address",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "example.com"
}
```

#### Cloud Mail 无人收件模式

```json
{
  "email_provider": "cloudmail",
  "cloudmail_api_base": "https://你的-Cloud-Mail-域名",
  "cloudmail_public_token": "公共 API Token",
  "cloudmail_domains": "example.com,example.net",
  "cloudmail_path_messages": "/api/public/emailList"
}
```

## 代理与代理池

默认：

```json
{
  "proxy_mode": "auto",
  "proxy": ""
}
```

`auto` 用于兼容传统单代理配置：`proxy` 为空时直连，非空时使用该代理。

### 单代理

原生代理：

```json
{
  "proxy_mode": "single",
  "proxy": "http://user:password@127.0.0.1:7890"
}
```

`single` 也可以直接填写受支持的高级协议 URI；高级协议需要本机可执行的 `sing-box`。

### 代理池

```json
{
  "proxy_mode": "pool",
  "proxy_fallback": "none",
  "proxy_pool_file": "./proxies.txt",
  "proxy_pool_subscription_url": "",
  "proxy_pool_endpoint_mode": "auto",
  "proxy_pool_max_concurrent_per_node": 1,
  "proxy_protocol_backend": "auto",
  "proxy_singbox_path": "",
  "proxy_protocol_start_timeout_sec": 10,
  "proxy_runtime_idle_ttl_sec": 120,
  "proxy_runtime_cache_max": 32
}
```

代理源支持普通文本或整份 Base64 编码，解码后可以混合：

```text
http://...
socks5://...
vless://...
vmess://...
trojan://...
hysteria2://...
tuic://...
ss://...
```

当前支持：

- HTTP / HTTPS / SOCKS / SOCKS4 / SOCKS4A / SOCKS5 / SOCKS5H
- VLESS / VMess / Trojan / Hysteria2 (`hy2`) / TUIC / Shadowsocks (`ss`)
- 本地文件与 HTTP/HTTPS 订阅
- 标准 Base64 与 URL-safe Base64 订阅
- VLESS/VMess/Trojan 常见 TCP/WS/gRPC/HTTP/HTTPUpgrade/QUIC transport
- VLESS TLS / uTLS / Reality 常见参数
- 节点解析统计、健康探测、失败冷却和自动恢复
- 固定/旋转入口、`{account}`、并发限制和账号级稳定 Proxy Lease

代理 runtime 采用 lazy + idle cache 机制：节点只有在实际被选中、probe 或 preflight 时才建立本地 runtime。需要统一 HTTP 出口的原生代理会使用 `LocalProxyBridge`；VLESS / VMess / Trojan / Hysteria2 / TUIC / Shadowsocks 使用 sing-box。Lease 引用数降为 0 后 runtime 默认不会立即退出，而是进入空闲缓存；默认 `proxy_runtime_idle_ttl_sec=120`、`proxy_runtime_cache_max=32`，TTL 到期、缓存淘汰或 Manager shutdown 时才会关闭。设置 `proxy_runtime_idle_ttl_sec=0` 可恢复零引用立即关闭。

同一个账号 attempt 内，浏览器、邮箱、NSFW 和默认 CPA 保持同一个 Lease。等待验证码期间若确认尚未取得可用验证码，会在同一个 Lease 内更换邮箱重试；一旦进入验证码填写/提交阶段，后续异常不会再通过换邮箱或换代理重放注册，而会按“结果不确定”处理。

完整参数、协议映射、运行时和健康度规则见 [`docs/proxy-pool.md`](docs/proxy-pool.md)。

## 可选多线程注册

默认关闭：

```json
{
  "multi_thread_enabled": false,
  "multi_thread_workers": 4
}
```

需要并发时：

```json
{
  "multi_thread_enabled": true,
  "multi_thread_workers": 4
}
```

- worker 范围 `1–8`，实际数量不会超过 `register_count`。
- 每个 worker 使用独立邮箱模块和浏览器运行状态。
- 共享输出使用锁保护。
- 代理健康状态由所有 worker 共享，但每个账号拥有独立 Proxy Lease。

## grok2api token 入池

所有入池功能都是可选的。

### 本地池

```json
{
  "grok2api_auto_add_local": true,
  "grok2api_local_token_file": "",
  "grok2api_pool_name": "ssoBasic"
}
```

### 远端池

远端支持两种凭据方式，二选一：

1. `grok2api_remote_app_key`
2. `grok2api_remote_admin_username` + `grok2api_remote_admin_password`

```json
{
  "grok2api_auto_add_remote": true,
  "grok2api_remote_base": "https://你的-grok2api-域名",
  "grok2api_remote_app_key": "",
  "grok2api_remote_admin_username": "admin",
  "grok2api_remote_admin_password": "你的管理员密码",
  "grok2api_pool_name": "ssoBasic",
  "grok2api_allow_legacy_full_save": false
}
```

两套远端凭据不能同时填写。新版管理员账号/密码模式对非本机地址强制要求 HTTPS；`localhost` / `127.0.0.1` / `::1` 可以使用 HTTP。旧版 `app_key` 兼容接口当前接受 HTTP/HTTPS，但远程部署仍建议使用 HTTPS。

## CPA / xAI OIDC 导出

```json
{
  "cpa_export_enabled": true,
  "cpa_auth_dir": "./cpa_auths",
  "cpa_copy_to_hotload": false,
  "cpa_hotload_dir": "",
  "cpa_base_url": "https://cli-chat-proxy.grok.com/v1",
  "cpa_proxy": "",
  "cpa_headless": false,
  "cpa_force_standalone": true,
  "cpa_mint_timeout_sec": 300,
  "cpa_mint_cookie_inject": true,
  "cpa_oidc_request_timeout_sec": 15,
  "cpa_oidc_poll_timeout_sec": 15,
  "api_reverse_tools": ""
}
```

- `cpa_copy_to_hotload=true` 时必须填写 `cpa_hotload_dir`。
- 显式 `cpa_proxy` 始终优先。
- 未配置 `cpa_proxy` 且当前账号使用 Proxy Lease 时，CPA 会继承同一个出口，包括高级协议对应的 localhost runtime。
- CPA 导出失败只记录后处理警告，不会删除已保存账号。

## 输出与 pending 恢复

| 文件 / 目录 | 内容 |
| --- | --- |
| `accounts_*.txt` | 已成功保存的账号、密码和 SSO token |
| `<sso_risk_rejected_file>` | 被 `botFlagSource=1/2` 或 `policy=deny` 隔离的 SSO；默认 `./sso_risk_rejected.txt` |
| `mail_credentials.txt` | 注册过程中创建的临时邮箱地址与邮箱凭据；邮箱创建后会在提交注册前提前持久化，因此可能包含后续失败、重试或结果不确定 attempt 的记录 |
| `accounts_*.txt.pending.jsonl` | 已注册成功但主账号结果文件未成功写入的普通账号 pending；可使用 `retry-pending` 恢复 |
| `<sso_risk_rejected_file>.pending.jsonl` | 风控账号写入主隔离文件失败后的独立 risk pending；不要使用普通 `retry-pending` 恢复 |
| `<grok2api_local_token_file>` | 可选 grok2api 本地 token 池；留空时默认项目目录下 `token.json` |
| `<cpa_auth_dir>/xai-*.json` | 可选 CPA xAI OIDC 凭证；默认目录 `./cpa_auths` |
| `<cpa_auth_dir>/cpa_auth_failed.txt` | CPA 导出失败记录 |
| `screenshots/` | CPA 浏览器失败调试截图 |

### 恢复 pending

```bash
python grok_register_ttk.py retry-pending <pending文件> [输出文件]
```

恢复过程使用文件锁、去重和原子替换，重复执行不会重复写入已经恢复成功的同一账号。

> `retry-pending` **只用于普通账号结果 pending**（例如 `accounts_*.txt.pending.jsonl`），不适用于 `<sso_risk_rejected_file>.pending.jsonl`。风控 risk pending 是独立隔离队列，成功恢复后应进入配置的 `sso_risk_rejected_file`；当前没有对应的 CLI 子命令，内部恢复入口为 `sso_risk.retry_sso_risk_pending_file()`。

## 项目结构

```text
.
├── grok_register_ttk.py       # GUI / CLI 入口与主适配层
├── registration_flow.py       # GUI / CLI / WebUI 共用注册状态机、批量编排与阶段感知重试
├── registration_parallel.py   # 可选多 worker 并发协调器
├── registration_browser.py    # Chromium 注册页面状态与提交逻辑
├── browser_runtime.py         # 共享 HTTP、Chromium Options 与代理注入
├── proxy_pool.py              # proxy_pool_v3 的兼容导出层
├── proxy_pool_v3.py           # 代理池核心：Source、Lease、健康度、冷却、刷新与 Probe
├── proxy_bridge.py            # HTTP/HTTPS/SOCKS → localhost HTTP 代理桥与 Chromium 兼容
├── proxy_protocols.py         # HTTP/SOCKS/VLESS/VMess/Trojan/HY2/TUIC/SS 订阅解析
├── proxy_protocol_runtime.py  # Native bridge / sing-box lazy runtime 与 idle cache
├── mail_service.py            # 四种邮箱服务
├── app_config.py              # 默认配置、校验、加载与保存
├── account_outputs.py         # 账号、pending 与 token 输出
├── sso_risk.py                # SSO botFlag / policy 早停
├── cpa_export.py              # CPA/OIDC 导出入口
├── cpa_xai/                   # CPA 浏览器、OAuth、代理辅助与凭证写入
├── web/
│   ├── server.py              # FastAPI WebUI 控制层
│   ├── index.html             # WebUI 页面
│   ├── proxy-pool.js          # 代理池 WebUI 交互
│   └── proxy-pool.css         # 代理池 WebUI 样式
├── docs/proxy-pool.md         # 代理池详细说明
├── config.example.json        # 完整配置示例
├── requirements.txt           # 核心依赖
├── requirements-web.txt       # WebUI 可选依赖
└── tests/                     # 单元与兼容回归测试
```

## 常见问题

### CLI 为什么仍然打开浏览器？

CLI 只是不启动 Tk GUI。注册页交互、验证码提交和 SSO cookie 获取仍依赖真实 Chromium / Chrome。

### GUI 无法启动怎么办？

确认 Python 环境包含 Tkinter。Linux 发行版可能需要单独安装 `python3-tk`。也可以改用 CLI 或 WebUI。

### 为什么高级协议节点显示 unavailable？

VLESS / VMess / Trojan / Hysteria2 / TUIC / Shadowsocks 需要本地 sing-box。默认从系统 `PATH` 查找，也可以在 WebUI / `config.json` 设置 `proxy_singbox_path`。HTTP/HTTPS/SOCKS 不受影响。

### 为什么某些 V2Ray 订阅节点会被跳过？

WebUI 会显示订阅协议数量和解析错误。无法映射的 transport 或无效 URI 会只跳过对应节点，不影响同一订阅里的其他有效节点。详细映射范围见 [`docs/proxy-pool.md`](docs/proxy-pool.md)。

### 为什么配置文件不完整时 GUI / WebUI 仍能打开？

配置保存和运行校验分开。界面允许先打开并编辑配置，开始注册时才检查当前启用服务所需字段。

### 注册成功后 grok2api 或 CPA 失败怎么办？

账号本身仍然属于成功。此类错误只计入“后处理警告”。

### NSFW 开启失败会丢失账号吗？

不会。NSFW 是可选步骤，失败后仍会继续保存账号。

### 代理池为什么显示用户名和密码？

当前 WebUI 按个人部署场景设计，会显示完整代理节点和认证信息。不要把 WebUI 暴露到不受信任的网络环境。

### 如何查看代理池更详细的参数？

参见 [`docs/proxy-pool.md`](docs/proxy-pool.md)。

### 为什么账号会进入 pending？

普通 `accounts_*.txt.pending.jsonl` 表示注册已经完成，但主账号结果文件没有成功写入；使用 `retry-pending` 恢复即可，不需要重新注册。

如果是 `<sso_risk_rejected_file>.pending.jsonl`，则表示账号已经明确命中风控，但主隔离文件写入失败。这是独立 risk pending，不能使用普通 `retry-pending`。

## License

[MIT](LICENSE).

## Acknowledgments

Thanks to [linux.do](https://linux.do) — a vibrant tech community where this project is shared and discussed.

## Star History

<a href="https://www.star-history.com/?repos=AaronL725%2Fgrok-register&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=AaronL725/grok-register&type=date&theme=dark&legend=top-left&sealed_token=VULsKQIgBogi6zyY1L6IOYiMLw4H0evK6wIsKCUK3xC92v3ghjcba4-Ls0iH4o8tQPw-GCBrMvouvn5Vf-rpFK08_Djz8fAy2ABgtDO1piH286QhqUHJS1qlVi19tpWDKv_5h3I1-l2T9q4OPDkpKLdE2NYkmmgUPtvzFmisyzI36efqn_3vL06Wg-Qd" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=AaronL725/grok-register&type=date&legend=top-left&sealed_token=VULsKQIgBogi6zyY1L6IOYiMLw4H0evK6wIsKCUK3xC92v3ghjcba4-Ls0iH4o8tQPw-GCBrMvouvn5Vf-rpFK08_Djz8fAy2ABgtDO1piH286QhqUHJS1qlVi19tpWDKv_5h3I1-l2T9q4OPDkpKLdE2NYkmmgUPtvzFmisyzI36efqn_3vL06Wg-Qd" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=AaronL725/grok-register&type=date&legend=top-left&sealed_token=VULsKQIgBogi6zyY1L6IOYiMLw4H0evK6wIsKCUK3xC92v3ghjcba4-Ls0iH4o8tQPw-GCBrMvouvn5Vf-rpFK08_Djz8fAy2ABgtDO1piH286QhqUHJS1qlVi19tpWDKv_5h3I1-l2T9q4OPDkpKLdE2NYkmmgUPtvzFmisyzI36efqn_3vL06Wg-Qd" />
 </picture>
</a>
