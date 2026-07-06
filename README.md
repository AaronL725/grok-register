<div align="center">

[![Grok Register — GUI and CLI registration automation toolkit](assets/banner.png)](https://github.com/AaronL725/grok-register)

Grok Register 是一个面向自动化流程研究、测试环境验证和个人学习的 Python 自动化注册工具 — 支持 GUI / CLI、临时邮箱、浏览器流程控制、账号输出和 grok2api token 池写入。

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

> 本项目仅用于自动化流程研究、测试环境验证和个人学习。请遵守目标网站服务条款、当地法律法规和第三方服务限制。

## Contents

- [功能](#功能)
- [环境要求](#环境要求)
- [安装](#安装)
- [配置](#配置)
- [运行](#运行)
- [输出文件](#输出文件)
- [稳定性机制](#稳定性机制)
- [常见问题](#常见问题)
- [目录结构](#目录结构)
- [License](#license)
- [Acknowledgments](#acknowledgments)
- [Star History](#star-history)

## 功能

- 支持 GUI 图形界面运行。
- 支持 CLI 终端运行，不启动 Tk GUI。
- 注册流程使用 Chromium/Chrome 浏览器页面完成。
- 支持 DuckMail、YYDS、Cloudflare 临时邮箱接口。
- 支持验证码邮件轮询和解析。
- 支持成功账号实时写入 `accounts_*.txt`。
- 支持将 SSO token 写入 grok2api 本地或远端池。
- 支持注册后尝试开启 NSFW。
- 支持页面卡住检测、当前账号重试、浏览器重启和内存清理。

## 环境要求

- Python 3.9+
- Google Chrome 或 Chromium
- 可访问注册页面和临时邮箱 API 的网络环境

## 安装

下载项目到电脑：

```bash
git clone https://github.com/AaronL725/grok-register.git
cd grok-register
```

安装依赖：

```bash
pip install -r requirements.txt
```

复制配置文件：

```bash
cp config.example.json config.json
```

然后按需编辑 `config.json`。

## 配置

常用配置项：

| 配置项 | 说明 |
| --- | --- |
| `email_provider` | 邮箱服务商：`duckmail`、`yyds`、`cloudflare`、`mailnest` |
| `register_count` | 本次目标注册数量 |
| `proxy` | 代理地址，可留空 |
| `enable_nsfw` | 注册后是否尝试开启 NSFW |
| `cloudflare_api_base` | Cloudflare 临时邮箱 API 地址 |
| `cloudflare_api_key` | Cloudflare 临时邮箱接口密钥；默认匿名模式留空，admin 模式填 `ADMIN_PASSWORD` |
| `cloudflare_auth_mode` | Cloudflare API 鉴权模式；默认 `none`，可选 `bearer`、`x-api-key`、`x-admin-auth`、`query-key` |
| `cloudflare_path_domains` | Cloudflare 域名列表路径；默认 `/api/domains` |
| `cloudflare_path_accounts` | Cloudflare 创建邮箱路径；默认匿名模式用 `/api/new_address`，admin 模式用 `/admin/new_address` |
| `cloudflare_path_token` | Cloudflare token 路径；默认 `/api/token` |
| `cloudflare_path_messages` | Cloudflare 收件列表路径；默认 `/api/mails` |
| `defaultDomains` | Cloudflare 临时邮箱默认域名 |
| `grok2api_auto_add_local` | 是否写入本地 grok2api token 池 |
| `grok2api_local_token_file` | 本地 grok2api token 文件路径 |
| `grok2api_auto_add_remote` | 是否写入远端 grok2api |
| `grok2api_remote_base` | 远端 grok2api 地址，可填站点根地址或 `/admin/api` 管理 API 地址 |
| `grok2api_remote_app_key` | 远端 grok2api app key |
| `mailnest_api_key` | outlook 临时邮箱提供商[迈巢](https://mailnest.top/)的 `api-key` |
| `mailnest_project_code` | outlook 临时邮箱提供商[迈巢](https://mailnest.top/)中`x.ai`项目的[项目代码](https://mailnest.top/buy-email)，默认为 `x-ai001` |

### Cloudflare 临时邮箱匿名模式（默认）

默认情况下，Cloudflare 邮箱使用 `dreamhunter2333/cloudflare_temp_email` 的匿名接口创建邮箱并读取邮件：

- 创建邮箱：`POST /api/new_address`
- 读取邮件：`GET /api/mails`
- 鉴权模式：`none`
- `cloudflare_api_key`：留空

这是项目的默认路线。没有特殊需求时，保持下面配置即可：

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
  "defaultDomains": "你的收信域名.com"
}
```

### Cloudflare 临时邮箱 admin 模式（可选）

如果使用 `dreamhunter2333/cloudflare_temp_email` 且匿名 `/api/new_address` 开启了 Turnstile，可以改用 admin 创建邮箱接口：

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://你的-worker-api-域名",
  "cloudflare_api_key": "你的 ADMIN_PASSWORD",
  "cloudflare_auth_mode": "x-admin-auth",
  "cloudflare_path_accounts": "/admin/new_address",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "你的收信域名.com"
}
```

创建邮箱会使用 `x-admin-auth` 调用 `/admin/new_address`，后续收件仍使用接口返回的地址 JWT 调用 `/api/mails`。也就是说，admin 密码只用于创建邮箱，不用于读取邮箱邮件。

可先用调试脚本验证 admin 创建接口：

```bash
python cf_mail_debug.py --api-base "https://你的-worker-api-域名" --auth-mode x-admin-auth --api-key "你的 ADMIN_PASSWORD" --create-path /admin/new_address --domain "你的收信域名.com"
```

### 迈巢 MailNest 临时邮箱

`outlook`邮箱提供商[迈巢](https://mailnest.top/)，其临时邮箱采用项目制模式，因此除了在配置文件中配置`api-key`，还需配置项目代码`mailnest_project_code`，项目代码默认为`x-ai001`：

```json
{
      "mailnest_api_key": "",
      "mailnest_project_code": "x-ai001"
}
```

- `api-key`获取页面：https://mailnest.top/account
- 项目代码`mailnest_project_code`获取页面：https://mailnest.top/buy-email。默认为`x-ai001`，可以直接使用

配置后，即可在`qt`界面的邮箱服务商选择`mailnest`进行使用。

### grok2api 远端入池配置

如果开启 `grok2api_auto_add_remote`，`grok2api_remote_base` 可以填写站点根地址，也可以直接填写管理 API 地址：

```json
{
  "grok2api_auto_add_remote": true,
  "grok2api_remote_base": "https://你的-grok2api-域名",
  "grok2api_remote_app_key": "你的 app_key"
}
```

或：

```json
{
  "grok2api_auto_add_remote": true,
  "grok2api_remote_base": "https://你的-grok2api-域名/admin/api",
  "grok2api_remote_app_key": "你的 app_key"
}
```

程序会优先尝试 `/tokens/add`，并兼容 `/admin/api/tokens/add`；旧版全量保存接口也会兼容 `/tokens` 和 `/admin/api/tokens`。

`config.json` 包含个人配置和密钥，不要提交到 Git。

## 运行

### CLI 模式

CLI 模式不会启动 Tk GUI，但注册流程仍会打开 Chromium/Chrome 浏览器页面。

```bash
python grok_register_ttk.py cli
```

看到提示后输入：

```text
start
```

停止任务：

```text
Ctrl+C
```

CLI 模式适合长时间批量运行。程序每成功注册 5 个账号会关闭浏览器、清理运行时对象并重新启动浏览器，降低长任务内存占用。

### GUI 模式

```bash
python grok_register_ttk.py
```

GUI 模式会打开 Tkinter 窗口，适合手动调整配置和观察日志。

## 输出文件

运行过程中会生成：

- `accounts_*.txt`：成功账号、密码和 SSO token。
- `mail_credentials.txt`：临时邮箱凭证。
- `*.log`：可选日志文件。

这些文件包含敏感信息，已被 `.gitignore` 忽略。

## 稳定性机制

- 每个账号结束后重启浏览器。
- 每成功 5 个账号执行一次内存清理。
- CLI 模式支持 `Ctrl+C` 中断并清理浏览器。
- 最终页长时间无变化时自动重试当前账号。
- 验证码未收到时自动更换邮箱重试。

## 常见问题

### CLI 模式为什么还会打开浏览器？

CLI 模式只是不启动 Tk GUI。注册页、Turnstile、验证码提交和 SSO cookie 获取仍依赖真实浏览器环境。

### NSFW 开启失败怎么办？

如果日志显示 `Cloudflare 防护拦截，HTTP 403`，说明请求被目标站点防护拦截。程序会继续保存账号和写入 grok2api。

### GUI 显示的数量和配置不同？

GUI 数量控件可能有上限。CLI 模式直接读取 `config.json` 中的 `register_count`。

## 目录结构

```text
.
├── grok_register_ttk.py   # 主程序
├── cf_mail_debug.py       # Cloudflare 邮箱调试工具
├── config.example.json    # 配置示例
├── requirements.txt       # Python 依赖
└── README.md
```

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
