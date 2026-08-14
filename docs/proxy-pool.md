# 注册代理池

代理池是主注册流程的可选网络层。默认 `proxy_mode=auto`，继续保持旧配置的历史单代理/直连行为；只有显式选择 `single` 或 `pool` 时才启用账号级 `ProxyLease`、节点调度、探测和健康反馈。

## 核心原则

- **一个账号 attempt 一个稳定租约**：浏览器、邮箱请求、注册阶段 HTTP、NSFW，以及未显式覆盖的 CPA/OIDC 共用同一个出口。
- **安全重试优先于盲目重放**：只有尚未进行有状态提交的阶段才允许释放 Lease 后重新开始；邮箱、验证码、资料等提交之后发生传输错误会标记为结果不确定，不自动换代理重放整个注册流程。
- **所有 managed 网络组件消费统一 HTTP-compatible endpoint**：HTTP/SOCKS/高级协议最终都可被 Chromium、curl_cffi、urllib、CPA 和 probe 统一消费。
- **Probe 与 Runtime Health 分离**：主动探测回答“现在能否连通”，业务健康回答“真实注册历史表现如何”。
- **Fixed 与 Rotating 分离**：固定节点使用 Health/Failure/Cooldown；旋转入口统计出口成功/失败和成功率，不因为一次坏出口冷却整个 gateway。
- **Source-scoped Last-Known-Good**：文件源和订阅源独立刷新；单个源临时刷新失败时保留该源最近一次成功节点。
- **默认兼容**：`auto` / `direct` 仍走历史路径，不强制启用 managed proxy 行为。

## 统一网络出口

`single` / `pool` 模式下：

```text
plain HTTP (no auth)
    → 原 HTTP endpoint

HTTP + auth / HTTPS proxy / SOCKS4 / SOCKS5
    → LocalProxyBridge
    → http://127.0.0.1:<port>

VLESS / VMess / Trojan / Hysteria2 / TUIC / Shadowsocks
    → sing-box
    → http://127.0.0.1:<port>
```

随后统一进入：

```text
ProxyLease.proxy_url
      ↓
Chromium / curl_cffi / Mail / NSFW / CPA OAuth / CPA Browser / Probe / Preflight
```

`ProxyLease.source_uri` 保留原始节点 URI，用于日志、WebUI 和诊断。

## 配置

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

| 值 | 行为 |
| --- | --- |
| `auto` | 默认兼容模式，继续使用历史 `proxy` 行为。 |
| `direct` | 强制主注册流程直连。 |
| `single` | 将 `proxy` 作为受 Lease 和健康管理的单节点。 |
| `pool` | 从文件和/或订阅加载并调度多个节点。 |

### `proxy_fallback`

| 值 | 行为 |
| --- | --- |
| `none` | 无可用节点时不回退。 |
| `direct` | 新 attempt 获取 Lease 失败/超时时允许直连。 |
| `single` | 新 attempt 获取 Lease 失败/超时时使用 `proxy`。 |

Fallback 只发生在新的账号 attempt 开始前，不会在已经进行中的注册流程里静默换 IP。

## Registration safe-retry boundary

Managed 注册流程跟踪当前阶段：

```text
lease_acquire
browser_start
page_open
email_submit
code_submit
profile_submit
sso_wait
account_confirmed
postprocess
```

重试原则：

```text
lease_acquire / browser_start / page_open
→ SAFE_NEW_LEASE
→ 可以释放旧 Lease 后重新开始

email_submit / code_submit / profile_submit / sso_wait
→ OUTCOME_UNCERTAIN
→ 不自动换代理重放整个注册流程

account_confirmed / postprocess
→ NO_RETRY
→ 已确认账号不重新注册
```

这样可以避免“提交已可能被服务端接收，但本地读取响应时断线”后再次换 IP 重放，从而降低重复账号和重复提交风险。邮箱验证码重试仍在同一个账号 Lease 内完成。

WebUI 状态额外记录 `uncertain` 数量。

## 支持协议

代理源可以混合包含：

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

高级协议由 sing-box 按需转换成本机 HTTP endpoint。`proxy_singbox_path` 留空时从系统 `PATH` 查找 `sing-box`；项目不自动下载或更新它。

Shadowsocks 支持常见 SIP002 / legacy Base64 URI；当前内置支持常见 AEAD / 2022 method，不支持的 plugin 或 method 会明确报错，不会静默降级。

## Native URI 规范化

HTTP/HTTPS/SOCKS 原生代理必须表示明确的 proxy endpoint：

```text
scheme://[user:password@]host:port
```

规则：

- 必须有端口；
- 不接受 routing path 或 query；
- `#fragment` 仅作为节点展示名，不进入 canonical URI / node identity；
- `{account}` 最多出现一次，并且只允许出现在代理 username 中；
- `socks://` 规范化为 `socks5://`。

## SOCKS DNS 语义

共享 bridge 明确区分：

```text
socks5://
→ 本地 DNS resolve
→ 向 SOCKS server 发送 IP

socks5h://
→ 不在本地解析 hostname
→ hostname 交给 SOCKS server resolve
```

SOCKS4 / SOCKS4A 同样分别保持 local / remote DNS 语义。

## Runtime idle cache

Runtime 仍然按需创建，不会因为订阅里有大量节点就一次性启动大量 bridge / sing-box。

引用数降到 0 后默认进入 idle cache：

```text
proxy_runtime_idle_ttl_sec = 120
proxy_runtime_cache_max = 32
```

同一节点在 TTL 内再次 acquire 可直接复用；超过 TTL 或空闲缓存上限时清理最久未使用 runtime。设置 `proxy_runtime_idle_ttl_sec=0` 可恢复“零引用立即关闭”。Manager shutdown 会关闭全部残留 runtime。

## Base64、订阅刷新与 Last-Known-Good

`proxy_pool_file` 和 `proxy_pool_subscription_url` 支持：

- 普通逐行 URI；
- 整份标准 Base64；
- URL-safe Base64；
- 混合多协议节点。

单个源最大 2 MiB、最多 10000 个节点。解析结果记录总行数、Base64 状态、成功节点数、跳过数、协议数量和错误。

文件源与订阅源分别维护：

```text
last_success_at
last_error
generation
nodes
diagnostics
```

例如文件刷新成功、订阅临时 timeout：

```text
file         → 使用最新 generation
subscription → 保留上一次成功 generation，并标记 stale
```

不会因为另一个 source 正常刷新而把失败 source 的最近成功节点清空。

## 订阅目标限制（可选）

`proxy_pool_subscription_public_only=true` 时，订阅初始 URL 和每一次 HTTP redirect 都会重新检查目标：

- 只允许 `http` / `https`；
- 必须可解析；
- 拒绝 private / loopback / link-local / multicast / reserved / unspecified 地址；
- redirect 最多 3 次；
- 响应仍受 2 MiB 内容限制。

该选项默认关闭，便于本地研究环境继续使用局域网或自建订阅服务。

## Probe：IPv4 / IPv6 与 false-positive 防护

支持：

```text
proxy_pool_probe_provider = cloudflare | ipinfo
proxy_pool_probe_dual_stack = true | false
```

启用双栈后，IPv4 和 IPv6 独立探测并保存：

```text
status
tested_at
latency_ms
exit_ip
error
```

一个 family 正常、另一个 family 失败时，节点整体仍可判定为可用，同时保留两个 family 的独立结果。

**HTTP 2xx 不再等于 probe healthy。** Probe 必须同时满足：

```text
HTTP 2xx
+ 成功解析有效 exit IP
+ IP family 与当前 IPv4/IPv6 probe 匹配
```

否则判定为 `unhealthy`，解决“200 但响应格式异常/没有 IP”造成的 false positive。

## Probe-aware soft selection

节点调度先满足：

```text
enabled
not retired
capacity available
fixed node not cooling
```

然后按最近 probe 分层：

```text
Tier 0: recent healthy
Tier 1: unknown / stale
Tier 2: recent unhealthy
```

优先从更好 tier 中做 affinity / health / inflight 选择。Recent unhealthy 是 **soft deprioritize**，不是永久 hard ban；当它是唯一可选节点时仍可继续尝试。

## Fixed 与 Rotating 健康模型

### Fixed node

真实成功：

```text
registration_successes += 1
business_samples += 1
health = min(1.0, health + 0.1)
failure_count = 0
cooldown = none
```

确认 transport failure：

```text
transport_failures += 1
business_samples += 1
failure_count += 1
health = max(0.05, health * 0.7)
```

冷却：

```text
30s → 60s → 120s → 240s → 480s → 最大 600s
```

### Rotating gateway

旋转入口不显示固定节点 Health，也不因单次坏出口执行 gateway-wide cooldown。它记录：

```text
exit_successes
exit_failures
gateway_success_rate
```

这样不会让一个经常更换出口的 gateway 因成功样本而长期误显示成 `Health=1.0`。

### Business sample 去重

一个 account attempt 最多贡献一个业务健康样本。成功后又发生 suspected failure probe 时，不会把同一 attempt 重复统计成两个业务样本。

**配置/认证错误不属于业务健康样本。** 它只增加 `configuration_failures` 并将节点标记为不可用，不降低 Health、不增加 `business_samples`、不进入指数 transport cooldown。

## 五类失败语义

网络反馈分为：

1. **compatibility**：内部组件/协议 contract 不兼容；不处罚节点 Health。
2. **configuration**：代理认证、凭据、明显配置问题；节点标记不可用，但不作为 transport Health sample。
3. **hard_transport**：代理连接、SOCKS CONNECT、HTTP CONNECT、网络不可达等明确出口传输失败；固定节点降 Health 并 cooldown。
4. **suspected_transport**：TLS、EOF、reset、timeout 等可能来自代理也可能来自目标链路的问题；先立即复测，只有复测也失败才处罚。
5. **application**：401、429、OAuth 正常状态、业务参数等应用层问题；不处罚代理节点。

## Structured bridge diagnostics

LocalProxyBridge 不再只把内部异常吞成 EOF，而会记录结构化 failure kind，例如：

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

ProxyPool 优先使用这些 structured diagnostics 分类；字符串匹配只作为 fallback。

## NSFW / CPA 后处理

NSFW 或 CPA 失败不会让已经注册成功的账号被丢弃或重新注册。

- 明确代理 transport error → 进入对应代理反馈；
- TLS/EOF/timeout → suspected，立即复测后决定是否处罚；
- compatibility/config/application → 按对应类别处理；
- CPA 显式 `cpa_proxy` 和 Registration Lease 均先转换成 HTTP-compatible endpoint，避免 raw SOCKS 被直接交给不支持该 scheme 的网络组件。

## Registration-path preflight（可选）

提供非破坏性的节点路径预检：

```text
accounts.x.ai
grok.com
```

只检查 reachability、HTTP status、latency 和明显 Cloudflare block indication，不创建邮箱、不创建账号、不修改账号设置，也不作为 Runtime Health sample。

Web API：

```text
POST /api/proxy-pool/preflight?node_id=<node-id>
```

任务运行期间禁止手动 preflight。可通过：

```text
proxy_pool_preflight_enabled = false
```

关闭该入口。

## 健康状态持久化（可选）

默认：

```text
proxy_pool_persist_health = false
```

启用后把节点业务健康状态原子写入：

```text
proxy_pool_state_file = ./proxy_pool_state.json
```

重建 Manager 后，相同 stable node ID 会恢复 Health、业务计数、Failure/Cooldown 和最近业务错误等状态。该文件默认加入 `.gitignore`。

## WebUI

代理池页显示或保存：

- 完整节点 URI；
- protocol / backend / fixed-or-rotating；
- IPv4 / IPv6 probe；
- fixed Runtime Health 或 rotating gateway success rate；
- business / transport / configuration counters；
- inflight / cooldown / recent error；
- subscription LKG / stale diagnostics；
- dual-stack、runtime cache、health persistence、public-only subscription、preflight 等配置。

Web API：

```text
GET  /api/proxy-pool/status
POST /api/proxy-pool/reload
POST /api/proxy-pool/test
POST /api/proxy-pool/preflight?node_id=<node-id>
```

项目当前本地使用模式下，WebUI、状态 API 和相关日志继续显示完整代理地址，包括认证信息。

## 兼容性边界

本轮 V3 行为集中在 `single` / `pool` managed 模式。默认 `proxy_mode=auto` 继续保持旧 GUI/CLI/WebUI、邮箱、结果落盘、pending、token sync 与历史代理行为。

普通 HTTP/SOCKS 不会因为高级协议支持而启动 sing-box；VLESS/VMess/Trojan/Hysteria2/TUIC/Shadowsocks 只有在实际 acquire / probe / preflight 时才需要 sing-box。