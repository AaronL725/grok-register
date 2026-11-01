import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  api, post, postText,
  type AccountDetail, type AccountListItem, type ReauthStatus,
} from '../../lib/api'
import { type Language } from '../configFields'

type Notice = { text: string; error?: boolean }
type Filters = {
  registration_status: string
  authorization_status: string
  email_provider: string
  cpa_status: string
  grok2api_local_status: string
  grok2api_remote_status: string
  date_from: string
  date_to: string
}

const PAGE_SIZE = 50
const emptyFilters: Filters = {
  registration_status: '',
  authorization_status: '',
  email_provider: '',
  cpa_status: '',
  grok2api_local_status: '',
  grok2api_remote_status: '',
  date_from: '',
  date_to: '',
}

const words = {
  zh: {
    title: '账号管理', description: '搜索、筛选和管理 SQLite 中保存的账号。',
    search: '搜索邮箱', searchPlaceholder: '输入邮箱地址…', apply: '查询', reset: '重置',
    registration: '注册状态', authorization: '授权状态', provider: '邮箱服务商',
    cpa: 'CPA 状态', local: '本地 Token', remote: '远端 Token',
    from: '开始日期', to: '结束日期', all: '全部',
    selectPage: '选择本页', selected: '已选择', export: '导出 TXT', remove: '删除账号',
    reauth: '重新登录并恢复授权', refresh: '刷新', loading: '正在加载…',
    noAccounts: '没有找到账号', page: '页', total: '共', previous: '上一页', next: '下一页',
    details: '账号详情', close: '关闭', created: '创建时间', updated: '更新时间',
    lastAuth: '最近授权时间', source: '来源', run: '注册批次', profile: '资料',
    artifacts: '关联文件', attempts: '注册尝试', stage: '阶段', result: '结果',
    failure: '失败类型', message: '错误信息', traceback: '异常堆栈', screenshot: '失败截图',
    noAttempts: '没有注册尝试记录', noArtifacts: '没有关联文件', noScreenshot: '没有截图',
    reauthTitle: '重新登录与授权恢复', reauthDesc: '使用已保存的邮箱和密码通过官方登录流程更新 SSO。',
    noReauth: '当前没有重新登录任务', reauthRunning: '重新登录进行中', stop: '停止任务',
    requested: '目标账号', processed: '已处理', succeeded: '成功', failed: '失败', skipped: '跳过',
    stopSent: '已发送停止请求', startSent: '重新登录任务已启动', deleteConfirm: '确定删除选中的账号吗？此操作不会修改旧版 TXT 文件。',
    deleteDone: '已删除账号', exportDone: '账号导出已开始', reauthLimit: '每批最多选择 200 个账号进行重新登录。',
    noPassword: '没有保存密码的账号会在任务中标记为失败。',
    selectionLimit: '一次最多选择 200 个账号。',
    registered: '已注册', pending_recovery: '待恢复', registered_unsaved: '未保存',
    authorized: '已授权', unknown: '未知', expired: '已过期', pending: '等待中',
    running: '进行中', completed: '已完成', cancelled: '已取消', succeeded_state: '成功',
    failed_state: '失败', warning: '警告', not_requested: '未请求',
    native: '原生注册', legacy_txt: '旧版 TXT', noProvider: '未记录',
  },
  en: {
    title: 'Account management', description: 'Search, filter and manage accounts stored in SQLite.',
    search: 'Search email', searchPlaceholder: 'Enter an email address…', apply: 'Apply', reset: 'Reset',
    registration: 'Registration', authorization: 'Authorization', provider: 'Email provider',
    cpa: 'CPA status', local: 'Local token', remote: 'Remote token',
    from: 'From date', to: 'To date', all: 'All',
    selectPage: 'Select page', selected: 'selected', export: 'Export TXT', remove: 'Delete accounts',
    reauth: 'Reauthenticate and recover access', refresh: 'Refresh', loading: 'Loading…',
    noAccounts: 'No accounts found', page: 'Page', total: 'Total', previous: 'Previous', next: 'Next',
    details: 'Account details', close: 'Close', created: 'Created', updated: 'Updated',
    lastAuth: 'Last authenticated', source: 'Source', run: 'Registration run', profile: 'Profile',
    artifacts: 'Artifacts', attempts: 'Registration attempts', stage: 'Stage', result: 'Result',
    failure: 'Failure type', message: 'Error message', traceback: 'Traceback', screenshot: 'Failure screenshot',
    noAttempts: 'No registration attempts', noArtifacts: 'No linked files', noScreenshot: 'No screenshot',
    reauthTitle: 'Reauthentication and authorization recovery', reauthDesc: 'Use the saved email and password through the official sign-in flow to refresh SSO.',
    noReauth: 'No reauthentication job', reauthRunning: 'Reauthentication in progress', stop: 'Stop job',
    requested: 'Requested', processed: 'Processed', succeeded: 'Succeeded', failed: 'Failed', skipped: 'Skipped',
    stopSent: 'Stop request sent', startSent: 'Reauthentication job started', deleteConfirm: 'Delete the selected accounts? This does not modify legacy TXT files.',
    deleteDone: 'Accounts deleted', exportDone: 'Account export started', reauthLimit: 'Select at most 200 accounts per reauthentication batch.',
    noPassword: 'Accounts without a saved password will be marked as failed by the job.',
    selectionLimit: 'Select at most 200 accounts at a time.',
    registered: 'Registered', pending_recovery: 'Pending recovery', registered_unsaved: 'Unsaved',
    authorized: 'Authorized', unknown: 'Unknown', expired: 'Expired', pending: 'Pending',
    running: 'Running', completed: 'Completed', cancelled: 'Cancelled', succeeded_state: 'Succeeded',
    failed_state: 'Failed', warning: 'Warning', not_requested: 'Not requested',
    native: 'Native registration', legacy_txt: 'Legacy TXT', noProvider: 'Not recorded',
  },
} as const

function label(language: Language, key: keyof typeof words.zh): string {
  return words[language][key]
}

function formatDate(value: string | null | undefined, language: Language): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US')
}

function stateLabel(value: string | null | undefined, language: Language): string {
  if (!value) return '—'
  const key = value === 'succeeded' ? 'succeeded_state'
    : value === 'failed' ? 'failed_state'
      : value as keyof typeof words.zh
  return key in words[language] ? words[language][key as keyof typeof words.zh] : value
}

function Metric({ label: title, value, tone = '' }: { label: string; value: number; tone?: string }) {
  return <div className="metric-card"><span className="metric-label">{title}</span><strong className={'metric-value ' + tone}>{value.toLocaleString()}</strong></div>
}

function StatePill({ value, language }: { value: string; language: Language }) {
  const tone = value === 'authorized' || value === 'registered' || value === 'succeeded'
    ? 'good' : value === 'failed' || value === 'expired' ? 'bad'
      : value === 'warning' || value === 'pending_recovery' || value === 'pending'
        || value === 'running' || value === 'skipped' ? 'warn' : ''
  return <span className={'state-pill ' + tone}>{stateLabel(value, language)}</span>
}

function Screenshot({ runId, attemptId, language }: { runId: number; attemptId: number; language: Language }) {
  const [failed, setFailed] = useState(false)
  const src = '/api/jobs/history/' + runId + '/attempts/' + attemptId + '/screenshot'
  if (failed) return <span className="muted">{label(language, 'noScreenshot')}</span>
  return <a className="screenshot-link" href={src} target="_blank" rel="noreferrer">
    <img src={src} alt={label(language, 'screenshot')} onError={() => setFailed(true)} />
  </a>
}

function AccountDetailPanel({ account, language, onClose }: {
  account: AccountDetail; language: Language; onClose: () => void
}) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose()
  }}>
    <section className="detail-modal" role="dialog" aria-modal="true" aria-labelledby="account-detail-title">
      <header className="detail-modal-header">
        <div><span className="eyebrow">ACCOUNT / #{account.id}</span><h2 id="account-detail-title">{account.email}</h2></div>
        <button className="button button-quiet" type="button" onClick={onClose}>{label(language, 'close')} ×</button>
      </header>
      <div className="detail-status-grid">
        <div><small>{label(language, 'registration')}</small><StatePill value={account.registration_status} language={language} /></div>
        <div><small>{label(language, 'authorization')}</small><StatePill value={account.authorization_status} language={language} /></div>
        <div><small>{label(language, 'cpa')}</small><StatePill value={account.cpa_status} language={language} /></div>
        <div><small>{label(language, 'local')}</small><StatePill value={account.grok2api_local_status} language={language} /></div>
        <div><small>{label(language, 'remote')}</small><StatePill value={account.grok2api_remote_status} language={language} /></div>
      </div>
      <dl className="detail-metadata">
        <div><dt>{label(language, 'provider')}</dt><dd>{account.email_provider || '—'}</dd></div>
        <div><dt>{label(language, 'source')}</dt><dd>{stateLabel(account.source, language)}</dd></div>
        <div><dt>{label(language, 'run')}</dt><dd>{account.registration_run_id ?? '—'}</dd></div>
        <div><dt>{label(language, 'created')}</dt><dd>{formatDate(account.created_at, language)}</dd></div>
        <div><dt>{label(language, 'updated')}</dt><dd>{formatDate(account.updated_at, language)}</dd></div>
        <div><dt>{label(language, 'lastAuth')}</dt><dd>{formatDate(account.last_authenticated_at, language)}</dd></div>
      </dl>
      {Object.keys(account.profile || {}).length > 0 && <details className="detail-section">
        <summary>{label(language, 'profile')}</summary>
        <pre>{JSON.stringify(account.profile, null, 2)}</pre>
      </details>}
      <section className="detail-section">
        <h3>{label(language, 'artifacts')}</h3>
        {account.artifacts.length ? <ul className="artifact-list">
          {account.artifacts.map(item => <li key={item.id}><strong>{item.artifact_type}</strong><span>{item.path}</span><small>{item.format}</small></li>)}
        </ul> : <p className="empty-state">{label(language, 'noArtifacts')}</p>}
      </section>
      <section className="detail-section">
        <h3>{label(language, 'attempts')} · {account.attempts.length}</h3>
        {account.attempts.length ? <div className="attempt-list">
          {account.attempts.map(attempt => <article className="attempt-card" key={attempt.id}>
            <div className="attempt-heading">
              <strong>#{attempt.id} · {attempt.stage || '—'}</strong>
              <span>{formatDate(attempt.started_at, language)}</span>
              <StatePill value={attempt.status} language={language} />
            </div>
            <div className="attempt-meta">
              <span>{label(language, 'failure')}: {attempt.failure_type || '—'}</span>
              <span>{attempt.error_type || '—'}</span>
              <span>Worker: {attempt.worker_key || '—'}</span>
            </div>
            {attempt.error_message && <p className="attempt-error">{attempt.error_message}</p>}
            {attempt.screenshot_path && <div className="attempt-screenshot">
              <small>{label(language, 'screenshot')}</small>
              <Screenshot runId={attempt.registration_run_id} attemptId={attempt.id} language={language} />
            </div>}
            {attempt.traceback && <details><summary>{label(language, 'traceback')}</summary><pre>{attempt.traceback}</pre></details>}
          </article>)}
        </div> : <p className="empty-state">{label(language, 'noAttempts')}</p>}
      </section>
    </section>
  </div>
}

export function AccountsPage({ language, busy, onNotice }: {
  language: Language; busy: boolean; onNotice: (notice: Notice) => void
}) {
  const [items, setItems] = useState<AccountListItem[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [searchDraft, setSearchDraft] = useState('')
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState<Filters>(emptyFilters)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [loading, setLoading] = useState(false)
  const [actionBusy, setActionBusy] = useState(false)
  const [detail, setDetail] = useState<AccountDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [reauth, setReauth] = useState<ReauthStatus>({ running: false, job: null })
  const [reauthBusy, setReauthBusy] = useState(false)
  const accountRequestVersion = useRef(0)
  const detailRequestVersion = useRef(0)
  const reauthRequestVersion = useRef(0)
  const previousBusy = useRef(busy)

  const refreshAccounts = useCallback(async () => {
    const requestVersion = ++accountRequestVersion.current
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
      if (search) params.set('search', search)
      for (const [key, value] of Object.entries(filters)) {
        if (value) params.set(key, value)
      }
      const result = await api<{ items: AccountListItem[]; total: number }>(
        '/api/accounts?' + params.toString(),
      )
      if (requestVersion !== accountRequestVersion.current) return
      setItems(result.items)
      setTotal(result.total)
      if (offset > 0 && offset >= result.total) setOffset(Math.max(0, offset - PAGE_SIZE))
    } catch (error) {
      if (requestVersion === accountRequestVersion.current) {
        onNotice({ text: error instanceof Error ? error.message : String(error), error: true })
      }
    } finally {
      if (requestVersion === accountRequestVersion.current) setLoading(false)
    }
  }, [filters, offset, onNotice, search])

  const refreshReauth = useCallback(async () => {
    const requestVersion = ++reauthRequestVersion.current
    try {
      const result = await api<ReauthStatus>('/api/reauth/status')
      if (requestVersion === reauthRequestVersion.current) setReauth(result)
    } catch {
      // Reauthentication status is refreshed again on the next poll.
    }
  }, [])

  useEffect(() => { void refreshAccounts() }, [refreshAccounts])
  useEffect(() => {
    if (previousBusy.current && !busy) void refreshAccounts()
    previousBusy.current = busy
  }, [busy, refreshAccounts])
  useEffect(() => {
    void refreshReauth()
    const timer = window.setInterval(() => { void refreshReauth() }, 1500)
    return () => window.clearInterval(timer)
  }, [refreshReauth])

  const selectedIds = useMemo(() => Array.from(selected), [selected])
  const currentIds = items.map(item => item.id)
  const allCurrentSelected = currentIds.length > 0 && currentIds.every(id => selected.has(id))

  const updateFilter = (key: keyof Filters, value: string) => {
    setFilters(previous => ({ ...previous, [key]: value }))
    setOffset(0)
    setSelected(new Set())
  }

  const toggleAccount = (id: number, checked: boolean) => {
    if (checked && !selected.has(id) && selected.size >= 200) {
      onNotice({ text: label(language, 'selectionLimit'), error: true })
      return
    }
    setSelected(previous => {
      const next = new Set(previous)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  const toggleCurrentPage = () => {
    if (!allCurrentSelected && selected.size + currentIds.filter(id => !selected.has(id)).length > 200) {
      onNotice({ text: label(language, 'selectionLimit'), error: true })
      return
    }
    setSelected(previous => {
      const next = new Set(previous)
      if (allCurrentSelected) currentIds.forEach(id => next.delete(id))
      else currentIds.forEach(id => next.add(id))
      return next
    })
  }

  const openDetail = async (id: number) => {
    const requestVersion = ++detailRequestVersion.current
    setDetailLoading(true)
    try {
      const result = await api<{ account: AccountDetail }>('/api/accounts/' + id)
      if (requestVersion === detailRequestVersion.current) setDetail(result.account)
    } catch (error) {
      if (requestVersion === detailRequestVersion.current) {
        onNotice({ text: error instanceof Error ? error.message : String(error), error: true })
      }
    } finally {
      if (requestVersion === detailRequestVersion.current) setDetailLoading(false)
    }
  }

  const deleteSelected = async () => {
    if (!selectedIds.length || busy || actionBusy) return
    if (!window.confirm(label(language, 'deleteConfirm'))) return
    setActionBusy(true)
    try {
      const result = await api<{ deleted_count: number }>('/api/accounts', {
        method: 'DELETE', body: JSON.stringify({ ids: selectedIds }),
      })
      setSelected(new Set())
      onNotice({ text: label(language, 'deleteDone') + ': ' + result.deleted_count })
      await refreshAccounts()
    } catch (error) {
      onNotice({ text: error instanceof Error ? error.message : String(error), error: true })
    } finally {
      setActionBusy(false)
    }
  }

  const exportSelected = async () => {
    if (!selectedIds.length || actionBusy) return
    setActionBusy(true)
    try {
      const text = await postText('/api/accounts/export', { ids: selectedIds })
      const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'accounts-export.txt'
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 0)
      onNotice({ text: label(language, 'exportDone') })
    } catch (error) {
      onNotice({ text: error instanceof Error ? error.message : String(error), error: true })
    } finally {
      setActionBusy(false)
    }
  }

  const startReauth = async () => {
    if (!selectedIds.length || selectedIds.length > 200 || busy || reauthBusy || reauth.running) return
    setReauthBusy(true)
    try {
      await post<{ job_id: number }>('/api/reauth/start', { ids: selectedIds })
      await refreshReauth()
      onNotice({ text: label(language, 'startSent') })
    } catch (error) {
      onNotice({ text: error instanceof Error ? error.message : String(error), error: true })
    } finally {
      setReauthBusy(false)
    }
  }

  const stopReauth = async () => {
    setReauthBusy(true)
    try {
      const result = await post<{ stopped: boolean }>('/api/reauth/stop')
      onNotice({ text: result.stopped ? label(language, 'stopSent') : label(language, 'noReauth') })
      await refreshReauth()
    } catch (error) {
      onNotice({ text: error instanceof Error ? error.message : String(error), error: true })
    } finally {
      setReauthBusy(false)
    }
  }

  const job = reauth.job
  const jobItems = job?.items || []
  const counts = {
    succeeded: jobItems.filter(item => item.status === 'succeeded').length,
    failed: jobItems.filter(item => item.status === 'failed').length,
    skipped: jobItems.filter(item => item.status === 'skipped').length,
  }

  return <div className="page-stack">
    <section className="surface">
      <div className="surface-heading">
        <div><h2>{label(language, 'title')}</h2><p>{label(language, 'description')}</p></div>
        <button className="button button-quiet" type="button" disabled={loading || actionBusy} onClick={() => void refreshAccounts()}>{label(language, 'refresh')}</button>
      </div>
      <form className="account-filters" onSubmit={event => { event.preventDefault(); setSearch(searchDraft); setOffset(0); setSelected(new Set()) }}>
        <label className="search-box"><span>⌕</span><input value={searchDraft} onChange={event => setSearchDraft(event.target.value)} placeholder={label(language, 'searchPlaceholder')} /></label>
        <button className="button button-primary" type="submit">{label(language, 'apply')}</button>
        <button className="button button-quiet" type="button" onClick={() => {
          setSearchDraft(''); setSearch(''); setFilters(emptyFilters); setOffset(0); setSelected(new Set())
        }}>{label(language, 'reset')}</button>
        <div className="account-filter-grid">
          {([
            ['registration_status', 'registration', ['registered', 'pending_recovery', 'registered_unsaved']],
            ['authorization_status', 'authorization', ['authorized', 'unknown', 'expired', 'failed']],
            ['email_provider', 'provider', ['duckmail', 'yyds', 'cloudflare', 'cloudmail', 'outlook']],
            ['cpa_status', 'cpa', ['succeeded', 'failed', 'warning', 'not_requested', 'unknown']],
            ['grok2api_local_status', 'local', ['succeeded', 'failed', 'warning', 'not_requested', 'unknown']],
            ['grok2api_remote_status', 'remote', ['succeeded', 'failed', 'warning', 'not_requested', 'unknown']],
          ] as Array<[keyof Filters, keyof typeof words.zh, string[]]>).map(([key, title, options]) => <label className="config-field" key={key}>
            <span className="field-label">{label(language, title)}</span>
            <select value={filters[key]} onChange={event => updateFilter(key, event.target.value)}>
              <option value="">{label(language, 'all')}</option>
              {options.map(option => <option value={option} key={option}>{stateLabel(option, language)}</option>)}
            </select>
          </label>)}
          <label className="config-field"><span className="field-label">{label(language, 'from')}</span>
            <input type="date" value={filters.date_from} onChange={event => updateFilter('date_from', event.target.value)} /></label>
          <label className="config-field"><span className="field-label">{label(language, 'to')}</span>
            <input type="date" value={filters.date_to} onChange={event => updateFilter('date_to', event.target.value)} /></label>
        </div>
      </form>
      <div className="account-toolbar">
        <label className="account-select-all"><input type="checkbox" checked={allCurrentSelected} onChange={toggleCurrentPage} />{label(language, 'selectPage')}</label>
        <span className="muted">{selected.size} {label(language, 'selected')}</span>
        <div className="toolbar-actions">
          <button className="button button-quiet" type="button" disabled={!selected.size || actionBusy} onClick={() => void exportSelected()}>{label(language, 'export')}</button>
          <button className="button button-danger" type="button" disabled={!selected.size || busy || actionBusy} onClick={() => void deleteSelected()}>{label(language, 'remove')}</button>
          <button className="button button-primary" type="button" disabled={!selected.size || selected.size > 200 || busy || reauth.running || reauthBusy} onClick={() => void startReauth()}>{reauthBusy && !reauth.running ? label(language, 'loading') : label(language, 'reauth')}</button>
        </div>
      </div>
      {loading ? <p className="empty-state">{label(language, 'loading')}</p> : items.length ? <div className="table-wrap">
        <table className="data-table account-table"><thead><tr>
          <th /><th>ID / Email</th><th>{label(language, 'provider')}</th><th>{label(language, 'registration')}</th>
          <th>{label(language, 'authorization')}</th><th>{label(language, 'cpa')}</th><th>{label(language, 'local')} / {label(language, 'remote')}</th>
          <th>{label(language, 'created')}</th><th />
        </tr></thead><tbody>{items.map(account => <tr key={account.id}>
          <td><input type="checkbox" checked={selected.has(account.id)} onChange={event => toggleAccount(account.id, event.target.checked)} /></td>
          <td><strong>#{account.id} · {account.email}</strong></td>
          <td>{account.email_provider || '—'}</td>
          <td><StatePill value={account.registration_status} language={language} /></td>
          <td><StatePill value={account.authorization_status} language={language} /></td>
          <td><StatePill value={account.cpa_status} language={language} /></td>
          <td><StatePill value={account.grok2api_local_status} language={language} /> <StatePill value={account.grok2api_remote_status} language={language} /></td>
          <td>{formatDate(account.created_at, language)}</td>
          <td><button className="button button-small" type="button" disabled={detailLoading} onClick={() => void openDetail(account.id)}>{label(language, 'details')}</button></td>
        </tr>)}</tbody></table>
      </div> : <p className="empty-state">{label(language, 'noAccounts')}</p>}
      <div className="pagination">
        <span>{label(language, 'total')} {total}</span>
        <div className="toolbar-actions">
          <button className="button button-quiet" type="button" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>{label(language, 'previous')}</button>
          <span>{label(language, 'page')} {Math.floor(offset / PAGE_SIZE) + 1} / {Math.max(1, Math.ceil(total / PAGE_SIZE))}</span>
          <button className="button button-quiet" type="button" disabled={offset + PAGE_SIZE >= total || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>{label(language, 'next')}</button>
        </div>
      </div>
    </section>
    <section className="surface reauth-panel">
      <div className="surface-heading">
        <div><h2>{label(language, 'reauthTitle')}</h2><p>{label(language, 'reauthDesc')}</p></div>
        {reauth.running && <StatePill value="running" language={language} />}
      </div>
      {!job ? <p className="empty-state">{label(language, 'noReauth')}</p> : <>
        <div className="reauth-summary">
          <Metric label={label(language, 'requested')} value={job.requested_count} />
          <Metric label={label(language, 'processed')} value={job.processed_count} />
          <Metric label={label(language, 'succeeded')} value={counts.succeeded} tone="tone-good" />
          <Metric label={label(language, 'failed')} value={counts.failed} tone={counts.failed ? 'tone-bad' : ''} />
          <Metric label={label(language, 'skipped')} value={counts.skipped} />
        </div>
        <div className="reauth-job-meta"><span>#{job.id} · {stateLabel(job.status, language)}</span><span>{formatDate(job.started_at, language)}</span></div>
        {job.error_message && <p className="attempt-error">{job.error_message}</p>}
        {job.items && <div className="reauth-item-list">{job.items.map(item => <div className="reauth-item" key={item.account_id ?? item.email}>
          <strong>{item.email}</strong><StatePill value={item.status} language={language} />
          {item.error_message && <small>{item.error_message}</small>}
        </div>)}</div>}
      </>}
      <div className="toolbar-actions">
        {reauth.running && <button className="button button-danger" type="button" disabled={reauthBusy} onClick={() => void stopReauth()}>{label(language, 'stop')}</button>}
        <span className="muted">{label(language, 'noPassword')}</span>
      </div>
    </section>
    {detail && <AccountDetailPanel account={detail} language={language} onClose={() => setDetail(null)} />}
  </div>
}
