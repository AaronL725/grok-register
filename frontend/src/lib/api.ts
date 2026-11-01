export type ApiError = Error & { status?: number }

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    cache: 'no-store',
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let detail = response.statusText || 'Request failed'
    try {
      const data = await response.json() as { detail?: string }
      detail = data.detail || detail
    } catch {
      // Keep the HTTP status when the server did not return JSON.
    }
    const error = new Error(detail) as ApiError
    error.status = response.status
    throw error
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function post<T>(path: string, payload?: unknown): Promise<T> {
  return api<T>(path, {
    method: 'POST',
    ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
  })
}

export function put<T>(path: string, payload: unknown): Promise<T> {
  return api<T>(path, { method: 'PUT', body: JSON.stringify(payload) })
}

export async function postText(path: string, payload: unknown): Promise<string> {
  const response = await fetch(path, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    let detail = response.statusText || 'Request failed'
    try {
      const data = await response.json() as { detail?: string }
      detail = typeof data.detail === 'string' ? data.detail : detail
    } catch {
      // Keep the HTTP status when the server did not return JSON.
    }
    const error = new Error(detail) as ApiError
    error.status = response.status
    throw error
  }
  return response.text()
}


export type ConfigValue = string | number | boolean | null
export type Config = Record<string, ConfigValue>
export type ConfigResponse = { ok: boolean; config: Config }

export type TaskStatus = {
  running: boolean
  target: number
  success: number
  fail: number
  pending: number
  uncertain: number
  warnings: number
  cancelled: boolean
  error?: string
  maintenance?: string | null
  started_at?: number | null
  finished_at?: number | null
  accounts_file?: string
}

export type LogEntry = { seq: number; line: string }
export type LogBatch = { entries: LogEntry[]; latest: number }

export type HealthEntry = {
  key: string
  name: string
  status: 'OK' | 'WARN' | 'ERROR'
  required: boolean
  message: string
  connection_tested: boolean
}
export type HealthReport = {
  ready: boolean
  result: string
  error_count: number
  warning_count: number
  checks: HealthEntry[]
}

export type ProxyProbe = { status?: string; latency_ms?: number; exit_ip?: string }
export type ProxyNode = {
  id: string
  name?: string
  proxy?: string
  protocol?: string
  backend?: string
  rotating?: boolean
  probe_status?: string
  health?: string
  business_samples?: number
  gateway_success_rate?: number | null
  exit_successes?: number
  exit_failures?: number
  ipv4_probe?: ProxyProbe
  ipv6_probe?: ProxyProbe
  exit_ip?: string
  inflight?: number
  failure_count?: number
  transport_failures?: number
  configuration_failures?: number
  cooldown_sec?: number
  probe_error?: string
  last_error?: string
}
export type ProxySource = {
  supported?: number
  total_lines?: number
  decoded_base64?: boolean
  protocol_counts?: Record<string, number>
  skipped?: number
  stale?: boolean
  error?: string
}
export type ProxySnapshot = {
  mode?: string
  persist_health?: boolean
  nodes?: ProxyNode[]
  sources?: Record<string, ProxySource>
  error?: string
}
export type ProxyPreflightTarget = {
  url: string
  reachable: boolean
  usable: boolean
  status_code: number
  latency_ms: number
  cloudflare_block: boolean
  error: string
}

export type ProxyPreflightResult = {
  id: string
  ok: boolean
  targets: ProxyPreflightTarget[]
}

export type ProxyPreflight = ProxySnapshot & {
  ok: boolean
  result: ProxyPreflightResult
}

export type MailboxPool = {
  path: string
  data: string
  count: number
  invalid: number
  duplicates: string[]
  accounts?: Array<{ email: string; mode: string }>
}
export type MailboxChannel = { ok?: boolean; folders?: string[]; error?: string }
export type MailboxTest = {
  healthy: number
  unhealthy: number
  count: number
  imap: number
  graph: number
  results: Array<{
    email: string
    mode: string
    usable: boolean
    imap?: MailboxChannel
    graph?: MailboxChannel
  }>
}

export type AccountListItem = {
  id: number
  email: string
  email_provider: string
  registration_status: string
  authorization_status: string
  cpa_status: string
  grok2api_local_status: string
  grok2api_remote_status: string
  source: string
  registration_run_id: number | null
  created_at: string
  updated_at: string
  last_authenticated_at: string | null
}

export type RegistrationAttempt = {
  id: number
  registration_run_id: number
  slot_index: number
  account_id: number | null
  attempt_number: number
  worker_key: string
  stage: string
  status: string
  error_type: string
  error_message: string
  traceback: string
  failure_type: string
  screenshot_path: string
  started_at: string
  finished_at: string | null
}

export type AccountArtifact = {
  id: number
  artifact_type: string
  path: string
  format: string
  created_at: string
}

export type AccountDetail = AccountListItem & {
  profile: Record<string, unknown>
  attempts: RegistrationAttempt[]
  artifacts: AccountArtifact[]
}

export type RegistrationRun = {
  id: number
  status: string
  requested_count: number
  processed_count: number
  success_count: number
  failed_count: number
  uncertain_count: number
  registered_unsaved_count: number
  postprocess_warning_count: number
  cancelled: number
  started_at: string
  finished_at: string | null
  error_message: string
  success_rate: number
  attempt_count: number
}

export type ReauthJobItem = {
  account_id: number | null
  email: string
  status: string
  error_message: string
  updated_at: string
}

export type ReauthJob = {
  id: number
  job_type: string
  status: string
  requested_count: number
  processed_count: number
  success_count: number
  failed_count: number
  cancelled: number
  started_at: string
  finished_at: string | null
  error_message: string
  items?: ReauthJobItem[]
}

export type ReauthStatus = {
  running: boolean
  job: ReauthJob | null
}
