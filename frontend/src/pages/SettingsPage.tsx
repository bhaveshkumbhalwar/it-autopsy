import { useEffect, useState } from 'react'
import { describeApiError } from '../lib/api'
import { Check, Database, KeyRound, Monitor, Moon, Plug, ShieldCheck, Sun } from 'lucide-react'
import { api, type AppSettings, type LlmHealth, type MlMetrics } from '../lib/api'
import { Badge, Button, Card, ErrorState, SectionTitle, Skeleton, Toast, inputClass } from '../components/ui'
import { useTheme } from '../lib/theme'
import { categoryLabel } from '../lib/format'

function healthTone(health: LlmHealth | null): 'success' | 'warning' | 'danger' | 'neutral' {
  if (!health) return 'neutral'
  if (health.state === 'ready') return 'success'
  if (health.state === 'key_missing' || health.state === 'misconfigured') return 'warning'
  return 'danger'
}

function healthLabel(health: LlmHealth | null, checking: boolean): string {
  if (checking) return 'Checking LLM…'
  if (!health) return 'LLM status unknown'
  switch (health.state) {
    case 'ready':
      return 'LLM ready'
    case 'key_missing':
      return 'Key missing'
    case 'key_invalid':
      return 'Key invalid'
    case 'model_unavailable':
      return 'Model unavailable'
    case 'rate_limited':
      return 'Rate limited'
    case 'misconfigured':
      return 'Config mismatch'
    default:
      return 'Provider unreachable'
  }
}

export default function SettingsPage() {
  const { theme, setTheme } = useTheme()
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [metrics, setMetrics] = useState<MlMetrics | null>(null)
  const [integrations, setIntegrations] = useState<{ native: string[]; documented: { framework: string; workflow: string }[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [model, setModel] = useState('')
  const [retention, setRetention] = useState(90)
  const [health, setHealth] = useState<LlmHealth | null>(null)
  const [checking, setChecking] = useState(false)

  const load = () => {
    setError(null)
    api
      .settings()
      .then((s) => {
        setSettings(s)
        setModel(s.ai.model)
        setRetention(s.preferences.retention_days)
      })
      .catch((e) => setError(describeApiError(e)))
    api.mlMetrics().then(setMetrics).catch(() => setMetrics(null))
    api.llmHealth().then(setHealth).catch(() => setHealth(null))
    api.integrations().then(setIntegrations).catch(() => setIntegrations(null))
  }
  useEffect(load, [])

  const save = async (body: Record<string, unknown>) => {
    setSaving(true)
    try {
      await api.updateSettings(body)
      setToast('Preferences saved.')
      setTimeout(() => setToast(null), 2500)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save')
    } finally {
      setSaving(false)
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight text-app">Settings</h1>
        <p className="mt-0.5 text-[13px] text-muted-app">Appearance, AI configuration, retention and security posture.</p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <SectionTitle title="Theme preferences" description="Applies instantly and is stored in this browser" />
          <div className="mt-3 flex gap-2">
            {(
              [
                { id: 'light', label: 'Light', icon: Sun },
                { id: 'dark', label: 'Dark', icon: Moon },
                { id: 'system', label: 'System', icon: Monitor },
              ] as const
            ).map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => setTheme(id)}
                className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-[12.5px] font-medium ${
                  theme === id ? 'border-brand-500 bg-brand-500/10 text-brand-700 dark:text-brand-300' : 'border-app text-muted-app hover:text-app'
                }`}
              >
                <Icon className="h-3.5 w-3.5" /> {label}
                {theme === id && <Check className="h-3 w-3" />}
              </button>
            ))}
          </div>
        </Card>

        <Card className="p-4">
          <SectionTitle title="AI model configuration" description="Keys are read from the environment, never stored here" />
          {!settings ? (
            <Skeleton className="mt-3 h-24 w-full" />
          ) : (
            <div className="mt-3 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={healthTone(health)}>
                  <KeyRound className="h-3 w-3" /> {healthLabel(health, checking)}
                </Badge>
                <Badge tone="neutral">provider: {settings.ai.provider}</Badge>
                <Badge tone="neutral">{settings.ai.model}</Badge>
                <Button
                  size="sm"
                  variant="ghost"
                  loading={checking}
                  onClick={async () => {
                    setChecking(true)
                    try {
                      setHealth(await api.llmHealth(true))
                    } catch {
                      setHealth(null)
                    } finally {
                      setChecking(false)
                    }
                  }}
                >
                  Check now
                </Button>
              </div>
              {health?.message && (
                <p className={health.state === 'ready' ? 'text-[11.5px] text-muted-app' : 'text-[11.5px] text-amber-600 dark:text-amber-400'}>
                  {health.message}
                  {health.latency_ms != null && health.state === 'ready' ? ` (${health.latency_ms} ms)` : ''}
                </p>
              )}
              {settings.ai.problem && <p className="text-[11.5px] text-rose-600 dark:text-rose-400">{settings.ai.problem}</p>}
              <label className="block">
                <span className="mb-1.5 block text-[12px] font-medium text-app">Model</span>
                <input className={inputClass} value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-4o-mini" />
              </label>
              <Button size="sm" onClick={() => save({ llm_model: model })} loading={saving}>
                Save model
              </Button>
              <p className="text-[11.5px] text-muted-app">{settings.ai.note}</p>
            </div>
          )}
        </Card>

        <Card className="p-4">
          <SectionTitle title="Log retention" description="Traces older than this can be purged" />
          {!settings ? (
            <Skeleton className="mt-3 h-16 w-full" />
          ) : (
            <div className="mt-3 flex items-end gap-2">
              <label className="flex-1">
                <span className="mb-1.5 block text-[12px] font-medium text-app">Days</span>
                <input
                  type="number"
                  min={1}
                  max={3650}
                  className={inputClass}
                  value={retention}
                  onChange={(e) => setRetention(Number(e.target.value))}
                />
              </label>
              <Button size="sm" onClick={() => save({ retention_days: retention })} loading={saving}>
                <Database className="h-3 w-3" /> Save
              </Button>
            </div>
          )}
        </Card>

        <Card className="p-4">
          <SectionTitle title="Security" description="Defaults enforced by the backend" />
          {!settings ? (
            <Skeleton className="mt-3 h-28 w-full" />
          ) : (
            <ul className="mt-2.5 space-y-1.5 text-[12px] text-app">
              {[
                `Redaction of secrets & PII: ${settings.security.redaction_enabled ? 'on' : 'off'}`,
                `API token required: ${settings.security.api_token_required ? 'yes' : 'no (development)'}`,
                `Max upload: ${settings.security.max_upload_mb} MB`,
                `Allowed files: ${settings.security.allowed_extensions.join(', ')}`,
                `Rate limit: ${settings.security.rate_limit}`,
                'Uploaded traces are parsed, never executed.',
              ].map((line) => (
                <li key={line} className="flex gap-1.5">
                  <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" /> {line}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card className="p-4">
        <SectionTitle
          title="ML classifier evaluation"
          description="Measured on a held-out split of the labeled corpus — never hardcoded"
        />
        {!metrics ? (
          <Skeleton className="mt-3 h-40 w-full" />
        ) : (
          <div className="mt-3 space-y-3">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                { label: '5-fold CV accuracy', value: `${(metrics.cross_validation.accuracy * 100).toFixed(1)}%` },
                { label: '5-fold CV macro F1', value: metrics.cross_validation.macro_f1.toFixed(3) },
                { label: 'Holdout accuracy', value: `${(metrics.holdout.accuracy * 100).toFixed(1)}%` },
                { label: 'Holdout macro F1', value: metrics.holdout.macro_f1.toFixed(3) },
              ].map((s) => (
                <div key={s.label} className="rounded-lg border border-app p-2.5">
                  <p className="text-[11px] text-muted-app">{s.label}</p>
                  <p className="text-[17px] font-semibold text-app">{s.value}</p>
                </div>
              ))}
            </div>
            <p className="text-[11.5px] text-muted-app">
              {metrics.model} · {metrics.dataset.examples} labeled examples · {metrics.dataset.classes} classes ·{' '}
              {metrics.holdout.samples} held out.
            </p>
            <p className="rounded-lg border border-amber-500/25 bg-amber-500/5 p-2.5 text-[11.5px] text-amber-700 dark:text-amber-400">
              {metrics.caveat}
            </p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-left text-[11.5px]">
                <thead>
                  <tr className="border-b border-app text-muted-app">
                    <th className="py-1.5 pr-3">Category</th>
                    <th className="px-2 py-1.5">Precision</th>
                    <th className="px-2 py-1.5">Recall</th>
                    <th className="px-2 py-1.5">F1</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(metrics.holdout.per_class).map(([label, row]) => (
                    <tr key={label} className="border-b border-app last:border-0">
                      <td className="py-1.5 pr-3 text-app">{categoryLabel(label)}</td>
                      <td className="px-2 py-1.5 text-muted-app">{row.precision.toFixed(2)}</td>
                      <td className="px-2 py-1.5 text-muted-app">{row.recall.toFixed(2)}</td>
                      <td className="px-2 py-1.5 text-muted-app">{row['f1-score'].toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Card>

      <Card className="p-4">
        <SectionTitle title="Framework integrations" description="Native parsers and documented import workflows" />
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {(integrations?.native ?? []).map((n) => (
            <Badge key={n} tone="success">
              <Plug className="h-3 w-3" /> {n}
            </Badge>
          ))}
        </div>
        <div className="mt-3 space-y-2">
          {(integrations?.documented ?? []).map((d) => (
            <div key={d.framework} className="rounded-lg border border-app p-2.5">
              <p className="text-[12.5px] font-medium text-app">{d.framework}</p>
              <p className="mt-0.5 text-[11.5px] text-muted-app">{d.workflow}</p>
            </div>
          ))}
        </div>
      </Card>

      {toast && <Toast kind="success" message={toast} onClose={() => setToast(null)} />}
    </div>
  )
}
