# 注册代理池

代理池是主注册流程的可选网络层。默认 `proxy_mode=auto`，因此旧配置仍按原来的单代理/直连逻辑运行；只有显式选择 `single` 或 `pool` 时才启用账号级代理租约。

## 设计原则

- **一个账号 attempt 一个稳定租约**：浏览器、邮箱请求、注册阶段 HTTP、NSFW 和未显式覆盖的 CPA/OIDC 使用同一个代理。
- **邮箱重试不换代理**：验证码邮箱更换和浏览器重启仍属于同一个账号 attempt。
- **slot 重试才释放租约**：确认代理 transport failure 后，当前 attempt 结束，下一 attempt 才重新获取代理。
- **并发共享健康状态**：所有 worker 共用一个 `ProxyPoolManager`，但通过 thread-local 保存各自租约。
- **固定代理与旋转入口分开处理**：固定代理 transport failure 会降健康度并进入冷却；旋转代理的一次坏出口不会冷却整个入口。
- **高级协议不侵入注册核心**：HTTP/SOCKS 继续原生使用；VLESS、VMess、Trojan、Hysteria2、TUIC 在被选中时按需转换成本机 HTTP 出口，再交给现有 ProxyLease。
- **管理 API 不继承注册代理**：显式 `proxies={}` 的请求保持直连语义。

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
| `auto` | 默认兼容模式。`proxy` 为空则直连，非空则继续使用旧单代理行为和旧直连回退。 |
| `direct` | 强制主注册流程直连。 |
| `single` | 将 `proxy` 作为一个受健康管理的固定/旋转节点；可直接填写原生代理或受支持的高级协议 URI。 |
| `pool` | 从本地文件和/或订阅加载多个节点并调度。 |

### `proxy_fallback`

| 值 | 行为 |
| --- | --- |
| `none` | 没有可用节点时不回退。 |
| `direct` | 新账号租约获取失败/超时时允许直连。 |
| `single` | 新账号租约获取失败/超时时使用 `proxy`；该 fallback 保持传统 HTTP/SOCKS 单代理语义。 |

Fallback 只发生在新账号 attempt 开始之前，不会在一个已经进行中的账号流程里静默换 IP。

## 支持的节点协议

代理源现在可混合包含：

```text
HTTP / HTTPS
SOCKS / SOCKS4 / SOCKS4A / SOCKS5 / SOCKS5H
VLESS
VMess
Trojan
Hysteria2 / hy2
TUIC
```

处理方式分成两类：

```text
HTTP / SOCKS
    → Python 原生代理路径

VLESS / VMess / Trojan / Hysteria2 / TUIC
    → sing-box outbound
    → 127.0.0.1:随机端口 HTTP inbound
    → 现有 ProxyLease / Chromium / HTTP / CPA
```

因此注册核心始终只消费一个普通 `ProxyLease.proxy_url`。高级协议不会要求 `browser_runtime.py`、邮箱模块或 CPA 自己实现对应协议。

## Base64 与订阅解析

`proxy_pool_file` 与 `proxy_pool_subscription_url` 都支持：

1. 普通的一行一个节点 URI；
2. 整份文本经过标准 Base64 或 URL-safe Base64 编码的订阅。

例如 Base64 解码后可以是：

```text
vless://...
socks5://...
trojan://...
hysteria2://...
vmess://...
tuic://...
```

解析器会逐行识别协议、去重并统计：

- 总行数
- 是否经过 Base64 解码
- 成功节点数
- 跳过节点数
- 每种协议的节点数量
- 最多前 50 条解析错误

WebUI 会把这些统计直接显示在代理节点表上方。某个不支持的 transport 或坏节点不会导致同一订阅里的其他有效节点一起丢失。

单个代理源最大 2 MiB、最多 10000 个节点。相对文件路径以项目根目录为基准。

## 各协议解析范围

### VLESS

支持常见 URI 字段，包括 UUID、TLS、SNI、ALPN、uTLS fingerprint、Reality `public key / short id`、flow，以及 `tcp/raw / ws / grpc / http(h2) / httpupgrade / quic` transport。

未知 transport（例如当前未映射的 `xhttp`）会明确记录为 unsupported，而不是悄悄降级为 TCP。

### VMess

支持常见 `vmess://Base64(JSON)` 节点，读取 server、port、UUID、alterId、security、TLS/SNI/fingerprint 与常见 V2Ray transport。

### Trojan

支持密码、TLS/SNI/ALPN/fingerprint，以及 `tcp/raw / ws / grpc / http(h2) / httpupgrade / quic` transport。

### Hysteria2

同时接受 `hysteria2://` 和 `hy2://`，支持 password、TLS/SNI/insecure、带宽参数，以及常见 obfs 配置。

### TUIC

支持 UUID/password、TLS/SNI/ALPN、congestion control、UDP relay mode、0-RTT 与 heartbeat 等常见参数。

### SOCKS

`socks://` 会规范化为 SOCKS5。原有 SOCKS4/4A/5/5H 与认证代理 bridge 保持不变。

## 高级协议运行时

默认：

```json
{
  "proxy_protocol_backend": "auto",
  "proxy_singbox_path": "",
  "proxy_protocol_start_timeout_sec": 10
}
```

### `proxy_protocol_backend`

| 值 | 行为 |
| --- | --- |
| `auto` | HTTP/SOCKS 原生处理；高级协议自动交给 sing-box。 |
| `sing-box` | 与 `auto` 的高级协议处理一致，明确选择 sing-box 后端。 |
| `native-only` | 只允许原生 HTTP/SOCKS；高级节点保留在解析结果中，但无法建立 runtime。 |

### `proxy_singbox_path`

留空时使用系统 `PATH` 中的 `sing-box`。也可以填写可执行文件路径。

项目不会自动下载或更新 sing-box，避免把平台安装、版本和校验逻辑耦合进注册器。检测到高级节点但找不到 executable 时会给出明确错误。

### Lazy runtime

不会因为订阅里有 500 个高级节点就启动 500 个进程。

只有节点真正被 acquire 或 probe 时才：

```text
生成临时 sing-box config
→ sing-box check
→ 启动本机 HTTP inbound
→ 等待 localhost 端口就绪
→ 返回给 ProxyLease
```

同一节点若同时被多个 lease 使用，会复用同一个 runtime；引用数降到 0 后停止进程并删除临时配置。临时配置文件权限会尽量设为 `0600`。

## 代理源

原生代理仍支持一行一个：

```text
# comment
http://127.0.0.1:8080
http://user:password@127.0.0.1:8080
https://user:password@127.0.0.1:8443
socks4://127.0.0.1:1080
socks5://user:password@127.0.0.1:1080
```

高级协议 URI 可以与它们放在同一文件或同一订阅中，最终按稳定节点 ID 去重。高级节点 ID 根据规范化 outbound 配置生成，因此只修改 `#节点名称` 不会重置已有健康状态。

`proxy_pool_subscription_proxy` 只负责**下载订阅本身**，因此仍只接受 HTTP/HTTPS/SOCKS，避免产生“先依赖高级节点才能下载包含该节点的订阅”的循环依赖。

## 旋转代理与 `{account}`

`proxy_pool_endpoint_mode`：

- `auto`：原生 URL 含 `{account}` 时自动视为旋转入口，否则视为固定代理。
- `fixed`：强制按固定节点处理。
- `rotating`：强制按旋转入口处理。

例如：

```text
http://user-{account}:password@proxy.example.com:8000
```

租约建立时 `{account}` 会替换为当前注册 attempt 的随机稳定 session key。同一 attempt 内值不变，slot retry 时会生成新的 key。

## 健康度与冷却

固定代理成功后：

```text
health = min(1.0, health + 0.1)
failure_count = 0
cooldown = none
```

明确 transport failure 后：

```text
failure_count += 1
health = max(0.05, health * 0.7)
```

冷却时间：

```text
30s → 60s → 120s → 240s → 480s → 最大 600s
```

进入 transport cooldown 后会安排独立健康探测；同一个节点同时只会存在一个失败恢复探测。401、429、验证码缺失、页面 selector 变化、OIDC 错误、结果文件写入错误等不会因为“注册失败”而自动处罚代理。

高级协议配置解析错误、后端缺失或 `sing-box check` 拒绝属于 backend/configuration failure，不会伪装成普通注册失败。该节点会暂时从 acquire 候选中移除，并可通过重新加载或手动探测恢复。

## 节点选择与并发

节点先过滤：

```text
enabled
+ not retired
+ not cooling down
+ inflight < capacity
```

之后按 `worker + slot` 构造 affinity，并通过 SHA-256 稳定选择节点。首选节点健康度低于 `0.8` 时，会优先选择健康度更高的可用节点。

`proxy_pool_max_concurrent_per_node` 默认 `1`。节点全部占用时等待 `proxy_pool_acquire_timeout_sec`；超时后根据 `proxy_fallback` 决定是否回退。

## 健康探测

支持：

```text
proxy_pool_probe_provider = cloudflare | ipinfo
```

原生代理直接进行探测；高级协议节点会临时或复用已有 runtime，再通过相同 Cloudflare/ipinfo 请求检查出口。手动测试结束后，没有活动 lease 的临时 runtime 会被停止，因此不会因为“测试全部节点”长期留下大量进程。

探测会记录：

- `healthy / unhealthy / unavailable`
- 延迟
- 出口 IP
- 最近错误
- failure count
- cooldown
- inflight

批量探测最多同时使用 8 个 probe worker。

## CPA/OIDC

CPA 的代理优先级：

```text
显式 cpa_proxy
    > 当前 Registration ProxyLease
    > 旧 proxy
    > direct
```

高级协议被选择时，Registration ProxyLease 保存的是对应的 localhost HTTP endpoint，因此未单独配置 `cpa_proxy` 的 CPA 会继续复用同一个高级协议出口。

## WebUI

WebUI 的代理池页现在显示：

- 节点名称与完整 URI
- 协议
- runtime backend
- fixed / rotating
- 健康度
- 延迟
- 出口 IP
- inflight
- failure count
- cooldown
- Base64 / protocol count / skipped parse diagnostics

Web API 仍为：

```text
GET  /api/proxy-pool/status
POST /api/proxy-pool/reload
POST /api/proxy-pool/test
```

节点状态直接返回完整代理地址，包括用户名和密码。当前项目按个人部署场景设计，因此 WebUI、状态 API 与代理相关日志不会对代理凭据做脱敏。注册任务运行期间仍禁止手动 reload/test。

## Chromium 认证代理

原生 HTTP/HTTPS/SOCKS 继续通过既有本地认证 bridge 适配 Chromium。

高级协议则由 sing-box 暴露本机 HTTP inbound，因此 Chromium 同样只连接 `127.0.0.1`。浏览器层不需要理解 VLESS/VMess/Trojan/Hysteria2/TUIC。

## 兼容性

不修改旧配置时：

```text
proxy_mode = auto
```

所以旧 `proxy`、GUI/CLI/WebUI、串行注册、多线程、邮箱服务、结果落盘、pending、grok2api 和 CPA 的既有入口仍然存在。HTTP/SOCKS 节点不会因为此次升级而启动 sing-box；只有高级协议节点被实际选择或测试时才需要该 executable。
