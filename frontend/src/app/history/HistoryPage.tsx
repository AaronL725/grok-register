import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  type RegistrationAttempt, type RegistrationRun, type ReauthJob,
} from '../../lib/api'
import { type Language } from '../configFields'

type Notice = { text: string; error?: boolean }
const PAGE_SIZE = 50

const words = {
  zh: {
    title: '任务历史', description: '查看注册批次、逐次尝试、失败截图和重新登录历史。',
    registration: '注册批次', reauth: '重新登录', refresh: '刷新',
    loading: '正在加载…', emptyRuns: '暂无注册批次', emptyJobs: '暂无重新登录记录',
    id: '批次 ID', status: '状态', requested: '目标', processed: '已处理',
    succeeded: '成功', failed: '失败', uncertain: '不确定', warnings: '警告',
    rate: '成功率', attempts: '尝试次数', started: '开始时间', finished: '结束时间',
    error: '错误', details: '查看尝试', noAttempts: '该批次没有尝试记录',
    stage: '阶段', outcome: '结果', worker: 'Worker', failure: '失败类型',
    errorType: '异常类型', message: '错误信息', screenshot: '失败截图',
    traceback: '异常堆栈', slot: '槽位', attempt: '第几次尝试',
    requestedCount: '目标账号', processedCount: '已处理', successCount: '成功',
    failedCount: '失败', cancelled: '已取消', jobItems: '账号处理明细',
    noItems: '没有账号明细', previous: '上一页', next: '下一页',
    page: '页', total: '共', close: '关闭', noScreenshot: '截图不可用',
    running: '运行中', completed: '已完成', cancelledStatus: '已取消', failedStatus: '失败',
    pending: '等待中', skipped: '跳过',
  },
  en: {
    title: 'Task history', description: 'Inspect registration runs, individual attempts, failure screenshots and reauthentication history.',
    registration: 'Registration runs', reauth: 'Reauthentication', refresh: 'Refresh',
    loading: 'Loading…', emptyRuns: 'No registration runs', emptyJobs: 'No reauthentication history',
    id: 'Run ID', status: 'Status', requested: 'Target', processed: 'Processed',
    succeeded: 'Succeeded', failed: 'Failed', uncertain: 'Uncertain', warnings: 'Warnings',
    rate: 'Success rate', attempts: 'Attempts', started: 'Started', finished: 'Finished',
    error: 'Error', details: 'View attempts', noAttempts: 'No attempts for this run',
    stage: 'Stage', outcome: 'Outcome', worker: 'Worker', failure: 'Failure type',
    errorType: 'Exception', message: 'Error message', screenshot: 'Failure screenshot',
    traceback: 'Traceback', slot: 'Slot', attempt: 'Attempt',
    requestedCount: 'Requested', processedCount: 'Processed', successCount: 'Succeeded',
    failedCount: 'Failed', cancelled: 'Cancelled', jobItems: 'Account results',
    noItems: 'No account details', previous: 'Previous', next: 'Next',
    page: 'Page', total: 'Total', close: 'Close', noScreenshot: 'Screenshot unavailable',
    running: 'Running', completed: 'Completed', cancelledStatus: 'Cancelled', failedStatus: 'Failed',
    pending: 'Pending', skipped: 'Skipped',
  },
} as const

function t(language: Language, key: keyof typeof words.zh): string {
  return words[language][key]
}

function date(value: string | null | undefined, language: Language): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US')
}

function statusText(value: string, language: Language): string {
  const key = value === 'running' ? 'running'
    : value === 'completed' ? 'completed'
      : value === 'cancelled' ? 'cancelledStatus'
        : value === 'failed' ? 'failedStatus'
          : value === 'succeeded' ? 'succeeded'
            : value === 'pending' ? 'pending'
            : value === 'skipped' ? 'skipped' : ''
  return key ? t(language, key) : value
}

function Pill({ value, language }: { value: string; language: Language }) {
  const tone = value === 'succeeded' || value === 'completed' ? 'good'
    : value === 'failed' ? 'bad'
      : value === 'running' || value === 'pending' || value === 'skipped' ? 'warn' : ''
  return <span className={'state-pill ' + tone}>{statusText(value, language)}</span>
}

function Screenshot({ runId, attemptId, language }: { runId: number; attemptId: number; language: Language }) {
  const [failed, setFailed] = useState(false)
  const src = '/api/jobs/history/' + runId + '/attempts/' + attemptId + '/screenshot'
  return failed ? <span className="muted">{t(language, 'noScreenshot')}</span>
    : <a className="screenshot-link" href={src} target="_blank" rel="noreferrer">
      <img src={src} alt={t(language, 'screenshot')} onError={() => setFailed(true)} />
    </a>
}

function AttemptList({ runId, attempts, language }: {
  runId: number; attempts: RegistrationAttempt[]; language: Language
}) {
  return <div className="attempt-list">
    {attempts.map(attempt => <article className="attempt-card" key={attempt.id}>
      <div className="attempt-heading">
        <strong>#{attempt.id} · {attempt.stage || '—'}</strong>
        <span>{date(attempt.started_at, language)}</span>
        <Pill value={attempt.status} language={language} />
      </div>
      <div className="attempt-meta">
        <span>{t(language, 'slot')}: {attempt.slot_index}</span>
        <span>{t(language, 'attempt')}: {attempt.attempt_number}</span>
        <span>{t(language, 'worker')}: {attempt.worker_key || '—'}</span>
        <span>{t(language, 'failure')}: {attempt.failure_type || '—'}</span>
        <span>{t(language, 'errorType')}: {attempt.error_type || '—'}</span>
      </div>
      {attempt.error_message && <p className="attempt-error">{attempt.error_message}</p>}
      {attempt.screenshot_path && <div className="attempt-screenshot">
        <small>{t(language, 'screenshot')}</small>
        <Screenshot runId={runId} attemptId={attempt.id} language={language} />
      </div>}
      {attempt.traceback && <details><summary>{t(language, 'traceback')}</summary><pre>{attempt.traceback}</pre></details>}
    </article>)}
  </div>
}

export function HistoryPage({ language, onNotice }: {
  language: Language; onNotice: (notice: Notice) => void
}) {
  const [tab, setTab] = useState<'registration' | 'reauth'>('registration')
  const [offset, setOffset] = useState(0)
  const [runs, setRuns] = useState<RegistrationRun[]>([])
  const [runTotal, setRunTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [selectedRun, setSelectedRun] = useState<RegistrationRun | null>(null)
  const [attempts, setAttempts] = useState<RegistrationAttempt[]>([])
  const [attemptLoading, setAttemptLoading] = useState(false)
  const [attemptOffset, setAttemptOffset] = useState(0)
  const [attemptTotal, setAttemptTotal] = useState(0)
  const [reauthJobs, setReauthJobs] = useState<ReauthJob[]>([])
  const [reauthTotal, setReauthTotal] = useState(0)
  const [selectedJob, setSelectedJob] = useState<ReauthJob | null>(null)
  const historyRequestVersion = useRef(0)
  const attemptRequestVersion = useRef(0)
  const jobRequestVersion = useRef(0)

  const refreshRegistration = useCallback(async () => {
    const requestVersion = ++historyRequestVersion.current
    setLoading(true)
    try {
      const result = await api<{ items: RegistrationRun[]; total: number }>(
        '/api/jobs/history?limit=' + PAGE_SIZE + '&offset=' + offset,
      )
      if (requestVersion !== historyRequestVersion.current) return
      setRuns(result.items)
      setRunTotal(result.total)
      if (offset > 0 && offset >= result.total) setOffset(Math.max(0, offset - PAGE_SIZE))
    } catch (error) {
      if (requestVersion === historyRequestVersion.current) {
        onNotice({ text: error instanceof Error ? error.message : String(error), error: true })
      }
    } finally {
      if (requestVersion === historyRequestVersion.current) setLoading(false)
    }
  }, [offset, onNotice])

  const refreshReauth = useCallback(async () => {
    const requestVersion = ++historyRequestVersion.current
    setLoading(true)
    try {
      const result = await api<{ items: ReauthJob[]; total: number }>(
        '/api/reauth/history?limit=' + PAGE_SIZE + '&offset=' + offset,
      )
      if (requestVersion !== historyRequestVersion.current) return
      setReauthJobs(result.items)
      setReauthTotal(result.total)
      if (offset > 0 && offset >= result.total) setOffset(Math.max(0, offset - PAGE_SIZE))
    } catch (error) {
      if (requestVersion === historyRequestVersion.current) {
        onNotice({ text: error instanceof Error ? error.message : String(error), error: true })
      }
    } finally {
      if (requestVersion === historyRequestVersion.current) setLoading(false)
    }
  }, [offset, onNotice])

  useEffect(() => {
    if (tab === 'registration') void refreshRegistration()
    else void refreshReauth()
  }, [tab, refreshRegistration, refreshReauth])

  const loadAttempts = useCallback(async (runId: number, nextOffset: number) => {
    const requestVersion = ++attemptRequestVersion.current
    setAttemptLoading(true)
    try {
      const result = await api<{ items: RegistrationAttempt[]; total: number }>(
        '/api/jobs/history/' + runId + '/attempts?limit=' + PAGE_SIZE + '&offset=' + nextOffset,
      )
      if (requestVersion === attemptRequestVersion.current) {
        setAttempts(result.items)
        setAttemptTotal(result.total)
        setAttemptOffset(nextOffset)
      }
    } catch (error) {
      if (requestVersion === attemptRequestVersion.current) {
        onNotice({ text: error instanceof Error ? error.message : String(error), error: true })
      }
    } finally {
      if (requestVersion === attemptRequestVersion.current) setAttemptLoading(false)
    }
  }, [onNotice])

  const openRun = async (run: RegistrationRun) => {
    jobRequestVersion.current += 1
    setSelectedRun(run)
    setSelectedJob(null)
    setAttempts([])
    setAttemptTotal(0)
    setAttemptOffset(0)
    await loadAttempts(run.id, 0)
  }

  const openJob = async (job: ReauthJob) => {
    attemptRequestVersion.current += 1
    const requestVersion = ++jobRequestVersion.current
    setSelectedJob(job)
    setSelectedRun(null)
    try {
      const result = await api<{ job: ReauthJob }>('/api/reauth/jobs/' + job.id)
      if (requestVersion === jobRequestVersion.current) setSelectedJob(result.job)
    } catch (error) {
      if (requestVersion === jobRequestVersion.current) {
        onNotice({ text: error instanceof Error ? error.message : String(error), error: true })
      }
    }
  }

  const changeTab = (next: 'registration' | 'reauth') => {
    setTab(next)
    setOffset(0)
    setSelectedRun(null)
    setSelectedJob(null)
    attemptRequestVersion.current += 1
    jobRequestVersion.current += 1
  }

  const total = tab === 'registration' ? runTotal : reauthTotal
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return <div className="page-stack">
    <section className="surface">
      <div className="surface-heading">
        <div><h2>{t(language, 'title')}</h2><p>{t(language, 'description')}</p></div>
        <button className="button button-quiet" type="button" disabled={loading}
          onClick={() => tab === 'registration' ? void refreshRegistration() : void refreshReauth()}>
          {t(language, 'refresh')}
        </button>
      </div>
      <div className="history-tabs">
        <button className={tab === 'registration' ? 'active' : ''} type="button" onClick={() => changeTab('registration')}>{t(language, 'registration')}</button>
        <button className={tab === 'reauth' ? 'active' : ''} type="button" onClick={() => changeTab('reauth')}>{t(language, 'reauth')}</button>
      </div>
      {loading ? <p className="empty-state">{t(language, 'loading')}</p> : tab === 'registration' ? (
        runs.length ? <div className="table-wrap">
          <table className="data-table history-table"><thead><tr>
            <th>{t(language, 'id')}</th><th>{t(language, 'status')}</th><th>{t(language, 'requested')}</th>
            <th>{t(language, 'processed')}</th><th>{t(language, 'succeeded')}</th><th>{t(language, 'failed')}</th>
            <th>{t(language, 'uncertain')}</th><th>{t(language, 'warnings')}</th><th>{t(language, 'rate')}</th>
            <th>{t(language, 'attempts')}</th><th>{t(language, 'started')}</th><th />
          </tr></thead><tbody>{runs.map(run => <tr key={run.id}>
            <td>#{run.id}</td><td><Pill value={run.status} language={language} /></td>
            <td>{run.requested_count}</td><td>{run.processed_count}</td><td>{run.success_count}</td>
            <td>{run.failed_count}</td><td>{run.uncertain_count}</td><td>{run.postprocess_warning_count + run.registered_unsaved_count}</td>
            <td>{run.success_rate}%</td><td>{run.attempt_count}</td><td>{date(run.started_at, language)}</td>
            <td><button className="button button-small" type="button" onClick={() => void openRun(run)}>{t(language, 'details')}</button></td>
          </tr>)}</tbody></table>
        </div> : <p className="empty-state">{t(language, 'emptyRuns')}</p>
      ) : reauthJobs.length ? <div className="table-wrap">
        <table className="data-table history-table"><thead><tr>
          <th>{t(language, 'id')}</th><th>{t(language, 'status')}</th><th>{t(language, 'requested')}</th>
          <th>{t(language, 'processed')}</th><th>{t(language, 'succeeded')}</th><th>{t(language, 'failed')}</th>
          <th>{t(language, 'started')}</th><th>{t(language, 'finished')}</th><th />
        </tr></thead><tbody>{reauthJobs.map(job => <tr key={job.id}>
          <td>#{job.id}</td><td><Pill value={job.status} language={language} /></td>
          <td>{job.requested_count}</td><td>{job.processed_count}</td><td>{job.success_count}</td><td>{job.failed_count}</td>
          <td>{date(job.started_at, language)}</td><td>{date(job.finished_at, language)}</td>
          <td><button className="button button-small" type="button" onClick={() => void openJob(job)}>{t(language, 'details')}</button></td>
        </tr>)}</tbody></table>
      </div> : <p className="empty-state">{t(language, 'emptyJobs')}</p>}
      <div className="pagination">
        <span>{t(language, 'total')} {total}</span>
        <div className="toolbar-actions">
          <button className="button button-quiet" type="button" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>{t(language, 'previous')}</button>
          <span>{t(language, 'page')} {Math.floor(offset / PAGE_SIZE) + 1} / {pageCount}</span>
          <button className="button button-quiet" type="button" disabled={offset + PAGE_SIZE >= total || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>{t(language, 'next')}</button>
        </div>
      </div>
    </section>

    {selectedRun && <section className="surface">
      <div className="surface-heading">
        <div><h2>Registration run #{selectedRun.id}</h2><p>{date(selectedRun.started_at, language)} · {selectedRun.status} · {t(language, 'attempts')}: {attemptTotal}</p></div>
        <button className="button button-quiet" type="button" onClick={() => { setSelectedRun(null); attemptRequestVersion.current += 1 }}>{t(language, 'close')} ×</button>
      </div>
      {selectedRun.error_message && <p className="attempt-error">{selectedRun.error_message}</p>}
      {attemptLoading ? <p className="empty-state">{t(language, 'loading')}</p>
        : attempts.length ? <>
          <AttemptList runId={selectedRun.id} attempts={attempts} language={language} />
          <div className="pagination">
            <span>{t(language, 'total')} {attemptTotal}</span>
            <div className="toolbar-actions">
              <button className="button button-quiet" type="button" disabled={attemptOffset === 0 || attemptLoading}
                onClick={() => void loadAttempts(selectedRun.id, Math.max(0, attemptOffset - PAGE_SIZE))}>{t(language, 'previous')}</button>
              <span>{t(language, 'page')} {Math.floor(attemptOffset / PAGE_SIZE) + 1} / {Math.max(1, Math.ceil(attemptTotal / PAGE_SIZE))}</span>
              <button className="button button-quiet" type="button" disabled={attemptOffset + PAGE_SIZE >= attemptTotal || attemptLoading}
                onClick={() => void loadAttempts(selectedRun.id, attemptOffset + PAGE_SIZE)}>{t(language, 'next')}</button>
            </div>
          </div>
        </> : <p className="empty-state">{t(language, 'noAttempts')}</p>}
    </section>}

    {selectedJob && <section className="surface">
      <div className="surface-heading">
        <div><h2>Reauthentication job #{selectedJob.id}</h2><p>{date(selectedJob.started_at, language)} · {statusText(selectedJob.status, language)}</p></div>
        <button className="button button-quiet" type="button" onClick={() => {
          setSelectedJob(null)
          jobRequestVersion.current += 1
        }}>{t(language, 'close')} ×</button>
      </div>
      <div className="detail-status-grid">
        <div><small>{t(language, 'requestedCount')}</small><strong>{selectedJob.requested_count}</strong></div>
        <div><small>{t(language, 'processedCount')}</small><strong>{selectedJob.processed_count}</strong></div>
        <div><small>{t(language, 'successCount')}</small><strong>{selectedJob.success_count}</strong></div>
        <div><small>{t(language, 'failedCount')}</small><strong>{selectedJob.failed_count}</strong></div>
      </div>
      {selectedJob.error_message && <p className="attempt-error">{selectedJob.error_message}</p>}
      <h3>{t(language, 'jobItems')}</h3>
      {selectedJob.items?.length ? <div className="reauth-item-list">
        {selectedJob.items.map(item => <div className="reauth-item" key={item.account_id ?? item.email}>
          <strong>{item.email}</strong><Pill value={item.status} language={language} />
          {item.error_message && <small>{item.error_message}</small>}
        </div>)}
      </div> : <p className="empty-state">{t(language, 'noItems')}</p>}
    </section>}
  </div>
}
