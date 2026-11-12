import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardPaste,
  FileJson,
  Info,
  Play,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  Wand2,
} from 'lucide-react'
import clsx from 'clsx'
import { api, ApiError, type ParseIssue } from '../lib/api'
import { Badge, Button, Card, Field, SectionTitle, inputClass } from '../components/ui'

const SAMPLE = `{
  "execution_id": "exec-001",
  "agent_name": "Research Agent",
  "status": "failed",
  "steps": [
    { "type": "task", "input": "Summarise the latest RAG papers" },
    { "type": "tool_call", "tool_name": "fetch_page",
      "tool_arguments": { "url": "https://arxiv.org/abs/2404.1" },
      "status": "failed",
      "error": "ReadTimeout: read timed out after 30s" }
  ]
}`

type Mode = 'upload' | 'paste'

/** Shown when /api/formats is unreachable, so the panel never hangs on "Loading…". */
const FALLBACK_FORMATS = [
  { id: 'openai', label: 'OpenAI Chat / Responses / Agents SDK' },
  { id: 'anthropic', label: 'Anthropic Messages' },
  { id: 'langsmith', label: 'LangSmith runs' },
  { id: 'otlp', label: 'OTLP GenAI spans' },
  { id: 'mcp', label: 'MCP JSON-RPC' },
  { id: 'generic', label: 'Generic JSON / JSONL trace' },
]

export default function AnalyzePage() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<Mode>('paste')
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [agentName, setAgentName] = useState('')
  const [framework, setFramework] = useState('')
  const [task, setTask] = useState('')
  const [expected, setExpected] = useState('')
  const [useLlm, setUseLlm] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [issue, setIssue] = useState<ParseIssue | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [llmAvailable, setLlmAvailable] = useState<boolean | null>(null)
  const [formats, setFormats] = useState<{ id: string; label: string }[]>([])
  const [formatsError, setFormatsError] = useState<string | null>(null)
  const [demos, setDemos] = useState<{ id: string; label: string }[]>([])
  const [demoLoading, setDemoLoading] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api
      .settings()
      .then((s) => setLlmAvailable(s.ai.enabled))
      .catch(() => setLlmAvailable(null))
    api
      .formats()
      .then((f) => {
        setFormats(f.formats)
        setFormatsError(null)
      })
      .catch((e) => {
        setFormats(FALLBACK_FORMATS)
        setFormatsError(e instanceof Error ? e.message : 'Could not load formats from the API')
      })
    api
      .demos()
      .then((d) => setDemos(d.demos))
      .catch(() => setDemos([]))
  }, [])

  const lineCount = useMemo(() => text.split('\n').length, [text])
  const validation = useMemo(() => {
    if (!text.trim()) return { ok: false, message: 'Paste a trace to continue.' }
    const first = text.trim()[0]
    if (first !== '{' && first !== '[') return { ok: false, message: 'The trace should start with "{" or "[".' }
    return { ok: true, message: `Looks like JSON${lineCount > 1 ? '/JSONL' : ''} · ${text.length.toLocaleString()} characters` }
  }, [text, lineCount])

  const onDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    setDragging(false)
    const dropped = event.dataTransfer.files?.[0]
    if (dropped) {
      setFile(dropped)
      setMode('upload')
    }
  }, [])

  const submit = async () => {
    setError(null)
    setIssue(null)
    setSubmitting(true)
    try {
      let result
      if (mode === 'upload') {
        if (!file) {
          setError('Choose a file to upload first.')
          setSubmitting(false)
          return
        }
        const form = new FormData()
        form.append('file', file)
        if (agentName) form.append('agent_name', agentName)
        if (framework) form.append('framework', framework)
        if (task) form.append('task_description', task)
        if (expected) form.append('expected_output', expected)
        form.append('use_llm', String(useLlm))
        result = await api.upload(form)
      } else {
        result = await api.paste({
          text,
          agent_name: agentName || undefined,
          framework: framework || undefined,
          task_description: task || undefined,
          expected_output: expected || undefined,
          use_llm: useLlm,
        })
      }
      navigate(`/executions/${result.execution.id}`)
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 422 && e.detail && typeof e.detail === 'object' && 'message' in (e.detail as object)) {
          setIssue(e.detail as ParseIssue)
        } else {
          setError(e.message)
        }
      } else {
        setError(e instanceof Error ? e.message : 'Unknown error')
      }
    } finally {
      setSubmitting(false)
    }
  }

  const runDemo = async (id: string) => {
    setDemoLoading(id)
    setError(null)
    try {
      const result = await api.demo(id, useLlm)
      navigate(`/executions/${result.execution.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Demo failed')
    } finally {
      setDemoLoading(null)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight text-app">Analyze Agent</h1>
        <p className="mt-0.5 text-[13px] text-muted-app">
          Upload or paste an execution trace. We parse it, detect failures, classify them and explain the root cause.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-4 lg:col-span-2">
          <div className="flex items-center gap-1 rounded-lg border border-app p-0.5 w-fit">
            {(
              [
                { id: 'paste', label: 'Paste logs', icon: ClipboardPaste },
                { id: 'upload', label: 'Upload file', icon: UploadCloud },
              ] as const
            ).map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => setMode(id)}
                className={clsx(
                  'flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12.5px] font-medium transition-colors',
                  mode === id ? 'bg-brand-600 text-white' : 'text-muted-app hover:text-app',
                )}
              >
                <Icon className="h-3.5 w-3.5" /> {label}
              </button>
            ))}
          </div>

          {mode === 'paste' ? (
            <div className="mt-3">
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-[12px] font-medium text-app">Trace (JSON / JSONL / text log)</span>
                <button
                  onClick={() => setText(SAMPLE)}
                  className="flex items-center gap-1 text-[11.5px] font-medium text-brand-600 dark:text-brand-400 hover:underline"
                >
                  <Wand2 className="h-3 w-3" /> Insert example
                </button>
              </div>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                spellCheck={false}
                placeholder={'Paste an OpenAI, Anthropic, LangSmith, OTLP, MCP or generic JSON trace…'}
                className="h-[300px] w-full resize-y rounded-lg border border-app bg-[var(--surface-muted)] p-3 font-mono text-[12px] leading-5 text-app outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
              />
              <div className="mt-1.5 flex items-center justify-between text-[11.5px]">
                <span className={validation.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-app'}>
                  {validation.message}
                </span>
                <span className="text-muted-app">{lineCount} lines</span>
              </div>
            </div>
          ) : (
            <div className="mt-3">
              <div
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
                onClick={() => fileInput.current?.click()}
                className={clsx(
                  'flex h-[300px] cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed text-center transition-colors',
                  dragging ? 'border-brand-500 bg-brand-500/5' : 'border-app hover:border-brand-400',
                )}
              >
                <FileJson className="h-7 w-7 text-muted-app" />
                <p className="text-[13px] font-medium text-app">
                  {file ? file.name : 'Drop a .json / .jsonl / .txt trace here'}
                </p>
                <p className="text-[11.5px] text-muted-app">
                  {file ? `${(file.size / 1024).toFixed(1)} KB selected` : 'or click to browse · max 10 MB'}
                </p>
                <input
                  ref={fileInput}
                  type="file"
                  accept=".json,.jsonl,.ndjson,.txt,.log"
                  className="hidden"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              </div>
            </div>
          )}

          {issue && (
            <div className="mt-3 rounded-lg border border-rose-500/25 bg-rose-500/5 p-3">
              <p className="flex items-center gap-1.5 text-[12.5px] font-semibold text-rose-600 dark:text-rose-400">
                <AlertTriangle className="h-3.5 w-3.5" /> Could not parse this trace
              </p>
              <p className="mt-1 text-[12.5px] text-app">{issue.message}</p>
              {issue.hint && <p className="mt-1 text-[11.5px] text-muted-app">Hint: {issue.hint}</p>}
            </div>
          )}
          {error && (
            <div className="mt-3 rounded-lg border border-rose-500/25 bg-rose-500/5 p-3 text-[12.5px] text-rose-600 dark:text-rose-400">
              {error}
            </div>
          )}

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Field label="Agent name" hint="Optional — overrides the name found in the trace">
              <input className={inputClass} value={agentName} onChange={(e) => setAgentName(e.target.value)} placeholder="Research Agent" />
            </Field>
            <Field label="Framework" hint="Optional — e.g. langchain, langgraph, openai-agents">
              <input className={inputClass} value={framework} onChange={(e) => setFramework(e.target.value)} placeholder="langgraph" />
            </Field>
            <Field label="Expected task" hint="What the agent was supposed to do">
              <input className={inputClass} value={task} onChange={(e) => setTask(e.target.value)} placeholder="Find the Pro plan price" />
            </Field>
            <Field label="Expected output" hint="Optional — used to judge correctness">
              <input className={inputClass} value={expected} onChange={(e) => setExpected(e.target.value)} placeholder="$49 per month" />
            </Field>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-[12.5px] text-app">
              <input
                type="checkbox"
                checked={useLlm}
                onChange={(e) => setUseLlm(e.target.checked)}
                className="h-4 w-4 rounded border-app accent-brand-600"
              />
              Use LLM analysis
              {llmAvailable === false && <Badge tone="warning">no API key configured</Badge>}
              {llmAvailable === true && <Badge tone="success">LLM ready</Badge>}
            </label>
            <Button variant="primary" onClick={submit} loading={submitting} disabled={mode === 'paste' ? !text.trim() : !file}>
              <Sparkles className="h-3.5 w-3.5" /> Analyze
            </Button>
          </div>
          {llmAvailable === false && (
            <p className="mt-2 flex items-start gap-1.5 text-[11.5px] text-muted-app">
              <Info className="mt-0.5 h-3 w-3 shrink-0" />
              Without an LLM key the analysis uses the rule engine and the ML classifier only. Add LLM_API_KEY in
              backend/.env to enable contextual reasoning.
            </p>
          )}
        </Card>

        <div className="space-y-4">
          <Card className="p-4">
            <SectionTitle title="Supported formats" description="Detected automatically" />
            <ul className="mt-2.5 space-y-1.5">
              {formats.map((f) => (
                <li key={f.id} className="flex items-center gap-2 text-[12.5px] text-app">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" /> {f.label}
                </li>
              ))}
              {formats.length === 0 && <li className="text-[12px] text-muted-app">No formats reported.</li>}
            </ul>
            {formatsError && (
              <p className="mt-2 rounded-md border border-amber-500/25 bg-amber-500/5 p-2 text-[11px] text-amber-700 dark:text-amber-400">
                Could not reach the API for the live list ({formatsError}). Showing the built-in list — analysis will not
                work until the backend is running.
              </p>
            )}
          </Card>

          <Card className="p-4">
            <SectionTitle title="Demo traces" description="Real analysis of clearly-labelled sample traces" />
            <div className="mt-2.5 space-y-1.5">
              {demos.map((d) => (
                <button
                  key={d.id}
                  onClick={() => runDemo(d.id)}
                  disabled={demoLoading !== null}
                  className="flex w-full items-center gap-2 rounded-lg border border-app px-2.5 py-2 text-left text-[12.5px] text-app hover:border-brand-400 hover:bg-brand-500/5 disabled:opacity-60"
                >
                  <Play className="h-3 w-3 text-muted-app" />
                  <span className="flex-1">{d.label}</span>
                </button>
              ))}
            </div>
          </Card>

          <Card className="p-4">
            <div className="flex items-center gap-2 text-[12.5px] font-medium text-app">
              <ShieldCheck className="h-4 w-4 text-emerald-500" /> Safety
            </div>
            <ul className="mt-2 space-y-1.5 text-[11.5px] text-muted-app">
              <li>· Uploaded code or commands are never executed — traces are only read.</li>
              <li>· API keys, tokens, emails and phone numbers are redacted before storage.</li>
              <li>· Files over 10 MB and unknown extensions are rejected.</li>
            </ul>
          </Card>
        </div>
      </div>
    </div>
  )
}
