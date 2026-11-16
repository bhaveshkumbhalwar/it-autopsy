import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  Ban,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Eye,
  FlaskConical,
  HelpCircle,
  MinusCircle,
  Play,
  RotateCcw,
  ThumbsDown,
  ThumbsUp,
  X,
} from 'lucide-react'
import clsx from 'clsx'
import { describeApiError } from '../lib/api'
import type { AgentEvent, AgentRemediation, AgentSession, VerificationStatus } from '../lib/api'
import { Badge, Button, Card, inputClass } from './ui'
import { formatDateTime, formatPercent } from '../lib/format'

/* Labels and tones for states the backend owns. Nothing here decides an
   outcome: it only renders what the agent reported. */

const VERIFICATION_TONE: Record<VerificationStatus, 'success' | 'warning' | 'danger' | 'neutral'> = {
  VERIFIED_RESOLVED: 'success',
  PARTIALLY_RESOLVED: 'warning',
  STILL_FAILING: 'danger',
  NEW_FAILURE: 'danger',
  INCONCLUSIVE: 'neutral',
}

const STATUS_TONE: Record<string, 'success' | 'warning' | 'danger' | 'neutral' | 'brand'> = {
  proposed: 'brand',
  awaiting_approval: 'warning',
  rejected: 'neutral',
  executed: 'brand',
  execution_failed: 'danger',
  action_refused: 'warning',
  verified: 'success',
  not_verified: 'danger',
}

const ACTION_TYPE_LABEL: Record<string, string> = {
  automatic: 'Automatic (safe)',
  approval: 'Needs approval',
  recommendation: 'Recommendation only',
}

const RISK_TONE: Record<string, 'success' | 'warning' | 'danger'> = {
  low: 'success',
  medium: 'warning',
  high: 'danger',
}

const PHASE_ICON: Record<string, typeof Eye> = {
  observe: Eye,
  analyze: Activity,
  decide: FlaskConical,
  act: Play,
  verify: CheckCircle2,
  learn: RotateCcw,
}

const PHASE_ORDER = ['observe', 'analyze', 'decide', 'act', 'verify', 'learn']

export type AgentCallbacks = {
  onStart: () => Promise<void>
  onApprove: (id: number, approved: boolean, executeIfReady?: boolean) => Promise<void>
  onExecute: (id: number) => Promise<void>
  onVerify: (id: number, trace: string) => Promise<void>
}

/** A panel heading with its own icon, in the same type scale as SectionTitle. */
function PanelTitle({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <h2 className="flex items-center gap-1.5 text-[15px] font-semibold text-app">
      {icon}
      {children}
    </h2>
  )
}

export function AgentPanel({ session, callbacks }: { session: AgentSession; callbacks: AgentCallbacks }) {
  const open = session.remediations.filter((r) => ['proposed', 'awaiting_approval', 'action_refused'].includes(r.status))
  const current = open[open.length - 1] ?? session.remediations[session.remediations.length - 1] ?? null

  return (
    <div className="space-y-4">
      <SessionCard session={session} callbacks={callbacks} />
      {current && (
        <div className="grid gap-4 lg:grid-cols-5">
          <div className="lg:col-span-2">
            <DecisionCard remediation={current} />
          </div>
          <div className="lg:col-span-3">
            <RemediationCard
              remediation={current}
              session={session}
              callbacks={callbacks}
              isCurrent={open.includes(current)}
            />
          </div>
        </div>
      )}
      {current?.verification?.status && (
        <VerificationCard remediation={current} />
      )}
      <ActivityTimeline events={session.events} />
      {session.remediations.length > 1 && <AttemptHistory rows={session.remediations} />}
    </div>
  )
}

/* ------------------------------------------------------------- session card */

function SessionCard({ session, callbacks }: { session: AgentSession; callbacks: AgentCallbacks }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const stateTone =
    session.state === 'resolved'
      ? 'success'
      : session.state === 'exhausted'
        ? 'danger'
        : session.state === 'awaiting_approval'
          ? 'warning'
          : 'brand'

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      await callbacks.onStart()
    } catch (e) {
      setError(describeApiError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Activity className="h-4 w-4 text-brand-app" />
            <h2 className="text-[14.5px] font-semibold text-app">Diagnostic agent</h2>
            <Badge tone={stateTone}>{session.state.replace(/_/g, ' ')}</Badge>
          </div>
          <p className="mt-1 max-w-[70ch] text-[12px] text-muted-app">
            The agent observes the trace, decides what failed, proposes a fix from a fixed allowlist, runs it when it is
            allowed to, and verifies the outcome against a follow-up trace. It never claims a fix it has not verified.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11.5px] text-muted-app">
            Attempt {session.attempts_used} of {session.attempt_limit}
          </span>
          <Button variant={session.state === 'not_started' ? 'primary' : 'secondary'} onClick={start} loading={busy}>
            {session.state === 'not_started' ? (
              <>
                <FlaskConical className="h-3.5 w-3.5" /> Start diagnosing
              </>
            ) : (
              <>
                <ChevronRight className="h-3.5 w-3.5" /> Next attempt
              </>
            )}
          </Button>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {PHASE_ORDER.map((phase) => {
          const done = session.events.some((e) => e.phase === phase)
          const Icon = PHASE_ICON[phase] ?? CircleDot
          return (
            <span
              key={phase}
              className={clsx(
                'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-medium capitalize',
                done
                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                  : 'border-app text-muted-app',
              )}
            >
              <Icon className="h-3 w-3" /> {phase}
            </span>
          )
        })}
      </div>

      {session.stop_reason && (
        <p className="mt-3 rounded-lg border border-app surface-2 p-2.5 text-[11.5px] text-app">
          <span className="font-semibold">Stopped: </span>
          {session.stop_reason}
        </p>
      )}
      {error && (
        <p className="mt-2 text-[11.5px] text-rose-600 dark:text-rose-400">
          <AlertTriangle className="mr-1 inline h-3.5 w-3.5" />
          {error}
        </p>
      )}
    </Card>
  )
}

/* ----------------------------------------------------------- decision card */

function DecisionCard({ remediation }: { remediation: AgentRemediation }) {
  const d = remediation.decision
  const unsure = d.verdict !== 'diagnosed'
  return (
    <Card className="h-full p-4">
      <PanelTitle icon={<FlaskConical className="h-3.5 w-3.5 text-brand-app" />}>
        Agent decision <span className="font-normal text-muted-app">· attempt {remediation.attempt}</span>
      </PanelTitle>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Badge tone={unsure ? 'warning' : 'brand'}>{d.verdict.replace(/_/g, ' ')}</Badge>
        {d.failure_category && <Badge tone="neutral">{d.failure_category.replace(/_/g, ' ')}</Badge>}
        {!unsure && <span className="text-[11.5px] text-muted-app">confidence {formatPercent(d.confidence)}</span>}
      </div>

      {unsure ? (
        <div className="mt-3 space-y-2 text-[12px] text-app">
          <p>
            <span className="font-semibold">No root cause claimed. </span>
            {d.action_reason}
          </p>
          {d.uncertainty.length > 0 && (
            <ul className="space-y-1">
              {d.uncertainty.map((line, i) => (
                <li key={i} className="flex gap-1.5 text-muted-app">
                  <HelpCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {line}
                </li>
              ))}
            </ul>
          )}
          {d.needed_evidence && (
            <p className="rounded-lg border border-app surface-2 p-2 text-[11.5px] text-muted-app">
              <span className="font-semibold text-app">What would settle it: </span>
              {d.needed_evidence}
            </p>
          )}
        </div>
      ) : (
        <div className="mt-3 space-y-2 text-[12px] text-app">
          <p>
            <span className="font-semibold">Root cause{d.root_cause_step ? ` (step ${d.root_cause_step})` : ''}: </span>
            {d.root_cause}
          </p>
          {d.evidence.length > 0 && (
            <ul className="space-y-1.5">
              {d.evidence.slice(0, 4).map((ev, i) => (
                <li key={i} className="rounded-lg border border-app surface-2 p-2">
                  <p className="text-[10.5px] font-medium text-muted-app">
                    {ev.field ?? 'evidence'}
                    {ev.source ? ` · ${ev.source}` : ''}
                  </p>
                  <p className="mt-0.5 font-mono text-[11px] break-words text-app">{ev.quote}</p>
                </li>
              ))}
            </ul>
          )}
          <p className="text-[11.5px] text-muted-app">
            <span className="font-semibold text-app">Retry is {d.retry_appropriate ? 'appropriate' : 'not appropriate'}: </span>
            {d.retry_reason}
          </p>
        </div>
      )}

      {d.ml?.prediction && (
        <p className="mt-2 text-[10.5px] text-muted-app">
          Classifier hint: {d.ml.prediction.replace(/_/g, ' ')} ({formatPercent(d.ml.confidence ?? 0)}) — shown beside the
          decision, never as the diagnosis.
        </p>
      )}
    </Card>
  )
}

/* -------------------------------------------------------- remediation card */

function RemediationCard({
  remediation,
  session,
  callbacks,
  isCurrent,
}: {
  remediation: AgentRemediation
  session: AgentSession
  callbacks: AgentCallbacks
  isCurrent: boolean
}) {
  const plan = remediation.plan
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [trace, setTrace] = useState('')
  const [showVerifier, setShowVerifier] = useState(false)

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(describeApiError(e))
    } finally {
      setBusy(null)
    }
  }

  // A plan can be acted on while it is open. `proposed` is the normal state; an
  // approval-gated plan stays `awaiting_approval` until it is approved, and a
  // refused attempt leaves the plan open so it can be approved then run.
  const canApprove =
    isCurrent &&
    (remediation.status === 'proposed' ||
      remediation.status === 'awaiting_approval' ||
      remediation.status === 'action_refused')
  const isRecommendation = plan.action_type === 'recommendation'
  const approved = remediation.approved === true
  const needsApproval = plan.action_type === 'approval' && !approved
  // Run is offered only where the backend will actually run something.
  const canExecute =
    canApprove && !isRecommendation && !plan.repeat_of_failed_action && remediation.status !== 'executed'
  // One click for the whole decision: approve + run in the same request, for
  // plans the action allowlist lets a human execute.
  const canApproveAndRun = canApprove && !isRecommendation && !plan.repeat_of_failed_action && !approved

  return (
    <Card className="h-full p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <PanelTitle icon={<Play className="h-3.5 w-3.5 text-brand-app" />}>Remediation plan</PanelTitle>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={STATUS_TONE[remediation.status] ?? 'neutral'}>{remediation.status.replace(/_/g, ' ')}</Badge>
          <Badge tone={RISK_TONE[plan.risk] ?? 'neutral'}>risk {plan.risk}</Badge>
        </div>
      </div>

      <p className="mt-2 text-[13px] font-semibold text-app">{plan.title}</p>
      <p className="text-[11px] text-muted-app">{ACTION_TYPE_LABEL[plan.action_type]}</p>

      <dl className="mt-3 space-y-2 text-[11.5px]">
        <Row label="Problem" value={plan.problem} />
        <Row label="Root cause" value={plan.root_cause} />
        <Row label="Solution" value={plan.proposed_solution} />
        <Row label="Why this action" value={plan.why_selected} />
        <Row label="Expected outcome" value={plan.expected_outcome} />
        <Row label="How it is verified" value={plan.verification_criteria} />
      </dl>

      {plan.instructions.length > 0 && (
        <div className="mt-3 rounded-lg border border-app surface-2 p-2.5">
          <p className="text-[10.5px] font-semibold tracking-wide text-muted-app uppercase">Steps for you</p>
          <ol className="mt-1 list-decimal space-y-1 pl-4 text-[11.5px] text-app">
            {plan.instructions.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ol>
        </div>
      )}

      {remediation.execution_result?.detail && (
        <p className="mt-2 rounded-lg border border-app surface-2 p-2 text-[11.5px] text-app">
          <span className="font-semibold">Action result: </span>
          {remediation.execution_result.detail}
        </p>
      )}

      {isCurrent && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {canApprove && (
            <>
              {canApproveAndRun ? (
                <Button
                  variant="primary"
                  size="sm"
                  loading={busy === 'approve'}
                  title="Approve this plan and run the action in one step."
                  onClick={() => run('approve', () => callbacks.onApprove(remediation.id, true, true))}
                >
                  <ThumbsUp className="h-3.5 w-3.5" /> Approve &amp; run
                </Button>
              ) : (
                <Button
                  variant="primary"
                  size="sm"
                  loading={busy === 'approve'}
                  onClick={() => run('approve', () => callbacks.onApprove(remediation.id, true))}
                >
                  <ThumbsUp className="h-3.5 w-3.5" /> {approved ? 'Approved' : 'Approve'}
                </Button>
              )}
              <Button
                size="sm"
                loading={busy === 'reject'}
                onClick={() => run('reject', () => callbacks.onApprove(remediation.id, false))}
              >
                <ThumbsDown className="h-3.5 w-3.5" /> Reject
              </Button>
            </>
          )}
          {canExecute && (
            <Button
              size="sm"
              loading={busy === 'execute'}
              disabled={needsApproval}
              title={needsApproval ? 'Approve this plan before running it.' : undefined}
              onClick={() => run('execute', () => callbacks.onExecute(remediation.id))}
            >
              <Play className="h-3.5 w-3.5" /> {needsApproval ? 'Approve to run' : 'Run action'}
            </Button>
          )}
          {isRecommendation && canApprove && (
            <span className="text-[10.5px] text-muted-app">
              Recommendation only: approving records the decision, then submit a follow-up trace to verify.
            </span>
          )}
          <Button size="sm" variant="ghost" onClick={() => setShowVerifier((v) => !v)}>
            <CheckCircle2 className="h-3.5 w-3.5" /> Submit verification trace
          </Button>
        </div>
      )}

      {!isCurrent && (
        <p className="mt-3 text-[11px] text-muted-app">
          This attempt is finished. A newer plan is open above.
        </p>
      )}

      {showVerifier && isCurrent && (
        <div className="mt-3 space-y-2 rounded-lg border border-app p-2.5">
          <p className="text-[11.5px] text-muted-app">
            Paste the trace of a new run of your agent (any supported format). It is diagnosed on its own and compared
            with the original run: nothing is called resolved unless the comparison says so.
          </p>
          <textarea
            className={clsx(inputClass, 'h-32 font-mono text-[11px]')}
            placeholder='{"execution_id": "...", "steps": [...]}'
            value={trace}
            onChange={(e) => setTrace(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              loading={busy === 'verify'}
              disabled={!trace.trim()}
              onClick={() =>
                run('verify', async () => {
                  await callbacks.onVerify(remediation.id, trace)
                  setTrace('')
                  setShowVerifier(false)
                })
              }
            >
              <CheckCircle2 className="h-3.5 w-3.5" /> Verify
            </Button>
            <span className="text-[10.5px] text-muted-app">
              Verification is simulated: this app does not run your agent, it grades the run you hand it.
            </span>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-2 text-[11.5px] text-rose-600 dark:text-rose-400">
          <AlertTriangle className="mr-1 inline h-3.5 w-3.5" /> {error}
        </p>
      )}
      {session.state === 'resolved' && remediation.verification_status === 'VERIFIED_RESOLVED' && (
        <p className="mt-2 text-[11.5px] text-emerald-600 dark:text-emerald-400">
          <Check className="mr-1 inline h-3.5 w-3.5" /> Resolved and verified against a follow-up run.
        </p>
      )}
    </Card>
  )
}

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null
  return (
    <div>
      <dt className="text-[10.5px] font-semibold tracking-wide text-muted-app uppercase">{label}</dt>
      <dd className="text-app">{value}</dd>
    </div>
  )
}

/* -------------------------------------------------------- verification card */

function VerificationCard({ remediation }: { remediation: AgentRemediation }) {
  const v = remediation.verification
  if (!v?.status) return null
  const tone = VERIFICATION_TONE[v.status] ?? 'neutral'
  const Icon = v.status === 'VERIFIED_RESOLVED' ? CheckCircle2 : v.status === 'INCONCLUSIVE' ? MinusCircle : X

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <PanelTitle icon={<Icon className="h-3.5 w-3.5 text-brand-app" />}>Verification</PanelTitle>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={tone}>{v.status?.replace(/_/g, ' ')}</Badge>
          <Badge tone="neutral">{v.mode === 'real' ? 'real' : 'simulated'}</Badge>
        </div>
      </div>

      {v.reasons && v.reasons.length > 0 && (
        <ul className="mt-2 space-y-1 text-[12px] text-app">
          {v.reasons.map((r, i) => (
            <li key={i} className="flex gap-1.5">
              <Ban className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-app" /> {r}
            </li>
          ))}
        </ul>
      )}

      {v.checks && v.checks.length > 0 && (
        <ul className="mt-3 grid gap-1.5 sm:grid-cols-3">
          {v.checks.map((c) => (
            <li
              key={c.name}
              className={clsx(
                'rounded-lg border p-2 text-[11px]',
                c.passed ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-rose-500/30 bg-rose-500/5',
              )}
            >
              <p className="flex items-center gap-1 font-semibold text-app">
                {c.passed ? <Check className="h-3 w-3 text-emerald-500" /> : <X className="h-3 w-3 text-rose-500" />}
                {c.name.replace(/_/g, ' ')}
              </p>
              <p className="mt-0.5 text-muted-app">{c.detail}</p>
            </li>
          ))}
        </ul>
      )}

      {v.evidence?.followup && (
        <p className="mt-3 text-[11px] text-muted-app">
          Compared with{' '}
          <Link className="text-brand-app hover:underline" to={`/executions/${remediation.verification_execution_pk}`}>
            {v.evidence.followup.execution_id}
          </Link>{' '}
          ({v.evidence.followup.status}, {v.evidence.followup.step_count} steps
          {v.evidence.followup.failed_step
            ? `, failing at step ${v.evidence.followup.failed_step.number ?? '?'}`
            : ', no failing step'}
          ).
        </p>
      )}
      {v.note && <p className="mt-1 text-[10.5px] text-muted-app">{v.note}</p>}
      {v.criteria && <p className="mt-1 text-[10.5px] text-muted-app">Criterion: {v.criteria}</p>}
    </Card>
  )
}

/* -------------------------------------------------------------- activity log */

function ActivityTimeline({ events }: { events: AgentEvent[] }) {
  if (events.length === 0) return null
  return (
    <Card className="p-4">
      <PanelTitle icon={<Activity className="h-3.5 w-3.5 text-brand-app" />}>Agent activity</PanelTitle>
      <ol className="mt-2 space-y-2">
        {events.map((event) => {
          const Icon = PHASE_ICON[event.phase] ?? CircleDot
          return (
            <li key={event.id} className="flex gap-2.5">
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-app surface-2">
                <Icon className="h-3 w-3 text-muted-app" />
              </span>
              <div className="min-w-0">
                <p className="text-[11.5px] text-app">
                  <span className="font-semibold capitalize">{event.phase}</span>
                  <span className="text-muted-app"> · {event.kind.replace(/-/g, ' ')}</span>
                  {event.created_at && <span className="text-muted-app"> · {formatDateTime(event.created_at)}</span>}
                </p>
                <p className="text-[11.5px] break-words text-muted-app">{event.summary}</p>
              </div>
            </li>
          )
        })}
      </ol>
    </Card>
  )
}

/* ------------------------------------------------------------- attempt history */

function AttemptHistory({ rows }: { rows: AgentRemediation[] }) {
  const [open, setOpen] = useState<number | null>(null)
  return (
    <Card className="p-4">
      <PanelTitle icon={<RotateCcw className="h-3.5 w-3.5 text-brand-app" />}>Attempts</PanelTitle>
      <div className="mt-2 space-y-1.5">
        {rows.map((row) => (
          <div key={row.id} className="rounded-lg border border-app">
            <button
              type="button"
              className="flex w-full items-center gap-2 p-2 text-left"
              onClick={() => setOpen(open === row.id ? null : row.id)}
            >
              {open === row.id ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              <span className="text-[11.5px] font-semibold text-app">#{row.attempt}</span>
              <span className="min-w-0 flex-1 truncate text-[11.5px] text-app">{row.plan.title ?? row.action_id}</span>
              <Badge tone={STATUS_TONE[row.status] ?? 'neutral'}>{row.status.replace(/_/g, ' ')}</Badge>
              {row.verification_status && (
                <Badge tone={VERIFICATION_TONE[row.verification_status] ?? 'neutral'}>
                  {row.verification_status.replace(/_/g, ' ')}
                </Badge>
              )}
            </button>
            {open === row.id && (
              <div className="space-y-1 border-t border-app p-2 text-[11px] text-muted-app">
                <p>
                  <span className="font-semibold text-app">Why: </span>
                  {row.plan.why_selected}
                </p>
                {row.approved !== null && (
                  <p>
                    <span className="font-semibold text-app">Approval: </span>
                    {row.approved ? 'approved' : 'rejected'}
                    {row.approved_by ? ` by ${row.approved_by}` : ''}
                    {row.approved_at ? ` · ${formatDateTime(row.approved_at)}` : ''}
                  </p>
                )}
                {row.execution_result?.detail && (
                  <p>
                    <span className="font-semibold text-app">Action: </span>
                    {row.execution_result.detail}
                  </p>
                )}
                {row.verification?.reasons?.[0] && (
                  <p>
                    <span className="font-semibold text-app">Verification: </span>
                    {row.verification.reasons[0]}
                  </p>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}
