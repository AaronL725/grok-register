# 注册代理池

代理池是主注册流程的可选网络层。默认 `proxy_mode=auto`，旧配置仍按原来的单代理/直连逻辑运行；只有显式选择 `single` 或 `pool` 时才启用账号级代理租约。

## 核心原则

- **一个账号 attempt 一个稳定租约**：浏览器、邮箱请求、注册阶段 HTTP、NSFW，以及未显式覆盖的 CPA/OIDC 共用同一个 `ProxyLease`。
- **所有 managed 网络组件消费同一种 endpoint**：业务层只接收 HTTP-compatible proxy，不再分别处理 SOCKS、VLESS、Trojan 等协议。
- **邮箱重试不换代理**：验证码邮箱更换和浏览器重启仍属于同一个账号 attempt。
- **slot 重试才释放租约**：明确代理 transport failure 后，当前 attempt 结束，下一 attempt 才重新获取节点。
- **Probe 与 Health 分离**：主动探测回答“现在能否连通”，运行健康分回答“真实业务长期表现如何”。
- **固定代理与旋转入口分开处理**：固定代理的 transport failure 会降健康分并冷却；旋转入口的一次坏出口不会冷却整个入口。
- **管理 API 不继承注册代理**：显式 `proxies={}` 的管理请求保持直连语义。

## 统一网络出口

在 `single` / `pool` 模式下，原始节点首先转换成业务组件都能消费的 endpoint：

```text
plain HTTP (no auth)
    → 原 HTTP endpoint

HTTP + auth / HTTPS proxy / SOCKS4 / SOCKS5
    → Python LocalProxyBridge
    → http://127.0.0.1:<port>

VLESS / VMess / Trojan / Hysteria2 / TUIC
    → sing-box
    → http://127.0.0.1:<port>
```

随后统一进入：

```text
ProxyLease.proxy_url
      ↓
Chromium / curl_cffi / Mail / NSFW / CPA OAuth / CPA Browser / Probe
```

因此业务代码不会再出现“Chromium 能使用 SOCKS，但 `urllib` 不认识 `socks5://`”这类组件间协议差异。`ProxyLease.source_uri` 仍保留原始节点 URI，便于日志、WebUI 和诊断显示。

> `proxy_mode=auto` 仍保留历史网络行为，避免无意改变旧用户配置。

## 配置

```json
{
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

  "proxy_pool_max_concurrent_per_node": 1,
  "proxy_pool_acquire_timeout_sec": 30,

  "proxy_protocol_backend": "auto",
  "proxy_singbox_path": "",
  "proxy_protocol_start_timeout_sec": 10
}
```

### `proxy_mode`

| 值 | 行为 |
| --- | --- |
| `auto` | 默认兼容模式。继续使用历史 `proxy` 行为。 |
| `direct` | 强制主注册流程直连。 |
| `single` | 将 `proxy` 作为一个受租约和健康管理的节点。 |
| `pool` | 从本地文件和/或订阅加载多个节点并调度。 |

### `proxy_fallback`

| 值 | 行为 |
| --- | --- |
| `none` | 没有可用节点时不回退。 |
| `direct` | 新账号租约获取失败/超时时允许直连。 |
| `single` | 新账号租约获取失败/超时时使用 `proxy`。 |

Fallback 只发生在新账号 attempt 开始之前，不会在一个已经进行中的账号流程里静默换 IP。

## 支持的节点协议

代理源可以混合包含：

```text
HTTP / HTTPS
SOCKS / SOCKS4 / SOCKS4A / SOCKS5 / SOCKS5H
VLESS
VMess
Trojan
Hysteria2 / hy2
TUIC
```

### 高级协议运行时

高级协议由 sing-box 按需转换成本机 HTTP endpoint。默认配置：

```json
{
  "proxy_protocol_backend": "auto",
  "proxy_singbox_path": "",
  "proxy_protocol_start_timeout_sec": 10
}
```

`proxy_protocol_backend`：

| 值 | 行为 |
| --- | --- |
| `auto` | 原生代理由 Python endpoint adapter 处理；高级协议自动交给 sing-box。 |
| `sing-box` | 高级协议明确使用 sing-box。 |
| `native-only` | 允许 HTTP/SOCKS，禁用高级协议 runtime。 |

`proxy_singbox_path` 留空时从系统 `PATH` 查找 `sing-box`。项目不自动下载或更新 sing-box。

### Lazy runtime

不会因为订阅中存在大量节点就启动同等数量的进程/bridge。只有节点真正被 acquire 或 probe 时才创建 runtime；同一节点可复用 runtime，引用数降到 0 后自动停止并清理。

## Base64 与订阅解析

`proxy_pool_file` 与 `proxy_pool_subscription_url` 都支持普通逐行 URI，以及整份文本经过标准 Base64 / URL-safe Base64 编码的订阅。

例如解码后：

```text
vless://...
socks5://...
trojan://...
hysteria2://...
vmess://...
tuic://...
```

解析器会记录：总行数、Base64 状态、成功节点数、跳过数、协议数量和解析错误。单个坏节点不会导致同一订阅的其他有效节点被丢弃。

单个代理源最大 2 MiB、最多 10000 个节点。相对文件路径以项目根目录为基准。

`proxy_pool_subscription_proxy` 只负责下载订阅本身，因此仍只接受 HTTP/HTTPS/SOCKS。

## 协议解析范围

- **VLESS**：UUID、TLS、SNI、ALPN、uTLS fingerprint、Reality、flow，以及常见 tcp/raw/ws/grpc/http/httpupgrade/quic transport。
- **VMess**：`vmess://Base64(JSON)`，包括 server、port、UUID、alterId、security、TLS/SNI/fingerprint 和常见 transport。
- **Trojan**：password、TLS/SNI/ALPN/fingerprint 和常见 transport。
- **Hysteria2 / hy2**：password、TLS/SNI/insecure、带宽、常见 obfs。
- **TUIC**：UUID/password、TLS/SNI/ALPN、congestion control、UDP relay mode、0-RTT、heartbeat。
- **SOCKS**：`socks://` 规范化为 SOCKS5；SOCKS4/4A/5/5H 均可进入统一 HTTP bridge。

不支持的 transport 会明确记录为 unsupported，不会悄悄降级。

## 旋转代理与 `{account}`

`proxy_pool_endpoint_mode`：

- `auto`：原生 URI 含 `{account}` 时视为旋转入口。
- `fixed`：强制按固定节点处理。
- `rotating`：强制按旋转入口处理。

例如：

```text
http://user-{account}:password@proxy.example.com:8000
```

租约建立时 `{account}` 会替换为当前 attempt 的随机稳定 session key。同一 attempt 内不变，slot retry 后重新生成。

## Probe 与运行健康分

这是两个不同指标。

### Probe Status

表示最近一次主动连通性检查：

```text
unknown / healthy / unhealthy / unavailable
```

Probe 保存：

- `probe_status`
- `last_probed_at`
- `probe_latency_ms`
- `probe_error`
- `exit_ip`

**普通 probe 失败不会直接降低运行 Health。** 一个节点可能历史业务表现很好，但某次主动探测遇到临时网络问题；反过来，新节点也可能 Health 初始为 `1.0`，但尚无任何真实业务样本。

### Runtime Health

表示真实注册/网络业务历史表现。

新节点：

```text
health = 1.0
business_samples = 0
```

因此 WebUI 在 `business_samples=0` 时显示“未产生业务样本”，而不是把 `1.0` 误解为已经验证过的满分节点。

真实业务成功：

```text
business_samples += 1
health = min(1.0, health + 0.1)
failure_count = 0
cooldown = none
```

固定节点确认 transport failure：

```text
business_samples += 1
failure_count += 1
health = max(0.05, health * 0.7)
```

冷却：

```text
30s → 60s → 120s → 240s → 480s → 最大 600s
```

### 失败分类

网络错误分成三类处理：

1. **Hard proxy failure**：代理认证失败、SOCKS connect failure、CONNECT tunnel failure、local bridge failure 等。直接进入 transport failure / cooldown。
2. **Suspected route failure**：TLS/SSL handshake、EOF、reset、timeout 等。这些不一定由代理造成，因此先安排当前节点立即 probe；只有复测也失败才处罚 Health。
3. **Application / compatibility failure**：401、429、OAuth pending、业务参数错误、内部组件不支持某 scheme 等，不处罚代理节点。

独立 probe 成功也不会随意把低 Health 拉回高分；只有节点之前明确处于 transport/backend failure 状态时，成功 probe 才用于确认线路恢复并清除相应 cooldown。

## NSFW 后处理

NSFW 失败不会让已经注册成功的账号被丢弃或重新注册。

如果 NSFW 报告明确代理错误，会反馈给当前 Lease；如果是 TLS/EOF/timeout 这类可疑错误，会先立即复测；如果是普通 HTTP/业务错误，则只保留 warning。

这样可以避免“注册成功但 NSFW 网络失败，ProxyPool 最后仍无条件记为完美成功”的错误反馈。

## CPA/OIDC

CPA 代理优先级保持：

```text
显式 cpa_proxy
    > 当前 Registration ProxyLease
    > 旧 proxy
    > direct
```

无论代理来自 Registration Lease 还是用户显式填写 `cpa_proxy`，在进入 Python `urllib` OAuth discovery/token poll 前都会先转换成 HTTP-compatible endpoint。因此原生：

```text
socks5://user:pass@host:port
```

不会再直接进入 `urllib.request.ProxyHandler`，从而避免：

```text
unknown url type: socks5
```

OAuth 层本身还会 fail-fast：如果未来某处错误地把非 HTTP-compatible scheme 传入，会给出明确的 transport contract 错误，而不是底层模糊异常。

CPA 导出失败仍会记录 `cpa_auth_failed.txt`，但不会影响主账号保存。

## 健康探测

支持：

```text
proxy_pool_probe_provider = cloudflare | ipinfo
```

所有节点都通过与真实业务相同的 endpoint runtime 进行 probe。高级协议由 sing-box 提供本地 HTTP；SOCKS/HTTPS/auth HTTP 由共享 Python bridge 提供本地 HTTP。批量探测最多 8 个 worker。

对于失败 probe，WebUI 会显示“X ms 后失败”及 `probe_error`，而不会把失败耗时展示成一个看似优秀的正常延迟。

## WebUI

代理池页显示：

- 节点完整 URI
- 协议
- backend
- fixed / rotating
- **探测状态**
- **运行健康分 + 业务样本数**
- 探测延迟
- 出口 IP
- inflight
- failure count
- cooldown
- 最近 probe / runtime error
- Base64 / protocol count / skipped diagnostics

Web API：

```text
GET  /api/proxy-pool/status
POST /api/proxy-pool/reload
POST /api/proxy-pool/test
```

项目的当前本地使用模式下，节点状态、WebUI 和相关日志继续显示完整代理地址，包括用户名和密码。

## 兼容性边界

本次统一 endpoint 只作用于 `single` / `pool` managed 模式。默认：

```text
proxy_mode = auto
```

继续保持旧 GUI/CLI/WebUI、邮箱、结果落盘、pending、grok2api 与历史代理行为。不会因为本次修复让普通 HTTP/SOCKS 节点启动 sing-box；高级协议仍只有在实际使用/测试时才需要 sing-box。
