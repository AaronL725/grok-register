<div align="center">

[![Grok Register — GUI, CLI and WebUI registration automation toolkit](assets/banner.png)](https://github.com/AaronL725/grok-register)

<p><a href="README.md">简体中文</a> | <strong>English</strong></p>

Grok Register is a Python toolkit for automation workflow research, test-environment validation, and personal learning. It provides GUI / CLI / WebUI interfaces, four temporary-email services plus an Outlook mailbox pool, optional 1–8 worker concurrency, and an account-level proxy pool. It also integrates Chromium browser automation, safe account persistence, pending-result recovery, grok2api token-pool synchronization, and optional CPA xAI OIDC credential export.

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
> This project is intended only for automation workflow research, test-environment validation, and personal learning. Users are responsible for complying with the target website's terms of service, applicable local laws and regulations, and third-party service restrictions. Do not use this project for abuse, bypassing platform restrictions, or unauthorized commercial purposes.

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Configuration](#configuration)
- [Proxy & Proxy Pool](#proxy--proxy-pool)
- [Optional Multi-Worker Registration](#optional-multi-worker-registration)
- [grok2api Token Pool](#grok2api-token-pool)
- [CPA / xAI OIDC Export](#cpa--xai-oidc-export)
- [Outputs & Pending Recovery](#outputs--pending-recovery)
- [Project Structure](#project-structure)
- [FAQ](#faq)
- [License](#license)
- [Acknowledgments](#acknowledgments)
- [Star History](#star-history)

## Sponsor

<div align="center">

<a href="https://www.ipwo.net/?ref=githubgrokregister">
  <img alt="IPWO Residential Proxies" src="./assets/ipwo-banner.png" />
</a>

</div>

<p><sub>Need stable residential IPs? Try <a href="https://www.ipwo.net/?ref=githubgrokregister">IPWO</a> residential proxies.</sub></p>

<p><sub>
Coverage across 195+ countries and regions<br>
Real residential IP resources<br>
Flexible IP rotation<br>
HTTP / HTTPS / SOCKS5 support
</sub></p>

<p><sub>Suitable for automation registration, account management, data collection, and cross-border business scenarios, with flexible integration into browser automation tools and proxy pools. Free trial available. Discount code: <strong><code>0205</code></strong></sub></p>

<p><sub>For advertising partnerships, contact me at: 2309501984</sub></p>

## Features

Grok Register uses a real Chromium / Chrome browser to complete the registration flow, with GUI, CLI, and WebUI all connected to the same registration core.

Key features:

- Automatically opens the registration page, submits an email address, polls for the verification code, fills in profile information, and obtains the SSO cookie.
- Supports five email sources: **DuckMail / YYDS / Cloudflare temporary email / Cloud Mail / Outlook mailbox pool**.
- Supports three interfaces: **GUI / CLI / WebUI**.
- Supports optional **1–8 worker concurrent registration**; disabled by default.
- Supports `direct / single / pool` proxy modes, health checks, cooldowns, subscriptions, fixed/rotating nodes, and stable account-level Proxy Leases.
- The proxy pool can parse mixed **HTTP / HTTPS / SOCKS / VLESS / VMess / Trojan / Hysteria2 / TUIC / Shadowsocks** nodes.
- Can optionally attempt to enable NSFW after registration; failure does not discard an account that was already registered successfully.
- Supports pre-storage SSO risk screening for `botFlagSource` / `policy=deny`; confirmed matches are quarantined and skipped for grok2api / CPA. Risk checks are fail-open: network failures, abnormal HTTP responses, or missing risk fields are logged for diagnostics and the account continues to storage.
- Supports writing SSO tokens to either a local or remote grok2api pool.
- Supports optional CPA xAI OIDC credential export and CLIProxyAPI hotload.
- Successful accounts are persisted immediately. If writing the main account result fails, the result is placed in the corresponding `accounts_*.txt.pending.jsonl` file for later idempotent recovery. Failed risk-quarantine writes use a separate risk pending queue and are not mixed with normal account pending results.
- Supports task stopping, browser restarts, email retries, runtime cleanup, and isolation of post-processing errors.

The main flow for a single account is:

```text
Open registration page
  → Create and submit email
  → Retrieve and enter verification code
  → Fill in profile
  → Obtain SSO cookie
  → Optionally enable NSFW
  → SSO risk screening (botFlagSource / policy)
  → Save account
  → Optionally add to grok2api
  → Optionally export CPA/OIDC
```

> grok2api pool insertion and CPA/OIDC are optional post-processing steps after registration. Post-processing failures are recorded as warnings and do not reclassify an already saved account as a registration failure. When SSO risk screening matches, the account is not written to the main account file and is not sent to grok2api / CPA.

## Quick Start

### 1. Requirements

- Python **3.9+**
- Google Chrome or Chromium
- Network access to the registration page and the selected email API
- Tkinter is required for the GUI; use the CLI or WebUI if Tkinter is unavailable
- **sing-box is required only when using VLESS / VMess / Trojan / Hysteria2 / TUIC / Shadowsocks nodes**; HTTP/HTTPS/SOCKS continue to use the project's native proxy implementation

### 2. Installation

```bash
git clone https://github.com/AaronL725/grok-register.git
cd grok-register

python -m venv .venv
```

Activate the virtual environment:

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

Install the core dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Copy the configuration file:

```bash
# macOS / Linux
cp config.example.json config.json

# Windows CMD
copy config.example.json config.json
```

### 3. Start with the minimum configuration

If you are starting with DuckMail, use the following minimal configuration:

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

Then fill in the fields required by your selected `email_provider` and any post-processing features you enable. See [`config.example.json`](config.example.json) for the complete field list.

> `config.example.json` is the complete configuration template. Values such as `example.com` and `temp-mail.example.com` are placeholders, not service addresses that can be used directly. If you use Cloudflare / Cloud Mail / YYDS, fill in the corresponding service parameters first.

### 4. Launch

GUI:

```bash
python grok_register_ttk.py
```

WebUI:

```bash
python -m pip install -r requirements-web.txt
python -m web.server
```

Open:

```text
http://127.0.0.1:8092
```

> GUI, CLI, and WebUI share the same `config.json` and registration logic. It is recommended to run a task from only one interface at a time.

## Usage

### WebUI (optional)

```bash
python -m pip install -r requirements-web.txt
python -m web.server
```

The WebUI listens on `127.0.0.1:8092` by default and provides bilingual Chinese/English configuration, start/stop controls, batch statistics, real-time logs, proxy-pool node status, subscription parsing statistics, reload, and manual testing.

### GUI

```bash
python grok_register_ttk.py
```

The GUI lets you configure the main email, proxy, proxy-pool, multi-worker, and registration settings, then click "Start Registration".

### CLI

The following three commands are equivalent:

```bash
python grok_register_ttk.py cli
python grok_register_ttk.py start
python grok_register_ttk.py --cli
```

The CLI reads `config.json`. After validation, it prompts:

```text
> start
```

Enter `start` to begin. Press `Ctrl+C` to request a stop.

> The CLI only omits the Tk GUI. The registration page still uses a real Chromium / Chrome browser.

## Configuration

The project performs structural validation at startup and checks fields required by currently enabled features only when a task actually starts. This allows you to open the GUI / WebUI first and configure the project incrementally.

### Basic configuration

| Setting | Description |
| --- | --- |
| `email_provider` | `duckmail` / `yyds` / `cloudflare` / `cloudmail` / `outlook` |
| `register_count` | Number of registrations in the current batch |
| `enable_nsfw` | Whether to attempt to enable NSFW after registration |
| `sso_risk_gate_enabled` | Whether to check grok.com `botFlagSource` / `policy=deny` before storage; default `true` |
| `sso_risk_rejected_file` | File for quarantined SSO records; default `./sso_risk_rejected.txt` |
| `user_agent` | User-Agent used by Chromium and HTTP requests |
| `proxy_mode` | `auto` / `direct` / `single` / `pool` |
| `proxy` | Single proxy address; in `auto` mode, leave empty for a direct connection |
| `multi_thread_enabled` | Whether concurrent registration is enabled; default `false` |
| `multi_thread_workers` | Number of concurrent workers, range `1–8` |

### Email services

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

At least one of `yyds_api_key` and `yyds_jwt` must be provided.

#### Outlook mailbox pool

Outlook mode uses existing Outlook / Microsoft mailboxes that can be read through OAuth2. It does not create Microsoft mailboxes. The configuration stores only the mailbox-pool file path:

```json
{
  "email_provider": "outlook",
  "outlook_accounts_file": "./output/mailboxes/outlook-accounts.txt"
}
```

Each line in the mailbox pool uses the following format:

```text
email----password----clientId----refreshToken----auto
```

The final column is optional and supports `auto` / `imap` / `graph`; if omitted, it defaults to `auto`. The same fields separated by `|` are also supported. The `password` field is retained in the pool record, but verification-code retrieval uses `clientId + refreshToken` to obtain an OAuth2 access token.

- `auto`: Before the email is submitted, independent pre-send cursors are established for IMAP and Microsoft Graph. Polling uses only the channels that actually passed preflight; if both are available, both are polled concurrently.
- `imap`: Reads Inbox, Junk, Archive, and other common folders through `outlook.office365.com:993` + XOAUTH2. It uses `UIDVALIDITY + UID` as a stable incremental cursor instead of message counts or sequence numbers that can shift when messages are deleted or moved. Folder LIST results plus the IMAP connection/access token established during preflight are reused during the short verification-code window, and reconnected/refreshed only after disconnects or authentication expiry.
- `graph`: Monitors both `Inbox` and `JunkEmail` through Microsoft Graph. It uses immutable message ID + `receivedDateTime` as the pre-send cursor, polls only lightweight message-frontier data during normal operation, and reads the message body only after a new message is confirmed.
- Microsoft OAuth/Graph performs limited retries for timeouts, connection errors, `429`, and `5xx`. `429` responses honor `Retry-After` where possible; other retries use exponential backoff with jitter, and Microsoft HTTP concurrency is limited.
- Verification codes are extracted only in explicit verification-code contexts, or when the sender is confirmed to belong to an official xAI/Grok domain, preventing ordinary ticket IDs such as `ABC-123` from being misidentified as OTPs.
- Each mailbox can be allocated at most once per registration task. Multi-worker registration shares a task-level allocator. Mailbox sessions use one-time opaque handles that expire when verification-code retrieval begins; mailbox state/access tokens are released from memory immediately after retrieval ends.
- A mailbox without a safe pre-send cursor is not submitted for registration. If the requested registration count exceeds the number of valid mailboxes in the pool, the task is automatically capped at the pool capacity.
- Outlook refresh tokens / access tokens are not written to `mail_credentials.txt`, ordinary logs, or `config.json`. The mailbox-pool file is atomically written with `0600` permissions where possible and is included in `.gitignore`.

The GUI's "Manage Outlook Mailbox Pool" and the WebUI mailbox-pool editor both provide health checks that can verify each mailbox's IMAP/Graph availability without triggering xAI registration. Results return only safe metadata such as email address, mode, channel status, and folders. The Web interface listens only on localhost and sets `no-store` on mailbox-pool-related responses.

#### Cloudflare temporary email

Common fields:

| Setting | Description |
| --- | --- |
| `cloudflare_api_base` | Mail API base URL |
| `cloudflare_api_key` | Authentication credential paired with `cloudflare_auth_mode`: may be empty for `none`; used as Bearer Token for `bearer`; as `X-API-Key` for `x-api-key`; as Admin Password for `x-admin-auth`; or as the URL `key` parameter for `query-key` |
| `cloudflare_auth_mode` | `none` / `bearer` / `x-api-key` / `x-admin-auth` / `query-key` |
| `cloudflare_path_accounts` | Mailbox-creation endpoint |
| `cloudflare_path_messages` | Message-list endpoint |
| `defaultDomains` | Default receiving domains; separate multiple domains with commas |

Anonymous creation example:

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://your-worker-api-domain",
  "cloudflare_api_key": "",
  "cloudflare_auth_mode": "none",
  "cloudflare_path_accounts": "/api/new_address",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "example.com"
}
```

Admin creation example:

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://your-worker-api-domain",
  "cloudflare_api_key": "your ADMIN_PASSWORD",
  "cloudflare_auth_mode": "x-admin-auth",
  "cloudflare_path_accounts": "/admin/new_address",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "example.com"
}
```

#### Cloud Mail no-inbox mode

```json
{
  "email_provider": "cloudmail",
  "cloudmail_api_base": "https://your-Cloud-Mail-domain",
  "cloudmail_public_token": "public API Token",
  "cloudmail_domains": "example.com,example.net",
  "cloudmail_path_messages": "/api/public/emailList"
}
```

Cloud Mail's Public Token is placed directly in the `Authorization` header without a `Bearer` prefix. The upstream service currently stores only one global Public Token, so regenerating the token invalidates the old one.

After the current registration slot / Proxy Lease is established and before the browser starts, the program checks Cloud Mail authentication through the same network exit. If it receives `401 token validation failed`, it waits about 70 seconds on the same exit for Workers KV convergence; it does not switch proxies, automatically generate a new token, or try alternate authentication formats.

If 401 responses persist after the waiting window, check `cloudmail_api_base`, the Public Token, and whether the KV namespace actually bound to the Cloud Mail Worker belongs to the same deployment instance. Do not repeatedly regenerate tokens. Error logs record only the token length and a short SHA-256 fingerprint, never the full Public Token.

## Proxy & Proxy Pool

Default:

```json
{
  "proxy_mode": "auto",
  "proxy": ""
}
```

`auto` preserves compatibility with the legacy single-proxy configuration: when `proxy` is empty, the connection is direct; when it is non-empty, that proxy is used.

### Single proxy

Native proxy:

```json
{
  "proxy_mode": "single",
  "proxy": "http://user:password@127.0.0.1:7890"
}
```

`single` can also accept a supported advanced-protocol URI directly; advanced protocols require a local executable `sing-box`.

### Proxy pool

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

Proxy sources can be plain text or an entire Base64-encoded document. After decoding, they may contain mixed protocols:

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

Currently supported:

- HTTP / HTTPS / SOCKS / SOCKS4 / SOCKS4A / SOCKS5 / SOCKS5H
- VLESS / VMess / Trojan / Hysteria2 (`hy2`) / TUIC / Shadowsocks (`ss`)
- Local files and HTTP/HTTPS subscriptions
- Standard Base64 and URL-safe Base64 subscriptions
- Common VLESS/VMess/Trojan TCP/WS/gRPC/HTTP/HTTPUpgrade/QUIC transports
- Common VLESS TLS / uTLS / Reality parameters
- Node parsing statistics, health probes, failure cooldowns, and automatic recovery
- Fixed/rotating endpoints, `{account}`, concurrency limits, and stable account-level Proxy Leases

The proxy runtime uses a lazy + idle-cache design: a local runtime is created only when a node is actually selected, probed, or preflighted. Native proxies that need a unified HTTP exit use `LocalProxyBridge`; VLESS / VMess / Trojan / Hysteria2 / TUIC / Shadowsocks use sing-box. When the Lease reference count drops to 0, the runtime does not exit immediately by default; it moves into the idle cache. Defaults are `proxy_runtime_idle_ttl_sec=120` and `proxy_runtime_cache_max=32`. The runtime closes when TTL expires, the cache evicts it, or the Manager shuts down. Set `proxy_runtime_idle_ttl_sec=0` to restore immediate shutdown at zero references.

Within a single account attempt, the browser, email, NSFW, and default CPA all stay on the same Lease. While waiting for a verification code, if no usable code has been obtained, the program may retry with a different email address while staying on the same Lease. Once verification-code entry/submission begins, later exceptions do not replay the registration by switching email or proxy; they are treated as an "outcome uncertain" state.

For complete parameters, protocol mappings, runtime behavior, and health rules, see [`docs/proxy-pool.en.md`](docs/proxy-pool.en.md).

## Optional Multi-Worker Registration

Disabled by default:

```json
{
  "multi_thread_enabled": false,
  "multi_thread_workers": 4
}
```

To enable concurrency:

```json
{
  "multi_thread_enabled": true,
  "multi_thread_workers": 4
}
```

- Worker count ranges from `1–8` and never exceeds `register_count`.
- Each worker uses its own email module and browser runtime state.
- Shared outputs are protected by locks.
- Proxy health state is shared across all workers, while each account has an independent Proxy Lease.

## grok2api Token Pool

All pool-insertion features are optional.

### Local pool

```json
{
  "grok2api_auto_add_local": true,
  "grok2api_local_token_file": "",
  "grok2api_pool_name": "ssoBasic"
}
```

### Remote pool

Remote mode supports two credential methods; choose one:

1. `grok2api_remote_app_key`
2. `grok2api_remote_admin_username` + `grok2api_remote_admin_password`

```json
{
  "grok2api_auto_add_remote": true,
  "grok2api_remote_base": "https://your-grok2api-domain",
  "grok2api_remote_app_key": "",
  "grok2api_remote_admin_username": "admin",
  "grok2api_remote_admin_password": "your administrator password",
  "grok2api_pool_name": "ssoBasic",
  "grok2api_allow_legacy_full_save": false
}
```

The two remote credential methods cannot be configured at the same time. The newer admin username/password mode requires HTTPS for non-local addresses; `localhost` / `127.0.0.1` / `::1` may use HTTP. The legacy `app_key` compatibility endpoint currently accepts HTTP/HTTPS, although HTTPS is still recommended for remote deployments.

## CPA / xAI OIDC Export

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

- `cpa_copy_to_hotload=true` requires `cpa_hotload_dir`.
- An explicit `cpa_proxy` always takes priority.
- If `cpa_proxy` is not configured and the current account uses a Proxy Lease, CPA inherits the same exit, including localhost runtimes for advanced protocols.
- CPA export failures are recorded only as post-processing warnings and do not delete saved accounts.

## Outputs & Pending Recovery

| File / Directory | Contents |
| --- | --- |
| `accounts_*.txt` | Successfully saved accounts, passwords, and SSO tokens |
| `<sso_risk_rejected_file>` | SSO records quarantined due to `botFlagSource=1/2` or `policy=deny`; default `./sso_risk_rejected.txt` |
| `mail_credentials.txt` | Temporary email addresses and email credentials created during registration. Email credentials are persisted before registration submission immediately after mailbox creation, so this file may include attempts that later failed, were retried, or ended with an uncertain outcome |
| `accounts_*.txt.pending.jsonl` | Normal account pending results for registrations that succeeded but could not be written to the main account result file; recover with `retry-pending` |
| `<sso_risk_rejected_file>.pending.jsonl` | Separate risk pending queue used when a quarantined account cannot be written to the main quarantine file; do not recover with the normal `retry-pending` command |
| `<grok2api_local_token_file>` | Optional local grok2api token pool; defaults to `token.json` in the project directory when left empty |
| `<cpa_auth_dir>/xai-*.json` | Optional CPA xAI OIDC credentials; default directory `./cpa_auths` |
| `<cpa_auth_dir>/cpa_auth_failed.txt` | CPA export failure records |
| `screenshots/` | CPA browser failure-debugging screenshots |

### Recover pending results

```bash
python grok_register_ttk.py retry-pending <pending-file> [output-file]
```

Recovery uses file locks, deduplication, and atomic replacement. Repeating the operation does not write the same successfully recovered account more than once.

> `retry-pending` is **only for normal account-result pending files** such as `accounts_*.txt.pending.jsonl`. It does not apply to `<sso_risk_rejected_file>.pending.jsonl`. Risk pending is a separate quarantine queue; after successful recovery it should be written to the configured `sso_risk_rejected_file`. There is currently no corresponding CLI subcommand; the internal recovery entry point is `sso_risk.retry_sso_risk_pending_file()`.

## Project Structure

```text
.
├── grok_register_ttk.py       # GUI / CLI entry point and main adapter layer
├── registration_flow.py       # Shared GUI / CLI / WebUI registration state machine, batch orchestration, and stage-aware retries
├── registration_parallel.py   # Optional multi-worker concurrency coordinator
├── registration_browser.py    # Chromium registration-page state and submission logic
├── browser_runtime.py         # Shared HTTP, Chromium Options, and proxy injection
├── proxy_pool.py              # Compatibility export layer for proxy_pool_v3
├── proxy_pool_v3.py           # Proxy-pool core: Source, Lease, health, cooldown, refresh, and Probe
├── proxy_bridge.py            # HTTP/HTTPS/SOCKS → localhost HTTP proxy bridge and Chromium compatibility
├── proxy_protocols.py         # HTTP/SOCKS/VLESS/VMess/Trojan/HY2/TUIC/SS subscription parsing
├── proxy_protocol_runtime.py  # Native bridge / sing-box lazy runtime and idle cache
├── mail_service.py            # Four email services
├── app_config.py              # Default configuration, validation, loading, and saving
├── account_outputs.py         # Account, pending, and token outputs
├── sso_risk.py                # SSO botFlag / policy early stop
├── cpa_export.py              # CPA/OIDC export entry point
├── cpa_xai/                   # CPA browser, OAuth, proxy helpers, and credential writing
├── web/
│   ├── server.py              # FastAPI WebUI control layer
│   ├── index.html             # WebUI page
│   ├── proxy-pool.js          # Proxy-pool WebUI interactions
│   └── proxy-pool.css         # Proxy-pool WebUI styles
├── docs/proxy-pool.en.md      # Detailed proxy-pool documentation
├── config.example.json        # Complete configuration example
├── requirements.txt           # Core dependencies
├── requirements-web.txt       # Optional WebUI dependencies
└── tests/                     # Unit and compatibility regression tests
```

## FAQ

### Why does the CLI still open a browser?

The CLI only skips the Tk GUI. Registration-page interactions, verification-code submission, and SSO cookie retrieval still depend on a real Chromium / Chrome browser.

### What should I do if the GUI does not start?

Make sure your Python environment includes Tkinter. Linux distributions may require installing `python3-tk` separately. You can also use the CLI or WebUI instead.

### Why are advanced-protocol nodes shown as unavailable?

VLESS / VMess / Trojan / Hysteria2 / TUIC / Shadowsocks require a local sing-box installation. By default, the project searches the system `PATH`; you can also set `proxy_singbox_path` in the WebUI / `config.json`. HTTP/HTTPS/SOCKS are unaffected.

### Why are some V2Ray subscription nodes skipped?

The WebUI shows subscription protocol counts and parsing errors. Unmappable transports or invalid URIs cause only the affected node to be skipped; other valid nodes in the same subscription are unaffected. See [`docs/proxy-pool.en.md`](docs/proxy-pool.en.md) for the complete mapping scope.

### Why can the GUI / WebUI open when the configuration file is incomplete?

Configuration saving and runtime validation are separate. The interface can open and let you edit configuration first; the required fields for currently enabled services are checked only when registration starts.

### What if grok2api or CPA fails after registration succeeds?

The account itself still counts as successfully registered. These errors are counted only as post-processing warnings.

### Will an NSFW enablement failure discard the account?

No. NSFW is optional; the account is still saved if that step fails.

### Why does the proxy pool display usernames and passwords?

The current WebUI is designed for personal/local deployment and displays complete proxy nodes and authentication information. Do not expose the WebUI to untrusted networks.

### Where can I find more detailed proxy-pool parameters?

See [`docs/proxy-pool.en.md`](docs/proxy-pool.en.md).

### Why does an account go into pending?

A normal `accounts_*.txt.pending.jsonl` file means registration completed, but the main account-result file could not be written. Use `retry-pending` to recover it; there is no need to register the account again.

If the file is `<sso_risk_rejected_file>.pending.jsonl`, the account was explicitly identified by risk screening but could not be written to the main quarantine file. This is a separate risk pending queue and cannot be recovered with the normal `retry-pending`.

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
