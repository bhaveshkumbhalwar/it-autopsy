/**
 * Single source of truth for the API location.
 *
 *  - VITE_API_BASE wins when set (e.g. "http://127.0.0.1:8000/api" in production
 *    builds served from a different origin).
 *  - Otherwise requests go to the same origin under /api, which the Vite dev
 *    server proxies to the backend (see vite.config.ts).
 */
const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/+$/, '') ?? '/api'

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

export type ParseIssue = { message: string; line?: number | null; hint?: string | null }

function messageFromDetail(detail: unknown): string {
  if (!detail) return 'Request failed.'
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === 'object' && d && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d)))
      .join(', ')
  }
  if (typeof detail === 'object' && detail && 'message' in detail) return String((detail as ParseIssue).message)
  return JSON.stringify(detail)
}

/**
 * Turns any thrown value into a message a user can act on.
 * HTTP 404/405 almost always mean "backend not restarted / wrong port", so we
 * say that instead of surfacing a bare "Not Found".
 */
export function describeApiError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return e.message
    if (e.status === 404) {
      return 'The API answered 404 for this request. The backend is reachable but does not expose this route — restart it so the current code is loaded, then retry.'
    }
    if (e.status === 405) return 'The API rejected the request method (405). Frontend and backend versions do not match.'
    if (e.status === 401 || e.status === 403) return 'The API rejected the request (authentication). Check API_TOKEN configuration.'
    if (e.status === 429) return 'Rate limit reached. Wait a moment and retry.'
    if (e.status >= 500) return `The server failed to process the request (${e.status}). Check the backend logs.`
    return `Request failed (${e.status}): ${e.message}`
  }
  return e instanceof Error ? e.message : 'Unknown error'
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...(init?.headers ?? {}),
      },
    })
  } catch {
    throw new ApiError(0, 'Cannot reach the API. Is the backend running on port 8000?')
  }
  if (!response.ok) {
    let detail: unknown = null
    try {
      detail = (await response.json()).detail
    } catch {
      detail = response.statusText
    }
    throw new ApiError(response.status, messageFromDetail(detail), detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

// NONE means "no failure was detected", which is not the same as a low score.
export type Severity = 'NONE' | 'LOW' | 'MODERATE' | 'HIGH' | 'CRITICAL'
export type ExecStatus = 'success' | 'failed' | 'partial' | 'recovered' | 'unknown'

export type ConfidenceFactor = { factor: string; detail: string; weight: number }

export type CausalNode = {
  step_index: number
  step_name: string | null
  error_type: string | null
  message: string
  role: "root_cause" | "downstream"
  caused_by: number | null
}

export type Evidence = {
  step_index: number | null
  step_name: string | null
  field: string
  quote: string
  why?: string
  category?: string
  categories?: string[]
  source?: string
  error_type?: string | null
  role?: "root_cause" | "contributing" | "downstream" | "context"
  causes?: number[]
  caused_by?: number | null
  weight?: number
  contribution?: string | null
}

export type Detection = {
  category: string
  severity: Severity
  confidence: number
  step_index: number | null
  title: string
  evidence: Evidence[]
  notes: string[]
  causal_role?: "root_cause" | "contributing" | "downstream"
  is_primary?: boolean
}

export type Recommendation = {
  id: string
  problem: string
  fix: string
  reason: string
  benefit: string
  priority: string
}

export type TimelineItem = {
  index: number
  type: string
  label: string
  status: string
  timestamp: string | null
  duration_ms: number | null
  detail: string
  is_failure: boolean
}

export type FixSnippet = { language: string; code: string; note?: string }

export type RecommendedFix = {
  id: string | null
  title: string
  why: string | null
  priority: string | null
  for_categories: string[]
  applies_to: string[]
  caption: string
  source: 'template' | 'llm'
  snippet: FixSnippet
}

export type TimelineEvidence = {
  field: string | null
  role: string | null
  quote: string | null
  why: string | null
  category: string | null
  source: string | null
  weight: number | null
  caused_by: number | null
  repeats_step_error: boolean
  adds_information: boolean
}

export type MergedTimelineItem = {
  index: number | null
  number: number | null
  kind: 'task' | 'plan' | 'llm' | 'tool' | 'retry' | 'error' | 'output' | 'other'
  type: string
  title: string
  kind_label: string
  status: string
  timestamp: string | null
  duration_ms: number | null
  is_root_cause: boolean
  is_failure: boolean
  causal_role: string | null
  summary: string | null
  detail: { input: string | null; output: string | null; error: string | null; tool_arguments: string | null; metadata: string | null }
  evidence: TimelineEvidence[]
  evidence_summary: string | null
  key: string
}

export type Observation = {
  kind: 'retry' | 'slow_step' | 'large_payload'
  title: string
  detail: string
  step_numbers: number[]
}

export type MergedTimeline = {
  items: MergedTimelineItem[]
  failed_step_index: number | null
  failed_step_number: number | null
  notable_indices: (number | null)[]
  collapsed_by_default: boolean
  total_steps: number
}

export type Analysis = {
  id: number
  execution_pk: number
  engine_version: string
  status: ExecStatus
  severity: Severity
  categories: Detection[]
  primary_category: string | null
  primary_label?: string | null
  failed_step_index: number | null
  failed_step?: Record<string, unknown> | null
  root_cause: string
  what_happened: string
  impact: string
  recoverable: string
  confidence: number
  confidence_note?: string
  confidence_factors?: ConfidenceFactor[]
  causal_chain?: CausalNode[]
  root_cause_step?: Record<string, unknown> | null
  evidence: Evidence[]
  recommendations: Recommendation[]
  prevention: string[]
  timeline: TimelineItem[]
  fixes?: RecommendedFix[]
  primary_title?: string | null
  llm_used: boolean
  llm_model: string | null
  llm_error: string | null
  detection_notes: string[]
  created_at: string | null
  severity_detail?: { severity: string; score: number; factors: { factor: string; detail: string; weight: number }[] }
  ml?: {
    used?: boolean
    prediction: string | null
    confidence: number | null
    top: [string, number][]
    hint_only?: boolean
    agrees?: boolean | null
  }
}

export type ExecutionSummary = {
  id: number
  execution_id: string
  agent_name: string
  framework: string | null
  source_format: string
  status: ExecStatus
  severity: Severity | null
  primary_category: string | null
  categories: string[]
  duration_ms: number | null
  started_at: string | null
  task_description: string | null
  step_count: number
  tool_call_count: number
  is_demo: boolean
  redactions: number
  created_at: string | null
  analysis_count: number
  retry_count: number
  latest_analysis?: Partial<Analysis>
}

export type ExecutionDetail = ExecutionSummary & { trace: Record<string, unknown> }

export type IngestResult = {
  execution: ExecutionDetail
  analysis: Analysis
  format: string | null
  warnings: string[]
  redactions: number
}

export type Dashboard = {
  totals: {
    executions: number
    agents: number
    success: number
    failed: number
    partial: number
    recovered: number
    failure_rate: number
    healthy: number
    healthy_rate: number | null
    avg_duration_ms: number | null
    analyses: number
    demo_executions: number
    include_demo: boolean
  }
  categories: { category: string; label: string; count: number }[]
  severity: Record<Severity, number>
  trend: { date: string; total: number; failed: number; success: number; recovered: number }[]
  recent: ExecutionSummary[]
}

export type Analytics = {
  failing_tools: { tool: string; calls: number; failures: number; failure_rate: number; low_sample: boolean }[]
  low_sample_threshold: number
  root_causes: { category: string; label: string; count: number }[]
  frameworks: { framework: string; count: number }[]
  reliability: {
    agent_name: string
    executions: number
    failure_rate: number
    recovery_rate: number
    avg_duration_ms: number | null
    top_tools: string[]
  }[]
  severity_distribution: Record<Severity, number>
  status_counts: { success: number; recovered: number; partial: number; failed: number; unknown: number }
}

export type LlmHealthState =
  | 'ready'
  | 'key_missing'
  | 'key_invalid'
  | 'unreachable'
  | 'model_unavailable'
  | 'rate_limited'
  | 'misconfigured'

export interface LlmHealth {
  state: LlmHealthState
  message: string
  provider: string
  model: string
  latency_ms: number | null
  checked_at: string | null
  cached: boolean
}

export type AppSettings = {
  preferences: {
    theme: string
    retention_days: number
    default_use_llm: boolean
    redact_secrets: boolean
  }
  ai: {
    provider: string
    model: string
    base_url?: string
    enabled: boolean
    key_configured: boolean
    problem?: string | null
    env_var?: string
    note: string
  }
  security: {
    redaction_enabled: boolean
    api_token_required: boolean
    max_upload_mb: number
    allowed_extensions: string[]
    rate_limit: string
  }
  retention: { days: number }
  app: { name: string; version: string; environment: string }
}

export const api = {
  formats: () => request<{ formats: { id: string; label: string }[]; allowed_extensions: string[]; max_upload_bytes: number }>('/formats'),
  demos: () => request<{ demos: { id: string; label: string; framework: string }[] }>('/demos'),
  paste: (body: {
    text: string
    agent_name?: string
    framework?: string
    task_description?: string
    expected_output?: string
    use_llm: boolean
  }) => request<IngestResult>('/ingest/paste', { method: 'POST', body: JSON.stringify(body) }),
  upload: (form: FormData) => request<IngestResult>('/ingest/upload', { method: 'POST', body: form }),
  demo: (demo_id: string, use_llm: boolean) =>
    request<IngestResult>('/ingest/demo', { method: 'POST', body: JSON.stringify({ demo_id, use_llm }) }),
  executions: (params: Record<string, string | number | boolean | undefined>) => {
    const search = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== '') search.set(k, String(v))
    })
    return request<{ items: ExecutionSummary[]; total: number }>(`/executions?${search.toString()}`)
  },
  execution: (id: number) =>
    request<{ execution: ExecutionDetail; analyses: Analysis[]; timeline: MergedTimeline; observations: Observation[] }>(
      `/executions/${id}`,
    ),
  deleteExecution: (id: number) => request<{ deleted: number }>(`/executions/${id}`, { method: 'DELETE' }),
  reanalyze: (id: number, use_llm: boolean) =>
    request<{ analysis: Analysis }>(`/executions/${id}/analyze`, { method: 'POST', body: JSON.stringify({ use_llm }) }),
  dashboard: (includeDemo = false) => request<Dashboard>(`/dashboard?include_demo=${includeDemo}`),
  analytics: (includeDemo = false) => request<Analytics>(`/analytics?include_demo=${includeDemo}`),
  compare: (ids: number[]) => request<CompareResult>(`/compare?ids=${ids.join(',')}`),
  settings: () => request<AppSettings>('/settings'),
  updateSettings: (body: Record<string, unknown>) => request<{ updated: Record<string, unknown> }>('/settings', { method: 'PUT', body: JSON.stringify(body) }),
  mlMetrics: () => request<MlMetrics>('/ml/metrics'),
  llmHealth: (refresh = false) => request<LlmHealth>(`/llm/health${refresh ? '?refresh=true' : ''}`),
  integrations: () => request<{ native: string[]; documented: { framework: string; workflow: string }[] }>('/integrations'),
  reportUrl: (analysisId: number, kind: 'json' | 'pdf') => `${API_BASE}/reports/${analysisId}.${kind}`,
}

export type CompareItem = {
  id: number
  execution_id: string
  agent_name: string
  status: ExecStatus
  severity: Severity | null
  duration_ms: number | null
  step_count: number
  tool_call_count: number
  categories: string[]
  root_cause: string | null
  confidence: number | null
  tools: string[]
}

export type DiffCell = {
  present: boolean
  kind: string | null
  tool: string | null
  status: string | null
  duration_ms: number | null
  tokens: number | null
  args: string | null
  error: string | null
  summary: string | null
  label: string
}

export type DiffRow = {
  number: number
  cells: DiffCell[]
  differing_fields: string[]
  is_divergence: boolean
}

export type CompareDiff = {
  runs: {
    id: number | null
    name: string
    execution_id: string | null
    status: string | null
    step_count: number
    duration_ms: number | null
    tool_calls: number | null
    categories: string[]
    root_cause: string | null
    agent_name?: string | null
    outcome?: string
  }[]
  rows: DiffRow[]
  first_divergence: DiffRow | null
  identical: boolean
  divergence_count: number
  total_steps: number
  summary: string
  summary_source: 'rules' | 'llm'
  llm_note: string | null
  /** False when the runs answer different questions: the banner explains why. */
  comparable: boolean
  comparison_banner: string | null
  comparison_caveat: string | null
  outcome_difference: string | null
  outcomes: string[]
  same_agent: boolean
  same_task: boolean
}

export type CompareResult = {
  items: CompareItem[]
  shared_categories: string[]
  summary: Record<string, number>
  comparison: CompareDiff
}

export type MlMetrics = {
  model: string
  dataset: { examples: number; classes: number; source: string }
  holdout: {
    test_size: number
    samples: number
    accuracy: number
    macro_f1: number
    weighted_f1: number
    per_class: Record<string, { precision: number; recall: number; 'f1-score': number }>
    confusion_matrix: { labels: string[]; matrix: number[][] }
  }
  cross_validation: { folds: number; accuracy: number; macro_f1: number }
  caveat: string
}
