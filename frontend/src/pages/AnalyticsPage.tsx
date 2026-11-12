import { useEffect, useState } from 'react'
import { describeApiError } from '../lib/api'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Activity, Plug, Wrench } from 'lucide-react'
import { api, type Analytics } from '../lib/api'
import { Badge, Card, EmptyState, ErrorState, SectionTitle, Skeleton } from '../components/ui'
import { CATEGORY_COLORS, formatMs, formatPercent, STATUS_STYLES } from '../lib/format'
import { useIncludeDemo } from '../lib/demo'
import { DemoToggle } from '../components/DemoToggle'

const STATUS_ORDER: { key: keyof Analytics['status_counts']; label: string }[] = [
  { key: 'success', label: 'Success' },
  { key: 'recovered', label: 'Recovered' },
  { key: 'partial', label: 'Partial' },
  { key: 'failed', label: 'Failed' },
]

export default function AnalyticsPage() {
  const [data, setData] = useState<Analytics | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const [includeDemo] = useIncludeDemo()

  const load = () => {
    setLoading(true)
    setError(null)
    api
      .analytics(includeDemo)
      .then(setData)
      .catch((e) => setError(describeApiError(e)))
      .finally(() => setLoading(false))
  }
  useEffect(load, [includeDemo])

  if (error) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight text-app">Failure Analytics</h1>
          <p className="mt-0.5 text-[13px] text-muted-app">Where failures cluster: tools, root causes, frameworks and agent reliability.</p>
        </div>
        <DemoToggle />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <SectionTitle title="Most common root causes" description="Primary category of each analysis" />
          <div className="mt-3 h-[260px]">
            {loading ? (
              <Skeleton className="h-full w-full" />
            ) : (data?.root_causes.length ?? 0) === 0 ? (
              <EmptyState title="No analyses yet" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                {/* Horizontal bars: the category names are long, and a
                    rotated axis tick was both cramped and truncated. */}
                <BarChart data={data?.root_causes} layout="vertical" margin={{ left: 4, right: 16, top: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                  <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} />
                  <YAxis
                    type="category"
                    dataKey="label"
                    width={150}
                    interval={0}
                    tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(value: string) => (value.length > 22 ? `${value.slice(0, 21)}…` : value)}
                  />
                  <Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, fontSize: 12, color: 'var(--text)' }} />
                  <Bar dataKey="count" radius={[0, 5, 5, 0]} barSize={14}>
                    {(data?.root_causes ?? []).map((_, i) => (
                      <Cell key={i} fill={CATEGORY_COLORS[i % CATEGORY_COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>

        <Card className="p-4">
          <SectionTitle title="Frequently failing tools" description="Failure rate per tool across all traces" />
          <div className="mt-2.5">
            {loading && <Skeleton className="h-40 w-full" />}
            {!loading && (data?.failing_tools.length ?? 0) === 0 && <EmptyState icon={<Wrench className="h-5 w-5" />} title="No tool calls recorded yet" />}
            <div className="space-y-2">
              {(data?.failing_tools ?? []).map((tool) => (
                <div
                  key={tool.tool}
                  className={tool.low_sample ? 'opacity-55' : undefined}
                  title={tool.low_sample ? `Based on ${tool.calls} call${tool.calls === 1 ? '' : 's'} — too few to read as a rate.` : undefined}
                >
                  <div className="flex items-center justify-between text-[12px]">
                    <span className="font-mono text-app">{tool.tool}</span>
                    <span className={`flex items-center gap-1.5 ${tool.low_sample ? 'text-muted-app' : 'text-muted-app'}`}>
                      {tool.failures}/{tool.calls} · {formatPercent(tool.failure_rate)}
                      {tool.low_sample && (
                        <span className="rounded border border-[var(--border)] px-1.5 py-px text-[10.5px] text-muted-app">
                          n={tool.calls} (low sample)
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[var(--border)]">
                    <div
                      className={`h-full rounded-full ${tool.low_sample ? 'bg-rose-400/50' : 'bg-rose-500'}`}
                      style={{ width: `${Math.max(4, tool.failure_rate * 100)}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-4 lg:col-span-2">
          <SectionTitle title="Agent reliability" description="Ordered by failure rate" />
          <div className="mt-3 overflow-x-auto">
            {loading ? (
              <Skeleton className="h-32 w-full" />
            ) : (
              <table className="w-full min-w-[560px] text-left">
                <thead>
                  <tr className="border-b border-app text-[11.5px] text-muted-app">
                    <th className="py-2 pr-3">Agent</th>
                    <th className="px-3 py-2">Executions</th>
                    <th className="px-3 py-2">Failure rate</th>
                    <th className="px-3 py-2">Recovery</th>
                    <th className="px-3 py-2">Avg duration</th>
                    <th className="px-3 py-2">Top tools</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.reliability ?? []).map((row) => (
                    <tr key={row.agent_name} className="border-b border-app last:border-0">
                      <td className="py-2 pr-3 text-[12.5px] font-medium text-app">{row.agent_name}</td>
                      <td className="px-3 py-2 text-[12px] text-muted-app">{row.executions}</td>
                      <td className="px-3 py-2">
                        <span className={row.failure_rate > 0.3 ? 'text-rose-600 dark:text-rose-400' : 'text-app'}>{formatPercent(row.failure_rate)}</span>
                      </td>
                      <td className="px-3 py-2 text-[12px] text-muted-app">{formatPercent(row.recovery_rate)}</td>
                      <td className="px-3 py-2 text-[12px] text-muted-app">{formatMs(row.avg_duration_ms)}</td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {row.top_tools.map((t) => (
                            <Badge key={t} tone="neutral">
                              {t}
                            </Badge>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Card>

        <Card className="p-4">
          <SectionTitle title="By framework" description="Import source distribution" />
          <div className="mt-2.5 space-y-2">
            {loading && <Skeleton className="h-24 w-full" />}
            {(data?.frameworks ?? []).map((f) => {
              const total = data?.frameworks.reduce((acc, x) => acc + x.count, 0) || 1
              return (
                <div key={f.framework}>
                  <div className="flex items-center justify-between text-[12px]">
                    <span className="flex items-center gap-1.5 text-app">
                      <Plug className="h-3 w-3 text-muted-app" /> {f.framework}
                    </span>
                    <span className="text-muted-app">{f.count}</span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[var(--border)]">
                    <div className="h-full rounded-full bg-brand-500" style={{ width: `${(f.count / total) * 100}%` }} />
                  </div>
                </div>
              )
            })}
            {!loading && (data?.frameworks.length ?? 0) === 0 && <EmptyState icon={<Activity className="h-5 w-5" />} title="No data yet" />}
          </div>
        </Card>

        <Card className="p-4 lg:col-span-3">
          <SectionTitle
            title="How executions ended"
            description="Failed = the run did not finish. Recovered = it failed and then completed anyway, so the incident counts."
          />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {loading && <Skeleton className="h-8 w-full" />}
            {!loading &&
              STATUS_ORDER.map(({ key, label }) => (
                <Badge key={key} className={STATUS_STYLES[key]}>
                  {label}: {data?.status_counts?.[key] ?? 0}
                </Badge>
              ))}
            {!loading && (data?.status_counts?.recovered ?? 0) > 0 && (
              <span className="text-[11.5px] text-muted-app">
                {data?.status_counts?.recovered} recovered run{(data?.status_counts?.recovered ?? 0) === 1 ? '' : 's'} — failed, then completed
              </span>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}
