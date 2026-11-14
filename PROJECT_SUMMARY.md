# Agent Autopsy — project handoff summary

This file is written to be handed to another AI so it can understand the project
without reading every source file. Everything below is verifiable from the code
in this folder.

---

## 1. What this project is

**Agent Autopsy** is a web app that debugs **AI agent runs**. You give it a
trace (the log of what an agent did: its LLM calls, tool calls, errors, retries,
final answer). It tells you **what went wrong, how badly, why, what evidence
proves it, and what code fixes it** — and keeps every run in a searchable
history with exports and a side-by-side comparison view.

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy, SQLite (Postgres-compatible URL).
- **Frontend:** React 18, Vite, TypeScript, Tailwind, Recharts.
- **One process, one port.** The FastAPI app serves the built SPA and the API on
  `http://127.0.0.1:8000`, so `/`, `/history`, `/analytics` and `/api/*` share one
  origin.
- Size: ~8 950 lines of Python (`backend/app`), ~3 800 lines of TS/TSX
  (`frontend/src`), 339 backend tests.

### The core design principle (important)

> **Rules are authoritative, the ML classifier is a hint, the LLM layer is
> optional.**

1. **Rules** decide *what* failed, and every rule must quote the log text that
   fired it (no quote → no detection).
2. **The ML classifier** (scikit-learn over a small labelled corpus) is recorded
   as a second opinion with its confidence and whether it agrees. It only becomes
   the diagnosis if **no rule matched**, and the UI says so.
3. **The LLM** (OpenAI / Groq / Anthropic, optional) writes prose explanation
   only. It never decides the category, never produces the code snippets, and the
   app is fully functional with no key at all.

Consequences that shape the whole codebase:
- Nothing in the analysis path executes uploaded content. Traces are parsed as
  data only; generated code snippets are displayed as text, never run by the app
  (only the test suite compiles/executes them, in-process, against stubs).
- Secrets are redacted at three points: on write, at the API boundary, and by a
  startup backfill for rows stored before redaction existed.

---

## 2. Folder layout

```
fai_m_p2/
├── README.md                 setup, config table, API table, security, test map
├── HEALTHY_RUNS.md           why the dashboard said "100% failure" and what changed
├── .gitignore                ignores .env, *.db, backups/, .venv, node_modules, dist
├── backend/
│   ├── requirements.txt      fastapi, uvicorn, SQLAlchemy, pydantic(-settings),
│   │                         python-multipart, httpx, groq, scikit-learn, numpy,
│   │                         joblib, reportlab, pytest, requests (tests only)
│   ├── run.sh                dev server
│   ├── .env.example          variable NAMES + placeholders only
│   ├── app/
│   │   ├── main.py           app factory, lifespan (init db → warm ML → seed demos),
│   │   │                     SPA fallback, JSON error handlers
│   │   ├── core/
│   │   │   ├── config.py     all settings, read once from env / .env
│   │   │   ├── models.py     enums (Status, Severity, FailureCategory, StepType)
│   │   │   │                 + dataclasses (TraceStep, NormalizedTrace)
│   │   │   └── redact.py     secret/PII redaction (keys, bearer, passwords, email…)
│   │   ├── db/
│   │   │   ├── database.py   SQLAlchemy models: Execution, Analysis
│   │   │   ├── migrations.py startup: add columns, backup, run data migrations
│   │   │   └── migrations_data.py  backfills (e.g. redaction of old rows)
│   │   ├── ingest/           one parser per format + detector + utils
│   │   │   ├── detector.py   sniff which format a blob is
│   │   │   ├── openai.py  anthropic.py  langsmith.py  otlp.py  mcp.py  generic.py
│   │   │   └── util.py       safe_filename, as_text, ParseError
│   │   ├── engine/           the diagnosis pipeline
│   │   │   ├── analyzer.py   diagnose() — orchestrates everything, ENGINE_VERSION="1.1"
│   │   │   ├── rules.py      rule engine: 12 failure families, each quoting evidence
│   │   │   ├── evidence.py   causal analysis (root cause vs downstream), dedupe
│   │   │   ├── severity.py   severity from explicit factors (impact, safety,
│   │   │   │                 position, frequency, recovery)
│   │   │   ├── recommendations.py  recommendation catalogue + prevention text
│   │   │   ├── snippets.py   deterministic, trace-filled code for each fix
│   │   │   └── llm.py        provider client, prompts (trace fenced as untrusted)
│   │   ├── ml/
│   │   │   ├── classifier.py TF-IDF + linear model, hint-only
│   │   │   └── corpus.py     small synthetic labelled examples
│   │   ├── api/
│   │   │   ├── ingest.py     paste / upload / demo; dedupe; redaction; _run_analysis
│   │   │   ├── executions.py dashboard, analytics, detail, re-analyze, delete
│   │   │   ├── timeline.py   merged per-step timeline + healthy-run observations
│   │   │   ├── diff.py       compare 2–3 runs step by step
│   │   │   ├── reports.py    JSON + PDF export
│   │   │   ├── deps.py       serialisers (analysis_to_dict, execution_to_dict)
│   │   │   └── schemas.py    request/response models
│   │   ├── report/pdf.py     ReportLab PDF
│   │   └── samples/
│   │       ├── demos.py      7 demo traces (3 healthy, 4 failing)
│   │       └── seed.py       idempotent demo seeding at startup
│   ├── tests/                339 tests across 12 files (see §7)
│   └── data/                 SQLite db + backups (git-ignored)
└── frontend/
    ├── src/
    │   ├── App.tsx           routes
    │   ├── components/       Layout, ui primitives, DemoToggle
    │   ├── lib/              api client, format helpers, theme, demo toggle state
    │   └── pages/            Dashboard, Analyze, ExecutionDetail, History,
    │                         Analytics, Compare, Settings, ModelQuality
    └── dist/                 built SPA (served by the backend)
```

---

## 3. How a trace flows through the system (step by step)

### Step 1 — Ingest (`app/api/ingest.py`)
Input is one of: pasted text, an uploaded file, or a built-in demo.

1. **Detect the format** (`ingest/detector.py`): OpenAI, Anthropic, LangSmith,
   OTLP/GenAI, MCP, or generic JSON/JSONL.
2. **Parse to a common shape** — every adapter produces a `NormalizedTrace`
   (`core/models.py`) with `execution_id, agent_name, framework, status, steps[],
   output`, where each `TraceStep` has `index, type, name, status, input, output,
   error, tool_name, tool_arguments, tool_result, duration_ms, metadata`.
   Unknown fields are tolerated; malformed input raises `ParseError` → HTTP 422.
3. **Redact** every string in the trace (`core/redact.py`) before anything is
   stored; the count of redactions is kept on the record.
4. **Dedupe**: a content hash over the *normalised* trace (excluding
   `execution_id`, `started_at`, `ended_at`, `content_hash`) means the same trace
   sent twice refreshes one record instead of creating two.
5. **Persist** an `Execution` row, then run the diagnosis and persist an
   `Analysis` row.

### Step 2 — Diagnose (`app/engine/analyzer.py::diagnose`)
In order:

1. **Rules** (`rules.py`) run over the trace and produce `Detection`s, each with
   a category, the step it points at, a severity hint, and the quoted evidence.
   Twelve families: `reasoning_failure, hallucination, tool_execution_failure,
   api_failure, memory_context_failure, planning_failure, infinite_loop,
   incorrect_output, safety_policy_violation, timeout_failure, database_failure,
   authentication_failure`.
2. **Causal analysis** (`evidence.py`) decides which detection is the *root
   cause* and which are *downstream* effects (an error at step 9 caused by a
   failure at step 3 is demoted), and collapses duplicate evidence rows.
3. **ML classifier** (`ml/classifier.py`) adds a hint: recorded with
   `ml_prediction`, `ml_confidence`, `ml_top`, `ml.hint_only`, `ml.agrees`. It is
   only used as the diagnosis when no rule fired.
4. **Severity** (`severity.py`) is computed from explicit, listed factors —
   impact (failed / partial / success), safety, position (last step), frequency
   (repeated failures), recovery — and the factors are shown to the user. A run
   with **no failure at all** gets severity `NONE` (not `LOW`).
5. **Root cause, impact, confidence**: rule-generated sentences that quote the
   trace. Confidence is the sum of stated factors and is labelled in the UI as a
   heuristic, not a probability.
6. **Recovery** (`recoveries()`): a failed step counts as recovered only when a
   **later step calls the same tool with compatible arguments and succeeds** (or
   a retry step follows a step that names no tool). Otherwise the run stays
   `failed`. A run whose trace finished successfully after such a recovery gets
   status `recovered`.
7. **Fixes** (`snippets.py`): for each recommended fix, a deterministic Python
   snippet filled with real trace values (tool name, timeout, HTTP status,
   step number, host), with a per-snippet note saying which framework to adapt
   it to. **The LLM never replaces this code.**
8. **Optional LLM** (`llm.py`): one `httpx` call to OpenAI/Groq/Anthropic. The
   trace is treated as untrusted data — fenced between `<<<TRACE … TRACE>>>` and
   the system prompt instructs the model to treat anything inside as evidence,
   never as commands. Its output only fills the "why" prose, and only after
   redaction.
9. The result is stored as an `Analysis` and also mirrored onto the `Execution`
   row (`status, severity, primary_category, categories, retry_count`) so lists
   and counters do not have to re-run the engine.

### Step 3 — Present
- `GET /api/executions/{id}` returns the execution, its analyses, and a **merged
  timeline** (`timeline.py`): every step with its status, its own evidence, and
  the root-cause step highlighted. For a run with **no failure** it also returns
  `observations` — only non-failure facts (a retry, a step ≥ 5 s, a ≥ 8 000-char
  payload).
- `ExecutionDetailPage.tsx` renders either a red **Root cause** card with the
  recommended fixes, or a green **"No failure detected" / "Recovered after N
  retries"** card with no fix card at all.
- `GET /api/compare?ids=1,2[,3]` (`diff.py`) aligns runs by step number and
  reports the first divergence, whether the runs are even comparable (different
  agents or tasks → banner), and a one-sentence outcome summary.
- `GET /api/reports/{id}.json|.pdf` exports the diagnosis.

---

## 4. Data model (`app/db/database.py`)

- **`Execution`** — one ingested trace: `execution_id, agent_name, framework,
  source_format, status, severity, primary_category, categories(JSON),
  duration_ms, started_at, task_description, expected_output, output, step_count,
  tool_call_count, is_demo, redactions, retry_count, content_hash,
  analysis_count, raw_trace(JSON), created_at`.
- **`Analysis`** — one diagnosis of an execution: `engine_version, status,
  severity, categories, primary_category, failed_step_index, root_cause,
  what_happened, impact, recoverable, confidence, evidence(JSON),
  recommendations(JSON), prevention(JSON), timeline(JSON), fixes(JSON),
  primary_title, causal_chain(JSON), severity_detail(JSON), confidence_factors,
  detection_notes, ml_*, llm_used/llm_model/llm_error, llm_prompt,
  llm_raw_response`.

Re-analysing an execution adds a new `Analysis`; the newest one wins in the UI.

---

## 5. API surface

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | liveness + version |
| GET | `/api/formats` | supported formats and extensions |
| GET | `/api/demos` | built-in demo traces |
| POST | `/api/ingest/paste` | analyse pasted trace `{text, agent_name?, use_llm?}` |
| POST | `/api/ingest/upload` | analyse an uploaded file (multipart) |
| POST | `/api/ingest/demo` | analyse a demo `{demo_id}` |
| GET | `/api/executions` | history: `q, status, category, severity, limit, offset, include_demo` |
| GET | `/api/executions/{id}` | execution + analyses + merged timeline + observations |
| DELETE | `/api/executions/{id}` | delete execution and its analyses |
| POST | `/api/executions/{id}/analyze` | re-run diagnosis |
| GET | `/api/analyses/{id}` | one analysis |
| GET | `/api/dashboard` | counters, category mix, 14-day trend, recent runs |
| GET | `/api/analytics` | category/severity trends, tool failure rates, agent reliability |
| GET | `/api/compare?ids=…` | step-by-step comparison of 2–3 runs |
| GET | `/api/reports/{id}.json` / `.pdf` | export |
| GET/PUT | `/api/settings` | runtime settings (no secrets returned) |
| GET | `/api/llm/health` | real, cached provider check behind the UI badge |
| GET | `/api/ml/metrics` | classifier held-out evaluation |
| GET | `/api/integrations` | LLM/integration status |
| POST | `/api/maintenance/purge` | retention cleanup |

Errors are always JSON with a `detail` that can be shown to a user; unknown
`/api/*` paths never fall through to the SPA.

---

## 6. Frontend pages

| Route | Page | Shows |
| --- | --- | --- |
| `/` | `DashboardPage` | Executions analysed, agents, **Completed (success + recovered + partial)**, Failed — with "N of M ingested runs failed" and a tooltip saying this is not a production failure rate; category mix, 14-day trend, full-width recent history, "Try a demo trace" cards (leading with a clean success) |
| `/analyze` | `AnalyzePage` | paste / upload, format detection, result card |
| `/executions/:id` | `ExecutionDetailPage` | header, root-cause card **or** no-failure card, merged timeline, recommended fixes with copy-able snippets, "how this was diagnosed" |
| `/history` | `HistoryPage` | search + status/severity filters |
| `/analytics` | `AnalyticsPage` | failure analytics charts |
| `/compare` | `ComparePage` | 2–3 runs side by side, first divergence, comparability banner |
| `/settings` | `SettingsPage` | LLM status badge, retention, integrations, link to model quality |
| `/settings/model-quality` | `ModelQualityPage` | classifier accuracy and per-category precision/recall |

A `DemoToggle` (include/exclude demo runs) appears on Dashboard and Compare;
demo executions are excluded by default.

---

## 7. Tests — 339 passing

```bash
cd backend
.venv/bin/python -m pytest tests -q      # 339 passed
```

| File | Covers |
| --- | --- |
| `test_ingest.py` | every parser, detection, redaction, malformed input |
| `test_engine_and_api.py` | rules, severity, endpoints, error shapes |
| `test_workflow.py` | analyze → save → history → detail → export |
| `test_security_llm.py` | no key in any response or stored row, provider config, health probe |
| `test_diagnosis_quality.py` | diagnosis quality on the labelled corpus |
| `test_execution_detail.py` | merged timeline and recommended fixes |
| `test_snippet_sandbox.py` | every generated snippet is compiled **and executed** against a stub raising the traced exception |
| `test_compare_diff.py` | identical runs, divergence, different lengths, three-way, incomparable runs, unrecorded input |
| `test_hardening.py` | hostile tool names cannot inject code into snippets; refined-code validator; prompt-injection trace |
| `test_healthy_traces.py` | clean / recovered / partial runs; the recovery heuristic (same tool + compatible args) |
| `test_no_failure_page.py` | no-failure payload, observations, dashboard counters, compare wording |
| `test_demo_seed.py` | demo seeding is idempotent (restores missing, never duplicates) |

Frontend: `npx tsc --noEmit` and `npm run build` are clean.

`tests/conftest.py` sets `SEED_DEMOS=false` so the startup demo seeding cannot
disturb exact-count assertions.

---

## 8. Security posture

- **Redaction** of keys (`sk-`, `gsk_`, bearer tokens), passwords and PII on
  write, at the API boundary, and by a startup backfill.
- **The provider key never leaves the backend.** `/api/settings` and
  `/api/llm/health` report state (`ready`, `key_missing`, `key_invalid`, …),
  never the value.
- **Traces are data, not code.** Nothing uploaded is executed; snippets are text
  with a Copy button.
- **Snippet injection is blocked**: every value interpolated into generated code
  must match a strict identifier/word/text policy, otherwise it becomes a safe
  placeholder (`your_tool`). Hostile names are covered by tests.
- **LLM refinement is gated**: even if a code path ever accepted model-written
  code, it must `ast.parse`, stay under 4 000 chars, import only from an
  allowlist (no `os`), and never call `eval/exec/open/system`. Today the engine
  does not accept LLM code at all.
- **Upload filenames** are reduced to a bare name; no local path is stored.
- **Auth + rate limits**: `API_TOKEN` (bearer) optional, per-client rate limits.
- `.env`, `*.db`, `backups/`, `.venv`, `node_modules`, `dist` are git-ignored;
  only `.env.example` files are tracked.

---

## 9. Current state and known limits

**State:** feature-complete through five upgrade phases plus two hardening
passes; 339 tests green; backend and frontend build clean; the app runs on
`http://127.0.0.1:8000`.

**Honest limits (stated in the UI/README, not hidden):**
- The ML corpus is small and synthetic — `/settings/model-quality` is a sanity
  check, not a benchmark.
- Confidence is a heuristic score, not a calibrated probability; the UI says so.
- Comparison aligns steps by number: an inserted step shows as one divergence
  plus a shifted column, not a sequence alignment.
- Generated snippets are generic Python, parameterised with the trace's tool
  name / timeout / step number, with a note naming the framework to adapt to.
- The dashboard's failure percentage describes **only the runs submitted to this
  app**, which is stated in the card's tooltip.
- Recovery detection is deliberately strict: a run that abandons a failing tool
  for a different one reads as `failed`, not `recovered`.

---

## 10. How to run it

```bash
# backend
cd backend
cp .env.example .env                 # optional — the app runs with defaults
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# frontend (only when changing the UI)
cd frontend && npm install && npm run build   # writes dist/, served by the backend
```

Environment variables are documented by name only in `README.md` (§Configuration);
`LLM_PROVIDER` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` enable the optional
LLM layer, and everything works without them.

---

## 11. Glossary for a reader new to the domain

- **Trace** — the recorded steps of one agent run.
- **Step** — one event in a trace: a task, an LLM call, a tool call, a retry, an
  error, an output.
- **Failure family (category)** — the kind of failure, one of the twelve above.
- **Root cause vs downstream** — the first failure that explains the others,
  versus errors that are its consequences.
- **Recovered** — the run hit a failure, retried the *same* work, and still
  produced its result.
- **Partial** — the run produced an incomplete result.
- **Snippet** — the copy-pasteable Python fix generated from the trace.
