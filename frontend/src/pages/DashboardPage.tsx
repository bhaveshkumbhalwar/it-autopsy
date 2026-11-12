import { useEffect, useState } from 'react'
import { describeApiError } from '../lib/api'
import { Link, useNavigate } from 'react-router-dom'
import {
  Activity,
  AlertOctagon,
  ArrowRight,
  CheckCircle2,
  Clock,
  FileSearch,
  Gauge,
  Info,
  Loader2,
  Play,
  Sparkles,
  XCircle,
} from 'lucide-react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, type Dashboard } from '../lib/api'
import { Badge, Button, Card, EmptyState, ErrorState, Skeleton, SectionTitle } from '../components/ui'
import { formatMs, relativeTime, SEVERITY_COLORS, STATUS_STYLES } from '../lib/format'
import { useIncludeDemo } from '../lib/demo'
import { DemoToggle } from '../components/DemoToggle'

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [demoLoading, setDemoLoading] = useState<string | null>(null)
  const navigate = useNavigate()

  const [includeDemo] = useIncludeDemo()

  const load = () => {
    setLoading(true)
    setError(null)
    api
      .dashboard(includeDemo)
      .then(setData)
      .catch((e) => setError(describeApiError(e)))
      .finally(() => setLoading(false))
  }

  useEffect(load, [includeDemo])

  const runDemo = async (demoId: string) => {
    setDemoLoading(demoId)
    try {
      const result = await api.demo(demoId, false)
      navigate(`/executions/${result.execution.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Demo failed')
    } finally {
      setDemoLoading(null)
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />

  const totals = data?.totals
  const hasData = (totals?.executions ?? 0) > 0

  const failed = totals?.failed ?? 0
  const healthy = totals?.healthy ?? (totals ? totals.success + totals.recovered + totals.partial : 0)
  const total = totals?.executions ?? 0

  const stats = [
    {
      label: 'Executions analysed',
      value: total,
      icon: FileSearch,
      hint: `${totals?.analyses ?? 0} analyses`,
    },
    { label: 'Agents tracked', value: totals?.agents ?? 0, icon: Activity, hint: 'distinct agent names' },
    {
      // "Completed", not "healthy": a partial run is not a healthy one, it is
      // one that finished. The split in the subtext keeps that visible.
      label: 'Completed (success + recovered + partial)',
      value: healthy,
      icon: CheckCircle2,
      hint: total
        ? `${totals?.success ?? 0} success · ${totals?.recovered ?? 0} recovered · ${totals?.partial ?? 0} partial`
        : 'no runs yet',
      tone: 'text-emerald-600 dark:text-emerald-400',
    },
    {
      label: 'Failed',
      value: failed,
      icon: XCircle,
      // Never a rate out of nothing: with no runs there is no percentage.
      hint: total ? `${failed} of ${total} ingested runs failed` : 'no runs yet',
      tone: 'text-rose-600 dark:text-rose-400',
      info: 'This only covers runs you submitted for analysis, not your production failure rate.',
    },
  ]

  const trend = data?.trend ?? []
  const severityData = Object.entries(data?.severity ?? {}).map(([name, value]) => ({ name, value }))

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight text-app">Dashboard</h1>
          <p className="mt-0.5 text-[13px] text-muted-app">
            Reliability of every agent execution you have ingested — and what is failing most.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <DemoToggle />
          <Link to="/analyze">
            <Button variant="primary">
              <Sparkles className="h-3.5 w-3.5" /> Analyze a trace
            </Button>
          </Link>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {stats.map(({ label, value, icon: Icon, hint, tone, info }) => (
          <Card key={label} className="p-4">
            <div className="flex items-center justify-between gap-1">
              <span className="text-[12px] font-medium text-muted-app">{label}</span>
              {info ? (
                <span title={info} aria-label={info} className="shrink-0 cursor-help">
                  <Info className="h-4 w-4 text-muted-app" />
                </span>
              ) : (
                <Icon className={`h-4 w-4 shrink-0 ${tone ?? 'text-muted-app'}`} />
              )}
            </div>
            {loading ? (
              <Skeleton className="mt-2 h-8 w-16" />
            ) : (
              <p className={`mt-1.5 text-[26px] leading-8 font-semibold tracking-tight ${tone ?? 'text-app'}`}>{value}</p>
            )}
            <p className="text-[11.5px] text-muted-app">{hint}</p>
          </Card>
        ))}
      </div>

      {!hasData && !loading && (
        <Card>
          <EmptyState
            icon={<FileSearch className="h-5 w-5" />}
            title="No executions yet"
            description="Upload or paste an agent trace to get a real failure classification, root cause and recommendations. Or start with a demo trace to see how it works."
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <Link to="/analyze">
                  <Button variant="primary">
                    <Play className="h-3.5 w-3.5" /> Analyze a trace
                  </Button>
                </Link>
                <Button onClick={() => runDemo('tool-timeout')} loading={demoLoading === 'tool-timeout'}>
                  Run the timeout demo
                </Button>
              </div>
            }
          />
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Trend */}
        <Card className="p-4 lg:col-span-2">
          <SectionTitle title="Failure trend" description="Executions per day over the last 14 days" />
          <div className="mt-3 h-[240px]">
            {loading ? (
              <Skeleton className="h-full w-full" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={trend} margin={{ left: -18, right: 8, top: 6 }}>
                  <defs>
                    <linearGradient id="failedGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#f43f5e" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#f43f5e" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="successGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#10b981" stopOpacity={0.3} />
                      <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                  <XAxis
                    dataKey="date"
                    tickFormatter={(v: string) => v.slice(5)}
                    tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--surface)',
                      border: '1px solid var(--border)',
                      borderRadius: 10,
                      fontSize: 12,
                      color: 'var(--text)',
                    }}
                  />
                  <Area type="monotone" dataKey="success" name="Success" stroke="#10b981" strokeWidth={2} fill="url(#successGrad)" />
                  <Area type="monotone" dataKey="failed" name="Failed" stroke="#f43f5e" strokeWidth={2} fill="url(#failedGrad)" />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>

        {/* Severity */}
        <Card className="p-4">
          <SectionTitle title="Severity distribution" description="Across all analysed executions" />
          <div className="mt-2 h-[240px]">
            {loading ? (
              <Skeleton className="h-full w-full" />
            ) : severityData.every((s) => s.value === 0) ? (
              <EmptyState title="No data" description="Analyse a trace to populate this chart." />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={severityData} dataKey="value" nameKey="name" innerRadius={52} outerRadius={78} paddingAngle={2}>
                    {severityData.map((entry) => (
                      <Cell key={entry.name} fill={SEVERITY_COLORS[entry.name as keyof typeof SEVERITY_COLORS] ?? '#94a3b8'} />
                    ))}
                  </Pie>
                  <Legend iconType="circle" wrapperStyle={{ fontSize: 11.5 }} />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--surface)',
                      border: '1px solid var(--border)',
                      borderRadius: 10,
                      fontSize: 12,
                      color: 'var(--text)',
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
      </div>

      <div>
        {/* Recent: full width — the category breakdown lives on Failure Analytics */}
        <Card className="p-4">
          <SectionTitle
            title="Recent analysis history"
            action={
              <Link to="/history" className="flex items-center gap-1 text-[12px] font-medium text-brand-600 dark:text-brand-400 hover:underline">
                View all <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            }
          />
          <div className="mt-3 divide-y divide-[var(--border)]">
            {loading &&
              Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="my-2 h-10 w-full" />)}
            {!loading && (data?.recent ?? []).length === 0 && (
              <EmptyState title="Nothing analysed yet" description="Your analysed traces will be listed here." />
            )}
            {(data?.recent ?? []).map((row) => (
              <Link
                key={row.id}
                to={`/executions/${row.id}`}
                className="flex flex-wrap items-center gap-2 py-2.5 transition-colors hover:bg-slate-500/5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-[13px] font-medium text-app">{row.agent_name}</p>
                    {row.is_demo && <Badge tone="brand">DEMO</Badge>}
                  </div>
                  <p className="truncate font-mono text-[11px] text-muted-app">
                    {row.execution_id} · {row.step_count} steps · {relativeTime(row.created_at)}
                  </p>
                </div>
                <Badge className={STATUS_STYLES[row.status]}>{row.status}</Badge>
                {row.severity && <Badge className={SEVERITY_STYLES_TONE[row.severity]}>{row.severity}</Badge>}
                <span className="hidden text-[11.5px] text-muted-app sm:block">{formatMs(row.duration_ms)}</span>
              </Link>
            ))}
          </div>
        </Card>
      </div>

      {/* Demo quick actions */}
      <Card className="p-4">
        <SectionTitle
          title="Try a demo trace"
          description="Clearly labelled sample executions. The analysis you get is a real analysis of these traces."
          action={
            <Badge tone="neutral">
              <Gauge className="h-3 w-3" /> demo data
            </Badge>
          }
        />
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {[
            { id: 'clean-success', title: 'Clean successful run', icon: CheckCircle2 },
            { id: 'rate-limit-recovered', title: '429 recovered by retry', icon: Activity },
            { id: 'partial-result', title: 'Partial result', icon: FileSearch },
            { id: 'tool-timeout', title: 'Search API timeout', icon: Clock },
            { id: 'reasoning-contradiction', title: 'Answer contradicts tool result', icon: AlertOctagon },
            { id: 'infinite-loop', title: 'Repeated identical tool calls', icon: Loader2 },
          ].map(({ id, title, icon: Icon }) => (
            <button
              key={id}
              onClick={() => runDemo(id)}
              disabled={demoLoading !== null}
              className="flex items-center gap-2.5 rounded-lg border border-app surface px-3 py-2.5 text-left text-[13px] text-app transition-colors hover:border-brand-400 hover:bg-brand-500/5 disabled:opacity-60"
            >
              {demoLoading === id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Icon className="h-4 w-4 text-muted-app" />}
              <span className="flex-1">{title}</span>
              <ArrowRight className="h-3.5 w-3.5 text-muted-app" />
            </button>
          ))}
        </div>
      </Card>
    </div>
  )
}

const SEVERITY_STYLES_TONE = {
  NONE: 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400 border-emerald-500/25',
  LOW: 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-400 border-emerald-500/25',
  MODERATE: 'bg-amber-500/12 text-amber-700 dark:text-amber-400 border-amber-500/25',
  HIGH: 'bg-orange-500/12 text-orange-700 dark:text-orange-400 border-orange-500/25',
  CRITICAL: 'bg-rose-500/12 text-rose-700 dark:text-rose-400 border-rose-500/25',
}
