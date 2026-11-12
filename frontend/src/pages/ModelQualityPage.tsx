import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, FlaskConical } from 'lucide-react'
import { api, describeApiError, type MlMetrics } from '../lib/api'
import { Button, Card, ErrorState, SectionTitle, Skeleton } from '../components/ui'
import { categoryLabel } from '../lib/format'

/**
 * The classifier's evaluation lives here, away from the day-to-day settings:
 * it is a developer view, and nothing on the other pages depends on it.
 */
export default function ModelQualityPage() {
  const [metrics, setMetrics] = useState<MlMetrics | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .mlMetrics()
      .then(setMetrics)
      .catch((e) => setError(describeApiError(e)))
  }, [])

  return (
    <div className="space-y-4">
      <div>
        <Link to="/settings" className="inline-flex items-center gap-1 text-[12px] text-muted-app hover:text-app">
          <ArrowLeft className="h-3 w-3" /> Settings
        </Link>
        <h1 className="mt-1 flex items-center gap-2 text-[22px] font-semibold tracking-tight text-app">
          <FlaskConical className="h-4.5 w-4.5 text-muted-app" /> Model quality
        </h1>
        <p className="mt-0.5 text-[13px] text-muted-app">
          How well the failure classifier does on held-out data. It never decides a diagnosis on its own: the rules do,
          and the classifier's category is shown beside them as a hint with its confidence.
        </p>
      </div>

      {error && <ErrorState message={error} />}

      <Card className="p-4">
        <SectionTitle
          title="Classifier evaluation"
          description="Measured on a held-out split of the labeled corpus - never hardcoded"
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
              ].map((stat) => (
                <div key={stat.label} className="rounded-lg border border-app p-2.5">
                  <p className="text-[11px] text-muted-app">{stat.label}</p>
                  <p className="text-[17px] font-semibold text-app">{stat.value}</p>
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
        <SectionTitle title="How the classifier is used" description="A hint beside the rules, never a verdict" />
        <ul className="mt-2 space-y-1.5 text-[12px] text-muted-app">
          <li>The rule engine decides the category, severity and confidence of every diagnosis.</li>
          <li>The classifier's top category and its confidence are shown next to the rule result.</li>
          <li>A disagreement between the two is surfaced rather than silently resolved.</li>
        </ul>
        <Link to="/" className="mt-3 inline-block">
          <Button size="sm" variant="ghost">
            Back to the dashboard
          </Button>
        </Link>
      </Card>
    </div>
  )
}
