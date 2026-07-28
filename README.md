<div align="center">

[![Grok Register — GUI and CLI registration automation toolkit](assets/banner.png)](https://github.com/AaronL725/grok-register)

Grok Register 是一个面向自动化流程研究、测试环境验证和个人学习的 Python 工具。项目提供 GUI / CLI、四种临时邮箱接入、Chromium 页面自动化、账号安全落盘、pending 恢复、grok2api token 入池，以及可选的 CPA xAI OIDC 凭证导出。

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/Interface-GUI%20%2B%20CLI-success.svg" alt="GUI + CLI">
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

- [当前功能](#当前功能)
- [运行流程](#运行流程)
- [环境要求](#环境要求)
- [安装](#安装)
- [配置](#配置)
- [运行方式](#运行方式)
- [输出与 pending 恢复](#输出与-pending-恢复)
- [稳定性与安全机制](#稳定性与安全机制)
- [项目架构](#项目架构)
- [常见问题](#常见问题)
- [License](#license)
- [Acknowledgments](#acknowledgments)
- [Star History](#star-history)

## 当前功能

- 使用真实 Chromium / Chrome 页面完成注册、验证码、资料填写、Turnstile 与 SSO cookie 获取。
- 支持四种邮箱服务：
  - DuckMail
  - YYDS
  - Cloudflare 临时邮箱
  - Cloud Mail 无人收件模式
- 成功账号实时写入 `accounts_*.txt`。
- 主结果写入失败时自动写入 `*.pending.jsonl`，可稍后幂等恢复。
- 支持将 SSO token 写入 grok2api 本地池和远端池。
- 支持注册成功后可选导出 CLIProxyAPI 使用的 CPA xAI OIDC 凭证。
- 支持注册后尝试开启 NSFW；失败不会影响账号保存。
- 支持浏览器重启、卡住重试、邮箱更换、定期内存清理和安全取消。
- GUI / CLI 均展示四项批次状态：
  - 成功
  - 失败
  - 待恢复
  - 后处理警告

## 运行流程

单个账号的主要流程如下：

```text
打开注册页
  → 创建临时邮箱并提交
  → 轮询并填写验证码
  → 填写资料
  → 等待 SSO cookie
  → 可选开启 NSFW
  → 保存账号
  → 可选写入 grok2api
  → 可选导出 CPA/OIDC
```

账号已经注册成功后，token 入池或 CPA 导出属于**附加后处理**。附加功能失败只会增加“后处理警告”，不会把已经保存的账号重新统计为注册失败。

## 环境要求

- Python **3.9+**
- Google Chrome 或 Chromium
- 可访问注册页面和所选邮箱 API 的网络环境
- GUI 模式需要 Tkinter；没有 Tkinter 时可使用 CLI 模式

## 安装

克隆仓库：

```bash
git clone https://github.com/AaronL725/grok-register.git
cd grok-register
```

建议创建虚拟环境：

```bash
python -m venv .venv
```

激活虚拟环境：

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

安装依赖：

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

然后编辑 `config.json`。该文件包含 API Key、JWT、代理和远端服务密钥等。

## 配置

配置校验分为两层：

1. **结构校验**：检查类型、枚举、URL 和数值范围。GUI 启动时只执行这一层，因此旧配置缺少当前服务所需字段时仍可打开界面修改。
2. **运行校验**：点击“开始注册”或启动 CLI 任务时，检查当前启用功能所需的配置。

### 基础配置

| 配置项 | 说明 |
| --- | --- |
| `email_provider` | `duckmail`、`yyds`、`cloudflare` 或 `cloudmail` |
| `register_count` | 本批次目标数量，允许范围由配置校验控制 |
| `proxy` | 主注册流程代理，可留空 |
| `enable_nsfw` | 注册后是否尝试开启 NSFW |
| `user_agent` | 浏览器和请求使用的 User-Agent |

### DuckMail

| 配置项 | 说明 |
| --- | --- |
| `duckmail_api_key` | 可选 DuckMail API Key |

### YYDS

| 配置项 | 说明 |
| --- | --- |
| `yyds_api_key` | YYDS API Key |
| `yyds_jwt` | YYDS JWT |

选择 `yyds` 时，`yyds_api_key` 和 `yyds_jwt` 至少配置一个，否则运行校验会直接拒绝启动。

### Cloudflare 临时邮箱

| 配置项 | 说明 |
| --- | --- |
| `cloudflare_api_base` | Cloudflare 临时邮箱 API 根地址 |
| `cloudflare_api_key` | 匿名模式留空；admin 模式填写 `ADMIN_PASSWORD` |
| `cloudflare_auth_mode` | `none`、`bearer`、`x-api-key`、`x-admin-auth` 或 `query-key` |
| `cloudflare_path_domains` | 域名列表路径，默认 `/api/domains` |
| `cloudflare_path_accounts` | 创建邮箱路径，默认 `/api/new_address` |
| `cloudflare_path_token` | token 路径，默认 `/api/token` |
| `cloudflare_path_messages` | 收件列表路径，默认 `/api/mails` |
| `defaultDomains` | 默认收信域名；多个域名用英文逗号分隔并轮换使用 |

#### 匿名创建模式

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://你的-worker-api-域名",
  "cloudflare_api_key": "",
  "cloudflare_auth_mode": "none",
  "cloudflare_path_domains": "/api/domains",
  "cloudflare_path_accounts": "/api/new_address",
  "cloudflare_path_token": "/api/token",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "example.com"
}
```

#### Admin 创建模式

当匿名 `/api/new_address` 受 Turnstile 限制时，可使用：

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

Admin 密码只用于创建邮箱。读取邮件仍使用创建接口返回的邮箱 JWT。

可先使用调试脚本验证接口：

```bash
python cf_mail_debug.py \
  --api-base "https://你的-worker-api-域名" \
  --auth-mode x-admin-auth \
  --api-key "你的 ADMIN_PASSWORD" \
  --create-path /admin/new_address \
  --domain "example.com"
```

### Cloud Mail 无人收件模式

| 配置项 | 说明 |
| --- | --- |
| `cloudmail_api_base` | Cloud Mail 站点根地址 |
| `cloudmail_public_token` | 公共收件 API Token |
| `cloudmail_domains` | 无人收件域名，多个域名用英文逗号分隔 |
| `cloudmail_path_messages` | 默认 `/api/public/emailList` |

示例：

```json
{
  "email_provider": "cloudmail",
  "cloudmail_api_base": "https://你的-Cloud-Mail-域名",
  "cloudmail_public_token": "公共 API Token",
  "cloudmail_domains": "example.com,example.net",
  "cloudmail_path_messages": "/api/public/emailList"
}
```

Cloud Mail 模式直接生成随机地址，不预先创建邮箱账户。公共 Token 只从 `config.json` 读取，不会作为邮箱 credential 写入 `mail_credentials.txt`。

### grok2api token 池

| 配置项 | 说明 |
| --- | --- |
| `grok2api_auto_add_local` | 是否写入本地 token 池 |
| `grok2api_local_token_file` | 本地 `token.json` 路径；留空使用项目默认路径 |
| `grok2api_pool_name` | `ssoBasic` 或 `ssoSuper` |
| `grok2api_auto_add_remote` | 是否写入远端 token 池 |
| `grok2api_remote_base` | 站点根地址、`/admin` 或 `/admin/api` 地址 |
| `grok2api_remote_app_key` | 旧版远端管理 API 的 app key |
| `grok2api_remote_admin_username` | 新版 `chenyme/grok2api` 管理员账号 |
| `grok2api_remote_admin_password` | 新版 `chenyme/grok2api` 管理员密码 |
| `grok2api_allow_legacy_full_save` | 是否允许旧版全量保存回退；默认关闭 |

远端入池会根据凭据自动选择版本：填写 `app_key` 使用旧版 `/tokens/add`；填写管理员账号和密码则通过新版 `/api/admin/v1/accounts/web/import` 导入 Grok Web。两套凭据不能同时填写。新版管理请求默认直连，远程地址必须使用 HTTPS，本机地址可使用 HTTP。旧版全量保存默认关闭，以避免并发覆盖。

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

### CPA / xAI OIDC 导出

| 配置项 | 说明 |
| --- | --- |
| `cpa_export_enabled` | 是否在注册成功后导出 CPA xAI OIDC 凭证 |
| `cpa_auth_dir` | 输出目录，默认 `./cpa_auths` |
| `cpa_copy_to_hotload` | 是否复制到 CLIProxyAPI auth-dir |
| `cpa_hotload_dir` | 热加载目录；仅导出开启且复制开启时必填 |
| `cpa_base_url` | CPA 凭证中的 API Base URL |
| `cpa_proxy` | CPA 专用代理；留空回退到主 `proxy` |
| `cpa_headless` | CPA 浏览器是否无头；默认建议 `false` |
| `cpa_force_standalone` | 是否使用独立 CPA 浏览器会话 |
| `cpa_mint_timeout_sec` | 浏览器授权整体超时 |
| `cpa_mint_cookie_inject` | 是否向 CPA 会话注入已取得的 cookie |
| `cpa_oidc_request_timeout_sec` | Device Authorization 请求超时 |
| `cpa_oidc_poll_timeout_sec` | 单次 token 轮询请求超时 |
| `api_reverse_tools` | 可选外部 `cpa_xai` 包目录 |

最小配置：

```json
{
  "cpa_export_enabled": true,
  "cpa_auth_dir": "./cpa_auths",
  "cpa_base_url": "https://cli-chat-proxy.grok.com/v1",
  "cpa_proxy": "",
  "cpa_headless": false,
  "cpa_force_standalone": true,
  "cpa_mint_cookie_inject": true
}
```

CPA 浏览器直接复用 `browser_runtime.py` 的 Chromium options 和 `cpa_xai/proxyutil.py` 的代理桥，不会反向导入主程序或创建第二份主模块全局状态。

## 运行方式

### GUI

```bash
python grok_register_ttk.py
```

GUI 启动时读取配置并执行结构校验。填写配置后点击“开始注册”，程序会执行完整运行校验，只保存一次配置，然后启动后台线程。

每个新批次开始前，成功、失败、待恢复和后处理警告四项统计都会全部清零。

### CLI

以下命令等价：

```bash
python grok_register_ttk.py cli
python grok_register_ttk.py start
python grok_register_ttk.py --cli
```

CLI 读取 `config.json` 中的 `register_count`，通过运行校验后提示：

```text
> start
```

输入 `start` 才会开始。按 `Ctrl+C` 可请求停止并执行最终清理。

> CLI 只是不启动 Tk GUI，注册过程仍会打开 Chromium / Chrome。

### 恢复 pending 结果

```bash
python grok_register_ttk.py retry-pending <pending文件> [输出文件]
```

示例：

```bash
python grok_register_ttk.py retry-pending accounts_20260715_120000.txt.pending.jsonl
```

指定其他输出文件：

```bash
python grok_register_ttk.py retry-pending \
  accounts_20260715_120000.txt.pending.jsonl \
  recovered_accounts.txt
```

程序会拒绝把 pending 输入文件本身作为输出文件。

## 输出与 pending 恢复

运行过程中可能生成：

| 文件 | 内容 |
| --- | --- |
| `accounts_*.txt` | 已成功保存的账号、密码和 SSO token |
| `mail_credentials.txt` | 临时邮箱地址与邮箱凭证 |
| `*.pending.jsonl` | 已注册但主结果文件未成功写入的账号 |
| `*.pending.jsonl.lock` | pending 恢复独占锁 |
| 本地 `token.json` | 可选 grok2api 本地池 |
| `cpa_auths/xai-*.json` | 可选 CPA xAI OIDC 凭证 |
| `cpa_auths/cpa_auth_failed.txt` | CPA 导出失败记录 |
| `screenshots/` | CPA 浏览器失败调试截图 |

pending 恢复具有以下保护：

- 使用 `filelock` 对同一 pending 文件加独占锁；
- 读取、恢复、重写或删除 pending 文件均在锁内完成；
- 主结果文件按 `email+sso` 去重；
- 已存在的记录直接视为恢复成功；
- pending 文件使用临时文件和原子替换更新；
- 输入路径与输出路径相同会被拒绝。

因此进程在“账号已追加、pending 尚未更新”之间中断后，重复执行恢复不会重复写入同一个账号。

## 稳定性与安全机制

### 批量流程

- 邮箱验证码失败时可更换邮箱重试。
- 页面流程卡住时按当前账号槽位重试，达到上限后才计为失败。
- 每个账号之间重启或重新创建浏览器。
- 每成功 5 个账号默认执行一次运行时清理。
- 定期清理失败只记录警告，不修改账号统计。
- 用户在账号间取消时设置批次 `cancelled` 状态并正常结束。
- 最终清理异常不会覆盖原始任务异常。
- GUI observer 异常不会终止批量流程。

### 文件写入

- 配置、本地 token 池和 pending 更新采用临时文件加原子替换。
- 本地 token 池使用文件锁，损坏 JSON 不会被静默覆盖。
- 已存在 token 会被去重。

### 后处理隔离

- 主账号保存完成后，token 入池和 CPA 导出分别捕获异常。
- 一个后处理步骤失败不会阻止另一个步骤执行。
- 后处理失败不会把账号重新归类为注册失败。

## 项目架构

```text
.
├── grok_register_ttk.py       # GUI、CLI、参数入口和兼容适配层
├── registration_flow.py       # GUI / CLI 唯一批量编排入口
├── app_config.py              # 默认配置、加载保存、结构校验与运行校验
├── account_outputs.py         # 账号输出、pending、token 池和原子写入
├── mail_service.py            # DuckMail、YYDS、Cloudflare、Cloud Mail
├── browser_runtime.py         # HTTP、代理和 Chromium options
├── registration_browser.py    # 主注册浏览器生命周期与页面自动化
├── cf_mail_debug.py           # Cloudflare 邮箱调试 CLI
├── cpa_export.py              # CPA/OIDC 导出兼容入口
├── cpa_xai/
│   ├── browser_session.py     # CPA 浏览器创建、复用、cookie 与清理
│   ├── browser_confirm.py     # 登录、授权页面与 mint 编排
│   ├── oauth_device.py        # Device Authorization 与 token 轮询
│   ├── proxyutil.py           # 项目唯一认证代理桥实现
│   ├── mint.py                # 凭证 mint 流程
│   ├── schema.py              # CPA 输出结构
│   └── writer.py              # 凭证文件写入
├── config.example.json        # 完整配置示例
├── requirements.txt           # Python 依赖
├── tests/                     # 单元与兼容回归测试
├── turnstilePatch/            # 浏览器扩展资源
├── assets/                    # README 资源
└── README.md
```

## 常见问题

### CLI 为什么仍然打开浏览器？

CLI 仅省略 Tk GUI。注册页交互、Turnstile、验证码提交和 SSO cookie 获取仍依赖真实 Chromium 环境。

### GUI 无法启动怎么办？

确认当前 Python 包含 Tkinter。Linux 发行版可能需要单独安装系统包，例如 `python3-tk`。也可以改用：

```bash
python grok_register_ttk.py cli
```

### 为什么配置文件不完整时 GUI 仍能打开？

这是预期行为。GUI 启动只做结构校验，方便在界面中修正服务商配置；点击开始时才做运行校验。

### 为什么账号成功数少于实际注册完成数？

“成功”表示账号注册完成且主结果文件已经保存。注册完成但主文件写入失败的账号会显示在“待恢复”，并写入 pending 文件。

### 什么是后处理警告？

账号已经保存，但 grok2api 入池或 CPA 导出至少一项失败。账号本身仍属于成功，不需要重新注册。

### NSFW 开启失败会丢失账号吗？

不会。NSFW 是可选步骤，失败会记录警告并继续保存账号。

### 远端 grok2api 为什么拒绝旧版全量写入？

全量读改写在多进程环境中可能覆盖其他实例刚写入的 token。项目默认只接受增量接口；显式允许旧版回退时仍要求 ETag 并使用条件写入。

### CPA 热加载目录为什么没有配置也能启动？

只有同时启用 `cpa_export_enabled=true` 和 `cpa_copy_to_hotload=true` 时，`cpa_hotload_dir` 才是必填项。

## License

[MIT](LICENSE).

## Acknowledgments

Thanks to [linux.do](https://linux.do) — a vibrant tech community where this project is shared and discussed.

## Star History

<a href="https://www.star-history.com/?repos=AaronL725%2Fgrok-register&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=AaronL725/grok-register&type=date&theme=dark&legend=top-left&sealed_token=uCM--S2xEp0n8rFUZHUg6wUJOgYcfO4XEVCIF9UZAT04YjL9YsMEOVOGAOlQfqwsoS7cQef0Rwc1cYCY4lAmTuMmcg-hKzNnx1A7KNekuCXQotFd4YifLIkvJWOEy5vxiREJX80Mwxbr8F-3GfCv0utIsQz_iq19nS57svUqwv0mSosV8OTxqXTLjmsI" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=AaronL725/grok-register&type=date&legend=top-left&sealed_token=uCM--S2xEp0n8rFUZHUg6wUJOgYcfO4XEVCIF9UZAT04YjL9YsMEOVOGAOlQfqwsoS7cQef0Rwc1cYCY4lAmTuMmcg-hKzNnx1A7KNekuCXQotFd4YifLIkvJWOEy5vxiREJX80Mwxbr8F-3GfCv0utIsQz_iq19nS57svUqwv0mSosV8OTxqXTLjmsI" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=AaronL725/grok-register&type=date&legend=top-left&sealed_token=uCM--S2xEp0n8rFUZHUg6wUJOgYcfO4XEVCIF9UZAT04YjL9YsMEOVOGAOlQfqwsoS7cQef0Rwc1cYCY4lAmTuMmcg-hKzNnx1A7KNekuCXQotFd4YifLIkvJWOEy5vxiREJX80Mwxbr8F-3GfCv0utIsQz_iq19nS57svUqwv0mSosV8OTxqXTLjmsI" />
 </picture>
</a>
