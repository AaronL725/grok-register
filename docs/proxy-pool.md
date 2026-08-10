# 注册代理池

代理池是主注册流程的可选网络层。默认 `proxy_mode=auto`，因此旧配置仍按原来的单代理/直连逻辑运行；只有显式选择 `single` 或 `pool` 时才启用账号级代理租约。

## 设计原则

- **一个账号 attempt 一个稳定租约**：浏览器、邮箱请求、注册阶段 HTTP、NSFW 和未显式覆盖的 CPA/OIDC 使用同一个代理。
- **邮箱重试不换代理**：验证码邮箱更换和浏览器重启仍属于同一个账号 attempt。
- **slot 重试才释放租约**：确认代理 transport failure 后，当前 attempt 结束，下一 attempt 才重新获取代理。
- **并发共享健康状态**：所有 worker 共用一个 `ProxyPoolManager`，但通过 thread-local 保存各自租约。
- **固定代理与旋转入口分开处理**：固定代理 transport failure 会降健康度并进入冷却；旋转代理的一次坏出口不会冷却整个入口。
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
  "proxy_pool_acquire_timeout_sec": 30
}
```

### `proxy_mode`

| 值 | 行为 |
| --- | --- |
| `auto` | 默认兼容模式。`proxy` 为空则直连，非空则继续使用旧单代理行为和旧直连回退。 |
| `direct` | 强制主注册流程直连。 |
| `single` | 将 `proxy` 作为一个受健康管理的固定/旋转节点。 |
| `pool` | 从本地文件和/或订阅加载多个节点并调度。 |

### `proxy_fallback`

| 值 | 行为 |
| --- | --- |
| `none` | 没有可用节点时不回退。 |
| `direct` | 新账号租约获取失败/超时时允许直连。 |
| `single` | 新账号租约获取失败/超时时使用 `proxy`。 |

Fallback 只发生在新账号 attempt 开始之前，不会在一个已经进行中的账号流程里静默换 IP。

## 代理源

`proxy_pool_file` 支持一行一个代理：

```text
# comment
http://127.0.0.1:8080
http://user:password@127.0.0.1:8080
https://user:password@127.0.0.1:8443
socks4://127.0.0.1:1080
socks5://user:password@127.0.0.1:1080
```

也支持整份 Base64 编码的代理列表。文件与 `proxy_pool_subscription_url` 可以同时启用，最终按规范化 URL 去重。单个代理源最大 2 MiB、最多 10000 个节点。

相对文件路径以项目根目录为基准。

## 旋转代理与 `{account}`

`proxy_pool_endpoint_mode`：

- `auto`：URL 含 `{account}` 时自动视为旋转入口，否则视为固定代理。
- `fixed`：强制按固定节点处理。
- `rotating`：强制按旋转入口处理。

例如：

```text
http://user-{account}:password@proxy.example.com:8000
```

租约建立时 `{account}` 会替换为当前注册 attempt 的随机稳定 session key。同一 attempt 内值不变，slot retry 时会生成新的 key，因此适用于需要通过用户名/session 参数切换出口的代理服务。

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

冷却时间按失败次数指数增长：

```text
30s → 60s → 120s → 240s → 480s → 最大 600s
```

进入 transport cooldown 后会安排一次独立健康探测；同一个节点同时只会存在一个失败恢复探测。探测恢复时会提前清除 transport cooldown。

401、429、验证码缺失、页面 selector 变化、OIDC 错误、结果文件写入错误等不会因为“注册失败”而自动处罚代理。

## 节点选择与并发

节点先过滤：

```text
enabled
+ not retired
+ not cooling down
+ inflight < capacity
```

之后按 `worker + slot` 构造 affinity，并通过 SHA-256 稳定选择节点。首选节点健康度低于 `0.8` 时，会优先选择健康度更高的可用节点。

`proxy_pool_max_concurrent_per_node` 默认 `1`。例如 4 个 worker 和 4 个固定代理通常会形成：

```text
T1 → P1
T2 → P2
T3 → P3
T4 → P4
```

节点全部占用时等待 `proxy_pool_acquire_timeout_sec`；超时后根据 `proxy_fallback` 决定是否回退。

## 健康探测

支持：

```text
proxy_pool_probe_provider = cloudflare | ipinfo
```

探测会记录：

- `healthy / unhealthy`
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

因此代理池模式下，未单独配置 `cpa_proxy` 时，CPA 会继续使用当前账号的注册出口。显式 `cpa_proxy` 仍保持最高优先级。

## WebUI

启动 WebUI 后会出现“代理池”配置页，包含配置字段、节点状态表以及：

- 重新加载代理源
- 手动测试全部节点
- 自动刷新节点状态

Web API：

```text
GET  /api/proxy-pool/status
POST /api/proxy-pool/reload
POST /api/proxy-pool/test
```

节点状态直接返回完整代理地址，包括用户名和密码，例如：

```text
http://user:password@127.0.0.1:8080
```

当前项目按个人部署场景设计，因此 WebUI、状态 API 与代理相关日志不会对代理凭据做脱敏。注册任务运行期间仍禁止手动 reload/test，避免人为改变活动调度状态。

## Chromium 认证代理

浏览器统一通过本地 HTTP bridge 处理带认证的上游代理。bridge 支持：

- HTTP
- HTTPS
- SOCKS4 / SOCKS4A
- SOCKS5 / SOCKS5H

因此 Chromium 侧只需要连接 `127.0.0.1` 的临时 bridge，而上游认证和 SOCKS 握手由 bridge 完成。

## 兼容性

不修改旧配置时：

```text
proxy_mode = auto
```

所以旧 `proxy`、GUI/CLI/WebUI、串行注册、多线程、邮箱服务、结果落盘、pending、grok2api 和 CPA 的既有入口仍然存在。高级代理池参数可以通过 `config.json` 或 WebUI 配置。
