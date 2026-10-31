import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  api, post, put,
  type Config, type ConfigValue, type HealthReport, type LogEntry,
  type MailboxPool, type MailboxTest, type ProxyNode, type ProxyPreflightResult,
  type ProxySnapshot, type TaskStatus,
} from '../lib/api'
import { configGroups, type ConfigField, type Language } from './configFields'

type Page = 'overview' | 'settings' | 'proxy' | 'mailbox' | 'console'
type Notice = { text: string; error?: boolean }
type ConfigResponse = { ok: boolean; config: Config }
type StatusResponse = TaskStatus & { ok: boolean }
type LogsResponse = { ok: boolean; latest: number; entries: LogEntry[] }
type MailboxResponse = MailboxPool & { ok: boolean }
type ProxyResponse = ProxySnapshot & { ok: boolean }
type ProxyActionResponse = ProxySnapshot & { ok: boolean; result?: ProxyPreflightResult }

const initialStatus: TaskStatus = {
  running: false, target: 0, success: 0, fail: 0, pending: 0,
  uncertain: 0, warnings: 0, cancelled: false,
}

const copy = {
  zh: {
    product: 'GROK REGISTER', subtitle: '本地自动化工作台',
    overview: '总览', settings: '配置中心', proxy: '代理池', mailbox: '邮箱池', console: '运行日志',
    overviewTitle: '工作台总览', settingsTitle: '配置中心', proxyTitle: '代理网络', mailboxTitle: 'Outlook 邮箱池', consoleTitle: '运行日志',
    overviewDesc: '查看任务状态、执行进度与系统检查结果。',
    settingsDesc: '管理注册参数、邮箱服务、代理网络和后处理集成。',
    proxyDesc: '查看节点探测、运行健康与出口状态，并执行注册路径预检。',
    mailboxDesc: '维护 Outlook 邮箱凭据池，并在保存前测试 IMAP / Microsoft Graph。',
    consoleDesc: '查看实时任务日志，支持搜索与复制。',
    local: '仅本机控制台', idle: '待机', running: '运行中', maintenance: '维护操作中', stopped: '已停止',
    target: '目标数量', success: '成功', failed: '失败', pending: '待恢复', uncertain: '结果不确定', warnings: '警告',
    progress: '执行进度', quickActions: '快捷入口', latestActivity: '最新活动', activityDesc: '最近的注册活动记录', systemHealth: '依赖健康检查',
    noActivity: '暂无活动记录', open: '打开', save: '保存修改', discard: '放弃修改', dirty: '项未保存',
    saved: '配置已保存', noChanges: '没有需要保存的修改', start: '开始任务', stop: '停止任务',
    staticCheck: '静态检查', onlineCheck: '联网检查', loading: '加载中…', loadFailed: '加载失败',
    reload: '重新加载', test: '测试节点', preflight: '路径预检', noNodes: '没有可显示的代理节点',
    source: '订阅源', node: '节点', protocol: '协议', backend: '后端', endpoint: '入口类型',
    probe: '探测状态', runtime: '运行健康', latency: '延迟', exitIp: '出口 IP',
    sessions: '并发', failures: '失败', cooldown: '冷却', lastError: '最近错误',
    healthy: '正常', unhealthy: '异常', unavailable: '不可用', unknown: '未探测',
    rotating: '旋转入口', fixed: '固定节点', noSamples: '暂无业务样本',
    mailboxPath: '邮箱池文件', mailboxFormat: '每行格式：email----password----clientId----refreshToken----auto/imap/graph',
    mailboxData: '邮箱账号列表', count: '账号数量', invalid: '无效记录', duplicates: '重复邮箱',
    loadPool: '读取文件', savePool: '保存邮箱池', testPool: '测试邮箱池', healthyMail: '可用', unhealthyMail: '不可用',
    testResults: '连接测试结果', imap: 'IMAP', graph: 'Graph', usable: '可用', notUsable: '不可用',
    logs: '日志', searchLogs: '搜索日志内容…', copyLogs: '复制日志', clearLogs: '清空视图',
    noLogs: '暂无日志', configGroups: '配置分组', error: '错误', warning: '警告',
    healthReady: '检查通过', healthIssues: '存在需要处理的问题', healthNone: '尚未运行健康检查',
    language: '语言', general: '基础设置', mail: '邮箱服务', grok2api: 'Grok2API 集成', cpa: 'CPA / OIDC 集成',
    unsaved: '有未保存的修改', proxyActionFailed: '代理池操作失败', mailboxSaved: '邮箱池已保存',
    mailboxTested: '邮箱池测试完成', preflightDone: '注册路径预检结果', preflightPassed: '注册路径预检通过', preflightFailed: '注册路径预检未通过',
    reachable: '网络可达', notReachable: '网络不可达', cloudflareBlock: '检测到 Cloudflare 拦截', noJob: '当前没有运行中的任务',
    stopSent: '已发送停止请求', copied: '日志已复制', copyFailed: '复制失败',
    saving: '正在保存…', checking: '正在检查…', starting: '正在启动…',
    configHint: '敏感配置以密码形式显示。保存时仅提交发生变化的字段。',
    invalidNumbers: '数值配置必须是范围内的整数，修正后才能保存。',
    healthSummary: '检查摘要', localOnly: 'LOCAL CONTROL PLANE',
  },
  en: {
    product: 'GROK REGISTER', subtitle: 'Local automation workspace',
    overview: 'Overview', settings: 'Configuration', proxy: 'Proxy pool', mailbox: 'Mailbox pool', console: 'Activity log',
    overviewTitle: 'Workspace overview', settingsTitle: 'Configuration', proxyTitle: 'Proxy network', mailboxTitle: 'Outlook mailbox pool', consoleTitle: 'Activity log',
    overviewDesc: 'Monitor task status, execution progress and system checks.',
    settingsDesc: 'Manage registration, email, proxy and post-processing settings.',
    proxyDesc: 'Inspect node probes, runtime health and exits; run registration-path preflight.',
    mailboxDesc: 'Manage Outlook credentials and test IMAP / Microsoft Graph before saving.',
    consoleDesc: 'Inspect live task logs, search messages and copy output.',
    local: 'Local console only', idle: 'Idle', running: 'Running', maintenance: 'Maintenance', stopped: 'Stopped',
    target: 'Target', success: 'Succeeded', failed: 'Failed', pending: 'Pending', uncertain: 'Uncertain', warnings: 'Warnings',
    progress: 'Execution progress', quickActions: 'Quick access', latestActivity: 'Latest activity', activityDesc: 'Recent registration activity', systemHealth: 'Dependency health',
    noActivity: 'No recent activity', open: 'Open', save: 'Save changes', discard: 'Discard changes', dirty: 'unsaved',
    saved: 'Configuration saved', noChanges: 'There are no changes to save', start: 'Start task', stop: 'Stop task',
    staticCheck: 'Static check', onlineCheck: 'Connectivity check', loading: 'Loading…', loadFailed: 'Load failed',
    reload: 'Reload', test: 'Test nodes', preflight: 'Path preflight', noNodes: 'No proxy nodes to display',
    source: 'Sources', node: 'Node', protocol: 'Protocol', backend: 'Backend', endpoint: 'Endpoint',
    probe: 'Probe', runtime: 'Runtime health', latency: 'Latency', exitIp: 'Exit IP',
    sessions: 'Sessions', failures: 'Failures', cooldown: 'Cooldown', lastError: 'Last error',
    healthy: 'Healthy', unhealthy: 'Unhealthy', unavailable: 'Unavailable', unknown: 'Not probed',
    rotating: 'Rotating gateway', fixed: 'Fixed node', noSamples: 'No business samples',
    mailboxPath: 'Mailbox file', mailboxFormat: 'One row: email----password----clientId----refreshToken----auto/imap/graph',
    mailboxData: 'Mailbox accounts', count: 'Accounts', invalid: 'Invalid rows', duplicates: 'Duplicate emails',
    loadPool: 'Load file', savePool: 'Save pool', testPool: 'Test pool', healthyMail: 'Healthy', unhealthyMail: 'Unhealthy',
    testResults: 'Connection results', imap: 'IMAP', graph: 'Graph', usable: 'Usable', notUsable: 'Unavailable',
    logs: 'Logs', searchLogs: 'Search log messages…', copyLogs: 'Copy logs', clearLogs: 'Clear view',
    noLogs: 'No logs yet', configGroups: 'Configuration groups', error: 'Error', warning: 'Warning',
    healthReady: 'Checks passed', healthIssues: 'Some checks need attention', healthNone: 'No health check has been run',
    language: 'Language', general: 'General', mail: 'Email services', grok2api: 'Grok2API integration', cpa: 'CPA / OIDC integration',
    unsaved: 'You have unsaved changes', proxyActionFailed: 'Proxy pool operation failed', mailboxSaved: 'Mailbox pool saved',
    mailboxTested: 'Mailbox pool test completed', preflightDone: 'Registration path preflight result', preflightPassed: 'Registration path preflight passed', preflightFailed: 'Registration path preflight failed',
    reachable: 'Reachable', notReachable: 'Unreachable', cloudflareBlock: 'Cloudflare block detected', noJob: 'No registration job is running',
    stopSent: 'Stop request sent', copied: 'Logs copied', copyFailed: 'Copy failed',
    saving: 'Saving…', checking: 'Checking…', starting: 'Starting…',
    configHint: 'Sensitive settings are masked. Only changed fields are submitted when saving.',
    invalidNumbers: 'Numeric settings must be whole numbers within their allowed ranges.',
    healthSummary: 'Check summary', localOnly: 'LOCAL CONTROL PLANE',
  },
} as const

const pageMeta: Record<Page, { label: keyof typeof copy.zh; title: keyof typeof copy.zh; description: keyof typeof copy.zh; mark: string }> = {
  overview: { label: 'overview', title: 'overviewTitle', description: 'overviewDesc', mark: '01' },
  settings: { label: 'settings', title: 'settingsTitle', description: 'settingsDesc', mark: '02' },
  proxy: { label: 'proxy', title: 'proxyTitle', description: 'proxyDesc', mark: '03' },
  mailbox: { label: 'mailbox', title: 'mailboxTitle', description: 'mailboxDesc', mark: '04' },
  console: { label: 'console', title: 'consoleTitle', description: 'consoleDesc', mark: '05' },
}

function t(language: Language, key: keyof typeof copy.zh): string {
  return copy[language][key]
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function invalidNumericFields(config: Config): ConfigField[] {
  return configGroups.flatMap(group => group.fields).filter(field => {
    if (field.kind !== 'number') return false
    const value = config[field.key]
    return typeof value !== 'number' || !Number.isInteger(value)
      || (field.min != null && value < field.min)
      || (field.max != null && value > field.max)
  })
}

function StatusBadge({ status, language }: { status: TaskStatus; language: Language }) {
  const label = status.running ? t(language, 'running')
    : status.maintenance ? t(language, 'maintenance')
      : status.cancelled ? t(language, 'stopped') : t(language, 'idle')
  return <span className={'status-badge ' + (status.running ? 'is-running' : status.maintenance ? 'is-maintenance' : '')}>
    <span className="status-dot" />{label}
  </span>
}

function Metric({ label, value, tone = '' }: { label: string; value: number; tone?: string }) {
  return <article className="metric-card">
    <span className="metric-label">{label}</span>
    <strong className={'metric-value ' + tone}>{value.toLocaleString()}</strong>
  </article>
}

function ConfigControl({ field, value, language, disabled, onChange }: {
  field: ConfigField; value: ConfigValue | undefined; language: Language
  disabled: boolean; onChange: (value: ConfigValue) => void
}) {
  const label = language === 'zh' ? field.zh : field.en
  const help = language === 'zh' ? field.helpZh : field.helpEn
  if (field.kind === 'checkbox') {
    return <label className="toggle-field">
      <span className="toggle-copy"><strong>{label}</strong>{help && <small>{help}</small>}</span>
      <input type="checkbox" checked={Boolean(value)} disabled={disabled} onChange={event => onChange(event.target.checked)} />
      <span className="toggle-track" aria-hidden="true" />
    </label>
  }
  return <label className={'config-field' + (field.wide ? ' is-wide' : '')}>
    <span className="field-label">{label}</span>
    {field.kind === 'select' ? <select value={String(value ?? '')} disabled={disabled} onChange={event => onChange(event.target.value)}>
      {(field.options || []).map(option => <option key={option} value={option}>{option}</option>)}
    </select> : <input
      type={field.kind === 'password' ? 'password' : field.kind === 'number' ? 'number' : 'text'}
      value={value == null ? '' : String(value)} min={field.min} max={field.max} disabled={disabled}
      autoComplete={field.kind === 'password' ? 'new-password' : 'off'} spellCheck={false}
      onChange={event => {
        const raw = event.target.value
        onChange(field.kind === 'number' ? (raw === '' ? '' : Number(raw)) : raw)
      }}
    />}
    {help && <small>{help}</small>}
  </label>
}

function SettingsPage({ config, dirty, language, disabled, saving, loading, loaded, onChange, onSave, onDiscard, onReload }: {
  config: Config; dirty: Set<string>; language: Language; disabled: boolean; saving: boolean
  loading: boolean; loaded: boolean
  onChange: (key: string, value: ConfigValue) => void
  onSave: () => void; onDiscard: () => void; onReload: () => void
}) {
  const invalidNumbers = invalidNumericFields(config)
  return <div className="page-stack">
    <div className="section-toolbar">
      <div><span className="eyebrow">{t(language, 'configGroups')}</span><p className="muted">{t(language, 'configHint')}</p>
        {invalidNumbers.length > 0 && <p className="validation-error">{t(language, 'invalidNumbers')} · {invalidNumbers.map(field => language === 'zh' ? field.zh : field.en).join(', ')}</p>}
      </div>
      <div className="toolbar-actions">
        {!loaded && <button className="button button-quiet" type="button" onClick={onReload} disabled={loading}>{loading ? t(language, 'loading') : t(language, 'reload')}</button>}
        <span className="dirty-count">{dirty.size ? dirty.size + ' ' + t(language, 'dirty') : ''}</span>
        <button className="button button-quiet" type="button" onClick={onDiscard} disabled={!dirty.size || disabled}>{t(language, 'discard')}</button>
        <button className="button button-primary" type="button" onClick={onSave} disabled={!dirty.size || disabled || saving || invalidNumbers.length > 0}>{saving ? t(language, 'saving') : t(language, 'save')}</button>
      </div>
    </div>
    {configGroups.map(group => <section className="surface config-section" key={group.id}>
      <div className="surface-heading">
        <div><h2>{language === 'zh' ? group.zh : group.en}</h2><p>{language === 'zh' ? group.descriptionZh : group.descriptionEn}</p></div>
        <span className="field-count">{String(group.fields.length).padStart(2, '0')}</span>
      </div>
      <div className="config-grid">
        {group.fields.map(field => <ConfigControl key={field.key} field={field} value={config[field.key]} language={language} disabled={disabled} onChange={value => onChange(field.key, value)} />)}
      </div>
    </section>)}
  </div>
}

function OverviewPage({ status, logs, health, language, onNavigate }: {
  status: TaskStatus; logs: LogEntry[]; health: HealthReport | null
  language: Language; onNavigate: (page: Page) => void
}) {
  const completed = status.success + status.fail + status.pending + status.uncertain
  const percent = status.target > 0 ? Math.min(100, completed / status.target * 100) : 0
  const actions: Array<{ page: Page; title: string; description: string; mark: string }> = [
    { page: 'settings', title: t(language, 'settings'), description: language === 'zh' ? '调整运行参数与集成配置' : 'Adjust runtime and integration settings', mark: 'CFG' },
    { page: 'proxy', title: t(language, 'proxy'), description: language === 'zh' ? '查看代理节点与出口状态' : 'Inspect proxy nodes and exits', mark: 'NET' },
    { page: 'mailbox', title: t(language, 'mailbox'), description: language === 'zh' ? '管理 Outlook 凭据与连通性' : 'Manage Outlook credentials and connectivity', mark: 'MAIL' },
    { page: 'console', title: t(language, 'console'), description: language === 'zh' ? '查看实时执行日志' : 'Inspect live execution logs', mark: 'LOG' },
  ]
  return <div className="page-stack">
    <section className="hero-panel">
      <div className="hero-copy">
        <span className="eyebrow">CONTROL / 01</span>
        <h2>{language === 'zh' ? '运行状态，一目了然。' : 'Your operations, at a glance.'}</h2>
        <p>{language === 'zh' ? '从一个工作台管理注册任务、依赖状态与本地资源。' : 'Manage registration jobs, dependencies and local resources from one workspace.'}</p>
      </div>
      <div className="hero-state"><StatusBadge status={status} language={language} /><span className="hero-state-label">{status.maintenance || t(language, 'local')}</span></div>
      <div className="hero-grid-mark" aria-hidden="true" />
    </section>
    <section className="metric-grid">
      <Metric label={t(language, 'target')} value={status.target} />
      <Metric label={t(language, 'success')} value={status.success} tone="tone-good" />
      <Metric label={t(language, 'failed')} value={status.fail} tone="tone-bad" />
      <Metric label={t(language, 'pending')} value={status.pending} tone="tone-warn" />
      <Metric label={t(language, 'uncertain')} value={status.uncertain} />
      <Metric label={t(language, 'warnings')} value={status.warnings} tone="tone-warn" />
    </section>
    <div className="content-grid">
      <section className="surface progress-surface">
        <div className="surface-heading"><div><h2>{t(language, 'progress')}</h2><p>{t(language, 'quickActions')}</p></div><strong className="progress-percent">{Math.round(percent)}%</strong></div>
        <div className="progress-track"><span style={{ width: percent + '%' }} /></div>
        <div className="progress-foot"><span>{completed} / {status.target || 0}</span><span>{status.running ? t(language, 'running') : status.maintenance ? t(language, 'maintenance') : t(language, 'idle')}</span></div>
        <div className="shortcut-grid">
          {actions.map(action => <button className="shortcut-card" key={action.page} type="button" onClick={() => onNavigate(action.page)}>
            <span className="shortcut-mark">{action.mark}</span><strong>{action.title}</strong><small>{action.description}</small><span className="shortcut-arrow">↗</span>
          </button>)}
        </div>
      </section>
      <section className="surface health-surface">
        <div className="surface-heading"><div><h2>{t(language, 'systemHealth')}</h2><p>{t(language, 'healthSummary')}</p></div><span className={'health-indicator ' + (health ? (health.ready ? 'good' : 'warn') : '')} /></div>
        {!health ? <p className="empty-state">{t(language, 'healthNone')}</p> : <>
          <div className="health-result"><strong>{health.ready ? t(language, 'healthReady') : t(language, 'healthIssues')}</strong><span>{health.error_count} {t(language, 'error')} · {health.warning_count} {t(language, 'warning')}</span></div>
          <div className="health-list">{health.checks.map(check => <div className="health-row" key={check.key}>
            <span className={'health-tag ' + check.status.toLowerCase()}>{check.status}</span><span>{check.name}</span><small>{check.message}</small>
          </div>)}</div>
        </>}
      </section>
    </div>
    <section className="surface activity-surface">
      <div className="surface-heading"><div><h2>{t(language, 'latestActivity')}</h2><p>{t(language, 'activityDesc')}</p></div><button className="text-button" type="button" onClick={() => onNavigate('console')}>{t(language, 'open')} ↗</button></div>
      {logs.length ? <div className="activity-list">{logs.slice(-8).reverse().map(entry => <div className="activity-row" key={entry.seq}>
        <span className="activity-seq">{String(entry.seq).padStart(5, '0')}</span><span>{entry.line}</span>
      </div>)}</div> : <p className="empty-state">{t(language, 'noActivity')}</p>}
    </section>
  </div>
}

function ProxyPage({ snapshot, preflight, busy, language, onAction }: {
  snapshot: ProxySnapshot | null; preflight: ProxyPreflightResult | null
  busy: boolean; language: Language
  onAction: (action: 'reload' | 'test' | 'preflight', nodeId?: string) => void
}) {
  const nodes = snapshot?.nodes || []
  const sources = snapshot?.sources || {}
  const probeLabel = (status?: string) => status === 'healthy' ? t(language, 'healthy')
    : status === 'unhealthy' ? t(language, 'unhealthy') : status === 'unavailable' ? t(language, 'unavailable') : t(language, 'unknown')
  const probeDetails = (node: ProxyNode, family: 'ipv4_probe' | 'ipv6_probe') => {
    const probe = node[family]
    if (!probe?.status || probe.status === 'unknown') return (family === 'ipv4_probe' ? 'IPv4' : 'IPv6') + ' —'
    return (family === 'ipv4_probe' ? 'IPv4' : 'IPv6') + ' ' + probeLabel(probe.status) +
      (probe.latency_ms ? ' · ' + probe.latency_ms + 'ms' : '') + (probe.exit_ip ? ' · ' + probe.exit_ip : '')
  }
  return <div className="page-stack">
    <div className="section-toolbar"><div><span className="eyebrow">NETWORK / PROBES</span><p className="muted">{snapshot?.mode || '—'} · {nodes.length} nodes</p></div>
      <div className="toolbar-actions">
        <button className="button button-quiet" type="button" disabled={busy} onClick={() => onAction('reload')}>{t(language, 'reload')}</button>
        <button className="button button-primary" type="button" disabled={busy} onClick={() => onAction('test')}>{busy ? t(language, 'checking') : t(language, 'test')}</button>
      </div>
    </div>
    {snapshot?.error && <section className="surface proxy-manager-error">
      <strong>{t(language, 'loadFailed')}</strong><p>{snapshot.error}</p>
    </section>}
    <section className="surface">
      <div className="surface-heading"><div><h2>{t(language, 'source')}</h2><p>{language === 'zh' ? '本地文件与订阅源解析摘要' : 'Local file and subscription parsing summary'}</p></div></div>
      <div className="source-grid">{(['subscription', 'file'] as const).map(key => {
        const source = sources[key]
        const protocols = Object.entries(source?.protocol_counts || {})
          .map(([name, count]) => name + ':' + count).join(' · ')
        return <div className="source-card" key={key}>
          <strong>{key === 'subscription' ? (language === 'zh' ? '远端订阅' : 'Subscription') : (language === 'zh' ? '本地文件' : 'Local file')}</strong>
          <span>{source ? (source.supported || 0) + ' / ' + (source.total_lines || 0) : '—'}</span>
          <small>{source ? [source.decoded_base64 ? 'Base64' : '', protocols, source.stale ? 'Last known good (stale)' : '', source.skipped ? 'Skipped: ' + source.skipped : '', source.error || ''].filter(Boolean).join(' · ') || 'OK' : '—'}</small>
        </div>
      })}</div>
    </section>
    <section className="surface">
      <div className="surface-heading"><div><h2>{language === 'zh' ? '节点运行状态' : 'Node runtime status'}</h2><p>{language === 'zh' ? '探测状态与业务运行健康分别展示' : 'Probe status and business runtime health are shown independently'}</p></div></div>
      {!nodes.length ? <p className="empty-state">{t(language, 'noNodes')}</p> : <div className="table-wrap">
        <table className="data-table proxy-data-table"><thead><tr>
          <th>{t(language, 'node')}</th><th>{t(language, 'protocol')}</th><th>{t(language, 'backend')}</th>
          <th>{t(language, 'probe')}</th><th>{t(language, 'runtime')}</th><th>{t(language, 'latency')}</th>
          <th>{t(language, 'exitIp')}</th><th>{t(language, 'sessions')}</th><th>{t(language, 'failures')}</th>
          <th>{t(language, 'cooldown')}</th><th />
        </tr></thead><tbody>{nodes.map(node => {
          const sampleCount = Number(node.business_samples || 0)
          const runtime = node.rotating
            ? t(language, 'rotating') + ' · ' + (node.gateway_success_rate == null ? '—' : Math.round(node.gateway_success_rate * 1000) / 10 + '%')
            : sampleCount ? (node.health || '—') + ' · n=' + sampleCount : t(language, 'noSamples')
          const latency = probeDetails(node, 'ipv4_probe') + ' / ' + probeDetails(node, 'ipv6_probe')
          return <tr key={node.id}>
            <td><strong>{node.name || node.proxy || node.id}</strong><small className="cell-sub">{node.id}</small></td>
            <td>{node.protocol || '—'}</td><td>{node.backend || 'native'}</td>
            <td><span className={'health-tag ' + (node.probe_status === 'healthy' ? 'ok' : node.probe_status === 'unhealthy' ? 'error' : 'warn')}>{probeLabel(node.probe_status)}</span></td>
            <td>{runtime}</td><td>{latency}</td><td>{node.exit_ip || '—'}</td>
            <td>{node.inflight ?? '—'}</td>
            <td>{node.rotating ? (node.exit_failures || 0) + ' exits' : (node.failure_count || 0) + ' · T' + (node.transport_failures || 0) + ' / C' + (node.configuration_failures || 0)}</td>
            <td>{node.rotating ? '—' : node.cooldown_sec ? node.cooldown_sec + 's' : '—'}</td>
            <td><button className="button button-small" type="button" disabled={busy} onClick={() => onAction('preflight', node.id)}>{t(language, 'preflight')}</button></td>
          </tr>
        })}</tbody></table>
      </div>}
      {nodes.some(node => node.probe_error || node.last_error) && <div className="node-errors">
        {nodes.filter(node => node.probe_error || node.last_error).map(node => <p key={node.id}><strong>{node.name || node.id}</strong> · {node.probe_error || node.last_error}</p>)}
      </div>}
    </section>
    {preflight && <section className="surface">
      <div className="surface-heading">
        <div><h2>{t(language, 'preflightDone')}</h2><p>{preflight.id}</p></div>
        <span className={'health-tag ' + (preflight.ok ? 'ok' : 'error')}>
          {preflight.ok ? t(language, 'preflightPassed') : t(language, 'preflightFailed')}
        </span>
      </div>
      <div className="preflight-results">
        {preflight.targets.map(target => <article className="preflight-target" key={target.url}>
          <strong>{target.url}</strong>
          <span className={'health-tag ' + (target.usable ? 'ok' : 'error')}>
            {target.usable ? t(language, 'usable') : t(language, 'notUsable')}
          </span>
          <small>
            {target.reachable ? t(language, 'reachable') : t(language, 'notReachable')}
            {' · HTTP '}{target.status_code || '—'}{' · '}{target.latency_ms}ms
            {target.cloudflare_block ? ' · ' + t(language, 'cloudflareBlock') : ''}
          </small>
          {target.error && <small className="preflight-error">{target.error}</small>}
        </article>)}
      </div>
    </section>}
  </div>
}

function MailboxPage({ pool, test, language, busy, dirty, onReload, onSave, onTest, onDataChange, onPathChange }: {
  pool: MailboxPool | null; test: MailboxTest | null; language: Language; busy: boolean; dirty: boolean
  onReload: () => void; onSave: () => void; onTest: () => void
  onDataChange: (value: string) => void; onPathChange: (value: string) => void
}) {
  return <div className="page-stack">
    <section className="surface mailbox-editor">
      <div className="surface-heading"><div><h2>{t(language, 'mailboxData')}</h2><p>{t(language, 'mailboxFormat')}</p></div>{dirty && <span className="dirty-count">{t(language, 'unsaved')}</span>}</div>
      <label className="config-field"><span className="field-label">{t(language, 'mailboxPath')}</span>
        <input value={pool?.path || ''} disabled={busy || !pool} onChange={event => onPathChange(event.target.value)} spellCheck={false} /></label>
      <label className="config-field mailbox-textarea"><span className="field-label">{t(language, 'mailboxData')}</span>
        <textarea value={pool?.data || ''} disabled={busy || !pool} onChange={event => onDataChange(event.target.value)} spellCheck={false} autoComplete="off" /></label>
      <div className="mailbox-actions"><div className="toolbar-actions">
        <button className="button button-quiet" type="button" disabled={busy} onClick={onReload}>{t(language, 'loadPool')}</button>
        <button className="button button-quiet" type="button" disabled={busy || !pool} onClick={onTest}>{busy ? t(language, 'checking') : t(language, 'testPool')}</button>
      </div><button className="button button-primary" type="button" disabled={busy || !pool || !pool.data.trim()} onClick={onSave}>{busy ? t(language, 'saving') : t(language, 'savePool')}</button></div>
    </section>
    <section className="metric-grid mailbox-metrics">
      <Metric label={t(language, 'count')} value={pool?.count || 0} />
      <Metric label={t(language, 'invalid')} value={pool?.invalid || 0} tone={pool?.invalid ? 'tone-bad' : ''} />
      <Metric label={t(language, 'duplicates')} value={pool?.duplicates.length || 0} tone={pool?.duplicates.length ? 'tone-warn' : ''} />
      {test && <Metric label={t(language, 'healthyMail')} value={test.healthy} tone="tone-good" />}
      {test && <Metric label={t(language, 'unhealthyMail')} value={test.unhealthy} tone={test.unhealthy ? 'tone-bad' : ''} />}
    </section>
    {pool?.duplicates.length ? <section className="surface"><div className="surface-heading"><h2>{t(language, 'duplicates')}</h2></div><p className="muted">{pool.duplicates.join(' · ')}</p></section> : null}
    {test && <section className="surface"><div className="surface-heading"><div><h2>{t(language, 'testResults')}</h2><p>{test.healthy}/{test.count} · IMAP {test.imap} · Graph {test.graph}</p></div></div>
      <div className="mail-test-list">{test.results.map(item => <article className="mail-test-row" key={item.email + ':' + item.mode}>
        <div><strong>{item.email}</strong><small>{item.mode.toUpperCase()}</small></div>
        <span className={'health-tag ' + (item.usable ? 'ok' : 'error')}>{item.usable ? t(language, 'usable') : t(language, 'notUsable')}</span>
        <div className="mail-channel"><strong>{t(language, 'imap')}</strong><span>{item.imap?.ok ? 'OK' : item.imap?.error || '—'}</span></div>
        <div className="mail-channel"><strong>{t(language, 'graph')}</strong><span>{item.graph?.ok ? 'OK' : item.graph?.error || '—'}</span></div>
      </article>)}</div>
    </section>}
  </div>
}

function ConsolePage({ logs, language, onClear, onCopy }: {
  logs: LogEntry[]; language: Language; onClear: () => void; onCopy: (entries: LogEntry[]) => void
}) {
  const [query, setQuery] = useState('')
  const terminalRef = useRef<HTMLDivElement>(null)
  const followLogs = useRef(true)
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return needle ? logs.filter(entry => entry.line.toLowerCase().includes(needle)) : logs
  }, [logs, query])
  const lastVisibleSeq = filtered[filtered.length - 1]?.seq
  useEffect(() => {
    const terminal = terminalRef.current
    if (terminal && followLogs.current) terminal.scrollTop = terminal.scrollHeight
  }, [lastVisibleSeq])
  return <section className="surface console-surface">
    <div className="console-toolbar">
      <label className="search-box"><span>⌕</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder={t(language, 'searchLogs')} /></label>
      <div className="toolbar-actions">
        <button className="button button-quiet" type="button" onClick={() => onCopy(filtered)}>{t(language, 'copyLogs')}</button>
        <button className="button button-quiet" type="button" onClick={onClear}>{t(language, 'clearLogs')}</button>
      </div>
    </div>
    <div className="terminal" ref={terminalRef} onScroll={() => {
      const terminal = terminalRef.current
      if (terminal) followLogs.current = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 100
    }}>
      {filtered.length ? filtered.map(entry => {
        const tone = entry.line.includes('[!]') ? 'log-error' : entry.line.toLowerCase().includes('warn') ? 'log-warn' : entry.line.includes('[+]') ? 'log-good' : ''
        return <div className={'terminal-line ' + tone} key={entry.seq}><span className="terminal-seq">{String(entry.seq).padStart(5, '0')}</span><span>{entry.line}</span></div>
      }) : <div className="terminal-empty">{logs.length ? t(language, 'noActivity') : t(language, 'noLogs')}</div>}
    </div>
    <div className="terminal-footer"><span>{filtered.length} / {logs.length} {t(language, 'logs')}</span><span>LIVE · NO STORE</span></div>
  </section>
}

export function App() {
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem('grok_register_lang') === 'en' ? 'en' : 'zh')
  const [page, setPage] = useState<Page>('overview')
  const [config, setConfig] = useState<Config>({})
  const [configLoaded, setConfigLoaded] = useState(false)
  const [configLoading, setConfigLoading] = useState(false)
  const [dirty, setDirty] = useState<Set<string>>(new Set())
  const [status, setStatus] = useState<TaskStatus>(initialStatus)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [health, setHealth] = useState<HealthReport | null>(null)
  const [proxy, setProxy] = useState<ProxySnapshot | null>(null)
  const [proxyPreflight, setProxyPreflight] = useState<ProxyPreflightResult | null>(null)
  const [mailbox, setMailbox] = useState<MailboxPool | null>(null)
  const [mailboxDirty, setMailboxDirty] = useState(false)
  const [mailboxTest, setMailboxTest] = useState<MailboxTest | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [saving, setSaving] = useState(false)
  const [healthBusy, setHealthBusy] = useState(false)
  const [proxyBusy, setProxyBusy] = useState(false)
  const [mailboxBusy, setMailboxBusy] = useState(false)
  const lastSeq = useRef(0)
  const serverLatest = useRef(0)
  const pollingLogs = useRef(false)
  const clearingLogs = useRef(false)
  const logRequestVersion = useRef(0)

  const refreshStatus = useCallback(async () => {
    try {
      const data = await api<StatusResponse>('/api/status')
      setStatus({
        running: data.running, target: data.target, success: data.success, fail: data.fail,
        pending: data.pending, uncertain: data.uncertain, warnings: data.warnings,
        cancelled: data.cancelled, error: data.error, maintenance: data.maintenance,
        started_at: data.started_at, finished_at: data.finished_at, accounts_file: data.accounts_file,
      })
    } catch {
      // Status polling is best-effort while the local server is starting.
    }
  }, [])

  const refreshLogs = useCallback(async () => {
    if (pollingLogs.current || clearingLogs.current) return
    pollingLogs.current = true
    const requestVersion = logRequestVersion.current
    try {
      const data = await api<LogsResponse>('/api/logs?after=' + lastSeq.current)
      if (requestVersion !== logRequestVersion.current) return
      serverLatest.current = data.latest
      if (data.entries.length) {
        setLogs(previous => [...previous, ...data.entries].slice(-2000))
        lastSeq.current = Math.max(lastSeq.current, data.entries[data.entries.length - 1].seq)
      }
    } catch {
      // Keep the console usable while the log endpoint is temporarily unavailable.
    } finally {
      pollingLogs.current = false
    }
  }, [])

  const refreshConfig = useCallback(async () => {
    setConfigLoading(true)
    try {
      const data = await api<ConfigResponse>('/api/config')
      setConfig(data.config)
      setDirty(new Set())
      setConfigLoaded(true)
    } finally {
      setConfigLoading(false)
    }
  }, [])

  const refreshProxy = useCallback(async () => {
    const data = await api<ProxyResponse>('/api/proxy-pool/status')
    setProxy(data)
  }, [])

  const refreshMailbox = useCallback(async () => {
    const data = await api<MailboxResponse>('/api/mailboxes/outlook')
    setMailbox(data)
    setMailboxDirty(false)
    setMailboxTest(null)
  }, [])

  useEffect(() => {
    localStorage.setItem('grok_register_lang', language)
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en'
  }, [language])

  useEffect(() => {
    void refreshConfig().catch(error => setNotice({ text: errorMessage(error), error: true }))
    void refreshStatus()
    void refreshLogs()
    const timer = window.setInterval(() => { void refreshStatus(); void refreshLogs() }, 1200)
    return () => window.clearInterval(timer)
  }, [refreshConfig, refreshStatus, refreshLogs])

  useEffect(() => {
    if (page !== 'proxy') return
    void refreshProxy().catch(error => setNotice({ text: errorMessage(error), error: true }))
    const timer = window.setInterval(() => { void refreshProxy().catch(() => undefined) }, 5000)
    return () => window.clearInterval(timer)
  }, [page, refreshProxy])

  useEffect(() => {
    if (page !== 'mailbox' || mailbox || mailboxDirty) return
    void refreshMailbox().catch(error => setNotice({ text: errorMessage(error), error: true }))
  }, [page, mailbox, mailboxDirty, refreshMailbox])

  const isBusy = status.running || Boolean(status.maintenance)

  const changeConfig = (key: string, value: ConfigValue) => {
    setHealth(null)
    if (key === 'proxy' || key.startsWith('proxy_')) setProxyPreflight(null)
    setConfig(previous => ({ ...previous, [key]: value }))
    setDirty(previous => new Set(previous).add(key))
  }

  const saveConfig = async (): Promise<boolean> => {
    const invalidNumbers = invalidNumericFields(config)
    if (invalidNumbers.length) {
      const field = invalidNumbers[0]
      const label = language === 'zh' ? field.zh : field.en
      const range = field.min + '–' + field.max
      setNotice({
        text: language === 'zh'
          ? label + ' 必须是 ' + range + ' 范围内的整数。'
          : label + ' must be a whole number between ' + range + '.',
        error: true,
      })
      setPage('settings')
      return false
    }
    if (!dirty.size) {
      setNotice({ text: t(language, 'noChanges') })
      return true
    }
    setSaving(true)
    try {
      const updates: Config = {}
      for (const key of dirty) updates[key] = config[key]
      const result = await put<ConfigResponse>('/api/config', updates)
      setConfig(result.config)
      setDirty(new Set())
      setNotice({ text: t(language, 'saved') })
      return true
    } catch (error) {
      setNotice({ text: errorMessage(error), error: true })
      return false
    } finally {
      setSaving(false)
      void refreshStatus()
    }
  }

  const runHealthCheck = async (connectivity: boolean) => {
    if (dirty.size && !(await saveConfig())) return
    setHealthBusy(true)
    try {
      const result = await post<HealthReport>('/api/health-check', { connectivity })
      setHealth(result)
      setNotice({ text: result.ready ? t(language, 'healthReady') : t(language, 'healthIssues'), error: !result.ready })
    } catch (error) {
      setNotice({ text: errorMessage(error), error: true })
    } finally {
      setHealthBusy(false)
      void refreshStatus()
    }
  }

  const startTask = async () => {
    if (dirty.size && !(await saveConfig())) return
    try {
      await post('/api/start')
      setNotice({ text: t(language, 'starting') })
      await refreshStatus()
    } catch (error) {
      setNotice({ text: errorMessage(error), error: true })
    }
  }

  const stopTask = async () => {
    try {
      const result = await post<{ stopped: boolean }>('/api/stop')
      setNotice({ text: result.stopped ? t(language, 'stopSent') : t(language, 'noJob') })
      await refreshStatus()
    } catch (error) {
      setNotice({ text: errorMessage(error), error: true })
    }
  }

  const proxyAction = async (action: 'reload' | 'test' | 'preflight', nodeId?: string) => {
    if (dirty.size && !(await saveConfig())) return
    setProxyPreflight(null)
    setProxyBusy(true)
    try {
      const paths = {
        reload: '/api/proxy-pool/reload',
        test: '/api/proxy-pool/test',
        preflight: '/api/proxy-pool/preflight?node_id=' + encodeURIComponent(nodeId || ''),
      }
      const result = await post<ProxyActionResponse>(paths[action])
      if (result.nodes) setProxy(result)
      else await refreshProxy()
      if (action === 'preflight' && result.result) {
        setProxyPreflight(result.result)
        setNotice({
          text: t(language, result.result.ok ? 'preflightPassed' : 'preflightFailed'),
          error: !result.result.ok,
        })
      } else {
        setProxyPreflight(null)
        setNotice({ text: t(language, action === 'reload' ? 'reload' : 'test') })
      }
    } catch (error) {
      setNotice({ text: errorMessage(error) || t(language, 'proxyActionFailed'), error: true })
    } finally {
      setProxyBusy(false)
      void refreshStatus()
    }
  }

  const mailboxAction = async (action: 'reload' | 'save' | 'test') => {
    if (!mailbox) return
    if (action === 'test') setMailboxTest(null)
    setMailboxBusy(true)
    try {
      if (action === 'reload') {
        await refreshMailbox()
        setNotice({ text: t(language, 'loadPool') })
      } else if (action === 'save') {
        const result = await put<{ ok: boolean; path: string; count: number; accounts?: MailboxPool['accounts'] }>('/api/mailboxes/outlook', {
          path: mailbox.path, data: mailbox.data,
        })
        setMailbox(previous => previous ? {
          ...previous, path: result.path, count: result.count, invalid: 0, duplicates: [],
          accounts: result.accounts,
        } : previous)
        setConfig(previous => ({ ...previous, outlook_accounts_file: result.path }))
        setHealth(null)
        setMailboxDirty(false)
        setMailboxTest(null)
        setNotice({ text: t(language, 'mailboxSaved') })
      } else {
        const result = await post<MailboxTest>('/api/mailboxes/outlook/test', { data: mailbox.data })
        setMailboxTest(result)
        setNotice({ text: t(language, 'mailboxTested'), error: result.unhealthy > 0 })
      }
    } catch (error) {
      setNotice({ text: errorMessage(error), error: true })
    } finally {
      setMailboxBusy(false)
      void refreshStatus()
    }
  }

  const clearLogs = async () => {
    if (clearingLogs.current) return
    clearingLogs.current = true
    const requestVersion = ++logRequestVersion.current
    setLogs([])
    try {
      const data = await api<LogsResponse>('/api/logs?after=0')
      if (requestVersion !== logRequestVersion.current) return
      serverLatest.current = data.latest
      lastSeq.current = data.latest
    } catch (error) {
      lastSeq.current = serverLatest.current
      setNotice({ text: errorMessage(error), error: true })
    } finally {
      clearingLogs.current = false
    }
  }

  const copyLogs = async (entries: LogEntry[]) => {
    try {
      await navigator.clipboard.writeText(entries.map(entry => entry.line).join('\n'))
      setNotice({ text: t(language, 'copied') })
    } catch {
      setNotice({ text: t(language, 'copyFailed'), error: true })
    }
  }

  const pageInfo = pageMeta[page]
  const statusText = status.running ? t(language, 'running') : status.maintenance ? t(language, 'maintenance') : t(language, 'idle')

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand-lockup"><div className="brand-symbol">GR<span>.</span></div><div><strong>{t(language, 'product')}</strong><small>{t(language, 'subtitle')}</small></div></div>
      <div className="sidebar-rule" />
      <span className="nav-caption">{language === 'zh' ? '工作区' : 'WORKSPACE'}</span>
      <nav className="side-nav" aria-label="Primary navigation">
        {(Object.keys(pageMeta) as Page[]).map(key => <button className={'nav-item' + (page === key ? ' active' : '')} type="button" key={key} onClick={() => setPage(key)}>
          <span className="nav-mark">{pageMeta[key].mark}</span><span>{t(language, pageMeta[key].label)}</span>{page === key && <span className="nav-current" />}
        </button>)}
      </nav>
      <div className="sidebar-bottom"><span className="local-indicator"><i />127.0.0.1 : 8092</span><span className="sidebar-note">{t(language, 'local')}</span></div>
    </aside>

    <main className="main-area">
      <header className="topbar">
        <div className="breadcrumb"><span>WORKSPACE</span><b>/</b><strong>{t(language, pageInfo.label)}</strong></div>
        <div className="topbar-actions">
          <StatusBadge status={status} language={language} />
          <div className="language-switch" aria-label={t(language, 'language')}>
            <button type="button" className={language === 'zh' ? 'active' : ''} onClick={() => setLanguage('zh')}>中</button>
            <button type="button" className={language === 'en' ? 'active' : ''} onClick={() => setLanguage('en')}>EN</button>
          </div>
          <a className="repo-link" href="https://github.com/AaronL725/grok-register" target="_blank" rel="noreferrer">GitHub ↗</a>
        </div>
      </header>

      <div className="page-heading">
        <div><span className="eyebrow">GROK REGISTER / {pageInfo.mark}</span><h1>{t(language, pageInfo.title)}</h1><p>{t(language, pageInfo.description)}</p></div>
        <div className="global-actions">
          <button className="button button-quiet" type="button" onClick={() => runHealthCheck(false)} disabled={isBusy || healthBusy || saving}>{healthBusy ? t(language, 'checking') : t(language, 'staticCheck')}</button>
          <button className="button button-quiet" type="button" onClick={() => runHealthCheck(true)} disabled={isBusy || healthBusy || saving}>{t(language, 'onlineCheck')}</button>
          {status.running
            ? <button className="button button-danger" type="button" onClick={stopTask}>{t(language, 'stop')}</button>
            : <button className="button button-primary" type="button" onClick={startTask} disabled={Boolean(status.maintenance) || saving || healthBusy}>{t(language, 'start')}</button>}
        </div>
      </div>

      {notice && <div className={'notice-bar' + (notice.error ? ' is-error' : '')} role="status">
        <span>{notice.error ? '!' : '•'}</span>{notice.text}<button type="button" onClick={() => setNotice(null)} aria-label="Dismiss">×</button>
      </div>}

      <div className="page-content">
        {page === 'overview' && <OverviewPage status={status} logs={logs} health={health} language={language} onNavigate={setPage} />}
        {page === 'settings' && <SettingsPage config={config} dirty={dirty} language={language} disabled={isBusy || saving || configLoading || !configLoaded} saving={saving}
          loading={configLoading} loaded={configLoaded}
          onChange={changeConfig} onSave={() => void saveConfig()}
          onDiscard={() => { void refreshConfig().catch(error => setNotice({ text: errorMessage(error), error: true })) }}
          onReload={() => { void refreshConfig().catch(error => setNotice({ text: errorMessage(error), error: true })) }} />}
        {page === 'proxy' && <ProxyPage snapshot={proxy} preflight={proxyPreflight} busy={proxyBusy || isBusy || saving} language={language} onAction={(action, nodeId) => void proxyAction(action, nodeId)} />}
        {page === 'mailbox' && <MailboxPage pool={mailbox} test={mailboxTest} language={language} busy={mailboxBusy || isBusy || saving} dirty={mailboxDirty}
          onReload={() => void mailboxAction('reload')} onSave={() => void mailboxAction('save')} onTest={() => void mailboxAction('test')}
          onDataChange={value => { setMailbox(previous => previous ? { ...previous, data: value } : previous); setMailboxTest(null); setMailboxDirty(true) }}
          onPathChange={value => { setMailbox(previous => previous ? { ...previous, path: value } : previous); setMailboxDirty(true) }} />}
        {page === 'console' && <ConsolePage logs={logs} language={language} onClear={() => void clearLogs()} onCopy={entries => void copyLogs(entries)} />}
      </div>

      <footer className="page-footer"><span>{t(language, 'localOnly')}</span><span>{statusText}{status.error ? ' · ' + status.error : ''}</span></footer>
    </main>
  </div>
}
