import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Filter, GitCompare, Search, Trash2, X } from 'lucide-react'
import { api, describeApiError, type ExecutionSummary } from '../lib/api'
import { Badge, Button, Card, EmptyState, ErrorState, Skeleton, inputClass } from '../components/ui'
import { CATEGORY_LABELS, categoryLabel, formatMs, relativeTime, SEVERITY_STYLES, STATUS_STYLES } from '../lib/format'
import { useIncludeDemo } from '../lib/demo'
import { DemoToggle } from '../components/DemoToggle'

export default function HistoryPage() {
  const [items, setItems] = useState<ExecutionSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [category, setCategory] = useState('')
  const [severity, setSeverity] = useState('')
  const [selected, setSelected] = useState<number[]>([])
  const [includeDemo] = useIncludeDemo()
  const navigate = useNavigate()

  const load = () => {
    setLoading(true)
    setError(null)
    api
      .executions({ q, status, category, severity, include_demo: includeDemo, limit: 100 })
      .then((res) => {
        setItems(res.items)
        setTotal(res.total)
      })
      .catch((e) => setError(describeApiError(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    const timer = setTimeout(load, 200)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, status, category, severity, includeDemo])

  const activeFilters = [status, category, severity].filter(Boolean).length

  const toggle = (id: number) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : prev.length < 10 ? [...prev, id] : prev))

  const remove = async (id: number) => {
    if (!confirm('Delete this execution and its analyses?')) return
    await api.deleteExecution(id)
    setSelected((prev) => prev.filter((x) => x !== id))
    load()
  }

  const canCompare = selected.length >= 2
  const categories = useMemo(() => Object.entries(CATEGORY_LABELS), [])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight text-app">Analysis History</h1>
          <p className="mt-0.5 text-[13px] text-muted-app">
            {total} execution{total === 1 ? '' : 's'} analysed · select two or more to compare
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => navigate(`/compare?ids=${selected.join(',')}`)} disabled={!canCompare}>
            <GitCompare className="h-3.5 w-3.5" /> Compare ({selected.length})
          </Button>
          <Link to="/analyze">
            <Button variant="primary">Analyze</Button>
          </Link>
        </div>
      </div>

      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[200px] flex-1">
            <Search className="pointer-events-none absolute top-2.5 left-2.5 h-3.5 w-3.5 text-muted-app" />
            <input
              className={`${inputClass} pl-8`}
              placeholder="Search by agent, execution id or category"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <select className={`${inputClass} w-auto`} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="success">Success</option>
            <option value="failed">Failed</option>
            <option value="recovered">Recovered</option>
            <option value="partial">Partial</option>
            <option value="unknown">Unknown</option>
          </select>
          <select className={`${inputClass} w-auto`} value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {categories.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select className={`${inputClass} w-auto`} value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">All severities</option>
            {['LOW', 'MODERATE', 'HIGH', 'CRITICAL'].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <DemoToggle />
          {activeFilters > 0 && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setStatus('')
                setCategory('')
                setSeverity('')
              }}
            >
              <X className="h-3 w-3" /> Clear
            </Button>
          )}
          <Badge tone="neutral">
            <Filter className="h-3 w-3" /> {activeFilters} filter{activeFilters === 1 ? '' : 's'}
          </Badge>
        </div>
      </Card>

      {error && <ErrorState message={error} onRetry={load} />}

      <Card className="overflow-hidden">
        {loading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-11 w-full" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<Search className="h-5 w-5" />}
            title="No executions match"
            description="Try clearing the filters, or analyse a new trace."
            action={
              <Link to="/analyze">
                <Button variant="primary">Analyze a trace</Button>
              </Link>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left">
              <thead>
                <tr className="border-b border-app text-[11.5px] font-medium text-muted-app">
                  <th className="w-9 px-3 py-2.5"></th>
                  <th className="px-3 py-2.5">Agent</th>
                  <th className="px-3 py-2.5">Execution ID</th>
                  <th className="px-3 py-2.5">Analysed</th>
                  <th className="px-3 py-2.5">Status</th>
                  <th className="px-3 py-2.5">Category</th>
                  <th className="px-3 py-2.5">Severity</th>
                  <th className="px-3 py-2.5">Duration</th>
                  <th className="px-3 py-2.5"></th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.id} className="group border-b border-app last:border-0 hover:bg-slate-500/5">
                    <td className="px-3 py-2.5">
                      <input
                        type="checkbox"
                        checked={selected.includes(row.id)}
                        onChange={() => toggle(row.id)}
                        className="h-3.5 w-3.5 accent-brand-600"
                        aria-label={`Select ${row.execution_id}`}
                      />
                    </td>
                    <td className="px-3 py-2.5">
                      <Link to={`/executions/${row.id}`} className="flex items-center gap-1.5 text-[13px] font-medium text-app hover:text-brand-600">
                        {row.agent_name}
                        {row.is_demo && <Badge tone="brand">DEMO</Badge>}
                      </Link>
                      <span className="text-[11px] text-muted-app">
                        {row.framework ?? row.source_format} · {row.step_count} steps
                      </span>
                    </td>
                    <td className="px-3 py-2.5 font-mono text-[11.5px] text-muted-app">{row.execution_id}</td>
                    <td className="px-3 py-2.5 text-[12px] text-muted-app">{relativeTime(row.created_at)}</td>
                    <td className="px-3 py-2.5">
                      <Badge className={STATUS_STYLES[row.status]}>{row.status}</Badge>
                    </td>
                    <td className="px-3 py-2.5 text-[12px] text-app">{categoryLabel(row.primary_category)}</td>
                    <td className="px-3 py-2.5">
                      {row.severity ? <Badge className={SEVERITY_STYLES[row.severity]}>{row.severity}</Badge> : <span className="text-muted-app">—</span>}
                    </td>
                    <td className="px-3 py-2.5 text-[12px] text-muted-app">{formatMs(row.duration_ms)}</td>
                    <td className="px-3 py-2.5">
                      <button
                        onClick={() => remove(row.id)}
                        className="text-muted-app opacity-0 transition-opacity group-hover:opacity-100 hover:text-rose-500"
                        aria-label="Delete execution"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
