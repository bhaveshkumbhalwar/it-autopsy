import { useEffect, useState } from 'react'
import { describeApiError } from '../lib/api'
import { Link, useSearchParams } from 'react-router-dom'
import { GitCompare } from 'lucide-react'
import { api, type CompareItem, type ExecutionSummary } from '../lib/api'
import { Badge, Button, Card, EmptyState, ErrorState, Skeleton } from '../components/ui'
import { categoryLabel, formatMs, formatPercent, SEVERITY_STYLES, STATUS_STYLES } from '../lib/format'
import { useIncludeDemo } from '../lib/demo'

export default function ComparePage() {
  const [params, setParams] = useSearchParams()
  const initial = (params.get('ids') ?? '')
    .split(',')
    .map((v) => Number(v))
    .filter((v) => Number.isFinite(v) && v > 0)
  const [selected, setSelected] = useState<number[]>(initial)
  const [options, setOptions] = useState<ExecutionSummary[]>([])
  const [result, setResult] = useState<{ items: CompareItem[]; shared_categories: string[]; summary: Record<string, number> } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [includeDemo] = useIncludeDemo()

  useEffect(() => {
    api
      .executions({ limit: 100, include_demo: includeDemo })
      .then((res) => setOptions(res.items))
      .catch(() => setOptions([]))
  }, [includeDemo])

  const run = () => {
    if (selected.length < 2) return
    setParams({ ids: selected.join(',') })
    setLoading(true)
    setError(null)
    api
      .compare(selected)
      .then(setResult)
      .catch((e) => setError(describeApiError(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (initial.length >= 2) run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const toggle = (id: number) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : prev.length < 6 ? [...prev, id] : prev))

  const rows: { label: string; render: (item: CompareItem) => React.ReactNode }[] = [
    { label: 'Agent', render: (i) => <span className="font-medium text-app">{i.agent_name}</span> },
    { label: 'Execution ID', render: (i) => <span className="font-mono text-[11px] text-muted-app">{i.execution_id}</span> },
    { label: 'Status', render: (i) => <Badge className={STATUS_STYLES[i.status]}>{i.status}</Badge> },
    { label: 'Severity', render: (i) => (i.severity ? <Badge className={SEVERITY_STYLES[i.severity]}>{i.severity}</Badge> : '—') },
    { label: 'Duration', render: (i) => formatMs(i.duration_ms) },
    { label: 'Steps', render: (i) => i.step_count },
    { label: 'Tool calls', render: (i) => i.tool_call_count },
    { label: 'Categories', render: (i) => (i.categories.length ? i.categories.map(categoryLabel).join(', ') : 'none') },
    { label: 'Confidence', render: (i) => (i.confidence !== null ? formatPercent(i.confidence) : '—') },
    { label: 'Root cause', render: (i) => <span className="text-[11.5px]">{i.root_cause ?? '—'}</span> },
    { label: 'Tools used', render: (i) => (i.tools.length ? i.tools.join(', ') : '—') },
  ]

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight text-app">Compare Executions</h1>
        <p className="mt-0.5 text-[13px] text-muted-app">Pick two or more executions of the same (or different) agents to compare behaviour.</p>
      </div>

      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="h-9 min-w-[260px] flex-1 rounded-lg border border-app surface px-2.5 text-[13px] text-app"
            value=""
            onChange={(e) => e.target.value && toggle(Number(e.target.value))}
          >
            <option value="">Add an execution…</option>
            {options
              .filter((o) => !selected.includes(o.id))
              .map((o) => (
                <option key={o.id} value={o.id}>
                  {o.agent_name} · {o.execution_id} · {o.status}
                </option>
              ))}
          </select>
          <Button variant="primary" onClick={run} disabled={selected.length < 2} loading={loading}>
            <GitCompare className="h-3.5 w-3.5" /> Compare {selected.length >= 2 ? `(${selected.length})` : ''}
          </Button>
        </div>
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {selected.map((id) => {
            const item = options.find((o) => o.id === id)
            return (
              <button
                key={id}
                onClick={() => toggle(id)}
                className="flex items-center gap-1.5 rounded-full border border-app px-2.5 py-1 text-[11.5px] text-app hover:border-rose-400"
                title="Remove"
              >
                {item ? `${item.agent_name} · ${item.execution_id}` : `#${id}`} ✕
              </button>
            )
          })}
        </div>
      </Card>

      {error && <ErrorState message={error} />}

      {loading && <Skeleton className="h-64 w-full" />}

      {!loading && !result && selected.length < 2 && (
        <EmptyState
          icon={<GitCompare className="h-5 w-5" />}
          title="Select at least two executions"
          description="You can also select executions from the Analysis History page."
          action={
            <Link to="/history">
              <Button>Go to history</Button>
            </Link>
          }
        />
      )}

      {result && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              { label: 'Success rate', value: formatPercent(result.summary.success_rate) },
              { label: 'Avg duration', value: formatMs(result.summary.avg_duration_ms) },
              { label: 'Avg tool calls', value: result.summary.avg_tool_calls },
              { label: 'Shared causes', value: result.shared_categories.length },
            ].map((s) => (
              <Card key={s.label} className="p-3">
                <p className="text-[11.5px] text-muted-app">{s.label}</p>
                <p className="mt-0.5 text-[19px] font-semibold text-app">{s.value}</p>
              </Card>
            ))}
          </div>

          <Card className="overflow-x-auto p-0">
            <table className="w-full min-w-[640px] text-left text-[12.5px]">
              <thead>
                <tr className="border-b border-app text-[11.5px] text-muted-app">
                  <th className="px-3 py-2.5">Metric</th>
                  {result.items.map((item) => (
                    <th key={item.id} className="px-3 py-2.5">
                      <Link to={`/executions/${item.id}`} className="text-brand-600 dark:text-brand-400 hover:underline">
                        #{item.id}
                      </Link>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.label} className="border-b border-app last:border-0">
                    <td className="px-3 py-2 text-muted-app">{row.label}</td>
                    {result.items.map((item) => (
                      <td key={item.id} className="px-3 py-2 text-app">
                        {row.render(item)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

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
