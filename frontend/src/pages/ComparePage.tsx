import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import clsx from 'clsx'
import { AlertTriangle, Check, GitCompare, Search, X } from 'lucide-react'
import { api, describeApiError } from '../lib/api'
import type { CompareDiff, CompareResult, ExecutionSummary } from '../lib/api'
import { Badge, Button, Card, EmptyState, ErrorState, SectionTitle, Skeleton } from '../components/ui'
import { categoryLabel, formatDateTime, formatMs, STATUS_STYLES } from '../lib/format'
import { DemoToggle } from '../components/DemoToggle'
import { useIncludeDemo } from '../lib/demo'

const MAX_RUNS = 3

/** "3 minutes ago" — the picker is about picking, so relative time reads faster. */
function relativeTime(iso: string | null): string {
  if (!iso) return 'unknown time'
  const then = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`).getTime()
  if (Number.isNaN(then)) return 'unknown time'
  const seconds = Math.round((Date.now() - then) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days} day${days === 1 ? '' : 's'} ago`
  const months = Math.round(days / 30)
  return months < 12 ? `${months} month${months === 1 ? '' : 's'} ago` : `${Math.round(months / 12)} year(s) ago`
}

export default function ComparePage() {
  const [params, setParams] = useSearchParams()
  const initial = (params.get('ids') ?? '')
    .split(',')
    .map((v) => Number(v))
    .filter((v) => Number.isFinite(v) && v > 0)
    .slice(0, MAX_RUNS)

  const [selected, setSelected] = useState<number[]>(initial)
  const [options, setOptions] = useState<ExecutionSummary[]>([])
  const [result, setResult] = useState<CompareResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [includeDemo] = useIncludeDemo()

  useEffect(() => {
    api
      .executions({ limit: 200, include_demo: includeDemo })
      .then((res) => setOptions(res.items))
      .catch(() => setOptions([]))
  }, [includeDemo])

  const run = (ids: number[]) => {
    if (ids.length < 2) return
    setParams({ ids: ids.join(',') })
    setLoading(true)
    setError(null)
    api
      .compare(ids)
      .then(setResult)
      .catch((e) => setError(describeApiError(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (initial.length >= 2) run(initial)
    // The URL is the source of truth on first paint only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const toggle = (id: number) => {
    const next = selected.includes(id)
      ? selected.filter((x) => x !== id)
      : selected.length < MAX_RUNS
        ? [...selected, id]
        : selected
    setSelected(next)
    if (next.length >= 2) run(next)
    else setResult(null)
  }

  const selectedItems = selected.map((id) => options.find((o) => o.id === id))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight text-app">Compare Executions</h1>
          <p className="mt-0.5 text-[13px] text-muted-app">
            Pick two or three runs. Their steps are lined up by step number, and the first step that diverges tells you why
            they behaved differently.
          </p>
        </div>
        <DemoToggle />
      </div>

      <ExecutionPicker
        options={options}
        selected={selected}
        selectedItems={selectedItems}
        onToggle={toggle}
        onClear={() => {
          setSelected([])
          setResult(null)
          setParams({})
        }}
        includeDemo={includeDemo}
      />

      {error && <ErrorState message={error} />}
      {loading && !result && <Skeleton className="h-64 w-full" />}

      {!loading && !result && selected.length < 2 && (
        <EmptyState
          icon={<GitCompare className="h-5 w-5" />}
          title="Select at least two executions"
          description="Search above, or tick executions in the history list."
          action={
            <Link to="/history">
              <Button>Go to history</Button>
            </Link>
          }
        />
      )}

      {result && (
        <>
          <SummaryCard result={result} loading={loading} />
          <DiffTable diff={result.comparison} />
          {result.shared_categories.length > 0 && (
            <Card className="p-4">
              <p className="text-[13px] font-semibold text-app">Repeated root causes</p>
              <p className="mt-1 text-[12.5px] text-muted-app">
                These categories appear in every selected execution:{' '}
                {result.shared_categories.map((c) => categoryLabel(c)).join(', ')}. Fixing them once should improve all of
                them.
              </p>
            </Card>
          )}
        </>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ picker */

function ExecutionPicker({
  options,
  selected,
  selectedItems,
  onToggle,
  onClear,
  includeDemo,
}: {
  options: ExecutionSummary[]
  selected: number[]
  selectedItems: (ExecutionSummary | undefined)[]
  onToggle: (id: number) => void
  onClear: () => void
  includeDemo: boolean
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)

  const matches = useMemo(() => {
    const term = query.trim().toLowerCase()
    return options
      .filter((option) => (term ? `${option.agent_name} ${option.execution_id} ${option.status}`.toLowerCase().includes(term) : true))
      .slice(0, 40)
  }, [options, query])

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[260px] flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted-app" />
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setOpen(true)
            }}
            onFocus={() => setOpen(true)}
            placeholder={includeDemo ? 'Search by agent, execution id or status…' : 'Search executions (demo traces hidden)…'}
            className="h-9 w-full rounded-lg border border-app surface pr-2.5 pl-8 text-[13px] text-app placeholder:text-muted-app"
          />
        </div>
        <Button variant="primary" onClick={() => selected.length >= 2 && onToggle(selected[0])} disabled={selected.length < 2} className="hidden">
          {/* Kept for alignment; comparison re-runs as you pick. */}
          <GitCompare className="h-3.5 w-3.5" /> Compare
        </Button>
        {selected.length > 0 && (
          <Button size="sm" variant="ghost" onClick={onClear}>
            <X className="h-3 w-3" /> Clear
          </Button>
        )}
      </div>

      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {selectedItems.map((item, index) =>
          item ? (
            <button
              key={item.id}
              onClick={() => onToggle(item.id)}
              className="flex items-center gap-1.5 rounded-full border border-app px-2.5 py-1 text-[11.5px] text-app hover:border-rose-400"
              title="Remove from comparison"
            >
              <span className="font-mono text-[10px] text-muted-app">{index + 1}</span>
              {item.agent_name} · {item.execution_id}
              <X className="h-3 w-3" />
            </button>
          ) : (
            <span key={`missing-${index}`} className="rounded-full border border-app px-2.5 py-1 text-[11.5px] text-muted-app">
              #{selected[index]}
            </span>
          ),
        )}
        {selected.length > 0 && selected.length < 2 && (
          <span className="self-center text-[11.5px] text-muted-app">Pick one more to compare.</span>
        )}
        {selected.length >= MAX_RUNS && <span className="self-center text-[11.5px] text-muted-app">Three runs is the maximum.</span>}
      </div>

      {(open || query.length > 0) && (
        <div className="mt-2 max-h-[280px] overflow-auto rounded-lg border border-app">
          {matches.length === 0 && <p className="p-3 text-[12px] text-muted-app">No execution matches “{query}”.</p>}
          {matches.map((option) => {
            const isSelected = selected.includes(option.id)
            return (
              <button
                key={option.id}
                onClick={() => onToggle(option.id)}
                className={clsx(
                  'flex w-full flex-wrap items-center gap-2 border-b border-app px-3 py-2 text-left last:border-0 hover:bg-[var(--surface-muted)]',
                  isSelected && 'bg-brand-500/5',
                )}
              >
                <span
                  className={clsx(
                    'flex h-4 w-4 shrink-0 items-center justify-center rounded border',
                    isSelected ? 'border-brand-500 bg-brand-500 text-white' : 'border-app',
                  )}
                >
                  {isSelected && <Check className="h-3 w-3" />}
                </span>
                <span className="text-[12.5px] font-medium text-app">{option.agent_name}</span>
                <span className="font-mono text-[11px] text-muted-app">{option.execution_id}</span>
                <Badge className={STATUS_STYLES[option.status]}>{option.status}</Badge>
                {option.primary_category && <span className="text-[11px] text-muted-app">{categoryLabel(option.primary_category)}</span>}
                <span className="ml-auto text-[11px] text-muted-app" title={formatDateTime(option.created_at)}>
                  {relativeTime(option.created_at)}
                </span>
              </button>
            )
          })}
        </div>
      )}
    </Card>
  )
}

/* ----------------------------------------------------------------- summary */

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? '' : 's'}`
}

function SummaryCard({ result, loading }: { result: CompareResult; loading: boolean }) {
  const c = result.comparison
  return (
    <Card className={clsx('border-l-4 p-4', c.identical ? 'border-l-emerald-500' : 'border-l-amber-500')}>
      <p className="flex items-center gap-1.5 text-[11.5px] font-semibold tracking-wide text-muted-app uppercase">
        <GitCompare className="h-3.5 w-3.5" /> Why these runs differed
      </p>
      {c.comparison_banner && (
        <div className="mt-2 flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
          <div>
            <p className="text-[12px] font-medium text-app">{c.comparison_banner}</p>
            {c.comparison_caveat && <p className="text-[11px] text-muted-app">{c.comparison_caveat}</p>}
          </div>
        </div>
      )}
      <p className="mt-1.5 text-[14px] leading-relaxed text-app">{c.summary}</p>
      {c.llm_note && (
        <p className="mt-1.5 text-[12.5px] text-muted-app">
          <span className="font-medium text-app">Model note: </span>
          {c.llm_note}
        </p>
      )}
      <p className="mt-1 text-[10.5px] text-muted-app">
        {c.summary_source === 'rules' ? 'Deterministic comparison of the traces' : 'Model-assisted'}
        {c.identical ? '' : ` · ${c.divergence_count} differing step${c.divergence_count === 1 ? '' : 's'}`}
        {loading ? ' · updating…' : ''}
      </p>

      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {c.runs.map((run, i) => (
          <div key={run.id ?? i} className="rounded-lg border border-app p-2.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-[10px] text-muted-app">#{i + 1}</span>
              <Link to={`/executions/${run.id}`} className="text-[12.5px] font-medium text-app hover:underline">
                {run.name}
              </Link>
              <Badge className={STATUS_STYLES[run.status ?? 'unknown'] ?? STATUS_STYLES.unknown}>{run.status}</Badge>
            </div>
            {run.root_cause && <p className="mt-1 line-clamp-2 text-[11.5px] text-muted-app">{run.root_cause}</p>}
            <p className="mt-1 flex flex-wrap gap-x-2 text-[11px] text-muted-app">
              <span>{plural(run.step_count, 'step')}</span>
              <span>{formatMs(run.duration_ms)}</span>
              <span>{plural(run.tool_calls ?? 0, 'tool call')}</span>
            </p>
          </div>
        ))}
      </div>
    </Card>
  )
}

/* -------------------------------------------------------------- diff table */

const FIELD_LABEL: Record<string, string> = {
  kind: 'type',
  tool: 'tool',
  status: 'status',
  duration_ms: 'latency',
  tokens: 'tokens',
  args: 'args',
}

function DiffTable({ diff }: { diff: CompareDiff }) {
  const [onlyDifferences, setOnlyDifferences] = useState(false)
  const rows = onlyDifferences ? diff.rows.filter((row) => row.is_divergence) : diff.rows
  const columns = `minmax(64px, 0.5fr) repeat(${diff.runs.length}, minmax(180px, 1fr))`

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SectionTitle
          title="Aligned timelines"
          description={
            diff.identical
              ? 'No differences found — every aligned step matches'
              : `Aligned by step number · first divergence highlighted at step ${diff.first_divergence?.number ?? '—'}`
          }
        />
        <Button size="sm" variant={onlyDifferences ? 'primary' : 'ghost'} onClick={() => setOnlyDifferences((v) => !v)}>
          {onlyDifferences ? 'Show every step' : `Only differences (${diff.divergence_count})`}
        </Button>
      </div>

      <div className="mt-3 overflow-x-auto">
        <div className="min-w-[560px]">
          <div className="grid items-end gap-2 border-b border-app pb-1.5" style={{ gridTemplateColumns: columns }}>
            <span className="text-[10.5px] font-medium tracking-wide text-muted-app uppercase">Step</span>
            {diff.runs.map((run, i) => (
              <span key={run.id ?? i} className="flex flex-wrap items-center gap-1.5 text-[11.5px] font-medium text-app">
                <span className="font-mono text-[10px] text-muted-app">#{i + 1}</span>
                {run.name}
                <Badge className={STATUS_STYLES[run.status ?? 'unknown'] ?? STATUS_STYLES.unknown}>{run.status}</Badge>
              </span>
            ))}
          </div>

          {rows.map((row) => {
            const isFirst = diff.first_divergence?.number === row.number
            return (
              <div
                key={row.number}
                className={clsx(
                  'grid gap-2 border-b border-app py-1.5 last:border-0',
                  isFirst && 'rounded-md bg-amber-500/10 ring-1 ring-amber-500/40',
                )}
                style={{ gridTemplateColumns: columns }}
              >
                <span className="flex items-center gap-1 text-[11.5px] text-muted-app">
                  {row.number}
                  {isFirst && <Badge tone="warning">diverge</Badge>}
                </span>
                {row.cells.map((cell, i) => (
                  <div key={i} className="min-w-0">
                    {!cell.present ? (
                      <span className="text-[11.5px] text-muted-app italic">no step</span>
                    ) : (
                      <>
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-[11.5px] font-medium text-app">{cell.label}</span>
                          <Badge className={STATUS_STYLES[cell.status ?? 'unknown'] ?? STATUS_STYLES.unknown}>{cell.status}</Badge>
                          {cell.duration_ms !== null && (
                            <span
                              className={clsx(
                                'text-[10.5px]',
                                row.differing_fields.includes('duration_ms') ? 'font-medium text-amber-600 dark:text-amber-400' : 'text-muted-app',
                              )}
                            >
                              {formatMs(cell.duration_ms)}
                            </span>
                          )}
                          {cell.tokens !== null && (
                            <span
                              className={clsx(
                                'text-[10.5px]',
                                row.differing_fields.includes('tokens') ? 'font-medium text-amber-600 dark:text-amber-400' : 'text-muted-app',
                              )}
                            >
                              {cell.tokens} tok
                            </span>
                          )}
                        </div>
                        {cell.args && (
                          <p
                            className={clsx(
                              'truncate font-mono text-[10.5px]',
                              row.differing_fields.includes('args') ? 'text-amber-600 dark:text-amber-400' : 'text-muted-app',
                            )}
                            title={cell.args}
                          >
                            {cell.args}
                          </p>
                        )}
                        {cell.error && <p className="line-clamp-2 font-mono text-[10.5px] break-words text-rose-600 dark:text-rose-400">{cell.error}</p>}
                      </>
                    )}
                  </div>
                ))}
              </div>
            )
          })}

          {rows.length === 0 && (
            <p className="py-4 text-center text-[12px] text-muted-app">
              {diff.identical ? 'These runs are identical step for step.' : 'No differing steps.'}
            </p>
          )}
        </div>
      </div>

      {!diff.identical && diff.first_divergence && (
        <div className="mt-3 rounded-lg border border-app p-2.5 text-[11.5px] text-muted-app">
          <span className="font-medium text-app">First divergence — step {diff.first_divergence.number}.</span>{' '}
          {diff.first_divergence.differing_fields.map((field) => FIELD_LABEL[field] ?? field).join(', ')} differ here.
        </div>
      )}
    </Card>
  )
}
