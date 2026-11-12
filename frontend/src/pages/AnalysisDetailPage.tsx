import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  Brain,
  ChevronDown,
  ChevronRight,
  Download,
  FileJson,
  Gauge,
  Info,
  Lightbulb,
  Quote,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Target,
} from 'lucide-react'
import clsx from 'clsx'
import { api, type Analysis, type ExecutionDetail } from '../lib/api'
import { Badge, Button, Card, EmptyState, ErrorState, SectionTitle, Skeleton } from '../components/ui'
import { describeApiError } from '../lib/api'
import {
  categoryLabel,
  formatDateTime,
  formatMs,
  formatPercent,
  SEVERITY_STYLES,
  STATUS_STYLES,
} from '../lib/format'

const PRIORITY_TONE: Record<string, 'danger' | 'warning' | 'brand' | 'neutral'> = {
  critical: 'danger',
  high: 'warning',
  medium: 'brand',
  low: 'neutral',
}

export default function AnalysisDetailPage() {
  const { id } = useParams()
  const [execution, setExecution] = useState<ExecutionDetail | null>(null)
  const [analyses, setAnalyses] = useState<Analysis[]>([])
  const [selected, setSelected] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reanalyzing, setReanalyzing] = useState(false)

  const load = () => {
    if (!id) return
    setLoading(true)
    setError(null)
    api
      .execution(Number(id))
      .then((res) => {
        setExecution(res.execution)
        setAnalyses(res.analyses)
        setSelected(0)
      })
      .catch((e) => setError(describeApiError(e)))
      .finally(() => setLoading(false))
  }

  useEffect(load, [id])

  const reanalyze = async () => {
    if (!id) return
    setReanalyzing(true)
    try {
      const res = await api.reanalyze(Number(id), true)
      setAnalyses((prev) => [res.analysis, ...prev])
      setSelected(0)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Re-analysis failed')
    } finally {
      setReanalyzing(false)
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }
  if (error) return <ErrorState message={error} onRetry={load} />
  if (!execution) return <EmptyState title="Execution not found" />
  if (analyses.length === 0)
    return (
      <EmptyState
        title="No analysis for this execution"
        description="Run the engine to produce a diagnosis."
        action={<Button variant="primary" onClick={reanalyze} loading={reanalyzing}>Analyze now</Button>}
      />
    )

  const analysis = analyses[selected]
  const trace = execution.trace as {
    steps?: Record<string, unknown>[]
    errors?: Record<string, unknown>[]
    tool_calls?: Record<string, unknown>[]
  }

  return (
    <div className="space-y-4">
      <Link to="/history" className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-muted-app hover:text-app">
        <ArrowLeft className="h-3.5 w-3.5" /> Back to history
      </Link>

      {/* Header */}
      <Card className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-[19px] font-semibold tracking-tight text-app">{execution.agent_name}</h1>
              {execution.is_demo && <Badge tone="brand">DEMO TRACE</Badge>}
              <Badge className={STATUS_STYLES[analysis.status]}>{analysis.status}</Badge>
              <Badge className={SEVERITY_STYLES[analysis.severity]}>{analysis.severity}</Badge>
            </div>
            <p className="mt-1 font-mono text-[11.5px] text-muted-app">
              {execution.execution_id} · {execution.framework ?? execution.source_format} · {execution.step_count} steps ·{' '}
              {formatMs(execution.duration_ms)} · {formatDateTime(execution.created_at)}
            </p>
            {execution.task_description && (
              <p className="mt-2 max-w-3xl text-[13px] text-app">
                <span className="text-muted-app">Task: </span>
                {execution.task_description}
              </p>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={reanalyze} loading={reanalyzing}>
              <RefreshCw className="h-3.5 w-3.5" /> Re-analyze
            </Button>
            <a href={api.reportUrl(analysis.id, 'pdf')} target="_blank" rel="noreferrer">
              <Button>
                <Download className="h-3.5 w-3.5" /> PDF
              </Button>
            </a>
            <a href={api.reportUrl(analysis.id, 'json')} target="_blank" rel="noreferrer">
              <Button>
                <FileJson className="h-3.5 w-3.5" /> JSON
              </Button>
            </a>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-app pt-3 text-[11.5px] text-muted-app">
          <span className="flex items-center gap-1.5">
            <Gauge className="h-3.5 w-3.5" /> Confidence {formatPercent(analysis.confidence)}
          </span>
          <span className="flex items-center gap-1.5">
            <Brain className="h-3.5 w-3.5" />
            {analysis.llm_used ? `LLM: ${analysis.llm_model}` : 'LLM not used'}
          </span>
          <span>Engine v{analysis.engine_version}</span>
          <span>Recoverable: {analysis.recoverable}</span>
          {execution.redactions > 0 && <span className="text-amber-600 dark:text-amber-400">{execution.redactions} redaction(s) applied</span>}
          {analyses.length > 1 && (
            <select
              className="ml-auto rounded-md border border-app surface px-2 py-1 text-[11.5px]"
              value={selected}
              onChange={(e) => setSelected(Number(e.target.value))}
            >
              {analyses.map((a, i) => (
                <option key={a.id} value={i}>
                  Analysis #{a.id} · {formatDateTime(a.created_at)}
                </option>
              ))}
            </select>
          )}
        </div>
      </Card>

      {analysis.llm_error && (
        <div className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-3 text-[12.5px] text-amber-700 dark:text-amber-400">
          <AlertTriangle className="mr-1 inline h-3.5 w-3.5" /> LLM analysis was requested but failed: {analysis.llm_error}. The
          rule-based diagnosis below is unaffected.
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {/* Summary + root cause */}
          <Card className="p-4">
            <SectionTitle title="Execution summary" description={analysis.what_happened} />
            <div className="mt-4 rounded-xl border border-brand-500/25 bg-brand-500/5 p-3.5">
              <p className="flex items-center gap-1.5 text-[12px] font-semibold text-brand-700 dark:text-brand-300">
                <Target className="h-3.5 w-3.5" /> Root cause
              </p>
              <p className="mt-1.5 text-[13px] leading-relaxed text-app">{analysis.root_cause}</p>
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <div className="rounded-lg border border-app p-3">
                <p className="text-[11px] font-medium text-muted-app">Impact</p>
                <p className="mt-0.5 text-[12.5px] text-app">{analysis.impact}</p>
              </div>
              <div className="rounded-lg border border-app p-3">
                <p className="text-[11px] font-medium text-muted-app">Failed step</p>
                <p className="mt-0.5 text-[12.5px] text-app">
                  {analysis.failed_step_index !== null ? `Step ${analysis.failed_step_index}` : 'Not localised'}
                  {typeof analysis.failed_step?.name === 'string' && analysis.failed_step.name ? ` · ${analysis.failed_step.name}` : ''}
                </p>
              </div>
            </div>
          </Card>

          {(analysis.causal_chain ?? []).length > 0 && (
            <Card className="p-4">
              <SectionTitle
                title="Causal chain"
                description="The earliest failure is the root cause; later failures are consequences of it"
              />
              <ol className="mt-3 space-y-2">
                {(analysis.causal_chain ?? []).map((node) => (
                  <li key={node.step_index} className="flex flex-wrap items-center gap-2 rounded-lg border border-app p-2.5">
                    <Badge tone={node.role === 'root_cause' ? 'danger' : 'neutral'}>
                      {node.role === 'root_cause' ? 'root cause' : 'downstream'}
                    </Badge>
                    <span className="text-[12px] font-medium text-app">
                      step {node.step_index}
                      {node.step_name ? ` · ${node.step_name}` : ''}
                    </span>
                    {node.error_type && <span className="font-mono text-[11px] text-muted-app">{node.error_type}</span>}
                    <span className="w-full font-mono text-[11px] break-words text-muted-app">{node.message}</span>
                    {node.caused_by !== null && node.caused_by !== undefined && (
                      <span className="text-[11px] text-muted-app">explained by step {node.caused_by}</span>
                    )}
                  </li>
                ))}
              </ol>
            </Card>
          )}

          {/* Timeline */}
          <Card className="p-4">
            <SectionTitle title="Execution timeline" description="Chronological steps; the failing step is highlighted" />
            <ol className="mt-3 space-y-0">
              {analysis.timeline.map((item, i) => (
                <li key={item.index} className="relative flex gap-3 pb-3 last:pb-0">
                  <div className="flex flex-col items-center">
                    <span
                      className={clsx(
                        'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] font-semibold',
                        item.is_failure
                          ? 'border-rose-500 bg-rose-500 text-white'
                          : 'border-app surface text-muted-app',
                      )}
                    >
                      {item.index}
                    </span>
                    {i < analysis.timeline.length - 1 && <span className="mt-1 w-px flex-1 bg-[var(--border)]" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className={clsx('text-[12.5px] font-medium', item.is_failure ? 'text-rose-600 dark:text-rose-400' : 'text-app')}>
                        {item.label}
                      </p>
                      <Badge className={STATUS_STYLES[item.status] ?? STATUS_STYLES.unknown}>{item.status}</Badge>
                      {item.duration_ms !== null && <span className="text-[11px] text-muted-app">{formatMs(item.duration_ms)}</span>}
                    </div>
                    {item.detail && (
                      <p className="mt-0.5 line-clamp-3 font-mono text-[11px] leading-4 break-words text-muted-app">{item.detail}</p>
                    )}
                  </div>
                </li>
              ))}
              {analysis.timeline.length === 0 && <EmptyState title="No steps recorded" />}
            </ol>
          </Card>

          {/* Evidence */}
          <Card className="p-4">
            <SectionTitle
              title="Supporting evidence"
              description="Every diagnosis links to the trace event it came from"
            />
            <div className="mt-3 space-y-2">
              {analysis.evidence.map((ev, i) => (
                <div key={i} className="rounded-lg border border-app p-2.5">
                  <div className="flex flex-wrap items-center gap-2 text-[11px]">
                    <Badge tone="neutral">step {ev.step_index ?? '—'}</Badge>
                    <span className="font-mono text-muted-app">{ev.field}</span>
                    <Badge tone={ev.source === 'llm' ? 'brand' : ev.source === 'ml' ? 'warning' : 'success'}>
                      {ev.source ?? 'rule'}
                    </Badge>
                    {ev.error_type && <span className="font-mono text-muted-app">{ev.error_type}</span>}
                    {ev.role && (
                      <Badge tone={ev.role === 'root_cause' ? 'danger' : 'neutral'}>
                        {ev.role.replace('_', ' ')}
                      </Badge>
                    )}
                  </div>
                  <p className="mt-1.5 flex gap-1.5 text-[12px] text-app">
                    <Quote className="mt-0.5 h-3 w-3 shrink-0 text-muted-app" />
                    <span className="font-mono break-words">{ev.quote}</span>
                  </p>
                  {ev.why && <p className="mt-1 text-[11.5px] text-muted-app">{ev.why}</p>}
                </div>
              ))}
              {analysis.evidence.length === 0 && <EmptyState title="No evidence rows" description="Nothing in the trace matched a detection rule." />}
            </div>
          </Card>

          {/* Recommendations */}
          <Card className="p-4">
            <SectionTitle title="Recommended solutions" description="Ordered by priority" />
            <div className="mt-3 space-y-2">
              {analysis.recommendations.map((rec) => (
                <div key={rec.id} className="rounded-lg border border-app p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Lightbulb className="h-3.5 w-3.5 text-amber-500" />
                    <p className="flex-1 text-[13px] font-medium text-app">{rec.fix}</p>
                    <Badge tone={PRIORITY_TONE[rec.priority] ?? 'neutral'}>{rec.priority}</Badge>
                  </div>
                  <dl className="mt-1.5 grid gap-1 text-[11.5px] text-muted-app sm:grid-cols-3">
                    <div>
                      <dt className="font-medium text-app">Problem</dt>
                      <dd>{rec.problem}</dd>
                    </div>
                    <div>
                      <dt className="font-medium text-app">Why</dt>
                      <dd>{rec.reason}</dd>
                    </div>
                    <div>
                      <dt className="font-medium text-app">Benefit</dt>
                      <dd>{rec.benefit}</dd>
                    </div>
                  </dl>
                </div>
              ))}
              {analysis.recommendations.length === 0 && <EmptyState title="No recommendations" />}
            </div>
          </Card>
        </div>

        {/* Sidebar */}
        <div className="space-y-4">
          <Card className="p-4">
            <SectionTitle title="Failure categories" />
            <div className="mt-2.5 space-y-2">
              {analysis.categories.map((cat) => (
                <div key={cat.category} className="rounded-lg border border-app p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-[12.5px] font-medium text-app">{categoryLabel(cat.category)}</p>
                    <Badge className={SEVERITY_STYLES[cat.severity]}>{cat.severity}</Badge>
                  </div>
                  {cat.causal_role && (
                    <p className="mt-0.5 text-[11px] text-muted-app">
                      {cat.causal_role === 'root_cause'
                        ? 'Root cause of this failure'
                        : cat.causal_role === 'downstream'
                          ? 'Downstream — caused by the root cause'
                          : 'Contributing factor'}
                    </p>
                  )}
                  <p className="mt-0.5 text-[11.5px] text-muted-app">{cat.title}</p>
                  <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-[var(--border)]">
                    <div className="h-full rounded-full bg-brand-500" style={{ width: `${Math.round(cat.confidence * 100)}%` }} />
                  </div>
                </div>
              ))}
              {analysis.categories.length === 0 && <p className="text-[12px] text-muted-app">No failure categories detected.</p>}
            </div>
          </Card>

          <Card className="p-4">
            <SectionTitle title="Severity assessment" />
            <div className="mt-2 space-y-1.5">
              {(analysis.severity_detail?.factors ?? []).map((f, i) => (
                <div key={i} className="flex items-start justify-between gap-2 text-[11.5px]">
                  <span className="text-muted-app">{f.detail}</span>
                  <span className={clsx('font-mono', f.weight >= 0 ? 'text-rose-500' : 'text-emerald-500')}>
                    {f.weight > 0 ? '+' : ''}
                    {f.weight}
                  </span>
                </div>
              ))}
              {!analysis.severity_detail && <p className="text-[12px] text-muted-app">Score details were not recorded for this analysis.</p>}
            </div>
          </Card>

          <Card className="p-4">
            <SectionTitle title="Why this confidence" description="Each point is tied to the evidence above" />
            <div className="mt-2 space-y-1.5">
              {(analysis.confidence_factors ?? []).map((f, i) => (
                <div key={i} className="flex items-start justify-between gap-2 text-[11.5px]">
                  <span className="text-muted-app">{f.detail}</span>
                  <span className="font-mono text-brand-600 dark:text-brand-300">
                    {f.weight > 0 ? '+' : ''}
                    {f.weight.toFixed(2)}
                  </span>
                </div>
              ))}
              {(analysis.confidence_factors ?? []).length === 0 && (
                <p className="text-[12px] text-muted-app">No contribution breakdown was recorded for this analysis.</p>
              )}
              <div className="flex items-center justify-between border-t border-app pt-1.5 text-[12px] font-medium text-app">
                <span>Confidence</span>
                <span className="font-mono">{formatPercent(analysis.confidence)}</span>
              </div>
              <p className="text-[11px] text-muted-app">
                Confidence describes how well the evidence supports this diagnosis. Severity above describes the impact — they are
                independent.
              </p>
            </div>
          </Card>

          {analysis.ml && (
            <Card className="p-4">
              <SectionTitle title="ML classifier opinion" description="TF-IDF + calibrated LinearSVC" />
              <p className="mt-2 text-[12.5px] text-app">
                {analysis.ml.prediction ? categoryLabel(analysis.ml.prediction) : 'No confident prediction'}
                {analysis.ml.confidence !== null && (
                  <span className="text-muted-app"> · {formatPercent(analysis.ml.confidence)} confidence</span>
                )}
              </p>
              <div className="mt-1.5 space-y-1">
                {(analysis.ml.top ?? []).slice(0, 3).map(([label, p]) => (
                  <div key={label} className="flex items-center gap-2 text-[11px]">
                    <span className="w-32 truncate text-muted-app">{categoryLabel(label)}</span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--border)]">
                      <div className="h-full rounded-full bg-violet-500" style={{ width: `${Math.round(p * 100)}%` }} />
                    </div>
                    <span className="w-8 text-right text-muted-app">{Math.round(p * 100)}%</span>
                  </div>
                ))}
              </div>
            </Card>
          )}

          <Card className="p-4">
            <SectionTitle title="Prevention strategies" />
            <ul className="mt-2 space-y-1.5">
              {analysis.prevention.map((p, i) => (
                <li key={i} className="flex gap-1.5 text-[12px] text-app">
                  <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" /> {p}
                </li>
              ))}
            </ul>
          </Card>

          <Card className="p-4">
            <SectionTitle title="How this was diagnosed" />
            <ul className="mt-2 space-y-1.5">
              {analysis.detection_notes.map((n, i) => (
                <li key={i} className="flex gap-1.5 text-[11.5px] text-muted-app">
                  <Info className="mt-0.5 h-3 w-3 shrink-0" /> {n}
                </li>
              ))}
              {analysis.confidence_note && (
                <li className="flex gap-1.5 text-[11.5px] text-muted-app">
                  <Info className="mt-0.5 h-3 w-3 shrink-0" /> {analysis.confidence_note}
                </li>
              )}
            </ul>
          </Card>
        </div>
      </div>

      <TraceInspector trace={trace} errors={(trace.errors as Record<string, unknown>[]) ?? []} />
    </div>
  )
}

function TraceInspector({ trace, errors }: { trace: Record<string, unknown>; errors: Record<string, unknown>[] }) {
  const [open, setOpen] = useState(false)
  const [showRaw, setShowRaw] = useState(false)
  const steps = (trace.steps as Record<string, unknown>[]) ?? []
  return (
    <Card className="p-4">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 text-left">
        {open ? <ChevronDown className="h-4 w-4 text-muted-app" /> : <ChevronRight className="h-4 w-4 text-muted-app" />}
        <Bot className="h-4 w-4 text-muted-app" />
        <span className="text-[14px] font-semibold text-app">Normalized trace</span>
        <Badge tone="neutral">{steps.length} steps</Badge>
        <Badge tone="neutral">{(trace.tool_calls as unknown[])?.length ?? 0} tool calls</Badge>
        {errors.length > 0 && <Badge tone="danger">{errors.length} errors</Badge>}
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          <div className="flex justify-end">
            <Button size="sm" variant="ghost" onClick={() => setShowRaw((v) => !v)}>
              <Sparkles className="h-3 w-3" /> {showRaw ? 'Show steps' : 'Show raw JSON'}
            </Button>
          </div>
          {showRaw ? (
            <pre className="max-h-[420px] overflow-auto rounded-lg border border-app bg-[var(--surface-muted)] p-3 font-mono text-[11px] leading-5">
              {JSON.stringify(trace, null, 2)}
            </pre>
          ) : (
            <div className="space-y-1.5">
              {steps.map((step, i) => (
                <div key={i} className="rounded-lg border border-app p-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[11px] text-muted-app">#{String(step.index ?? i)}</span>
                    <Badge tone="neutral">{String(step.type)}</Badge>
                    <span className="text-[12px] font-medium text-app">{String(step.name ?? '')}</span>
                    {step.tool_name ? <Badge tone="brand">{String(step.tool_name)}</Badge> : null}
                    
                    <Badge className={STATUS_STYLES[String(step.status)] ?? STATUS_STYLES.unknown}>{String(step.status)}</Badge>
                    {step.duration_ms ? <span className="text-[11px] text-muted-app">{formatMs(Number(step.duration_ms))}</span> : null}
                  </div>
                  {Boolean(step.error ?? step.output ?? step.tool_result) && (
                    <pre className="mt-1.5 max-h-40 overflow-auto rounded-md bg-[var(--surface-muted)] p-2 font-mono text-[11px] leading-4 whitespace-pre-wrap break-words">
                      {String(step.error ?? step.output ?? step.tool_result).slice(0, 1200)}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
