import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Copy,
  Download,
  FileJson,
  Gauge,
  Lightbulb,
  Quote,
  RefreshCw,
  ShieldCheck,
  Target,
  Wrench,
} from 'lucide-react'
import clsx from 'clsx'
import { api, describeApiError } from '../lib/api'
import type {
  Analysis,
  ExecutionDetail,
  MergedTimeline,
  MergedTimelineItem,
  Observation,
  RecommendedFix,
  TimelineEvidence,
} from '../lib/api'
import { Badge, Button, Card, EmptyState, ErrorState, SectionTitle, Skeleton } from '../components/ui'
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

const KIND_ICON: Record<string, string> = {
  task: '◆',
  plan: '▤',
  llm: '✦',
  tool: '⚙',
  retry: '↻',
  error: '!',
  output: '▣',
  other: '·',
}

export default function ExecutionDetailPage() {
  const { id } = useParams()
  const [execution, setExecution] = useState<ExecutionDetail | null>(null)
  const [analyses, setAnalyses] = useState<Analysis[]>([])
  const [timeline, setTimeline] = useState<MergedTimeline | null>(null)
  const [observations, setObservations] = useState<Observation[]>([])
  const [selected, setSelected] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reanalyzing, setReanalyzing] = useState(false)

  const apply = (res: {
    execution: ExecutionDetail
    analyses: Analysis[]
    timeline: MergedTimeline
    observations?: Observation[]
  }) => {
    setExecution(res.execution)
    setAnalyses(res.analyses)
    setTimeline(res.timeline)
    setObservations(res.observations ?? [])
    setSelected(0)
  }

  const load = () => {
    if (!id) return
    setLoading(true)
    setError(null)
    api
      .execution(Number(id))
      .then(apply)
      .catch((e) => setError(describeApiError(e)))
      .finally(() => setLoading(false))
  }

  useEffect(load, [id])

  const reanalyze = async () => {
    if (!id) return
    setReanalyzing(true)
    try {
      await api.reanalyze(Number(id), true)
      // Reload so the merged timeline picks up the new evidence.
      apply(await api.execution(Number(id)))
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
        action={
          <Button variant="primary" onClick={reanalyze} loading={reanalyzing}>
            Analyze now
          </Button>
        }
      />
    )

  const analysis = analyses[selected]
  // The timeline describes the newest analysis; older ones share the steps.
  const items = timeline?.items ?? []
  const fixes = analysis.fixes ?? []
  // "Healthy" is a run the engine found no failure in. A partial run still has
  // a cause behind what was missing, so it keeps the root-cause card.
  const healthy = analysis.status === 'success' || analysis.status === 'recovered'

  return (
    <div className="space-y-4">
      <Link to="/history" className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-muted-app hover:text-app">
        <ArrowLeft className="h-3.5 w-3.5" /> Back to history
      </Link>

      <Header
        execution={execution}
        analysis={analysis}
        analyses={analyses}
        selected={selected}
        onSelect={setSelected}
        reanalyze={reanalyze}
        reanalyzing={reanalyzing}
      />

      {analysis.llm_error && (
        <div className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-3 text-[12.5px] text-amber-700 dark:text-amber-400">
          <AlertTriangle className="mr-1 inline h-3.5 w-3.5" /> LLM analysis was requested but failed:{' '}
          <span className="font-mono break-words">{analysis.llm_error}</span> The rule-based diagnosis below is unaffected.
        </div>
      )}

      {healthy ? (
        <NoFailureCard analysis={analysis} execution={execution} observations={observations} />
      ) : (
        <RootCauseCard analysis={analysis} timeline={timeline} />
      )}

      <div className={healthy ? 'grid gap-4' : 'grid gap-4 lg:grid-cols-5'}>
        <div className={healthy ? '' : 'lg:col-span-3'}>
          <TimelineCard timeline={timeline} items={items} />
        </div>
        {/* A run with no failure gets no recommended fix: there is nothing to fix. */}
        {!healthy && (
          <div className="lg:col-span-2">
            <FixesCard fixes={fixes} />
          </div>
        )}
      </div>

      <HowDiagnosed analysis={analysis} />

      {analysis.prevention.length > 0 && (
        <Card className="p-4">
          <Collapsible
            title="Prevention strategies"
            count={analysis.prevention.length}
            defaultOpen={false}
            icon={<ShieldCheck className="h-3.5 w-3.5 text-emerald-500" />}
          >
            <ul className="space-y-1.5">
              {analysis.prevention.map((line, i) => (
                <li key={i} className="flex gap-1.5 text-[12px] text-app">
                  <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" /> {line}
                </li>
              ))}
            </ul>
          </Collapsible>
        </Card>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ header */

function Header({
  execution,
  analysis,
  analyses,
  selected,
  onSelect,
  reanalyze,
  reanalyzing,
}: {
  execution: ExecutionDetail
  analysis: Analysis
  analyses: Analysis[]
  selected: number
  onSelect: (index: number) => void
  reanalyze: () => void
  reanalyzing: boolean
}) {
  const recoverable =
    analysis.recoverable === 'yes' ? { label: 'yes', tone: 'success' as const } : analysis.recoverable === 'no' ? { label: 'no', tone: 'danger' as const } : { label: 'unknown', tone: 'neutral' as const }

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-[19px] font-semibold tracking-tight text-app">{execution.agent_name}</h1>
            <Badge className={STATUS_STYLES[analysis.status]}>{analysis.status}</Badge>
            <Badge className={SEVERITY_STYLES[analysis.severity]}>{analysis.severity}</Badge>
            {execution.is_demo && <Badge tone="brand">DEMO</Badge>}
          </div>
          <p className="mt-1 font-mono text-[11.5px] break-all text-muted-app">
            {execution.execution_id} · {execution.framework ?? execution.source_format} · {execution.step_count} steps ·{' '}
            {formatMs(execution.duration_ms)} · {analysis.primary_title ?? categoryLabel(analysis.primary_category)}
          </p>
          <p className="mt-0.5 text-[11.5px] text-muted-app">{formatDateTime(execution.created_at)}</p>
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

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-app pt-3 text-[11.5px] text-muted-app">
        <span className="flex items-center gap-1.5">
          <Gauge className="h-3.5 w-3.5" /> Confidence{' '}
          <span className="font-semibold text-app">{formatPercent(analysis.confidence)}</span>
        </span>
        <span className="flex items-center gap-1.5">
          Recoverable{' '}
          <Badge tone={recoverable.tone}>{recoverable.label}</Badge>
        </span>
        <span>Engine v{analysis.engine_version}</span>
        {execution.redactions > 0 && (
          <span className="text-amber-600 dark:text-amber-400">{execution.redactions} redaction(s) applied</span>
        )}
        {analyses.length > 1 && (
          <select
            className="ml-auto rounded-md border border-app surface px-2 py-1 text-[11.5px]"
            value={selected}
            onChange={(e) => onSelect(Number(e.target.value))}
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
  )
}

/* ------------------------------------------------------------- no failure */

function NoFailureCard({
  analysis,
  execution,
  observations,
}: {
  analysis: Analysis
  execution: ExecutionDetail
  observations: Observation[]
}) {
  const retries = execution.retry_count ?? 0
  const recovered = analysis.status === 'recovered' || retries > 0
  const headline = recovered
    ? `Recovered after ${retries || 1} ${retries === 1 ? 'retry' : 'retries'}`
    : 'No failure detected'
  return (
    <Card className="border-l-4 border-l-emerald-500 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-[11.5px] font-semibold tracking-wide text-emerald-600 uppercase dark:text-emerald-400">
          <CheckCircle2 className="h-3.5 w-3.5" /> {headline}
        </span>
        <Badge className={STATUS_STYLES[analysis.status]}>{analysis.status}</Badge>
      </div>
      <p className="mt-2 text-[14px] leading-relaxed text-app">
        {recovered
          ? 'A step failed and a later attempt succeeded, so the run still produced its result. The failed attempt is on the timeline below; nothing here needs fixing.'
          : 'Every step completed and the run produced its result. The engine found no failure category, so there is no root cause and no fix to recommend.'}
      </p>
      {observations.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-medium tracking-wide text-muted-app uppercase">Worth knowing</p>
          <ul className="mt-1.5 space-y-1.5">
            {observations.map((item) => (
              <li key={`${item.kind}-${item.step_numbers.join('-')}`} className="rounded-lg border border-app p-2.5">
                <p className="text-[12.5px] font-medium text-app">{item.title}</p>
                <p className="mt-0.5 text-[12px] text-muted-app">{item.detail}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

/* -------------------------------------------------------------- root cause */

function RootCauseCard({ analysis, timeline }: { analysis: Analysis; timeline: MergedTimeline | null }) {
  const failing = timeline?.failed_step_number ?? (analysis.failed_step_index !== null ? analysis.failed_step_index + 1 : null)
  const failingItem = timeline?.items.find((item) => item.is_root_cause) ?? null
  return (
    <Card className="border-l-4 border-l-rose-500 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-[11.5px] font-semibold tracking-wide text-rose-600 uppercase dark:text-rose-400">
          <Target className="h-3.5 w-3.5" /> Root cause
        </span>
        {analysis.primary_title && <Badge tone="danger">{analysis.primary_title}</Badge>}
      </div>
      <p className="mt-2 text-[14px] leading-relaxed text-app">{analysis.root_cause}</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg border border-app p-2.5">
          <p className="text-[11px] font-medium text-muted-app">Failing step</p>
          <p className="mt-0.5 text-[12.5px] text-app">
            {failing !== null ? `Step ${failing}` : 'Not localised'}
            {failingItem ? ` · ${failingItem.title}` : ''}
          </p>
        </div>
        <div className="rounded-lg border border-app p-2.5">
          <p className="text-[11px] font-medium text-muted-app">Category</p>
          <p className="mt-0.5 text-[12.5px] text-app">{categoryLabel(analysis.primary_category)}</p>
        </div>
        <div className="rounded-lg border border-app p-2.5 sm:col-span-1">
          <p className="text-[11px] font-medium text-muted-app">Impact</p>
          <p className="mt-0.5 text-[12.5px] text-app">{analysis.impact}</p>
        </div>
      </div>
    </Card>
  )
}

/* ---------------------------------------------------------------- timeline */

function TimelineCard({ timeline, items }: { timeline: MergedTimeline | null; items: MergedTimelineItem[] }) {
  const [showAll, setShowAll] = useState(!timeline?.collapsed_by_default)
  const [openKeys, setOpenKeys] = useState<Set<string>>(() => new Set())

  // Reset the expansion when a different execution is rendered.
  useEffect(() => {
    setShowAll(!timeline?.collapsed_by_default)
    setOpenKeys(new Set())
  }, [timeline?.failed_step_index, timeline?.total_steps, timeline?.collapsed_by_default])

  const failingKey = useMemo(() => items.find((item) => item.is_root_cause)?.key, [items])
  const hidden = timeline?.collapsed_by_default ? items.filter((item) => !item.is_failure && item.key !== failingKey) : []

  useEffect(() => {
    // A short trace opens on its failure: nothing to hunt for.
    if (!timeline?.collapsed_by_default && failingKey) setOpenKeys(new Set([failingKey]))
  }, [failingKey, timeline?.collapsed_by_default])

  const visible = showAll ? items : items.filter((item) => item.is_failure || item.key === failingKey)

  const toggle = (key: string) =>
    setOpenKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  return (
    <Card className="p-4">
      <SectionTitle
        title="Timeline"
        description={
          timeline?.collapsed_by_default
            ? `${items.length} steps · the failure path is shown, ${hidden.length} others are collapsed`
            : 'Every step, in order, with the evidence behind the diagnosis'
        }
      />
      <ol className="mt-3">
        {visible.map((item, position) => (
          <TimelineRow
            key={item.key}
            item={item}
            open={openKeys.has(item.key)}
            onToggle={() => toggle(item.key)}
            last={position === visible.length - 1 && (showAll || hidden.length === 0)}
          />
        ))}
        {items.length === 0 && <EmptyState title="No steps recorded" />}
      </ol>
      {timeline?.collapsed_by_default && (
        <div className="mt-2 border-t border-app pt-2">
          <Button size="sm" variant="ghost" onClick={() => setShowAll((v) => !v)}>
            {showAll ? (
              <>
                <ChevronRight className="h-3 w-3" /> Collapse successful steps ({hidden.length})
              </>
            ) : (
              <>
                <ChevronDown className="h-3 w-3" /> Show all {items.length} steps
              </>
            )}
          </Button>
        </div>
      )}
    </Card>
  )
}

function TimelineRow({
  item,
  open,
  onToggle,
  last,
}: {
  item: MergedTimelineItem
  open: boolean
  onToggle: () => void
  last: boolean
}) {
  const hasDetail = Boolean(item.detail.input || item.detail.output || item.detail.error || item.detail.tool_arguments)
  const expandable = hasDetail || item.evidence.length > 0
  return (
    <li className="relative flex gap-3 pb-3 last:pb-0">
      <div className="flex flex-col items-center">
        <span
          className={clsx(
            'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold',
            item.is_root_cause
              ? 'border-rose-500 bg-rose-500 text-white'
              : item.is_failure
                ? 'border-rose-500/60 bg-rose-500/15 text-rose-600 dark:text-rose-400'
                : 'border-app surface text-muted-app',
          )}
          title={item.kind_label}
        >
          {item.number ?? KIND_ICON[item.kind]}
        </span>
        {!last && <span className="mt-1 w-px flex-1 bg-[var(--border)]" />}
      </div>

      <div
        className={clsx(
          'min-w-0 flex-1 rounded-lg border p-2',
          item.is_root_cause ? 'border-rose-500/40 bg-rose-500/5' : 'border-app',
        )}
      >
        <button
          type="button"
          onClick={expandable ? onToggle : undefined}
          className={clsx('flex w-full flex-wrap items-center gap-2 text-left', expandable && 'cursor-pointer')}
          aria-expanded={expandable ? open : undefined}
        >
          {expandable &&
            (open ? (
              <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-app" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-app" />
            ))}
          <span className="text-[10.5px] font-medium tracking-wide text-muted-app uppercase">{item.kind_label}</span>
          <span
            className={clsx(
              'text-[12.5px] font-medium break-words',
              item.is_root_cause ? 'text-rose-600 dark:text-rose-400' : 'text-app',
            )}
          >
            {item.title}
          </span>
          {item.causal_role === 'downstream' && <Badge tone="warning">downstream</Badge>}
          {item.is_root_cause && <Badge tone="danger">root cause</Badge>}
          <Badge className={STATUS_STYLES[item.status] ?? STATUS_STYLES.unknown}>{item.status}</Badge>
          {item.duration_ms !== null && (
            <span className="flex items-center gap-1 text-[11px] text-muted-app">
              <Clock className="h-3 w-3" /> {formatMs(item.duration_ms)}
            </span>
          )}
          {item.evidence.length > 0 && (
            <span className="ml-auto text-[10.5px] text-muted-app">
              {item.evidence.length} evidence row{item.evidence.length === 1 ? '' : 's'}
            </span>
          )}
        </button>

        {!open && item.summary && (
          <p className="mt-1 line-clamp-2 font-mono text-[11px] leading-4 break-words text-muted-app">{item.summary}</p>
        )}

        {open && (
          <div className="mt-2 space-y-2">
            {item.detail.error && (
              <DetailBlock label="Raw error (redacted)" tone="rose">
                {item.detail.error}
              </DetailBlock>
            )}
            {item.detail.input && <DetailBlock label="Raw input (redacted)">{item.detail.input}</DetailBlock>}
            {item.detail.tool_arguments && <DetailBlock label="Tool arguments">{item.detail.tool_arguments}</DetailBlock>}
            {item.detail.output && <DetailBlock label="Raw output (redacted)">{item.detail.output}</DetailBlock>}
            {item.evidence.length > 0 && <EvidenceRows rows={item.evidence} summary={item.evidence_summary} />}
          </div>
        )}
      </div>
    </li>
  )
}

/**
 * A step's evidence, folded into one line. Rows that only repeat the error the
 * step already shows are counted, not quoted; a row that adds something keeps
 * its text, one click away.
 */
function EvidenceRows({ rows, summary }: { rows: TimelineEvidence[]; summary?: string | null }) {
  const [open, setOpen] = useState(false)
  const informative = rows.filter((row) => row.adds_information !== false)
  const label = summary ?? `${rows.length} evidence row${rows.length === 1 ? '' : 's'}`

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 text-left text-[10.5px] text-muted-app hover:text-app"
      >
        {open ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
        <Quote className="h-3 w-3 shrink-0" />
        <span>{label}</span>
        {informative.length === 0 && rows.length > 0 && <span className="italic">— the step text already says this</span>}
      </button>

      {open && (
        <div className="mt-1 space-y-1">
          {rows.map((ev, i) => (
            <div key={i} className="rounded-md border border-app p-2">
              <div className="flex flex-wrap items-center gap-1.5 text-[10.5px] text-muted-app">
                <Badge tone="neutral">{ev.field ?? 'field'}</Badge>
                {ev.role && <Badge tone={ev.role === 'root_cause' ? 'danger' : 'neutral'}>{ev.role.replace('_', ' ')}</Badge>}
                {ev.source && <span className="font-mono">{ev.source}</span>}
                {ev.weight ? <span className="font-mono">+{ev.weight.toFixed(2)}</span> : null}
              </div>
              {ev.adds_information !== false && ev.quote && (
                <p className="mt-1 font-mono text-[11px] leading-4 break-words text-app">{ev.quote}</p>
              )}
              {ev.adds_information === false && ev.quote && (
                <p className="mt-1 truncate font-mono text-[11px] text-muted-app" title={ev.quote}>
                  {ev.quote}
                </p>
              )}
              {ev.why && <p className="mt-1 text-[11px] text-muted-app">{ev.why}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function DetailBlock({ label, children, tone }: { label: string; children: string; tone?: 'rose' }) {
  return (
    <div>
      <p className="mb-1 text-[10.5px] font-medium tracking-wide text-muted-app uppercase">{label}</p>
      <pre
        className={clsx(
          'max-h-52 overflow-auto rounded-md p-2 font-mono text-[11px] leading-4 whitespace-pre-wrap break-words',
          tone === 'rose' ? 'bg-rose-500/8 text-rose-700 dark:text-rose-300' : 'bg-[var(--surface-muted)] text-app',
        )}
      >
        {children}
      </pre>
    </div>
  )
}

/* -------------------------------------------------------------------- fix */

function FixesCard({ fixes }: { fixes: RecommendedFix[] }) {
  return (
    <Card className="p-4">
      <SectionTitle title="Recommended fix" description="Concrete code for this failure — text only, never executed" />
      {fixes.length === 0 && (
        <EmptyState title="No code fix for this category" description="The recommendations above still apply." />
      )}
      <div className="mt-3 space-y-3">
        {fixes.map((fix, i) => (
          <FixBlock key={`${fix.id ?? 'fix'}-${i}`} fix={fix} />
        ))}
      </div>
    </Card>
  )
}

function FixBlock({ fix }: { fix: RecommendedFix }) {
  const [copied, setCopied] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const lines = fix.snippet.code.trimEnd().split('\n')
  const long = lines.length > 26
  const visible = !long || showAll ? lines : lines.slice(0, 26)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(fix.snippet.code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="rounded-lg border border-app">
      {/* Title, why, and the one control a fix has, always in the same corner. */}
      <div className="flex items-start justify-between gap-2 p-2.5 pb-1.5">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <Lightbulb className="h-3.5 w-3.5 shrink-0 text-amber-500" />
            <p className="text-[12.5px] font-semibold text-app">{fix.caption || fix.title}</p>
            {fix.priority && <Badge tone={PRIORITY_TONE[fix.priority] ?? 'neutral'}>{fix.priority}</Badge>}
          </div>
          {fix.why && <p className="mt-0.5 text-[11.5px] break-words text-muted-app">{fix.why}</p>}
          {fix.applies_to.length > 0 && (
            <p className="mt-1 flex flex-wrap items-center gap-1 text-[11px] text-muted-app">
              <Wrench className="h-3 w-3 shrink-0" />
              {fix.applies_to.map((part, i) => (
                <span key={i} className="font-mono">
                  {part}
                  {i < fix.applies_to.length - 1 ? ' ·' : ''}
                </span>
              ))}
            </p>
          )}
        </div>
        <Button size="sm" variant="ghost" onClick={copy} title="Copy this snippet" className="shrink-0">
          {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>

      <p className="px-2.5 text-[11px] text-muted-app">
        <span className="rounded bg-[var(--surface-muted)] px-1.5 py-0.5 font-medium text-app">
          {fix.snippet.note ?? 'Generic Python - adapt to your framework'}
        </span>
      </p>

      {/* No nested scroll: the code block is as tall as the code, and a long
          snippet folds behind "Show code" instead of scrolling inside itself. */}
      <pre className="mx-2.5 mt-1.5 overflow-x-auto rounded-md bg-[var(--surface-muted)] p-2.5 font-mono text-[11px] leading-[1.45] text-app">
        <code>{visible.join('\n')}</code>
      </pre>
      {long && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="mb-2 ml-2.5 flex items-center gap-1 text-[11px] font-medium text-brand-600 hover:underline dark:text-brand-400"
        >
          {showAll ? <ChevronRight className="h-3 w-3 rotate-[-90deg]" /> : <ChevronDown className="h-3 w-3" />}
          {showAll ? 'Collapse code' : `Show code (${lines.length - 26} more lines)`}
        </button>
      )}
      {!long && <div className="h-2" />}
    </div>
  )
}

/* ---------------------------------------------------------- how diagnosed */

function HowDiagnosed({ analysis }: { analysis: Analysis }) {
  const count =
    analysis.detection_notes.length +
    (analysis.confidence_factors?.length ?? 0) +
    (analysis.severity_detail?.factors.length ?? 0) +
    analysis.categories.length
  return (
    <Card className="p-4">
      <Collapsible title="How this was diagnosed" count={count} defaultOpen={false} icon={<Gauge className="h-3.5 w-3.5 text-muted-app" />}>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-3">
            <SubTitle>Rule result</SubTitle>
            <ul className="space-y-1">
              {analysis.categories.map((cat) => (
                <li key={cat.category} className="flex flex-wrap items-center gap-1.5 text-[11.5px]">
                  <Badge className={SEVERITY_STYLES[cat.severity]}>{cat.severity}</Badge>
                  <span className="text-app">{categoryLabel(cat.category)}</span>
                  {cat.causal_role === 'root_cause' && <Badge tone="danger">root cause</Badge>}
                  <span className="ml-auto font-mono text-muted-app">{formatPercent(cat.confidence)}</span>
                </li>
              ))}
            </ul>
            <ul className="space-y-1">
              {analysis.detection_notes.map((note, i) => (
                <li key={i} className="text-[11.5px] text-muted-app">
                  · {note}
                </li>
              ))}
            </ul>

            <SubTitle>Classifier hint</SubTitle>
            {analysis.ml?.prediction ? (
              <p className="text-[11.5px] text-muted-app">
                <span className="font-medium text-app">{categoryLabel(analysis.ml.prediction)}</span> ·{' '}
                {formatPercent(analysis.ml.confidence ?? 0)} ·{' '}
                {analysis.ml.agrees === true
                  ? 'agrees with the rule result'
                  : analysis.ml.agrees === false
                    ? 'differs from the rule result - the rules decide'
                    : 'no rule matched, so this is the only signal'}{' '}
                — a hint only; the diagnosis above does not depend on it.
              </p>
            ) : (
              <p className="text-[11.5px] text-muted-app">No confident classifier hint for this trace.</p>
            )}

            <SubTitle>LLM result</SubTitle>
            <p className="text-[11.5px] text-muted-app">
              {analysis.llm_used
                ? `Used ${analysis.llm_model} to cross-check the rule result.`
                : analysis.llm_error
                  ? 'Requested but failed — the diagnosis is rule-based.'
                  : 'Not used for this analysis.'}
            </p>
          </div>

          <div className="space-y-3">
            <SubTitle>Severity points</SubTitle>
            <div className="space-y-1">
              {(analysis.severity_detail?.factors ?? []).map((f, i) => (
                <div key={i} className="flex items-start justify-between gap-2 text-[11.5px]">
                  <span className="text-muted-app">{f.detail}</span>
                  <span className={clsx('font-mono', f.weight >= 0 ? 'text-rose-500' : 'text-emerald-500')}>
                    {f.weight > 0 ? '+' : ''}
                    {f.weight}
                  </span>
                </div>
              ))}
              <div className="flex items-center justify-between border-t border-app pt-1.5 text-[11.5px] font-medium text-app">
                <span>Severity</span>
                <span className="font-mono">
                  {analysis.severity_detail?.severity ?? analysis.severity}
                  {analysis.severity_detail ? ` · ${analysis.severity_detail.score}` : ''}
                </span>
              </div>
            </div>

            <SubTitle>Confidence points</SubTitle>
            <div className="space-y-1">
              {(analysis.confidence_factors ?? []).map((f, i) => (
                <div key={i} className="flex items-start justify-between gap-2 text-[11.5px]">
                  <span className="text-muted-app">{f.detail}</span>
                  <span className="font-mono text-brand-600 dark:text-brand-300">
                    {f.weight > 0 ? '+' : ''}
                    {f.weight.toFixed(2)}
                  </span>
                </div>
              ))}
              <div className="flex items-center justify-between border-t border-app pt-1.5 text-[11.5px] font-medium text-app">
                <span>Confidence</span>
                <span className="font-mono">{formatPercent(analysis.confidence)}</span>
              </div>
            </div>
            <p className="text-[10.5px] text-muted-app">
              Confidence describes how well the evidence supports the diagnosis; severity describes the impact. They are
              independent.
            </p>
          </div>
        </div>
      </Collapsible>
    </Card>
  )
}

function SubTitle({ children }: { children: React.ReactNode }) {
  return <p className="text-[10.5px] font-semibold tracking-wide text-muted-app uppercase">{children}</p>
}

function Collapsible({
  title,
  count,
  defaultOpen,
  icon,
  children,
}: {
  title: string
  count?: number
  defaultOpen: boolean
  icon?: React.ReactNode
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div>
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 text-left">
        {open ? <ChevronDown className="h-4 w-4 text-muted-app" /> : <ChevronRight className="h-4 w-4 text-muted-app" />}
        {icon}
        <span className="text-[13.5px] font-semibold text-app">{title}</span>
        {typeof count === 'number' && <Badge tone="neutral">{count}</Badge>}
      </button>
      {open && <div className="mt-3">{children}</div>}
    </div>
  )
}
