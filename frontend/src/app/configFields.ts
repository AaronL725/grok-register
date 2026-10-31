export type Language = 'zh' | 'en'
export type FieldKind = 'text' | 'password' | 'number' | 'checkbox' | 'select'

export type ConfigField = {
  key: string
  kind: FieldKind
  zh: string
  en: string
  helpZh?: string
  helpEn?: string
  options?: string[]
  min?: number
  max?: number
  wide?: boolean
}

export type ConfigGroup = {
  id: string
  zh: string
  en: string
  descriptionZh: string
  descriptionEn: string
  fields: ConfigField[]
}

const text = (key: string, zh: string, en: string, wide = false, helpZh = '', helpEn = ''): ConfigField =>
  ({ key, kind: 'text', zh, en, wide, helpZh, helpEn })
const secret = (key: string, zh: string, en: string, wide = false): ConfigField =>
  ({ key, kind: 'password', zh, en, wide })
const number = (key: string, zh: string, en: string, min: number, max: number): ConfigField =>
  ({ key, kind: 'number', zh, en, min, max })
const toggle = (key: string, zh: string, en: string, helpZh = '', helpEn = ''): ConfigField =>
  ({ key, kind: 'checkbox', zh, en, helpZh, helpEn })
const select = (key: string, zh: string, en: string, options: string[]): ConfigField =>
  ({ key, kind: 'select', zh, en, options })

export const configGroups: ConfigGroup[] = [
  {
    id: 'general',
    zh: '注册基础设置',
    en: 'Registration',
    descriptionZh: '注册数量、执行模式与本地数据存储位置。',
    descriptionEn: 'Batch size, execution mode and local data storage.',
    fields: [
      number('register_count', '注册数量', 'Registration count', 1, 2500),
      text('database_path', 'SQLite 数据库路径', 'SQLite database path', true),
      toggle('multi_thread_enabled', '启用多线程注册', 'Enable parallel registration'),
      number('multi_thread_workers', '并行 Worker 数量', 'Parallel workers', 1, 8),
      toggle('enable_nsfw', '启用 NSFW 后处理', 'Enable NSFW post-processing'),
      toggle('sso_risk_gate_enabled', '启用 SSO 风险检查', 'Enable SSO risk gate',
        '保存账号前检查 SSO 风险状态。', 'Check SSO risk state before saving an account.'),
      text('sso_risk_rejected_file', '风险账号隔离文件', 'Rejected SSO file', true),
      text('user_agent', '自定义 User-Agent', 'Custom User-Agent', true),
    ],
  },
  {
    id: 'mail',
    zh: '邮箱服务',
    en: 'Email providers',
    descriptionZh: '选择验证码邮箱来源并配置对应服务商。Outlook 凭据池在“邮箱池”页面单独管理。',
    descriptionEn: 'Choose the verification mailbox provider and configure its service. Manage Outlook credentials separately in Mailbox pool.',
    fields: [
      select('email_provider', '邮箱服务商', 'Email provider', ['duckmail', 'yyds', 'cloudflare', 'cloudmail', 'outlook']),
      text('defaultDomains', '默认邮箱域名', 'Default email domains', true),
      secret('duckmail_api_key', 'DuckMail API Key', 'DuckMail API key'),
      secret('yyds_api_key', 'YYDS API Key', 'YYDS API key'),
      secret('yyds_jwt', 'YYDS JWT', 'YYDS JWT'),
      text('cloudflare_api_base', 'Cloudflare API Base', 'Cloudflare API base', true),
      secret('cloudflare_api_key', 'Cloudflare API Key / Admin Password', 'Cloudflare API key / admin password'),
      select('cloudflare_auth_mode', 'Cloudflare 鉴权模式', 'Cloudflare auth mode', ['none', 'bearer', 'x-api-key', 'x-admin-auth', 'query-key']),
      text('cloudflare_path_domains', 'Cloudflare 域名接口路径', 'Cloudflare domains path'),
      text('cloudflare_path_accounts', 'Cloudflare 创建邮箱接口路径', 'Cloudflare create-address path'),
      text('cloudflare_path_token', 'Cloudflare Token 接口路径', 'Cloudflare token path'),
      text('cloudflare_path_messages', 'Cloudflare 邮件接口路径', 'Cloudflare messages path'),
      text('cloudmail_api_base', 'Cloud Mail API Base', 'Cloud Mail API base', true),
      secret('cloudmail_public_token', 'Cloud Mail Public Token', 'Cloud Mail public token'),
      text('cloudmail_domains', 'Cloud Mail 域名列表', 'Cloud Mail domains', true),
      text('cloudmail_path_messages', 'Cloud Mail 邮件接口路径', 'Cloud Mail messages path', true),
      text('outlook_accounts_file', 'Outlook 邮箱池文件路径', 'Outlook mailbox pool file', true,
        '这里只设置文件位置；邮箱密码与 Refresh Token 在“邮箱池”页面管理。', 'Only the file path is configured here. Manage mailbox passwords and refresh tokens in Mailbox pool.'),
    ],
  },
  {
    id: 'proxy',
    zh: '代理池',
    en: 'Proxy pool',
    descriptionZh: '代理模式、订阅源、健康探测与高级协议运行时。',
    descriptionEn: 'Proxy mode, subscription sources, health probes and advanced protocol runtimes.',
    fields: [
      select('proxy_mode', '代理模式', 'Proxy mode', ['auto', 'direct', 'single', 'pool']),
      text('proxy', '固定代理地址', 'Fixed proxy URL', true),
      select('proxy_fallback', '代理池回退模式', 'Proxy fallback', ['none', 'direct', 'single']),
      select('proxy_pool_endpoint_mode', '代理节点类型', 'Proxy endpoint mode', ['auto', 'fixed', 'rotating']),
      text('proxy_pool_file', '本地代理池文件', 'Local proxy pool file', true),
      text('proxy_pool_subscription_url', '代理订阅 URL', 'Proxy subscription URL', true),
      text('proxy_pool_subscription_proxy', '订阅下载代理', 'Subscription fetch proxy', true),
      number('proxy_pool_refresh_interval_sec', '订阅刷新间隔（秒）', 'Subscription refresh interval (s)', 0, 86400),
      number('proxy_pool_probe_interval_sec', '健康探测间隔（秒）', 'Health probe interval (s)', 0, 86400),
      number('proxy_pool_probe_timeout_sec', '单节点探测超时（秒）', 'Probe timeout (s)', 3, 120),
      select('proxy_pool_probe_provider', '探测服务', 'Probe provider', ['cloudflare', 'ipinfo']),
      toggle('proxy_pool_probe_dual_stack', '启用 IPv4 / IPv6 双栈探测', 'Enable IPv4 / IPv6 dual-stack probes'),
      number('proxy_pool_max_concurrent_per_node', '单节点最大并发数', 'Max concurrent sessions per node', 1, 64),
      number('proxy_pool_acquire_timeout_sec', '代理租约等待超时（秒）', 'Proxy lease timeout (s)', 1, 600),
      select('proxy_protocol_backend', '高级协议后端', 'Advanced protocol backend', ['auto', 'sing-box', 'native-only']),
      text('proxy_singbox_path', 'sing-box 可执行文件路径', 'sing-box executable path', true),
      number('proxy_protocol_start_timeout_sec', '协议后端启动超时（秒）', 'Protocol backend startup timeout (s)', 3, 60),
      number('proxy_runtime_idle_ttl_sec', '运行时空闲保留时间（秒）', 'Runtime idle TTL (s)', 0, 3600),
      number('proxy_runtime_cache_max', '运行时缓存上限', 'Runtime cache limit', 1, 256),
      toggle('proxy_pool_persist_health', '持久化代理健康状态', 'Persist proxy health state'),
      text('proxy_pool_state_file', '代理健康状态文件', 'Proxy health state file', true),
      toggle('proxy_pool_subscription_public_only', '订阅仅允许公网地址', 'Require public subscription endpoints'),
      toggle('proxy_pool_preflight_enabled', '启用注册路径预检', 'Enable registration path preflight'),
    ],
  },
  {
    id: 'grok2api',
    zh: 'Grok2API 集成',
    en: 'Grok2API integration',
    descriptionZh: '配置注册成功后的本地与远端 Token 池写入。',
    descriptionEn: 'Configure local and remote token-pool updates after registration.',
    fields: [
      toggle('grok2api_auto_add_local', '自动写入本地 Token 池', 'Auto-add to local token pool'),
      text('grok2api_local_token_file', '本地 Token 文件', 'Local token file', true),
      select('grok2api_pool_name', '目标 Token 池', 'Target token pool', ['ssoBasic', 'ssoSuper']),
      toggle('grok2api_auto_add_remote', '自动写入远端 Token 池', 'Auto-add to remote token pool'),
      text('grok2api_remote_base', '远端服务 Base URL', 'Remote service base URL', true),
      secret('grok2api_remote_app_key', '旧版 App Key', 'Legacy app key'),
      text('grok2api_remote_admin_username', '管理员用户名', 'Admin username'),
      secret('grok2api_remote_admin_password', '管理员密码', 'Admin password'),
      toggle('grok2api_allow_legacy_full_save', '允许旧版全量保存回退', 'Allow legacy full-save fallback'),
    ],
  },
  {
    id: 'cpa',
    zh: 'CPA / OIDC 集成',
    en: 'CPA / OIDC integration',
    descriptionZh: '配置 CPA 授权导出、热加载目录与 OIDC 超时。',
    descriptionEn: 'Configure CPA authorization export, hot-load paths and OIDC timeouts.',
    fields: [
      toggle('cpa_export_enabled', '启用 CPA / OIDC 导出', 'Enable CPA / OIDC export'),
      text('cpa_auth_dir', 'CPA 授权文件输出目录', 'CPA auth output directory'),
      toggle('cpa_copy_to_hotload', '复制到热加载目录', 'Copy to hot-load directory'),
      text('cpa_hotload_dir', '热加载目录', 'Hot-load directory'),
      text('cpa_base_url', 'CPA Base URL', 'CPA base URL', true),
      text('cpa_proxy', 'CPA 专用代理', 'CPA proxy', true),
      toggle('cpa_headless', 'CPA 使用无头浏览器', 'Use headless browser for CPA'),
      toggle('cpa_force_standalone', 'CPA 使用独立浏览器', 'Use standalone CPA browser'),
      number('cpa_mint_timeout_sec', '授权整体超时（秒）', 'Authorization timeout (s)', 30, 1800),
      toggle('cpa_mint_cookie_inject', '注入已有 Cookie', 'Inject existing cookies'),
      number('cpa_oidc_request_timeout_sec', 'OIDC 请求超时（秒）', 'OIDC request timeout (s)', 3, 120),
      number('cpa_oidc_poll_timeout_sec', 'OIDC 轮询超时（秒）', 'OIDC polling timeout (s)', 3, 120),
      text('api_reverse_tools', '外部 CPA 工具目录', 'External CPA tools directory', true),
    ],
  },
]

export const configKeys = configGroups.flatMap(group => group.fields.map(field => field.key))
