# Registration Proxy Pool

<p><a href="proxy-pool.md">简体中文</a> | <strong>English</strong></p>

The proxy pool is an optional network layer. By default, `proxy_mode=auto` uses the configured `proxy` or a direct connection when empty. Selecting `single` or `pool` enables account-level Proxy Leases, node scheduling, and health feedback.

## Core Principles

- **One stable lease per account attempt**: the browser, email requests, registration-stage HTTP traffic, NSFW, and CPA/OIDC unless explicitly overridden all share the same network exit.
- **Safe retries take priority over blind replay**: a Lease may be released and the attempt restarted only before any stateful submission occurs. While waiting for a verification code, but before code submission starts, recovery may switch the email address while staying on the same Lease. Errors after stateful boundaries such as email submission, verification-code entry/submission, or profile submission are marked as outcome uncertain; the full registration flow is not automatically replayed through a different proxy.
- **All managed network components consume a unified HTTP-compatible endpoint**: HTTP/SOCKS/advanced protocols are ultimately presented in a form consumable by Chromium, curl_cffi, urllib, CPA, and probes.
- **Probe and Runtime Health are separate**: active probing answers "is it reachable right now?", while business health answers "how has it actually performed during real registrations?".
- **Fixed and Rotating are handled separately**: fixed nodes use Health/Failure/Cooldown; rotating gateways track exit successes/failures and success rate without cooling the entire gateway because of one bad exit.
- **Source-scoped Last-Known-Good**: file and subscription sources refresh independently; if one source temporarily fails to refresh, its most recent successful node set is retained.
- **Backward compatibility by default**: `auto` / `direct` continue to use the legacy path and do not force managed-proxy behavior.

## Unified Network Exit

In `single` / `pool` mode:

```text
plain HTTP (no auth)
    → original HTTP endpoint

HTTP + auth / HTTPS proxy / SOCKS4 / SOCKS5
    → LocalProxyBridge
    → http://127.0.0.1:<port>

VLESS / VMess / Trojan / Hysteria2 / TUIC / Shadowsocks
    → sing-box
    → http://127.0.0.1:<port>
```

All traffic then flows through:

```text
ProxyLease.proxy_url
      ↓
Chromium / curl_cffi / Mail / NSFW / CPA OAuth / CPA Browser / Probe / Preflight
```

`ProxyLease.source_uri` retains the original node URI for logs, WebUI display, and diagnostics.

## Configuration

```json
{
  "proxy_mode": "auto",
  "proxy": "",
  "proxy_fallback": "none",

  "proxy_pool_file": "",
  "proxy_pool_subscription_url": "",
  "proxy_pool_subscription_proxy": "",
  "proxy_pool_subscription_public_only": false,

  "proxy_pool_endpoint_mode": "auto",
  "proxy_pool_refresh_interval_sec": 900,
  "proxy_pool_probe_interval_sec": 900,
  "proxy_pool_probe_timeout_sec": 15,
  "proxy_pool_probe_provider": "cloudflare",
  "proxy_pool_probe_dual_stack": true,

  "proxy_pool_max_concurrent_per_node": 1,
  "proxy_pool_acquire_timeout_sec": 30,

  "proxy_protocol_backend": "auto",
  "proxy_singbox_path": "",
  "proxy_protocol_start_timeout_sec": 10,
  "proxy_runtime_idle_ttl_sec": 120,
  "proxy_runtime_cache_max": 32,

  "proxy_pool_persist_health": false,
  "proxy_pool_state_file": "./proxy_pool_state.json",
  "proxy_pool_preflight_enabled": true
}
```

### `proxy_mode`

| Value | Behavior |
| --- | --- |
| `auto` | Default compatibility mode; continues to use the legacy `proxy` behavior. |
| `direct` | Forces the main registration flow to connect directly. |
| `single` | Treats `proxy` as a single node managed by Lease and health logic. |
| `pool` | Loads and schedules multiple nodes from a file and/or subscription. |

### `proxy_fallback`

| Value | Behavior |
| --- | --- |
| `none` | No fallback when no usable node is available. |
| `direct` | A new attempt may fall back to a direct connection if Lease acquisition fails or times out. |
| `single` | A new attempt may fall back to `proxy` if Lease acquisition fails or times out. |

Fallback is applied only before a new attempt, never in the middle of a registration.

`proxy_pool_endpoint_mode` supports `fixed`, `rotating`, and `auto` (default). In `auto`, **only native proxy URIs containing `{account}`** are classified as rotating gateways; other nodes are treated as fixed.

## Registration Stages and Safe Retries

| Stage | Handling |
| --- | --- |
| `lease_acquire` / `browser_start` / `page_open` | **SAFE_NEW_LEASE**: before email submission, the Lease may be released and the attempt retried within limits |
| `code_wait` | **SAME_LEASE_RECOVERY**: only if no code was obtained, retry with a new email while retaining the Lease |
| `email_submit` / `code_submit` / `profile_submit` / `sso_wait` | **OUTCOME_UNCERTAIN**: do not replay registration through another email or proxy |
| `account_confirmed` / `postprocess` | **NO_RETRY**: do not re-register an already confirmed account |

A stateful stage is marked **before** the submission attempt: if the server may have received it, an unconfirmed result is counted as `uncertain` instead of being replayed. The WebUI displays this count.

## Supported Protocols

Proxy sources may contain any mix of:

```text
HTTP / HTTPS
SOCKS / SOCKS4 / SOCKS4A / SOCKS5 / SOCKS5H
VLESS
VMess
Trojan
Hysteria2 / hy2
TUIC
Shadowsocks / ss
```

Advanced protocols are converted on demand by sing-box into a local HTTP endpoint. If `proxy_singbox_path` is empty, the project looks for `sing-box` in the system `PATH`; it does not automatically download or update it.

Shadowsocks supports common SIP002 / legacy Base64 URIs. The built-in implementation currently supports common AEAD / 2022 methods; unsupported plugins or methods produce explicit errors rather than silently degrading.

## Native URI Normalization

Native HTTP/HTTPS/SOCKS proxies must identify an explicit proxy endpoint:

```text
scheme://[user:password@]host:port
```

Rules:

- A port is required.
- Routing paths or query strings are not accepted.
- `#fragment` is used only as the display name and is excluded from the canonical URI / node identity.
- `{account}` may appear at most once and only in the proxy username.
- `socks://` is normalized to `socks5://`.

## SOCKS DNS Semantics

The shared bridge explicitly distinguishes:

```text
socks5://
→ resolve DNS locally
→ send the IP address to the SOCKS server

socks5h://
→ do not resolve the hostname locally
→ let the SOCKS server resolve the hostname
```

SOCKS4 / SOCKS4A likewise preserve local / remote DNS semantics respectively.

## Runtime Idle Cache

Runtimes are still created lazily; a large subscription does not cause a large number of bridge / sing-box runtimes to start all at once.

After its reference count reaches 0, a runtime is cached for **120 seconds, up to 32 entries** by default (`proxy_runtime_idle_ttl_sec`, `proxy_runtime_cache_max`). It can be reused until expiry, eviction, or Manager shutdown. Set the TTL to `0` for immediate shutdown.

## Base64, Subscription Refresh, and Last-Known-Good

`proxy_pool_file` and `proxy_pool_subscription_url` support:

- Plain line-by-line URIs.
- Entire documents encoded with standard Base64.
- URL-safe Base64.
- Mixed multi-protocol nodes.

Each source is limited to 2 MiB and 10,000 nodes. Parsing results record total line count, Base64 status, successful node count, skipped count, protocol counts, and errors.

File and subscription sources independently maintain:

```text
last_success_at
last_error
generation
nodes
diagnostics
```

For example, if the file refresh succeeds but the subscription temporarily times out:

```text
file         → use the latest generation
subscription → retain the last successful generation and mark it stale
```

A successful refresh from one source does not clear the most recent successful nodes from another source that temporarily failed.

## Subscription Target Restrictions (Optional)

When `proxy_pool_subscription_public_only=true`, the initial subscription URL and every HTTP redirect are revalidated:

- Only `http` / `https` are allowed.
- The hostname must resolve.
- Private / loopback / link-local / multicast / reserved / unspecified addresses are rejected.
- At most 3 redirects are allowed.
- The response remains subject to the 2 MiB content limit.

This option is disabled by default so local research environments can continue to use LAN or self-hosted subscription services.

## Probe: IPv4 / IPv6 and False-Positive Protection

`proxy_pool_probe_provider` supports `cloudflare` and `ipinfo`. With `proxy_pool_probe_dual_stack=true` (default), IPv4 and IPv6 are probed independently, retaining status, timestamp, latency, exit IP, and error per family.

If one family works and the other fails, the node may still be considered usable while retaining the independent results for both families.

**HTTP 2xx no longer automatically means a healthy probe.** A probe must satisfy all of the following:

```text
HTTP 2xx
+ successfully parse a valid exit IP
+ IP family matches the current IPv4/IPv6 probe
```

Otherwise it is marked `unhealthy`, preventing false positives such as "HTTP 200 but malformed response / no IP".

## Probe-Aware Soft Selection

Node scheduling first requires:

```text
enabled
not retired
capacity available
fixed node not cooling
```

It then groups nodes by recent probe status:

```text
Tier 0: recent healthy
Tier 1: unknown / stale
Tier 2: recent unhealthy
```

Affinity / health / inflight selection is performed from the best available tier first. Recent unhealthy status is a **soft deprioritization**, not a permanent hard ban; if it is the only available node, it may still be tried.

## Fixed and Rotating Health Models

### Fixed node

Real registration success:

```text
registration_successes += 1
business_samples += 1
health = min(1.0, health + 0.1)
failure_count = 0
cooldown = none
```

Confirmed transport failure:

```text
transport_failures += 1
business_samples += 1
failure_count += 1
health = max(0.05, health * 0.7)
```

Cooldown:

```text
30s → 60s → 120s → 240s → 480s → max 600s
```

### Rotating gateway

A rotating gateway does not display fixed-node Health and does not apply a gateway-wide cooldown because of one bad exit. It records:

```text
exit_successes
exit_failures
gateway_success_rate
```

This prevents a gateway that frequently changes exits from appearing permanently as `Health=1.0` simply because it accumulated successful samples.

### Business sample deduplication

A single account attempt contributes at most one business-health sample. If a suspected failure probe occurs after a successful attempt, that attempt is not counted twice as two business samples.

**Configuration/authentication errors are not business-health samples.** They increment `configuration_failures` and mark the node unavailable, but do not reduce Health, increment `business_samples`, or enter exponential transport cooldown.

## Five Failure Categories

Network feedback is divided into five categories:

1. **compatibility**: an internal component/protocol contract is incompatible; node Health is not penalized.
2. **configuration**: proxy authentication, credentials, or obvious configuration problems; the node is marked unavailable but the event is not counted as a transport Health sample.
3. **hard_transport**: explicit exit-transport failures such as proxy connection failure, SOCKS CONNECT, HTTP CONNECT, or network unreachable; fixed-node Health is reduced and cooldown applies.
4. **suspected_transport**: TLS, EOF, reset, timeout, and similar errors that may come from either the proxy or target path; the node is immediately reprobed and is penalized only if that reprobe also fails.
5. **application**: application-layer conditions such as 401, 429, normal OAuth states, or business parameters; proxy-node Health is not penalized.

## Structured Bridge Diagnostics

LocalProxyBridge no longer swallows internal exceptions as generic EOFs. It records structured failure kinds such as:

```text
upstream_connect
http_proxy_auth
http_connect
socks_auth
socks_connect
https_proxy_tls
local_dns
remote_dns
remote_reset
bridge
```

ProxyPool prioritizes these structured diagnostics for classification; string matching is only a fallback.

## NSFW / CPA Post-Processing

NSFW or CPA failures do not discard or re-register confirmed accounts. **CPA export is enabled by default and runs synchronously**; unless an explicit `cpa_proxy` is set, it holds the registration worker and Proxy Lease until export finishes.

- Explicit proxy transport error → feed back into the corresponding proxy category.
- TLS/EOF/timeout → treat as suspected and reprobe immediately before deciding whether to penalize.
- compatibility/config/application → handle according to the corresponding category.
- Both an explicit CPA `cpa_proxy` and the Registration Lease are first converted to HTTP-compatible endpoints, preventing raw SOCKS URLs from being passed directly to network components that do not support that scheme.

## Registration-Path Preflight (Optional)

A non-destructive node-path preflight is provided for:

```text
accounts.x.ai
grok.com
```

It checks only reachability, HTTP status, latency, and obvious Cloudflare block indications. It does not create mailboxes, create accounts, modify account settings, or count as a Runtime Health sample.

Web API:

```text
POST /api/proxy-pool/preflight?node_id=<node-id>
```

Manual preflight is disabled while a task is running. It can be turned off entirely with:

```text
proxy_pool_preflight_enabled = false
```

## Health-State Persistence (Optional)

Default:

```text
proxy_pool_persist_health = false
```

When enabled, node business-health state is atomically written to:

```text
proxy_pool_state_file = ./proxy_pool_state.json
```

After the Manager is rebuilt, nodes with the same stable node ID restore Health, business counters, Failure/Cooldown state, and recent business errors. This file is included in `.gitignore` by default.

## WebUI

The proxy-pool page displays or stores:

- Full node URI.
- protocol / backend / fixed-or-rotating.
- IPv4 / IPv6 probe results.
- fixed Runtime Health or rotating gateway success rate.
- business / transport / configuration counters.
- inflight / cooldown / recent error.
- subscription LKG / stale diagnostics.
- dual-stack, runtime cache, health persistence, public-only subscription, preflight, and related settings.

Web API:

```text
GET  /api/proxy-pool/status
POST /api/proxy-pool/reload
POST /api/proxy-pool/test
POST /api/proxy-pool/preflight?node_id=<node-id>
```

Under the project's current local-use model, the WebUI, status API, and related logs continue to display full proxy addresses, including authentication information.

## Compatibility Boundary

Lease management and health scheduling apply to `single` / `pool`. Default `auto` preserves the existing single-proxy/direct path without changing GUI/CLI/WebUI, mailbox, output, or pending behavior.

Ordinary HTTP/SOCKS does not start sing-box merely because advanced-protocol support exists. VLESS/VMess/Trojan/Hysteria2/TUIC/Shadowsocks require sing-box only when actually acquired, probed, or preflighted.
