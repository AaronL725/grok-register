# Registration Proxy Pool

<p><a href="proxy-pool.md">ç®€ä½“ä¸­æ–‡</a> | <strong>English</strong></p>

The proxy pool is an optional network layer for the main registration flow. By default, `proxy_mode=auto` preserves the legacy single-proxy/direct behavior from older configurations. Account-level `ProxyLease`, node scheduling, probing, and health feedback are enabled only when `single` or `pool` is explicitly selected.

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
    â†’ original HTTP endpoint

HTTP + auth / HTTPS proxy / SOCKS4 / SOCKS5
    â†’ LocalProxyBridge
    â†’ http://127.0.0.1:<port>

VLESS / VMess / Trojan / Hysteria2 / TUIC / Shadowsocks
    â†’ sing-box
    â†’ http://127.0.0.1:<port>
```

All traffic then flows through:

```text
ProxyLease.proxy_url
      â†“
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

Fallback occurs only before a new account attempt begins. It never silently changes IP during an in-progress registration flow.

## Registration Safe-Retry Boundary

The managed registration flow tracks the current stage:

```text
lease_acquire
browser_start
page_open
email_submit
code_wait
code_submit
profile_submit
sso_wait
account_confirmed
postprocess
```

Retry rules:

```text
lease_acquire / browser_start / page_open
â†’ SAFE_NEW_LEASE
â†’ release the old Lease and restart is allowed

code_wait
â†’ SAME_LEASE_RECOVERY
â†’ keep the current Lease, switch email, restart the browser, and continue

email_submit / code_submit / profile_submit / sso_wait
â†’ OUTCOME_UNCERTAIN
â†’ do not replay the full registration by switching email or proxy

account_confirmed / postprocess
â†’ NO_RETRY
â†’ a confirmed account is not registered again
```

This avoids replaying a registration through a new IP after a submission may already have reached the server but the local client lost the response, reducing duplicate accounts and duplicate submissions. Only when the flow is still at `code_wait` and no usable verification code has been obtained may recovery switch the email address while keeping the same account Lease. Once `code_submit` begins,[ˆ[˜ÛÛ™š\›YYİX›Z\ÜÚ[Ûˆ™\İ[\ÈÛİ[Y\Èİ]ÛÛYH[˜Ù\Z[ˆ[™[XZ[™]šY\È›ÈÛ™Ù\ˆ\\ÜÈHØY™K\™]H›İ[™\K‚‚•HÙX•RHY][Û˜[H˜XÚÜÈ[ˆ[˜Ù\Z[˜Ûİ[‚‚ˆÈÈİ\ÜY›İØÛÛÂ‚”›ŞHÛİ\˜Ù\ÈX^HÛÛZ[ˆ[HZ^Ù‚‚˜^’ÈB”ÓĞÒÔÈÈÓĞÒÔÍÈÓĞÒÔÍHÈÓĞÒÔÍHÈÓĞÒÔÍR•“TÔÂ•“Y\ÜÂ•›Ú˜[‚’\İ\šXLˆÈL‚•RPÂ”ÚYİÜÛØÚÜÈÈÜÂ˜‚Y˜[˜ÙY›İØÛÛÈ\™HÛÛ™\YÛˆ[X[™HÚ[™ËX›Ş[ÈHØØ[[™Ú[ˆYˆ›ŞWÜÚ[™Ø›ŞÜ]\È[\KH›Ú™XİÛÚÜÈ›ÜˆÚ[™ËX›Ş[ˆHŞ\İ[HUˆ]Ù\È›İ]]ÛX]XØ[HİÛ›ØYÜˆ\]H]‚‚”ÚYİÜÛØÚÜÈİ\ÜÈÛÛ[[ÛˆÒTˆÈYØXŞH˜\ÙMT’\ËˆHZ[Z[ˆ[\[Y[][Ûˆİ\œ™[Hİ\ÜÈÛÛ[[ÛˆQPQÈŒŒˆY]ÙÎÈ[œİ\ÜYYÚ[œÈÜˆY]ÙÈ›ÙXÙH^XÚ]\œ›ÜœÈ˜]\ˆ[ˆÚ[[HYÜ˜Y[™Ë‚‚ˆÈÈ˜]]™HT’H›Ü›X[^˜][Û‚‚“˜]]™HÒËÔÓĞÒÔÈ›ŞY\È]\İY[YH[ˆ^XÚ]›ŞH[™Ú[‚‚˜^œØÚ[YN‹ËÖİ\Ù\œ\ÜİÛÜ™ZÜİœÜ˜‚”[\Î‚‚‹HHÜ\È™\]Z\™Y‚‹H›İ][™È]ÈÜˆ]Y\Hİš[™ÜÈ\™H›İXØÙ\Y‚‹HÙœ˜YÛY[\È\ÙYÛ›H\ÈH\Ü^H˜[YH[™\È^ÛYYœ›ÛHHØ[›ÛšXØ[T’HÈ›ÙHY[]K‚‹HØXØÛİ[XX^H\X\ˆ][ÜİÛ˜ÙH[™Û›H[ˆH›ŞH\Ù\›˜[YK‚‹HÛØÚÜÎ‹ËØ\È›Ü›X[^™YÈÛØÚÜÍN‹ËØ‚‚ˆÈÈÓĞÒÔÈ”ÈÙ[X[XÜÂ‚•HÚ\™YœšYÙH^XÚ]H\İ[™İZ\Ú\Î‚‚˜^œÛØÚÜÍN‹ËÂ¸¡¤ˆ™\ÛÛ™H”ÈØØ[B¸¡¤ˆÙ[™HTY™\ÜÈÈHÓĞÒÔÈÙ\™\‚‚œÛØÚÜÍZ‹ËÂ¸¡¤ˆÈ›İ™\ÛÛ™HHÜİ˜[YHØØ[B¸¡¤ˆ]HÓĞÒÔÈÙ\™\ˆ™\ÛÛ™HHÜİ˜[YB˜‚”ÓĞÒÔÍÈÓĞÒÔÍHZÙ]Ú\ÙH™\Ù\™HØØ[È™[[İH”ÈÙ[X[XÜÈ™\ÜXİ]™[K‚‚ˆÈÈ[[YHYHØXÚB‚”[[Y\È\™Hİ[Ü™X]Y^š[NÈH\™ÙHİXœØÜš\[ÛˆÙ\È›İØ]\ÙHH\™ÙH[X™\ˆÙˆœšYÙHÈÚ[™ËX›Ş[[Y\ÈÈİ\[]Û˜ÙK‚‚Y\ˆH™Y™\™[˜ÙHÛİ[›ÜÈÈH[[YH[\œÈHYHØXÚHHY˜][‚‚˜^œ›ŞWÜ[[YWÚYWİÜÙXÈHLŒœ›ŞWÜ[[YWØØXÚWÛX^HÌ‚˜‚’YˆHØ[YH›ÙH\ÈXÜ]Z\™YYØZ[ˆÚ][ˆHH[[YHØ[ˆ™H™]\ÙY\™XİKˆÛ˜ÙHH^\™\ÈÜˆHYKXØXÚH[Z]\È^ÙYYYHX\İ™XÙ[H\ÙY[[YH\ÈÛX[™Y\ˆÙ]›ŞWÜ[[YWÚYWİÜÙXÏLÈ™\İÜ™H[[YYX]HÚ]İÛˆ]™\›È™Y™\™[˜Ù\ËˆX[˜YÙ\ˆÚ]İÛˆÛÜÙ\È[™[XZ[š[™È[[Y\Ë‚‚ˆÈÈ˜\ÙMİXœØÜš\[Ûˆ™Yœ™\Ú[™\İRÛ›İÛ‹QÛÛÙ‚˜›ŞWÜÛÛÙš[X[™›ŞWÜÛÛÜİXœØÜš\[Û—İ\›İ\Ü‚‚‹HZ[ˆ[™KXK[[™HT’\Ë‚‹H[\™HØİ[Y[È[˜ÛÙYÚ]İ[™\™˜\ÙM‚‹HT“\ØY™H˜\ÙM‚‹HZ^Y][K\›İØÛÛ›Ù\Ë‚‚‘XXÚÛİ\˜ÙH\È[Z]YÈˆZPˆ[™L›Ù\Ëˆ\œÚ[™È™\İ[È™XÛÜ™İ[[™HÛİ[˜\ÙMİ]\ËİXØÙ\ÜÙ[›ÙHÛİ[ÚÚ\YÛİ[›İØÛÛÛİ[Ë[™\œ›ÜœË‚‚‘š[H[™İXœØÜš\[ÛˆÛİ\˜Ù\È[™\[™[HXZ[Z[‚‚˜^›\İÜİXØÙ\Ü×Ø]›\İÙ\œ›Ü‚™Ù[™\˜][Û‚››Ù\Â™XYÛ›ÜİXÜÂ˜‚‘›Üˆ^[\KYˆHš[H™Yœ™\ÚİXØÙYYÈ]HİXœØÜš\[Ûˆ[\Ü˜\š[H[Y\Èİ]‚‚˜^™š[H8¡¤ˆ\ÙHH]\İÙ[™\˜][Û‚œİXœØÜš\[Ûˆ8¡¤ˆ™]Z[ˆH\İİXØÙ\ÜÙ[Ù[™\˜][Ûˆ[™X\šÈ]İ[B˜‚HİXØÙ\ÜÙ[™Yœ™\Úœ›ÛHÛ™HÛİ\˜ÙHÙ\È›İÛX\ˆH[Üİ™XÙ[İXØÙ\ÜÙ[›Ù\Èœ›ÛH[›İ\ˆÛİ\˜ÙH][\Ü˜\š[H˜Z[Y‚‚ˆÈÈİXœØÜš\[Ûˆ\™Ù]™\İšXİ[ÛœÈ
Ü[Û˜[
B‚•Ú[ˆ›ŞWÜÛÛÜİXœØÜš\[Û—ÜX›X×ÛÛ›O]YXH[š]X[İXœØÜš\[ÛˆT“[™]™\H™Y\™Xİ\™H™]˜[Y]Y‚‚‹HÛ›HÈØ\™H[İÙY‚‹HHÜİ˜[YH]\İ™\ÛÛ™K‚‹Hš]˜]HÈÛÜ˜XÚÈÈ[šË[ØØ[È][XØ\İÈ™\Ù\™YÈ[œÜXÚYšYYY™\ÜÙ\È\™H™Z™XİY‚‹H][ÜİÈ™Y\™XİÈ\™H[İÙY‚‹HH™\ÜÛœÙH™[XZ[œÈİXš™XİÈHˆZPˆÛÛ[[Z]‚‚•\ÈÜ[Ûˆ\È\ØX›YHY˜][ÛÈØØ[™\ÙX\˜Ú[š\›Û›Y[ÈØ[ˆÛÛ[YHÈ\ÙHSˆÜˆÙ[‹ZÜİYİXœØÜš\[ÛˆÙ\šXÙ\Ë‚‚ˆÈÈ›Ø™NˆTÈTˆ[™˜[ÙKTÜÚ]]™H›İXİ[Û‚‚”İ\ÜYÙ][™ÜÎ‚‚˜^œ›ŞWÜÛÛÜ›Ø™WÜ›İšY\ˆHÛİY›\™H\[™›Âœ›ŞWÜÛÛÜ›Ø™WÙX[ÜİXÚÈHYH˜[ÙB˜‚•Ú]X[\İXÚÈ[˜X›YT[™Tˆ\™H›Ø™Y[™\[™[H[™İÜ™N‚‚˜^œİ]\Â\İYØ]›][˜ŞWÛ\Â™^]Ú\™\œ›Ü‚˜‚’YˆÛ™H˜[Z[HÛÜšÜÈ[™Hİ\ˆ˜Z[ËH›ÙHX^Hİ[™HÛÛœÚY\™Y\ØX›HÚ[H™]Z[š[™ÈH[™\[™[™\İ[È›Üˆ›İ˜[Z[Y\Ë‚‚ŠŠ’›ÈÛ™Ù\ˆ]]ÛX]XØ[HYX[œÈHX[H›Ø™KŠŠˆH›Ø™H]\İØ]\ÙH[ÙˆH›ÛİÚ[™Î‚‚˜^’ŠÈİXØÙ\ÜÙ[H\œÙHH˜[Y^]TŠÈT˜[Z[HX]Ú\ÈHİ\œ™[TÒTˆ›Ø™B˜‚“İ\Ú\ÙH]\ÈX\šÙY[šX[X™]™[[™È˜[ÙHÜÚ]]™\ÈİXÚ\È’Œ]X[›Ü›YY™\ÜÛœÙHÈ›ÈT‹‚‚ˆÈÈ›Ø™KP]Ø\™HÛÙÙ[Xİ[Û‚‚“›ÙHØÚY[[™Èš\œİ™\]Z\™\Î‚‚˜^™[˜X›Y››İ™]\™Y˜Ø\XÚ]H]˜Z[X›B™š^Y›ÙH›İÛÛÛ[™Â˜‚’][ˆÜ›İ\È›Ù\ÈH™XÙ[›Ø™Hİ]\Î‚‚˜^•Y\ˆˆ™XÙ[X[B•Y\ˆNˆ[šÛ›İÛˆÈİ[B•Y\ˆˆ™XÙ[[šX[B˜‚Y™š[š]HÈX[È[™›YÚÙ[Xİ[Ûˆ\È\™›Ü›YYœ›ÛHH™\İ]˜Z[X›HY\ˆš\œİˆ™XÙ[[šX[Hİ]\È\ÈH
ŠœÛÙ\š[Üš]^˜][ÛŠŠ‹›İH\›X[™[\™˜[ÈYˆ]\ÈHÛ›H]˜Z[X›H›ÙK]X^Hİ[™HšYY‚‚ˆÈÈš^Y[™›İ][™ÈX[[Ù[Â‚ˆÈÈÈš^Y›ÙB‚”™X[™YÚ\İ˜][ÛˆİXØÙ\ÜÎ‚‚˜^œ™YÚ\İ˜][Û—ÜİXØÙ\ÜÙ\È
ÏHB˜\Ú[™\Ü×ÜØ[\\È
ÏHBšX[HZ[ŠKŒX[
ÈŒJB™˜Z[\™WØÛİ[H˜ÛÛÛİÛˆH›Û™B˜‚ÛÛ™š\›YY˜[œÜÜ˜Z[\™N‚‚˜^˜[œÜÜÙ˜Z[\™\È
ÏHB˜\Ú[™\Ü×ÜØ[\\È
ÏHB™˜Z[\™WØÛİ[
ÏHBšX[HX^
ŒKX[
ˆÊB˜‚ÛÛÛİÛ‚‚˜^ŒÌÈ8¡¤ˆŒÈ8¡¤ˆLŒÈ8¡¤ˆÈ8¡¤ˆÈ8¡¤ˆX^ŒÂ˜‚ˆÈÈÈ›İ][™ÈØ]]Ø^B‚H›İ][™ÈØ]]Ø^HÙ\È›İ\Ü^Hš^Y[›ÙHX[[™Ù\È›İ\HHØ]]Ø^K]ÚYHÛÛÛİÛˆ™XØ]\ÙHÙˆÛ™H˜Y^]ˆ]™XÛÜ™Î‚‚˜^™^]ÜİXØÙ\ÜÙ\Â™^]Ù˜Z[\™\Â™Ø]]Ø^WÜİXØÙ\Ü×Ü˜]B˜‚•\È™]™[ÈHØ]]Ø^H]œ™\]Y[HÚ[™Ù\È^]Èœ›ÛH\X\š[™È\›X[™[H\ÈX[LKŒÚ[\H™XØ]\ÙH]XØİ[][]YİXØÙ\ÜÙ[Ø[\\Ë‚‚ˆÈÈÈ\Ú[™\ÜÈØ[\HY\XØ][Û‚‚HÚ[™ÛHXØÛİ[][\ÛÛšX]\È][ÜİÛ™H\Ú[™\ÜËZX[Ø[\KˆYˆHİ\ÜXİY˜Z[\™H›Ø™HØØİ\œÈY\ˆHİXØÙ\ÜÙ[][\]][\\È›İÛİ[YÚXÙH\ÈÛÈ\Ú[™\ÜÈØ[\\Ë‚‚ŠŠÛÛ™šYİ\˜][Û‹Ø]][XØ][Ûˆ\œ›ÜœÈ\™H›İ\Ú[™\ÜËZX[Ø[\\ËŠŠˆ^H[˜Ü™[Y[ÛÛ™šYİ\˜][Û—Ù˜Z[\™\Ø[™X\šÈH›ÙH[˜]˜Z[X›K]È›İ™YXÙHX[[˜Ü™[Y[\Ú[™\Ü×ÜØ[\\ØÜˆ[\ˆ^Û™[X[˜[œÜÜÛÛÛİÛ‹‚‚ˆÈÈš]™H˜Z[\™HØ]YÛÜšY\Â‚“™]ÛÜšÈ™YY˜XÚÈ\È]šYY[Èš]™HØ]YÛÜšY\Î‚‚ŒKˆ
Š˜ÛÛ\]Xš[]JŠˆ[ˆ[\›˜[ÛÛ\Û™[Ü›İØÛÛÛÛ˜Xİ\È[˜ÛÛ\]X›NÈ›ÙHX[\È›İ[˜[^™Y‚Œ‹ˆ
Š˜ÛÛ™šYİ\˜][ÛŠŠˆ›ŞH]][XØ][Û‹Ü™Y[X[ËÜˆØš[İ\ÈÛÛ™šYİ\˜][Ûˆ›Ø›[\ÎÈH›ÙH\ÈX\šÙY[˜]˜Z[X›H]H]™[\È›İÛİ[Y\ÈH˜[œÜÜX[Ø[\K‚ŒËˆ
Šš\™İ˜[œÜÜ
Šˆ^XÚ]^]]˜[œÜÜ˜Z[\™\ÈİXÚ\È›ŞHÛÛ›™Xİ[Ûˆ˜Z[\™KÓĞÒÔÈÓÓ“‘PÕÓÓ“‘PÕÜˆ™]ÛÜšÈ[œ™XXÚX›NÈš^Y[›ÙHX[\È™YXÙY[™ÛÛÛİÛˆ\Y\Ë‚ˆ
Šœİ\ÜXİYİ˜[œÜÜ
ŠˆËSÑ‹™\Ù][Y[İ][™Ú[Z[\ˆ\œ›ÜœÈ]X^HÛÛYHœ›ÛHZ]\ˆH›ŞHÜˆ\™Ù]]ÈH›ÙH\È[[YYX][H™\›Ø™Y[™\È[˜[^™YÛ›HYˆ]™\›Ø™H[ÛÈ˜Z[Ë‚Kˆ
Š˜\XØ][ÛŠŠˆ\XØ][Û‹[^Y\ˆÛÛ™][ÛœÈİXÚ\ÈKK›Ü›X[Ğ]]İ]\ËÜˆ\Ú[™\ÜÈ\˜[Y]\œÎÈ›ŞK[›ÙHX[\È›İ[˜[^™Y‚‚ˆÈÈİXİ\™YœšYÙHXYÛ›ÜİXÜÂ‚“ØØ[›ŞPœšYÙH›ÈÛ™Ù\ˆİØ[İÜÈ[\›˜[^Ù\[ÛœÈ\ÈÙ[™\šXÈSÑœËˆ]™XÛÜ™ÈİXİ\™Y˜Z[\™HÚ[™ÈİXÚ\Î‚‚˜^\İ™X[WØÛÛ›™XİšÜ›ŞWØ]]šØÛÛ›™XİœÛØÚÜ×Ø]]œÛØÚÜ×ØÛÛ›™Xİš×Ü›ŞWİÂ›ØØ[ÙœÂœ™[[İWÙœÂœ™[[İWÜ™\Ù]˜œšYÙB˜‚”›ŞTÛÛš[Üš]^™\È\ÙHİXİ\™YXYÛ›ÜİXÜÈ›ÜˆÛ\ÜÚYšXØ][ÛÈİš[™ÈX]Ú[™È\ÈÛ›HH˜[˜XÚË‚‚ˆÈÈ”Ñ•ÈÈÔHÜİT›ØÙ\ÜÚ[™Â‚“”Ñ•ÈÜˆÔH˜Z[\™\ÈÈ›İ\ØØ\™Üˆ™K\™YÚ\İ\ˆ[ˆXØÛİ[]Ø\È[™XYH™YÚ\İ\™YİXØÙ\ÜÙ[K‚‚‹H^XÚ]›ŞH˜[œÜÜ\œ›Üˆ8¡¤ˆ™YY˜XÚÈ[ÈHÛÜœ™\ÜÛ™[™È›ŞHØ]YÛÜK‚‹HËÑSÑ‹İ[Y[İ]8¡¤ˆ™X]\Èİ\ÜXİY[™™\›Ø™H[[YYX][H™Y›Ü™HXÚY[™ÈÚ]\ˆÈ[˜[^™K‚‹HÛÛ\]Xš[]KØÛÛ™šYËØ\XØ][Ûˆ8¡¤ˆ[™HXØÛÜ™[™ÈÈHÛÜœ™\ÜÛ™[™ÈØ]YÛÜK‚‹H›İ[ˆ^XÚ]ÔHÜWÜ›ŞX[™H™YÚ\İ˜][ÛˆX\ÙH\™Hš\œİÛÛ™\YÈXÛÛ\]X›H[™Ú[Ë™]™[[™È˜]ÈÓĞÒÔÈT“Èœ›ÛH™Z[™È\ÜÙY\™XİHÈ™]ÛÜšÈÛÛ\Û™[È]È›İİ\Ü]ØÚ[YK‚‚ˆÈÈ™YÚ\İ˜][Û‹T]™Y›YÚ
Ü[Û˜[
B‚H›Û‹Y\İXİ]™H›ÙK\]™Y›YÚ\È›İšYY›Ü‚‚˜^˜XØÛİ[Ë˜ZB™Ü›ÚË˜ÛÛB˜‚’]ÚXÚÜÈÛ›H™XXÚXš[]Kİ]\Ë][˜ŞK[™Øš[İ\ÈÛİY›\™H›ØÚÈ[™XØ][ÛœËˆ]Ù\È›İÜ™X]HXZ[›Ş\ËÜ™X]HXØÛİ[Ë[ÙYHXØÛİ[Ù][™ÜËÜˆÛİ[\ÈH[[YHX[Ø[\K‚‚•ÙXˆTN‚‚˜^”ÔÕØ\KÜ›ŞK\ÛÛÜ™Y›YÚÛ›ÙWÚYO›ÙKZY‚˜‚“X[X[™Y›YÚ\È\ØX›YÚ[HH\ÚÈ\È[›š[™Ëˆ]Ø[ˆ™H\›™YÙ™ˆ[\™[HÚ]‚‚˜^œ›ŞWÜÛÛÜ™Y›YÚÙ[˜X›YH˜[ÙB˜‚ˆÈÈX[Tİ]H\œÚ\İ[˜ÙH
Ü[Û˜[
B‚‘Y˜][‚‚˜^œ›ŞWÜÛÛÜ\œÚ\İÚX[H˜[ÙB˜‚•Ú[ˆ[˜X›Y›ÙH\Ú[™\ÜËZX[İ]H\È]ÛZXØ[HÜš][ˆÎ‚‚˜^œ›ŞWÜÛÛÜİ]WÙš[HH‹Ü›ŞWÜÛÛÜİ]KšœÛÛ‚˜‚Y\ˆHX[˜YÙ\ˆ\È™XZ[›Ù\ÈÚ]HØ[YHİX›H›ÙHQ™\İÜ™HX[\Ú[™\ÜÈÛİ[\œË˜Z[\™KĞÛÛÛİÛˆİ]K[™™XÙ[\Ú[™\ÜÈ\œ›ÜœËˆ\Èš[H\È[˜ÛYY[ˆ™Ú]YÛ›Ü™XHY˜][‚‚ˆÈÈÙX•RB‚•H›ŞK\ÛÛYÙH\Ü^\ÈÜˆİÜ™\Î‚‚‹H[›ÙHT’K‚‹H›İØÛÛÈ˜XÚÙ[™Èš^Y[Ü‹\›İ][™Ë‚‹HTÈTˆ›Ø™H™\İ[Ë‚‹Hš^Y[[YHX[Üˆ›İ][™ÈØ]]Ø^HİXØÙ\ÜÈ˜]K‚‹H\Ú[™\ÜÈÈ˜[œÜÜÈÛÛ™šYİ\˜][ÛˆÛİ[\œË‚‹H[™›YÚÈÛÛÛİÛˆÈ™XÙ[\œ›Ü‹‚‹HİXœØÜš\[ÛˆÑÈÈİ[HXYÛ›ÜİXÜË‚‹HX[\İXÚË[[YHØXÚKX[\œÚ\İ[˜ÙKX›XË[Û›HİXœØÜš\[Û‹™Y›YÚ[™™[]YÙ][™ÜË‚‚•ÙXˆTN‚‚˜^‘ÑUØ\KÜ›ŞK\ÛÛÜİ]\Â”ÔÕØ\KÜ›ŞK\ÛÛÜ™[ØY”ÔÕØ\KÜ›ŞK\ÛÛİ\İ”ÔÕØ\KÜ›ŞK\ÛÛÜ™Y›YÚÛ›ÙWÚYO›ÙKZY‚˜‚•[™\ˆH›Ú™Xİ	ÜÈİ\œ™[ØØ[]\ÙH[Ù[HÙX•RKİ]\ÈTK[™™[]YÙÜÈÛÛ[YHÈ\Ü^H[›ŞHY™\ÜÙ\Ë[˜ÛY[™È]][XØ][Ûˆ[™›Ü›X][Û‹‚‚ˆÈÈÛÛ\]Xš[]H›İ[™\B‚•HŒÈ™Z]š[Üˆ\ØÜšX™Y\™H\ÈÛÛ˜Ù[˜]Y[ˆX[˜YÙYÚ[™ÛXÈÛÛ[ÙKˆHY˜][›ŞWÛ[ÙOX]]ØÛÛ[Y\ÈÈ™\Ù\™HHYØXŞHÕRKĞÓKÕÙX•RK[XZ[™\İ[\œÚ\İ[˜ÙK[™[™ËÚÙ[ˆŞ[˜Ë[™›ŞH™Z]š[Ü‹‚‚“Ü™[˜\HÔÓĞÒÔÈÙ\È›İİ\Ú[™ËX›ŞY\™[H™XØ]\ÙHY˜[˜ÙY\›İØÛÛİ\Ü^\İËˆ“TÔËÕ“Y\ÜËÕ›Ú˜[‹Ò\İ\šXL‹ÕRPËÔÚYİÜÛØÚÜÈ™\]Z\™HÚ[™ËX›ŞÛ›HÚ[ˆXİX[HXÜ]Z\™Y›Ø™YÜˆ™Y›YÚY‚