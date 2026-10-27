import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import './index.css'

type CaseRow = {
  id: string
  issue_text: string
  os: string
  status: string
  outcome: string | null
  priority?: string
  created_at: string
  updated_at?: string
}

type RunState = {
  issue: string
  os?: string
  triage?: { category?: string; priority?: string; priority_rationale?: string[] }
  evidence?: Array<{ doc_id: string; title: string; url: string; source: string; snippet?: string; score?: number }>
  results?: Array<{ tool_name: string; simulated?: boolean; ok?: boolean; summary?: string; error?: string }>
  case_state?: {
    hypotheses?: Array<{ hypothesis?: string; confidence?: string; status?: string; supporting_evidence?: string[]; contradictory_evidence?: string[] }>
    diagnostics?: Array<{ tool_name?: string; ok?: boolean; reused?: boolean; summary?: string; error?: string; decision_reason?: string; executed_at?: string }>
    next_step?: string
    reused_diagnostics?: Array<{ tool_name?: string; decision_reason?: string }>
  }
  stopped_reason?: string
  escalated?: boolean
  needs_clarification?: boolean
  clarification_question?: string
  analysis?: { symptoms?: string[]; missing_info?: string[]; diagnostic_confidence?: string }
  verification?: { outcome?: string; reason?: string; follow_up_question?: string }
  reused_diagnostics?: Array<{ tool_name?: string; decision_reason?: string }>
}

type Verification = {
  outcome?: string
  reason?: string
  follow_up_question?: string
}

type Escalation = {
  summary?: string
  category?: string
  priority?: string
  symptoms?: string
  sources?: string[]
  actions_attempted?: string[]
  results?: string[]
  not_attempted_reason?: string
  next_steps?: string[]
  to?: string
}

const pages: { id: string; label: string; icon: string }[] = [
  { id: 'help', label: 'AI Help Desk', icon: 'spark' },
  { id: 'cases', label: 'Cases', icon: 'cases' },
  { id: 'diagnostics', label: 'Diagnostics', icon: 'pulse' },
  { id: 'operations', label: 'Operations', icon: 'ops' },
]

function Icon({ name }: { name: string }) {
  const symbols: Record<string, string> = {
    spark: '✦', cases: '▦', pulse: '↗', ops: '📊', search: '⌕', settings: '⚙', send: '➤',
    check: '✓', clock: '◷', alert: '!', chevron: '›', apple: '', dots: '⋯', arrow: '→',
    shield: '🛡', menu: '☰', close: '×', plus: '+', refresh: '↻', filter: '≡', star: '★',
  }
  return <span aria-hidden="true">{symbols[name] || '•'}</span>
}

function Status({ children, value, tone = 'blue' }: { children?: any; value?: string; tone?: string }) {
  const content = children ?? value
  return <span className={`status status-${tone}`}><span className="status-dot" />{content}</span>
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="ia-card">
      <div className="text-sm text-slate-500">{label}</div>
      <div className="text-2xl font-bold text-slate-900 mt-1">{value}</div>
    </div>
  )
}

export default function App() {
  const [page, setPage] = useState('help')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [issueText, setIssueText] = useState('')
  const [osChoice, setOsChoice] = useState('macos')
  const [allowReal, setAllowReal] = useState(false)
  const [approved, setApproved] = useState(false)

  const [run, setRun] = useState<RunState | null>(null)
  const [verification, setVerification] = useState<Verification | null>(null)
  const [escalation, setEscalation] = useState<Escalation | null>(null)
  const [conversation, setConversation] = useState<Array<{ role: string; content: string }>>([])
  const [activeCaseId, setActiveCaseId] = useState<string | null>(null)

  const [cases, setCases] = useState<CaseRow[]>([])
  const [selectedCase, setSelectedCase] = useState<any>(null)
  const [caseQuery, setCaseQuery] = useState('')
  const [caseStatus, setCaseStatus] = useState('all')
  const [casePriority, setCasePriority] = useState('all')

  const [kbQuery, setKbQuery] = useState('')
  const [kbOs, setKbOs] = useState('macos')
  const [kbResults, setKbResults] = useState<any[]>([])
  const [tools, setTools] = useState<any[]>([])
  const [failures, setFailures] = useState<any[]>([])
  const [summary, setSummary] = useState<any>(null)

  const messagesRef = useRef<HTMLDivElement>(null)

  const loadCases = async () => {
    try {
      const data = await api.listCases(caseQuery, caseStatus, casePriority)
      setCases(data.cases || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load cases')
    }
  }

  const loadDiagnostics = async () => {
    try {
      const data = await api.tools()
      setTools(data.tools || [])
      const failureData = await api.operationsFailures()
      setFailures(failureData.failures || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load diagnostics')
    }
  }

  const loadOperations = async () => {
    try {
      const [summaryData, failureData] = await Promise.all([
        api.operationsSummary(),
        api.operationsFailures(),
      ])
      setSummary(summaryData.summary)
      setFailures(failureData.failures || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load operations')
    }
  }

  useEffect(() => {
    loadCases()
  }, [caseQuery, caseStatus, casePriority])

  useEffect(() => {
    if (page === 'Diagnostics') loadDiagnostics()
    if (page === 'Operations') loadOperations()
  }, [page])

  const runCase = async (text: string, continueId: string | null = null) => {
    if (!text.trim()) {
      setError('Please describe the issue.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = continueId
        ? await api.continueCase(continueId, text, osChoice, allowReal, approved)
        : await api.createCase(text, osChoice, allowReal, approved)
      setRun(data.run)
      setVerification(data.verification)
      setEscalation(data.escalation)
      setActiveCaseId(data.run.case_id)
      const entry = { role: 'assistant', content: data.run.stopped_reason || 'Agent completed.' }
      setConversation((current) => (continueId ? [...current, { role: 'user', content: text }, entry] : [{ role: 'user', content: text }, entry]))
      await loadCases()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to run agent')
    } finally {
      setLoading(false)
    }
  }

  const openCase = async (id: string) => {
    try {
      const data = await api.getCase(id)
      const c = data.case
      setSelectedCase(c)
      setActiveCaseId(id)
      setConversation(Array.isArray(c.conversation) ? c.conversation : [])
      if (c.issue_text) setIssueText(c.issue_text)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to open case')
    }
  }

  const runKnowledgeSearch = async () => {
    try {
      const data = await api.searchKnowledge(kbQuery, kbOs)
      setKbResults(data.results || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Knowledge search failed')
    }
  }

  const confirmResolution = async (resolved: boolean) => {
    if (!activeCaseId) return
    try {
      const data = await api.confirmCase(activeCaseId, resolved)
      setVerification(data.verification)
      setSelectedCase(data.case)
      await loadCases()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Confirmation failed')
    }
  }

  const state = run?.case_state || {}

  const helpDesk = (
    <div className="help-grid">
      <section className="workspace-panel conversation">
        <div className="panel-head">
          <div>
            <span className="eyebrow">Case conversation</span>
            <h2>Reported issue</h2>
          </div>
        </div>
        <div className="messages" ref={messagesRef}>
          {conversation.length === 0 ? (
            <div className="ia-empty">No conversation yet. Submit an issue to begin.</div>
          ) : (
            conversation.map((message, index) => (
              <div key={index} className={`message ${message.role === 'user' ? 'user-msg' : 'agent-msg'}`}>
                <div className="message-meta">
                  <strong>{message.role === 'user' ? 'You' : 'Autopsy Agent'}</strong>
                </div>
                <p>{message.content}</p>
              </div>
            ))
          )}
          {run?.needs_clarification ? (
            <div className="clarify">
              <span>Clarification needed</span>
              <strong>{run.clarification_question}</strong>
            </div>
          ) : null}
        </div>
        <div className="composer-wrap">
          <textarea
            className="composer-textarea"
            value={issueText}
            onChange={(e) => setIssueText(e.target.value)}
            placeholder="Describe the IT problem you're experiencing..."
          />
          <div className="composer-foot">
            <span>Enter details or answer the agent</span>
            <div className="composer-actions">
              <select value={osChoice} onChange={(e) => setOsChoice(e.target.value)}>
                <option value="unknown">Unknown OS</option>
                <option value="macos">macOS</option>
                <option value="windows">Windows</option>
              </select>
              <label><input type="checkbox" checked={allowReal} onChange={(e) => setAllowReal(e.target.checked)} /> Real diagnostics</label>
              <label><input type="checkbox" checked={approved} onChange={(e) => setApproved(e.target.checked)} /> Approve changes</label>
              <button className="primary" disabled={loading} onClick={() => runCase(issueText)}>
                {loading ? 'Running...' : 'Investigate issue'}
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="workspace-panel investigation">
        <div className="panel-head">
          <div>
            <span className="eyebrow">Live investigation</span>
            <h2>Diagnostic activity</h2>
          </div>
          <Status tone={run?.escalated ? 'red' : run?.stopped_reason === 'observation_complete' ? 'green' : 'blue'}>
            {run?.stopped_reason || 'Idle'}
          </Status>
        </div>
        {run ? (
          <div className="investigation-body">
            <div className="assessment-grid">
              <div><span>Issue received</span><strong>{run.issue}</strong></div>
              <div><span>Priority</span><strong>{run.triage?.priority || 'Unknown'}</strong></div>
              <div><span>Status</span><strong>{run.stopped_reason || 'running'}</strong></div>
              <div><span>Category</span><strong>{run.triage?.category || 'Unknown'}</strong></div>
              <div><span>Environment</span><strong>{run.os || 'unknown'} device confirmed by user.</strong></div>
            </div>
            <div className="section-block">
              <div className="section-label"><span>Current hypothesis</span><span className="confidence">{run.analysis?.diagnostic_confidence || 'low'} confidence</span></div>
              <p>{state.hypotheses?.[0]?.hypothesis || 'Awaiting analysis.'}</p>
              <small>{state.hypotheses?.[0]?.status === 'supported' ? 'Supported by evidence' : 'Probable explanation — not yet confirmed'}</small>
            </div>
            <div className="section-block">
              <div className="section-label"><span>Evidence</span><b>{state.hypotheses?.[0]?.supporting_evidence?.length || 0}</b></div>
              <div className="evidence-list">
                {(state.hypotheses?.[0]?.supporting_evidence || []).length ? (
                  (state.hypotheses?.[0]?.supporting_evidence || []).map((docId: string, index: number) => (
                    <div key={index} className="evidence-item">
                      <span className="evidence-icon good">✓</span>
                      <div><strong>{docId}</strong><span>Supporting evidence</span></div>
                    </div>
                  ))
                ) : (
                  <div className="empty-line"><span>Contradictory evidence</span><p>None collected yet</p></div>
                )}
              </div>
            </div>
            <div className="section-block">
              <div className="section-label"><span>Diagnostic activity</span></div>
              {(run.results || []).length ? (
                (run.results || []).map((result, index) => (
                  <div key={index} className="activity-row">
                    <div>
                      <strong>{result.tool_name}</strong>
                      <span>{result.simulated ? 'Simulated observation' : 'Read-only observation'}</span>
                    </div>
                    <Status tone={result.ok ? 'green' : 'red'}>{result.ok ? 'Completed' : 'Failed'}</Status>
                    <div className="activity-text">{result.summary || result.error}</div>
                  </div>
                ))
              ) : (
                <div className="empty-line"><span>No diagnostic action</span><p>No diagnostic action was run.</p></div>
              )}
              {(run.reused_diagnostics || []).length ? (
                <div className="mt-3">
                  <div className="text-xs text-slate-500 mb-1">Reused diagnostics</div>
                  {(run.reused_diagnostics || []).map((item, index) => (
                    <div key={index} className="text-xs text-slate-600">{item.tool_name}: {item.decision_reason}</div>
                  ))}
                </div>
              ) : null}
            </div>
            <div className="section-block">
              <div className="section-label"><span>Next recommended action</span></div>
              <strong>{state.next_step || 'Review cited guidance.'}</strong>
              <p>Identify the next safe diagnostic or remediation step before proposing a change.</p>
            </div>
            {run.needs_clarification ? (
              <div className="clarify">
                <span>Clarification needed</span>
                <strong>{run.clarification_question}</strong>
              </div>
            ) : null}
            {run.escalated || verification?.outcome === 'escalated' || verification?.outcome === 'not_resolved' ? (
              <div className="escalation-box">
                <strong>Escalation summary</strong>
                <p>{escalation?.not_attempted_reason || 'Troubleshooting stopped before completion.'}</p>
                {escalation?.next_steps?.filter(Boolean).map((step, index) => <div key={index} className="text-sm">- {step}</div>)}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="ia-empty">No active investigation. Submit an issue to begin.</div>
        )}
      </section>

      <div className="space-y-4">
        <div className="ia-card">
          <h2 className="text-base font-semibold text-slate-900 mb-3">Case intelligence</h2>
          {run ? (
            <>
              <div className="intel-row"><span>Current assessment</span><div>{state.hypotheses?.[0]?.hypothesis || 'Awaiting analysis.'}</div></div>
              <div className="intel-row"><span>Evidence</span><div>{state.hypotheses?.[0]?.supporting_evidence?.length ? 'Evidence attached.' : 'No evidence attached yet.'}</div></div>
              <div className="intel-row"><span>Contradictory evidence</span><div>{state.hypotheses?.[0]?.contradictory_evidence?.length ? 'Contradictory evidence found.' : 'None collected yet.'}</div></div>
              <div className="intel-row"><span>Next action</span><div>{state.next_step || 'Review cited guidance.'}</div></div>
              <div className="intel-row"><span>Reused diagnostics</span><div>{run.reused_diagnostics?.length ? `${run.reused_diagnostics.length} reused` : 'None reused.'}</div></div>
              <div className="intel-row"><span>Resolution verification</span><div>{verification?.outcome || 'Not started'}</div></div>
              <div className="intel-row"><span>Approval</span><div>{run.escalated ? 'Escalation active' : 'Not yet required'}</div></div>
            </>
          ) : (
            <div className="ia-muted">No active case.</div>
          )}
        </div>

        <div className="ia-card">
          <h2 className="text-base font-semibold text-slate-900 mb-3">Relevant evidence</h2>
          {(run?.evidence || []).length ? (
            (run?.evidence || []).map((item) => (
              <div key={item.doc_id} className="mb-3">
                <a className="font-medium text-blue-600" href={item.url} target="_blank" rel="noreferrer">{item.title}</a>
                <div className="text-xs text-slate-500">{item.source} · score {item.score}</div>
                <div className="text-sm text-slate-600 mt-1">{item.snippet}</div>
              </div>
            ))
          ) : (
            <div className="ia-muted">No relevant source was found.</div>
          )}
        </div>

        {verification?.outcome === 'needs_user_confirmation' ? (
          <div className="ia-card">
            <h2 className="text-base font-semibold text-slate-900 mb-3">Verification</h2>
            <div className="text-sm text-slate-600 mb-3">{verification.reason}</div>
            {verification.follow_up_question ? <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 mb-3">{verification.follow_up_question}</div> : null}
            <div className="flex gap-2">
              <button className="rounded-lg bg-emerald-600 px-3 py-1.5 text-white text-sm" onClick={() => confirmResolution(true)}>Resolved</button>
              <button className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm" onClick={() => confirmResolution(false)}>Still broken</button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )

  const casesPage = (
    <div className="space-y-4">
      <div>
        <h1 className="ia-title">Incident Cases</h1>
        <p className="ia-subtitle">Track investigations from initial report through verified resolution.</p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <input className="rounded-xl border border-slate-300 p-2" placeholder="Search case ID or issue description" value={caseQuery} onChange={(e) => setCaseQuery(e.target.value)} />
        <select className="rounded-xl border border-slate-300 p-2" value={caseStatus} onChange={(e) => setCaseStatus(e.target.value)}>
          <option value="all">All statuses</option>
          <option value="open">Open</option>
          <option value="investigating">Investigating</option>
          <option value="awaiting_user">Awaiting user</option>
          <option value="resolved">Resolved</option>
          <option value="escalated">Escalated</option>
        </select>
        <select className="rounded-xl border border-slate-300 p-2" value={casePriority} onChange={(e) => setCasePriority(e.target.value)}>
          <option value="all">All priorities</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
          <option value="unknown">Unknown</option>
        </select>
      </div>
      {cases.length ? (
        <div className="ia-card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-500">
                <th className="py-2">Case</th>
                <th>Priority</th>
                <th>Status</th>
                <th>Outcome</th>
                <th>Updated</th>
                <th>Issue summary</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((row) => (
                <tr key={row.id} className="border-t border-slate-100 cursor-pointer hover:bg-slate-50" onClick={() => openCase(row.id)}>
                  <td className="py-2 font-medium">{row.id.slice(0, 8)}</td>
                  <td>{row.priority || 'unknown'}</td>
                  <td><Status value={row.status || 'open'} /></td>
                  <td>{row.outcome || 'pending'}</td>
                  <td>{row.updated_at || row.created_at}</td>
                  <td className="max-w-md truncate">{row.issue_text}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ia-empty">No cases match the current filters.</div>
      )}
      {selectedCase ? (
        <div className="ia-card space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">{selectedCase.id.slice(0, 8)}</h2>
            <button className="rounded-xl border border-slate-300 px-4 py-2 font-medium hover:bg-slate-50" onClick={() => {
              setActiveCaseId(selectedCase.id)
              setIssueText(selectedCase.issue_text || '')
              setConversation(selectedCase.conversation || [])
              setPage('AI Help Desk')
            }}>
              Open in AI Help Desk
            </button>
          </div>
          <div><span className="font-medium">Issue:</span> {selectedCase.issue_text}</div>
          <div><span className="font-medium">OS:</span> {selectedCase.os || 'unknown'}</div>
          <div><span className="font-medium">Status:</span> {selectedCase.status || 'open'}</div>
          <div><span className="font-medium">Category:</span> {selectedCase.triage?.category || 'Unknown'}</div>
          <div><span className="font-medium">Priority:</span> {selectedCase.triage?.priority || 'Unknown'}</div>
          <div><span className="font-medium">Outcome:</span> {selectedCase.outcome || 'pending'}</div>
          {selectedCase.state?.hypotheses?.length ? (
            <div>
              <div className="font-medium">Current hypothesis</div>
              <div className="text-sm text-slate-600">{selectedCase.state.hypotheses[0]?.hypothesis}</div>
            </div>
          ) : null}
          {selectedCase.state?.diagnostics?.length ? (
            <div>
              <div className="font-medium">Diagnostic history</div>
              <ul className="list-disc pl-5 text-sm text-slate-600">
                {selectedCase.state.diagnostics.map((item: any, index: number) => (
                  <li key={index}>{item.tool_name} — {item.ok ? 'completed' : 'failed'} {item.reused ? '(reused)' : ''}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {selectedCase.conversation?.length ? (
            <div>
              <div className="font-medium">Conversation</div>
              {selectedCase.conversation.map((message: any, index: number) => (
                <div key={index} className="text-sm text-slate-600">
                  <span className="font-medium">{message.role}:</span> {message.content}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )

  const diagnosticsPage = (
    <div className="space-y-4">
      <div>
        <h1 className="ia-title">Diagnostics</h1>
        <p className="ia-subtitle">Inspect approved diagnostic executions and the evidence they produced.</p>
      </div>
      <div className="ia-card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-slate-500">
              <th className="py-2">Tool</th>
              <th>Topic</th>
              <th>Risk</th>
              <th>Mode</th>
              <th>Approval</th>
              <th>OS support</th>
            </tr>
          </thead>
          <tbody>
            {tools.map((tool) => (
              <tr key={tool.name} className="border-t border-slate-100">
                <td className="py-2 font-medium">{tool.name}</td>
                <td>{tool.topic}</td>
                <td>{tool.risk}</td>
                <td>{tool.simulated ? 'Simulated' : 'Real'}</td>
                <td>{tool.requires_approval ? 'Required' : 'No'}</td>
                <td>{tool.os.join(', ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="ia-card">
        <h2 className="text-base font-semibold text-slate-900 mb-3">Knowledge search</h2>
        <div className="flex gap-3 mb-3">
          <input className="flex-1 rounded-xl border border-slate-300 p-2" value={kbQuery} onChange={(e) => setKbQuery(e.target.value)} placeholder="Search troubleshooting guidance" />
          <select className="rounded-xl border border-slate-300 p-2" value={kbOs} onChange={(e) => setKbOs(e.target.value)}>
            <option value="unknown">Any OS</option>
            <option value="macos">macOS</option>
            <option value="windows">Windows</option>
          </select>
          <button className="rounded-xl bg-blue-600 px-4 py-2 text-white" onClick={runKnowledgeSearch}>Search</button>
        </div>
        {kbResults.length ? (
          kbResults.map((item) => (
            <div key={item.doc_id} className="border-t border-slate-100 py-3">
              <a className="font-medium text-blue-600" href={item.url} target="_blank" rel="noreferrer">{item.title}</a>
              <div className="text-xs text-slate-500">{item.source} · {item.doc_id} · score {item.score}</div>
              <div className="text-sm text-slate-600 mt-1">{item.snippet}</div>
            </div>
          ))
        ) : (
          <div className="ia-muted">No knowledge results yet.</div>
        )}
      </div>
      <div className="ia-card">
        <h2 className="text-base font-semibold text-slate-900 mb-3">Recent diagnostic failures</h2>
        {failures.length ? (
          <div className="space-y-2">
            {failures.slice(0, 12).map((event, index) => (
              <div key={index} className="text-sm text-slate-600">
                {event.ts} · {event.case_id?.slice(0, 8)} · {event.tool_name} · {event.status}
              </div>
            ))}
          </div>
        ) : (
          <div className="ia-muted">No diagnostic failures recorded.</div>
        )}
      </div>
    </div>
  )

  const operationsPage = (
    <div className="space-y-4">
      <div>
        <h1 className="ia-title">Operations</h1>
        <p className="ia-subtitle">Case health, diagnostic reliability and agent evaluation.</p>
      </div>
      {summary ? (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Metric label="Total cases" value={summary.total_cases} />
            <Metric label="Resolved" value={summary.resolved_cases} />
            <Metric label="Escalated" value={summary.status_counts?.escalated || 0} />
            <Metric label="Tool failure rate" value={`${Math.round((summary.tool_events?.failure_rate || 0) * 100)}%`} />
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Metric label="Awaiting user" value={summary.status_counts?.awaiting_user || 0} />
            <Metric label="Open" value={summary.status_counts?.open || 0} />
            <Metric label="Investigating" value={summary.status_counts?.investigating || 0} />
            <Metric label="Repeated issues" value={summary.repeated_issue_cases || 0} />
          </div>
          {summary.average_resolution_seconds != null ? (
            <div className="ia-muted">Average resolution time: {Math.round(summary.average_resolution_seconds)} seconds</div>
          ) : (
            <div className="ia-muted">Mean time to resolution is not shown. There is not enough verified resolution data to calculate a reliable value.</div>
          )}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="ia-card">
              <h2 className="text-base font-semibold text-slate-900 mb-3">Status distribution</h2>
              {Object.entries(summary.status_counts || {}).map(([key, value]) => (
                <div key={key} className="flex justify-between text-sm py-1">
                  <span>{key}</span>
                  <span className="font-medium">{String(value)}</span>
                </div>
              ))}
            </div>
            <div className="ia-card">
              <h2 className="text-base font-semibold text-slate-900 mb-3">Priority distribution</h2>
              {Object.entries(summary.priority_counts || {}).map(([key, value]) => (
                <div key={key} className="flex justify-between text-sm py-1">
                  <span>{key}</span>
                  <span className="font-medium">{String(value)}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="ia-card">
            <h2 className="text-base font-semibold text-slate-900 mb-3">Recent diagnostic failures</h2>
            {failures.length ? (
              <div className="space-y-2">
                {failures.slice(0, 12).map((event, index) => (
                  <div key={index} className="text-sm text-slate-600">
                    {event.ts} · {event.case_id?.slice(0, 8)} · {event.tool_name} · {event.status}
                  </div>
                ))}
              </div>
            ) : (
              <div className="ia-muted">No diagnostic failures recorded.</div>
            )}
          </div>
        </>
      ) : (
        <div className="ia-empty">Loading operations data...</div>
      )}
    </div>
  )

  return (
    <div className="ia-shell">
      <aside className="ia-sidebar">
        <div className="mb-6">
          <div className="text-2xl font-bold">IT Autopsy</div>
          <div className="text-sm text-slate-300">Autonomous help desk</div>
        </div>
        <nav className="space-y-1">
          {pages.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${page === item.id ? 'active' : ''}`}
              onClick={() => setPage(item.id)}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="mt-8 text-xs text-slate-400">
          Deterministic local baseline. No API key required.
        </div>
      </aside>
      <main className="p-4 md:p-6 lg:p-8">
        {error ? <div className="mb-4 rounded-xl border border-rose-200 bg-rose-50 p-3 text-rose-700">{error}</div> : null}
        {page === 'AI Help Desk' && helpDesk}
        {page === 'Incident Cases' && casesPage}
        {page === 'Diagnostics' && diagnosticsPage}
        {page === 'Operations' && operationsPage}
      </main>
    </div>
  )
}
