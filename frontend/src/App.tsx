import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import './index.css'

/* ─── Types ─────────────────────────────────────────────────────────── */
type CaseRow = {
  id: string; issue_text: string; os: string; status: string
  outcome: string | null; priority?: string; created_at: string; updated_at?: string
}
type RunState = {
  issue: string; case_id?: string; os?: string
  triage?: { category?: string; priority?: string }
  evidence?: Array<{ doc_id: string; title: string; url: string; source: string; snippet?: string; score?: number }>
  results?: Array<{ tool_name: string; simulated?: boolean; ok?: boolean; summary?: string; error?: string }>
  case_state?: {
    hypotheses?: Array<{ hypothesis?: string; confidence?: string; status?: string; supporting_evidence?: string[]; contradictory_evidence?: string[] }>
    diagnostics?: Array<{ tool_name?: string; ok?: boolean; reused?: boolean; summary?: string; error?: string; executed_at?: string }>
    next_step?: string
    reused_diagnostics?: Array<{ tool_name?: string; decision_reason?: string }>
  }
  stopped_reason?: string; escalated?: boolean; needs_clarification?: boolean
  clarification_question?: string
  analysis?: { symptoms?: string[]; missing_info?: string[]; diagnostic_confidence?: string }
  verification?: { outcome?: string; reason?: string; follow_up_question?: string }
  reused_diagnostics?: Array<{ tool_name?: string; decision_reason?: string }>
}
type Verification = { outcome?: string; reason?: string; follow_up_question?: string }
type Escalation = {
  summary?: string; category?: string; priority?: string; symptoms?: string
  sources?: string[]; actions_attempted?: string[]; results?: string[]
  not_attempted_reason?: string; next_steps?: string[]; to?: string
}
type Page = 'help' | 'cases' | 'diagnostics' | 'operations'
type Tone = 'blue' | 'green' | 'amber' | 'red' | 'gray' | 'purple'
type IconName = 'spark' | 'cases' | 'pulse' | 'ops' | 'search' | 'settings' | 'send' |
  'check' | 'clock' | 'alert' | 'chevron' | 'apple' | 'dots' | 'arrow' |
  'shield' | 'menu' | 'close' | 'windows' | 'linux' | 'plus' | 'activity'

/* ─── Icons ─────────────────────────────────────────────────────────── */
const iconPaths: Record<IconName, React.ReactNode> = {
  spark: <><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><path d="m5.6 5.6 2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1"/><circle cx="12" cy="12" r="3"/></>,
  cases: <><path d="M4 7.5h16v11H4z"/><path d="M9 7.5V5h6v2.5M4 12h16"/></>,
  pulse: <><path d="M3 12h4l2-6 4 12 2-6h6"/></>,
  ops: <><path d="M5 19V9m7 10V5m7 14v-7"/><path d="M3 19h18"/></>,
  search: <><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></>,
  send: <><path d="m4 4 17 8-17 8 3-8-3-8Z"/><path d="M7 12h14"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  alert: <><path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 9v4m0 3h.01"/></>,
  chevron: <path d="m9 18 6-6-6-6"/>,
  apple: <><path d="M16.7 13.1c0-2.7 2.2-4 2.3-4.1-1.3-1.9-3.3-2.1-4-2.1-1.7-.2-3.3 1-4.2 1-.9 0-2.2-1-3.6-1-1.8 0-3.5 1.1-4.5 2.7-1.9 3.3-.5 8.2 1.4 10.9.9 1.3 2 2.8 3.5 2.7 1.4-.1 1.9-.9 3.6-.9s2.2.9 3.7.9c1.5 0 2.5-1.3 3.4-2.7 1.1-1.5 1.5-3 1.5-3.1-.1 0-3.1-1.2-3.1-4.3Z" transform="scale(.8) translate(3 -2)"/><path d="M14.8 5.1c.7-.9 1.2-2.1 1.1-3.3-1.1 0-2.4.7-3.2 1.6-.7.8-1.3 2-1.2 3.2 1.2.1 2.5-.6 3.3-1.5Z" transform="scale(.8) translate(3 -2)"/></>,
  dots: <><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></>,
  arrow: <><path d="M5 12h14"/><path d="m14 7 5 5-5 5"/></>,
  shield: <><path d="M12 3 5 6v5c0 4.5 2.8 8 7 10 4.2-2 7-5.5 7-10V6l-7-3Z"/><path d="m9 12 2 2 4-4"/></>,
  menu: <><path d="M4 7h16M4 12h16M4 17h16"/></>,
  close: <><path d="m6 6 12 12M18 6 6 18"/></>,
  windows: <><path d="M3 5.5 10.5 4.5v7H3zm0 13 7.5 1V12.5H3zM11.5 4.3 21 3v8.5h-9.5zm0 15.4L21 21v-8.5h-9.5z"/></>,
  linux: <><ellipse cx="12" cy="11" rx="4" ry="5"/><path d="M5.5 17.5s1-1 3-3m-3 3s-2.5 2-2 4.5c0 0 2.5 1.5 6 .5m-4-5 2 3m7.5-3.5s-1-1-3-3m3 3s2.5 2 2 4.5c0 0-2.5 1.5-6 .5m4-5-2 3"/></>,
  plus: <><path d="M12 5v14M5 12h14"/></>,
  activity: <><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></>,
}

function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {iconPaths[name]}
    </svg>
  )
}

function Status({ children, tone = 'blue' }: { children: React.ReactNode; tone?: Tone }) {
  return <span className={`status status-${tone}`}><span className="status-dot" />{children}</span>
}

function DataRow({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="data-row"><span>{label}</span><div>{children}</div></div>
}

/* ─── Sidebar ────────────────────────────────────────────────────────── */
const navPages: { id: Page; label: string; icon: IconName }[] = [
  { id: 'help', label: 'AI Help Desk', icon: 'spark' },
  { id: 'cases', label: 'Cases', icon: 'cases' },
  { id: 'diagnostics', label: 'Diagnostics', icon: 'pulse' },
  { id: 'operations', label: 'Operations', icon: 'ops' },
]

function Sidebar({ page, setPage, mobile, close, caseCount }: { page: Page; setPage: (p: Page) => void; mobile: boolean; close: () => void; caseCount: number }) {
  return (
    <aside className={`sidebar ${mobile ? 'sidebar-mobile' : ''}`}>
      <div className="brand">
        <div className="brandmark"><Icon name="activity" size={17} /></div>
        <div><strong>IT Autopsy</strong><span>Autonomous help desk</span></div>
        {mobile && <button className="icon-btn-ghost close-btn" onClick={close}><Icon name="close" size={16} /></button>}
      </div>
      <nav>
        {navPages.map(item => (
          <button key={item.id} className={`nav-item ${page === item.id ? 'active' : ''}`}
            onClick={() => { setPage(item.id); close() }}>
            <Icon name={item.icon} size={16} />
            <span>{item.label}</span>
            {item.id === 'cases' && caseCount > 0 && <b>{caseCount}</b>}
          </button>
        ))}
      </nav>
      <div className="side-bottom">
        <div className="system-state">
          <span className="live-dot" />
          <div><strong>All systems operational</strong><span>Diagnostics online</span></div>
        </div>
        <button className="nav-item"><Icon name="settings" size={16} /><span>Configuration</span></button>
        <div className="profile">
          <div className="avatar">MC</div>
          <div><strong>Maya Ortiz</strong><span>Support operator</span></div>
          <button className="icon-btn-ghost"><Icon name="dots" size={14} /></button>
        </div>
      </div>
    </aside>
  )
}

function Topbar({ page, openMenu }: { page: Page; openMenu: () => void }) {
  const label = navPages.find(p => p.id === page)?.label
  return (
    <header className="topbar">
      <button className="icon-btn mobile-menu" onClick={openMenu}><Icon name="menu" /></button>
      <div className="breadcrumb"><span className="crumb">Workspace /</span><strong>{label}</strong></div>
      <div className="top-actions">
        <label className="search-bar">
          <Icon name="search" size={14} />
          <input placeholder="Search cases" aria-label="Search" />
          <kbd>⌘ K</kbd>
        </label>
        <button className="icon-btn"><Icon name="settings" size={16} /></button>
      </div>
    </header>
  )
}

/* ─── AI Help Desk ───────────────────────────────────────────────────── */
function HelpDesk({
  run, loading, error, activeCaseId,
  issueText, setIssueText,
  osChoice, setOsChoice,
  allowReal, setAllowReal,
  approved, setApproved,
  conversation, verification, escalation,
  onSubmit, onContinue, onConfirm,
}: {
  run: RunState | null; loading: boolean; error: string | null; activeCaseId: string | null
  issueText: string; setIssueText: (v: string) => void
  osChoice: string; setOsChoice: (v: string) => void
  allowReal: boolean; setAllowReal: (v: boolean) => void
  approved: boolean; setApproved: (v: boolean) => void
  conversation: Array<{ role: string; content: string }>
  verification: Verification | null; escalation: Escalation | null
  onSubmit: (text: string) => void
  onContinue: (text: string) => void
  onConfirm: (resolved: boolean) => void
}) {
  const messagesRef = useRef<HTMLDivElement>(null)
  const [followUp, setFollowUp] = useState('')

  useEffect(() => {
    if (messagesRef.current) messagesRef.current.scrollTop = messagesRef.current.scrollHeight
  }, [conversation, loading])

  const state = run?.case_state || {}
  const priority = (run?.triage?.priority || '').toLowerCase()
  const statusTone: Tone = run?.escalated ? 'red' : run?.stopped_reason === 'observation_complete' ? 'green' : loading ? 'blue' : 'gray'

  const suggestions = ['Wi-Fi connected but no internet', 'Laptop running slowly', 'Application service unavailable', 'High memory usage']

  return (
    <div className="page-content help-page">
      <div className="page-intro">
        <div>
          <span className="demo-label">AI Investigation</span>
          <h1>AI Help Desk</h1>
          <p>Investigate, diagnose, and resolve IT issues with evidence at every step.</p>
        </div>
        <button className="btn-secondary" onClick={() => { setIssueText(''); }}>
          <Icon name="cases" size={15} /><span>New case</span>
        </button>
      </div>

      {run && (
        <div className="case-strip">
          <div className="case-strip-group"><span>Active case</span><strong>{run.case_id?.slice(0, 8) || activeCaseId?.slice(0, 8) || 'New'}</strong></div>
          <Status tone={statusTone}>{loading ? 'Investigating' : run.stopped_reason || 'Idle'}</Status>
          <span className="strip-sep" />
          <div className="case-strip-group"><span>Issue</span><strong>{run.issue.slice(0, 60)}{run.issue.length > 60 ? '…' : ''}</strong></div>
          <div className="case-strip-group strip-end">
            <span>Priority</span>
            <strong className={`priority-pill priority-${priority}`}>{run.triage?.priority || '—'}</strong>
          </div>
        </div>
      )}

      <div className="help-grid">
        {/* Left: Conversation */}
        <section className="workspace-panel conversation">
          <div className="panel-head">
            <div><span className="eyebrow">Case conversation</span><h2>Reported issue</h2></div>
            <button className="icon-btn-ghost"><Icon name="dots" size={16} /></button>
          </div>
          <div className="messages" ref={messagesRef}>
            {conversation.length === 0 ? (
              <div className="msg-empty">Describe your IT issue below to start an investigation.</div>
            ) : (
              conversation.map((msg, i) => (
                <div key={i} className={`message ${msg.role === 'user' ? 'user-msg' : 'agent-msg'} fade-in`}>
                  <div className="message-meta">
                    {msg.role === 'user'
                      ? <span className="avatar small">MC</span>
                      : <span className="agent-avatar"><Icon name="spark" size={13} /></span>}
                    <strong>{msg.role === 'user' ? 'You' : 'Autopsy Agent'}</strong>
                    <time>{new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })}</time>
                  </div>
                  <p>{msg.content}</p>
                </div>
              ))
            )}
            {loading && (
              <div className="message agent-msg fade-in">
                <div className="message-meta">
                  <span className="agent-avatar"><Icon name="spark" size={13} /></span>
                  <strong>Autopsy Agent</strong>
                </div>
                <div className="typing-indicator"><span /><span /><span /></div>
              </div>
            )}
            {run?.needs_clarification && (
              <div className="clarify">
                <span>Clarification needed</span>
                <strong>{run.clarification_question}</strong>
              </div>
            )}
            {verification?.outcome === 'needs_user_confirmation' && (
              <div className="clarify">
                <span>Verification required</span>
                <strong>{verification.reason}</strong>
                {verification.follow_up_question && <p style={{ fontSize: 11, marginTop: 6, color: '#4a5c7a' }}>{verification.follow_up_question}</p>}
                <div className="clarify-options">
                  <button className="clarify-btn" onClick={() => onConfirm(true)}>Issue resolved ✓</button>
                  <button className="clarify-btn" onClick={() => onConfirm(false)}>Still having issues</button>
                </div>
              </div>
            )}
            {(run?.escalated || verification?.outcome === 'escalated') && escalation && (
              <div className="escalation-box">
                <strong>Escalation required</strong>
                <p>{escalation.not_attempted_reason || 'Troubleshooting reached its limit.'}</p>
                {escalation.next_steps?.filter(Boolean).map((step, i) => <div key={i} className="escalation-step">→ {step}</div>)}
              </div>
            )}
          </div>
          <div className="composer-wrap">
            <div className="suggestions">
              {suggestions.map(s => <button key={s} className="suggestion-chip" onClick={() => setIssueText(s)}>{s}</button>)}
            </div>
            <div className="composer">
              <textarea
                id="chat-input"
                value={issueText}
                onChange={e => setIssueText(e.target.value)}
                placeholder="Describe the IT problem you're experiencing..."
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); run ? onContinue(issueText) : onSubmit(issueText) } }}
                rows={3}
              />
              <div className="composer-foot ">
                <div className="composer-opts">
                  <select value={osChoice} onChange={e => setOsChoice(e.target.value)} className="mini-select">
                    <option value="macos">macOS</option>
                    <option value="windows">Windows</option>
                    <option value="linux">Linux</option>
                    <option value="unknown">Unknown</option>
                  </select>
                  <label className="mini-check"><input type="checkbox" checked={allowReal} onChange={e => setAllowReal(e.target.checked)} /> Real diagnostics</label>
                  <label className="mini-check"><input type="checkbox" checked={approved} onChange={e => setApproved(e.target.checked)} /> Approve actions</label>
                </div>
                <button className="btn-primary" id="investigate-btn"
                  disabled={loading || !issueText.trim()}
                  onClick={() => run ? onContinue(issueText) : onSubmit(issueText)}>
                  <span>{loading ? 'Running…' : run ? 'Continue' : 'Investigate issue'}</span>
                  <Icon name="send" size={13} />
                </button>
              </div>
            </div>
            {run?.needs_clarification && (
              <div style={{ marginTop: 8 }}>
                <input className="followup-input" value={followUp} onChange={e => setFollowUp(e.target.value)}
                  placeholder="Answer the agent's question…" onKeyDown={e => { if (e.key === 'Enter') { onContinue(followUp); setFollowUp('') } }} />
              </div>
            )}
          </div>
        </section>

        {/* Middle: Live investigation timeline */}
        <section className="workspace-panel investigation">
          <div className="panel-head">
            <div><span className="eyebrow">Live investigation</span><h2>Diagnostic activity</h2></div>
            <Status tone={loading ? 'blue' : run?.escalated ? 'red' : run ? 'green' : 'gray'}>
              {loading ? 'Running' : run?.escalated ? 'Escalated' : run ? 'Complete' : 'Idle'}
            </Status>
          </div>
          <div className="timeline">
            {run ? (
              <>
                <div className="timeline-item done">
                  <div className="timeline-rail"><span><Icon name="check" size={11} /></span></div>
                  <div className="timeline-content">
                    <div className="timeline-title"><strong>Issue received</strong><time>—</time></div>
                    <p>{run.issue.slice(0, 80)}</p>
                  </div>
                </div>
                <div className="timeline-item done">
                  <div className="timeline-rail"><span><Icon name="check" size={11} /></span></div>
                  <div className="timeline-content">
                    <div className="timeline-title"><strong>Environment identified</strong><time>—</time></div>
                    <p>{run.os || 'unknown'} device confirmed by user.</p>
                  </div>
                </div>
                {(run.results || []).map((r, i) => (
                  <div key={i} className={`timeline-item ${r.ok ? 'done' : 'pending'}`}>
                    <div className="timeline-rail"><span>{r.ok ? <Icon name="check" size={11} /> : <Icon name="alert" size={11} />}</span></div>
                    <div className="timeline-content">
                      <div className="timeline-title"><strong>{r.tool_name}</strong><time>{r.ok ? '✓' : '✗'}</time></div>
                      <p>{r.summary || r.error}</p>
                      {r.simulated !== undefined && <span className={`diag-badge ${r.simulated ? 'sim' : ''}`}>{r.simulated ? 'Simulated' : 'Real diagnostic'}</span>}
                    </div>
                  </div>
                ))}
                {loading && (
                  <div className="timeline-item running">
                    <div className="timeline-rail"><span><span className="spinner" /></span></div>
                    <div className="timeline-content">
                      <div className="timeline-title"><strong>Evaluating evidence</strong><time>Now</time></div>
                      <p>Agent is processing diagnostics…</p>
                    </div>
                  </div>
                )}
                {state.next_step && !loading && (
                  <div className="timeline-item pending">
                    <div className="timeline-rail"><span>{(run.results || []).length + 3}</span></div>
                    <div className="timeline-content">
                      <div className="timeline-title"><strong>Next action selected</strong><time>—</time></div>
                      <p>{state.next_step}</p>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="msg-empty" style={{ padding: '20px 4px' }}>No active investigation. Submit an issue to begin.</div>
            )}
          </div>
          {loading && (
            <div className="activity-foot">
              <span className="spinner" />
              <span>Evaluating supporting and contradictory evidence…</span>
            </div>
          )}
        </section>

        {/* Right: Intelligence */}
        <aside className="workspace-panel intelligence">
          <div className="panel-head">
            <div><span className="eyebrow">Case intelligence</span><h2>Current assessment</h2></div>
            <Icon name="shield" size={18} />
          </div>
          <div className="intel-section overview">
            <DataRow label="Priority">
              <span className={`priority-pill priority-${priority}`}>{run?.triage?.priority || '—'}</span>
            </DataRow>
            <DataRow label="Status">
              <Status tone={statusTone}>{loading ? 'Investigating' : run?.stopped_reason || 'Idle'}</Status>
            </DataRow>
            <DataRow label="Operating system">
              <span className="os-tag">
                <Icon name={run?.os === 'windows' ? 'windows' : run?.os === 'linux' ? 'linux' : 'apple'} size={13} />
                {run?.os || osChoice}
              </span>
            </DataRow>
            <DataRow label="Category">{run?.triage?.category || '—'}</DataRow>
          </div>

          {run && (
            <>
              <div className="intel-section">
                <div className="section-label">
                  <span>Current hypothesis</span>
                  <span className="confidence-badge">{run.analysis?.diagnostic_confidence || 'low'} confidence</span>
                </div>
                <p className="hypothesis">{state.hypotheses?.[0]?.hypothesis || 'Awaiting analysis.'}</p>
                <small>{state.hypotheses?.[0]?.status === 'supported' ? 'Supported by evidence' : 'Probable explanation — not yet confirmed'}</small>
              </div>

              <div className="intel-section evidence">
                <div className="section-label">
                  <span>Evidence</span>
                  <b className="evidence-count">{state.hypotheses?.[0]?.supporting_evidence?.length || 0}</b>
                </div>
                {(state.hypotheses?.[0]?.supporting_evidence || []).map((docId, i) => (
                  <div key={i} className="evidence-item">
                    <span className="evidence-icon good"><Icon name="check" size={12} /></span>
                    <div><strong>{docId}</strong><span>Supporting evidence</span></div>
                  </div>
                ))}
                <div className="empty-evidence">
                  <span>Contradictory evidence</span>
                  <p>{state.hypotheses?.[0]?.contradictory_evidence?.length ? 'Found' : 'None collected yet'}</p>
                </div>
              </div>

              <div className="intel-section action-section">
                <div className="section-label"><span>Next recommended action</span></div>
                <strong>{state.next_step || 'Review cited guidance.'}</strong>
                <p>Identify the next safe diagnostic or remediation step before proposing a change.</p>
              </div>

              <div className="intel-section footer-data">
                <DataRow label="Approval">{run.escalated ? 'Escalation active' : 'Not yet required'}</DataRow>
                <DataRow label="Verification">{verification?.outcome || 'Not started'}</DataRow>
                <DataRow label="Reused diagnostics">{run.reused_diagnostics?.length || 0}</DataRow>
              </div>
            </>
          )}
          {!run && (
            <div className="intel-section"><p style={{ color: 'var(--muted)', fontSize: 11 }}>Submit an issue to see case intelligence here.</p></div>
          )}
        </aside>
      </div>
    </div>
  )
}

/* ─── Cases Page ─────────────────────────────────────────────────────── */
function statusToneF(s: string): Tone {
  if (s === 'resolved') return 'green'
  if (s === 'escalated') return 'red'
  if (s === 'awaiting_approval') return 'amber'
  if (s === 'open') return 'gray'
  return 'blue'
}
function priTone(p: string): string {
  if (p === 'critical') return 'priority-critical'
  if (p === 'high') return 'priority-high'
  if (p === 'low') return 'priority-low'
  return 'priority-medium'
}

function CasesPage({ cases, loading, onOpen, onRefresh }: { cases: CaseRow[]; loading: boolean; onOpen: (id: string) => void; onRefresh: () => void }) {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [priorityFilter, setPriorityFilter] = useState('all')
  const [selected, setSelected] = useState<CaseRow | null>(cases[0] || null)
  const [activeTab, setActiveTab] = useState('Overview')

  const filtered = cases.filter(c => {
    const matchS = search === '' || c.id.toLowerCase().includes(search.toLowerCase()) || c.issue_text.toLowerCase().includes(search.toLowerCase())
    const matchSt = statusFilter === 'all' || c.status === statusFilter
    const matchP = priorityFilter === 'all' || c.priority === priorityFilter
    return matchS && matchSt && matchP
  })

  return (
    <div className="page-content standard-page">
      <div className="page-intro">
        <div><h1>Cases</h1><p>Track investigations from initial report through verified resolution.</p></div>
        <button className="btn-primary" id="start-investigation-btn" onClick={onRefresh}>
          <Icon name="spark" size={15} /><span>Start investigation</span>
        </button>
      </div>
      <div className="toolbar">
        <label className="filter-search">
          <Icon name="search" size={15} />
          <input placeholder="Search case ID or issue description" value={search} onChange={e => setSearch(e.target.value)} id="case-search" />
        </label>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} id="status-filter">
          <option value="all">All statuses</option>
          <option value="open">Open</option>
          <option value="investigating">Investigating</option>
          <option value="awaiting_approval">Awaiting Approval</option>
          <option value="escalated">Escalated</option>
          <option value="resolved">Resolved</option>
        </select>
        <select value={priorityFilter} onChange={e => setPriorityFilter(e.target.value)} id="priority-filter">
          <option value="all">All priorities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        <select id="category-filter">
          <option>All categories</option>
          <option>System performance</option>
          <option>Network</option>
        </select>
      </div>
      <div className="split-view">
        <div className="table-card">
          <div className="table-head-row">
            <strong>All cases</strong>
            <span>{cases.length} total</span>
          </div>
          <div className="case-table-wrap">
            {loading ? (
              <div className="empty-state">Loading cases…</div>
            ) : (
              <table className="case-table">
                <thead>
                  <tr><th>Case</th><th>Issue summary</th><th>Priority</th><th>Status</th><th>OS</th><th>Updated</th><th>Outcome</th></tr>
                </thead>
                <tbody>
                  {filtered.map(c => (
                    <tr key={c.id} className={selected?.id === c.id ? 'selected' : ''}
                      onClick={() => { setSelected(c); setActiveTab('Overview') }}>
                      <td><strong className="case-id">{c.id.slice(0, 8).toUpperCase()}</strong></td>
                      <td className="summary-cell">{c.issue_text.slice(0, 70)}{c.issue_text.length > 70 ? '…' : ''}</td>
                      <td><span className={`priority-pill ${priTone(c.priority || '')}`}>{c.priority || '—'}</span></td>
                      <td><Status tone={statusToneF(c.status)}>{c.status.replace(/_/g, ' ')}</Status></td>
                      <td><span className="os-tag"><Icon name={c.os === 'windows' ? 'windows' : c.os === 'linux' ? 'linux' : 'apple'} size={12} />{c.os}</span></td>
                      <td>{c.updated_at ? new Date(c.updated_at).toLocaleTimeString() : '—'}</td>
                      <td>{c.outcome || 'Not verified'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {!loading && filtered.length === 0 && <div className="empty-state">No cases match your filters.</div>}
          </div>
        </div>
        {selected && (
          <aside className="case-detail slide-in" key={selected.id}>
            <div className="detail-head">
              <div>
                <span className="detail-case-id">{selected.id.slice(0, 8).toUpperCase()}</span>
                <h2>{selected.issue_text.slice(0, 60)}{selected.issue_text.length > 60 ? '…' : ''}</h2>
              </div>
              <button className="icon-btn-ghost" onClick={() => setSelected(null)}><Icon name="close" size={15} /></button>
            </div>
            <div className="detail-tags">
              <Status tone={statusToneF(selected.status)}>{selected.status.replace(/_/g, ' ')}</Status>
              {selected.priority && <span className={`priority-pill ${priTone(selected.priority)}`}>{selected.priority}</span>}
              <span className="os-tag"><Icon name={selected.os === 'windows' ? 'windows' : selected.os === 'linux' ? 'linux' : 'apple'} size={13} />{selected.os}</span>
            </div>
            <div className="detail-tabs">
              {['Overview', 'Evidence', 'Actions'].map(tab => (
                <button key={tab} className={activeTab === tab ? 'active' : ''} onClick={() => setActiveTab(tab)}>{tab}</button>
              ))}
            </div>
            {activeTab === 'Overview' && (
              <div className="detail-block">
                <span className="eyebrow">Issue</span>
                <strong>{selected.issue_text}</strong>
                <p style={{ marginTop: 10 }}>Created: {new Date(selected.created_at).toLocaleString()}</p>
                <p>Outcome: {selected.outcome || 'Not verified'}</p>
              </div>
            )}
            {activeTab === 'Evidence' && (
              <div className="detail-block"><p style={{ color: 'var(--muted)', fontSize: 11 }}>No evidence linked yet.</p></div>
            )}
            {activeTab === 'Actions' && (
              <div className="detail-block"><p style={{ color: 'var(--muted)', fontSize: 11 }}>No approved actions yet.</p></div>
            )}
            <button className="btn-primary full-width detail-action-btn" onClick={() => onOpen(selected.id)}>
              Open in AI Help Desk <Icon name="arrow" size={14} />
            </button>
          </aside>
        )}
      </div>
    </div>
  )
}

/* ─── Diagnostics Page ───────────────────────────────────────────────── */
function DiagnosticsPage({ tools, loading }: { tools: any[]; loading: boolean }) {
  const [expanded, setExpanded] = useState<number | null>(null)
  const [search, setSearch] = useState('')

  const filtered = tools.filter(t => search === '' || (t.name || t.tool_name || '').toLowerCase().includes(search.toLowerCase()))

  return (
    <div className="page-content standard-page">
      <div className="page-intro">
        <div><h1>Diagnostics</h1><p>Inspect approved diagnostic executions and the evidence they produced.</p></div>
        <div className="legend">
          <span><i className="legend-dot real" />Real execution</span>
          <span><i className="legend-dot simulated" />Simulated</span>
        </div>
      </div>
      <div className="toolbar">
        <label className="filter-search">
          <Icon name="search" size={15} />
          <input placeholder="Search diagnostic tools" value={search} onChange={e => setSearch(e.target.value)} id="diag-search" />
        </label>
        <select id="diag-cat-filter"><option>All categories</option><option>System resources</option><option>Network</option></select>
        <select id="diag-platform-filter"><option>All platforms</option><option>macOS</option><option>Windows</option><option>Linux</option></select>
        <select id="diag-status-filter"><option>All statuses</option><option>Completed</option><option>Failed</option><option>Pending</option></select>
      </div>
      <div className="diagnostics-list">
        <div className="diag-header">
          <span>Diagnostic tool</span><span>Environment</span><span>Execution</span>
          <span>Status &amp; result</span><span>Case</span><span>Last run</span><span />
        </div>
        {loading && <div className="empty-state">Loading diagnostics…</div>}
        {!loading && filtered.length === 0 && <div className="empty-state">No diagnostic tools found.</div>}
        {filtered.map((d, i) => {
          const name = d.name || d.tool_name || 'Unknown tool'
          const category = d.category || 'General'
          const simulated = d.simulated ?? false
          const ok = d.ok ?? true
          const status = ok ? 'Completed' : 'Failed'
          const caseId = d.case_id?.slice(0, 8).toUpperCase() || '—'
          const lastRun = d.executed_at ? new Date(d.executed_at).toLocaleTimeString() : '—'
          return (
            <div key={i} className={`diag-row ${expanded === i ? 'expanded' : ''}`}
              onClick={() => setExpanded(expanded === i ? null : i)}>
              <div className="tool-name">
                <span className="tool-icon"><Icon name={ok ? 'activity' : 'alert'} size={15} /></span>
                <div><strong>{name}</strong><small>{category}</small></div>
              </div>
              <span>{d.os || 'All platforms'}</span>
              <span className={`exec-badge ${simulated ? 'sim' : 'real'}`}>{simulated ? 'Simulated' : 'Real'}</span>
              <div>
                <Status tone={ok ? 'green' : 'red'}>{status}</Status>
                <small>{d.summary || d.error || '—'}</small>
              </div>
              <strong className="case-link-text">{caseId}</strong>
              <span>{lastRun}</span>
              <span className={`chevron-icon ${expanded === i ? 'open' : ''}`}><Icon name="chevron" size={14} /></span>
              {expanded === i && (
                <div className="evidence-detail fade-in">
                  <div><span>Purpose</span><p>{d.description || 'Diagnostic tool execution.'}</p></div>
                  <div><span>Evidence detail</span><p>{d.summary || d.error || 'No result recorded.'}</p></div>
                  <div><span>Safety</span><p>{simulated ? 'Simulated diagnostic · Safe' : 'Read-only diagnostic · Approved tool'}</p></div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* ─── Operations Page ────────────────────────────────────────────────── */
function OperationsPage({ summary, failures, cases, loading }: { summary: any; failures: any[]; cases: CaseRow[]; loading: boolean }) {
  const statusCounts = summary?.status_counts || {}
  const priorityCounts = summary?.priority_counts || {}

  const metrics = [
    { label: 'Open cases', value: String(statusCounts.open || 0), sub: 'Active', attention: false },
    { label: 'Investigating', value: String(statusCounts.investigating || 0), sub: 'In progress', attention: true },
    { label: 'Awaiting approval', value: String(statusCounts.awaiting_approval || 0), sub: 'Operator action needed', attention: true },
    { label: 'Resolved', value: String(statusCounts.resolved || 0), sub: 'Last 7 days', attention: false },
    { label: 'Escalated', value: String(statusCounts.escalated || 0), sub: 'Engineering notified', attention: false },
    { label: 'Tool failures', value: String(failures.length), sub: 'Recent failures', attention: false },
  ]

  return (
    <div className="page-content standard-page">
      <div className="page-intro">
        <div><h1>Operations</h1><p>Actionable workload and resolution health for support operators.</p></div>
        <span className="updated-badge"><span className="live-dot" />Updated just now</span>
      </div>

      {loading ? <div className="empty-state">Loading operations data…</div> : (
        <>
          <div className="metric-grid">
            {metrics.map(m => (
              <div key={m.label} className={`metric-card ${m.attention ? 'attention' : ''}`}>
                <span>{m.label}</span>
                <strong>{m.value}</strong>
                <small>{m.sub}</small>
              </div>
            ))}
          </div>

          <div className="ops-grid">
            <section className="ops-panel">
              <div className="panel-head">
                <div><span className="eyebrow">Priority queue</span><h2>Requires attention</h2></div>
                <button className="text-link-btn">View all <Icon name="arrow" size={13} /></button>
              </div>
              <div className="attention-list">
                {cases.filter(c => c.status === 'awaiting_approval' || c.status === 'escalated' || c.status === 'investigating').slice(0, 4).map(c => (
                  <div key={c.id} className="attention-item">
                    <span className={`attention-icon ${c.status === 'escalated' ? 'red' : c.status === 'awaiting_approval' ? 'amber' : 'blue'}`}>
                      <Icon name={c.status === 'escalated' ? 'alert' : c.status === 'awaiting_approval' ? 'clock' : 'pulse'} size={15} />
                    </span>
                    <div className="attention-body">
                      <strong>{c.id.slice(0, 8).toUpperCase()} · {c.status.replace(/_/g, ' ')}</strong>
                      <span>{c.issue_text.slice(0, 60)}</span>
                    </div>
                    <time>{c.updated_at ? new Date(c.updated_at).toLocaleTimeString() : '—'}</time>
                  </div>
                ))}
                {cases.filter(c => ['awaiting_approval', 'escalated', 'investigating'].includes(c.status)).length === 0 && (
                  <div className="attention-item"><span style={{ color: 'var(--muted)', fontSize: 11 }}>No items require attention.</span></div>
                )}
              </div>
            </section>

            <section className="ops-panel">
              <div className="panel-head">
                <div><span className="eyebrow">Latest activity</span><h2>Recent cases</h2></div>
                <button className="text-link-btn">Open cases <Icon name="arrow" size={13} /></button>
              </div>
              <div className="recent-list">
                {cases.slice(0, 4).map(c => (
                  <div key={c.id} className="recent-item">
                    <div className="recent-body">
                      <strong>{c.id.slice(0, 8).toUpperCase()}</strong>
                      <span>{c.issue_text.slice(0, 55)}{c.issue_text.length > 55 ? '…' : ''}</span>
                    </div>
                    <Status tone={statusToneF(c.status)}>{c.status.replace(/_/g, ' ')}</Status>
                    <time>{c.updated_at ? new Date(c.updated_at).toLocaleTimeString() : '—'}</time>
                  </div>
                ))}
                {cases.length === 0 && <div className="attention-item"><span style={{ color: 'var(--muted)', fontSize: 11 }}>No cases yet.</span></div>}
              </div>
            </section>
          </div>

          {failures.length > 0 && (
            <div className="ops-panel" style={{ marginTop: 12, padding: '14px 15px' }}>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 10 }}>Recent diagnostic failures</div>
              {failures.slice(0, 6).map((f, i) => (
                <div key={i} style={{ display: 'flex', gap: 10, padding: '8px 0', borderTop: '1px solid var(--line-soft)', fontSize: 11 }}>
                  <span style={{ color: 'var(--muted)' }}>{f.ts}</span>
                  <strong>{f.case_id?.slice(0, 8)}</strong>
                  <span>{f.tool_name}</span>
                  <Status tone="red">{f.status}</Status>
                </div>
              ))}
            </div>
          )}

          <div className="data-note">
            <Icon name="alert" size={15} />
            <div>
              <strong>Mean time to resolution is not shown.</strong>
              <span>
                {summary?.average_resolution_seconds != null
                  ? `Average: ${Math.round(summary.average_resolution_seconds)} seconds`
                  : 'There is not enough verified resolution data to calculate a reliable value.'}
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

/* ─── Root App ───────────────────────────────────────────────────────── */
export default function App() {
  const [page, setPage] = useState<Page>('help')
  const [menu, setMenu] = useState(false)
  const [loading, setLoading] = useState(false)
  const [pageLoading, setPageLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Help desk state
  const [issueText, setIssueText] = useState('')
  const [osChoice, setOsChoice] = useState('macos')
  const [allowReal, setAllowReal] = useState(false)
  const [approved, setApproved] = useState(false)
  const [run, setRun] = useState<RunState | null>(null)
  const [verification, setVerification] = useState<Verification | null>(null)
  const [escalation, setEscalation] = useState<Escalation | null>(null)
  const [conversation, setConversation] = useState<Array<{ role: string; content: string }>>([])
  const [activeCaseId, setActiveCaseId] = useState<string | null>(null)

  // Cases state
  const [cases, setCases] = useState<CaseRow[]>([])
  const [caseQuery, setCaseQuery] = useState('')
  const [caseStatus, setCaseStatus] = useState('all')
  const [casePriority, setCasePriority] = useState('all')

  // Diagnostics & operations state
  const [tools, setTools] = useState<any[]>([])
  const [failures, setFailures] = useState<any[]>([])
  const [summary, setSummary] = useState<any>(null)

  const loadCases = async () => {
    try {
      const data = await api.listCases(caseQuery, caseStatus, casePriority)
      setCases(data.cases || [])
    } catch { /* backend may not be running */ }
  }

  const loadDiagnostics = async () => {
    setPageLoading(true)
    try {
      const data = await api.tools()
      setTools(data.tools || [])
    } catch { } finally { setPageLoading(false) }
  }

  const loadOperations = async () => {
    setPageLoading(true)
    try {
      const [s, f] = await Promise.all([api.operationsSummary(), api.operationsFailures()])
      setSummary(s.summary)
      setFailures(f.failures || [])
    } catch { } finally { setPageLoading(false) }
  }

  useEffect(() => { loadCases() }, [caseQuery, caseStatus, casePriority])
  useEffect(() => {
    if (page === 'diagnostics') loadDiagnostics()
    else if (page === 'operations') loadOperations()
  }, [page])

  const runCase = async (text: string, continueId: string | null = null) => {
    if (!text.trim()) return
    setLoading(true); setError(null)
    try {
      const data = continueId
        ? await api.continueCase(continueId, text, osChoice, allowReal, approved)
        : await api.createCase(text, osChoice, allowReal, approved)
      setRun(data.run)
      setVerification(data.verification)
      setEscalation(data.escalation)
      setActiveCaseId(data.run.case_id)
      const entry = { role: 'assistant', content: data.run.stopped_reason || 'Agent completed.' }
      setConversation(curr => continueId
        ? [...curr, { role: 'user', content: text }, entry]
        : [{ role: 'user', content: text }, entry])
      setIssueText('')
      await loadCases()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to run agent')
    } finally { setLoading(false) }
  }

  const openCase = async (id: string) => {
    setPage('help')
    try {
      const data = await api.getCase(id)
      const c = data.case
      setActiveCaseId(id)
      setConversation(Array.isArray(c.conversation) ? c.conversation : [])
      if (c.issue_text) setIssueText(c.issue_text)
    } catch { }
  }

  const confirmResolution = async (resolved: boolean) => {
    if (!activeCaseId) return
    try {
      const data = await api.confirmCase(activeCaseId, resolved)
      setVerification(data.verification)
      await loadCases()
    } catch { }
  }

  return (
    <div className="app-shell">
      <Sidebar page={page} setPage={setPage} mobile={false} close={() => {}} caseCount={cases.length} />
      {menu && (
        <>
          <div className="overlay" onClick={() => setMenu(false)} />
          <Sidebar page={page} setPage={setPage} mobile close={() => setMenu(false)} caseCount={cases.length} />
        </>
      )}
      <main>
        <Topbar page={page} openMenu={() => setMenu(true)} />
        {error && (
          <div className="error-bar">
            <Icon name="alert" size={14} />{error}
            <button onClick={() => setError(null)}><Icon name="close" size={13} /></button>
          </div>
        )}
        {page === 'help' && (
          <HelpDesk
            run={run} loading={loading} error={error} activeCaseId={activeCaseId}
            issueText={issueText} setIssueText={setIssueText}
            osChoice={osChoice} setOsChoice={setOsChoice}
            allowReal={allowReal} setAllowReal={setAllowReal}
            approved={approved} setApproved={setApproved}
            conversation={conversation} verification={verification} escalation={escalation}
            onSubmit={text => runCase(text)}
            onContinue={text => runCase(text, activeCaseId)}
            onConfirm={confirmResolution}
          />
        )}
        {page === 'cases' && (
          <CasesPage cases={cases} loading={pageLoading} onOpen={openCase} onRefresh={loadCases} />
        )}
        {page === 'diagnostics' && (
          <DiagnosticsPage tools={tools} loading={pageLoading} />
        )}
        {page === 'operations' && (
          <OperationsPage summary={summary} failures={failures} cases={cases} loading={pageLoading} />
        )}
      </main>
    </div>
  )
}
