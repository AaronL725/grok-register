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
}
export type ProxyPreflight = {
  ok: boolean
  result?: {
    ok?: boolean
    status?: string
    message?: string
    [key: string]: unknown
  }
  nodes?: ProxyNode[]
  mode?: string
  [key: string]: unknown
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
