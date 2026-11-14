# Agent Autopsy

Trace debugging and root-cause analysis for AI agents. Paste or upload an agent
trace (OpenAI, Anthropic, LangSmith, OTLP/GenAI, MCP or generic JSON/JSONL), get a
diagnosis — failure category, severity, root cause, evidence, and runnable fix
snippets — then keep it in a searchable history with JSON/PDF reports and a
step-by-step comparison view.

**Rules are authoritative, the classifier is a hint, the LLM layer is optional.**
Nothing in the analysis path executes the uploaded code or commands: traces are
parsed as data only. Secrets and PII are redacted before storage and again at the
API boundary

---

## Layout

```
backend/    FastAPI + SQLAlchemy + SQLite (Postgres-ready), analysis engine, tests
frontend/   React + Vite + TypeScript + Tailwind (dark/light, responsive)
```

## Setup

Requires Python 3.11+ and Node 18+.

```bash
# 1. Backend
cd backend
cp .env.example .env                      # optional; the app runs with defaults
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Frontend
cd ../frontend
npm install
npm run build                             # writes frontend/dist

# 3. Run (one port, one origin)
cd ../backend
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>. The API serves the built SPA, so `/`, `/history`,
`/analytics` and every `/api/*` route share one origin and one port. Interactive
API schema: <http://127.0.0.1:8000/docs>.

### Development (hot reload)

```bash
cd backend  && ./run.sh            # API on :8000
cd frontend && npm run dev         # SPA on :5173, /api proxied to :8000
```

The proxy target is configurable: `VITE_API_TARGET=http://127.0.0.1:8000` in
`frontend/.env.local` (see `frontend/.env.example`). The client itself only ever
calls `/api/...` (`VITE_API_BASE` overrides that for a split-origin deployment).

---

## Configuration

All settings are environment variables, read once at startup
(`backend/app/core/config.py`). Names only — **never commit a value**.

### Backend (`backend/.env`)

| Variable | What it does |
| --- | --- |
| `ENVIRONMENT` | `development` or `production` (affects logging verbosity) |
| `DATABASE_URL` | SQLAlchemy URL. Default is SQLite under `backend/data/`; Postgres works with `postgresql+psycopg://…` |
| `LLM_PROVIDER` | `openai`, `groq` or `anthropic`. Empty disables the LLM layer |
| `LLM_API_KEY` | Provider key. **Secret — never commit, never paste into a trace** |
| `LLM_BASE_URL` | Overrides the provider's base URL (Groq: `https://api.groq.com/openai/v1`) |
| `LLM_MODEL` | Model id, e.g. `openai/gpt-oss-20b` |
| `LLM_TIMEOUT_SECONDS` | Per-request timeout for the provider call |
| `LLM_MAX_INPUT_CHARS` | Caps how much trace text is sent to the provider |
| `LLM_HEALTH_CACHE_SECONDS` | How long the "LLM ready" badge trusts a real health probe |
| `MAX_UPLOAD_BYTES` | Largest accepted upload |
| `ALLOWED_EXTENSIONS` | Comma-separated allowlist of trace file extensions |
| `REDACT_SECRETS` | Redaction on/off (leave on) |
| `RETENTION_DAYS` | Default retention used by `/api/maintenance/purge` |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | General API rate limit |
| `RATE_LIMIT_ANALYZE` | Rate limit for the analysis endpoints |
| `CORS_ORIGINS` | Comma-separated origins allowed to call the API from a browser |
| `API_TOKEN` | If set, every `/api/*` call must send `Authorization: Bearer <token>` |
| `ML_MODEL_PATH` | Where the trained classifier is cached |

### Frontend (`frontend/.env.local`)

| Variable | What it does |
| --- | --- |
| `VITE_API_BASE` | API prefix when the API is on another origin (default `/api`) |
| `VITE_API_TARGET` | Dev-server proxy target (default `http://127.0.0.1:8000`) |

The engine is fully functional without any key: rules plus the local classifier
produce the diagnosis, and the UI says the LLM layer was not used.

---

## Screenshots

Screens worth capturing for a release (take them at a 1440×900 window; the same
pages are checked at 390×844 for phone layout):

1. **Dashboard** — totals, failure mix, recent runs.
2. **Analyze** — paste/upload form with format detection and the result card.
3. **Execution detail** — timeline with the root-cause step, evidence, and the
   recommended fixes with their code snippets.
4. **Compare** — two or three runs side by side, first divergence highlighted,
   and the "different agents or tasks" banner when the runs are not comparable.
5. **History** — searchable list with status/severity filters.
6. **Failure analytics** — root-cause and tool-failure charts.
7. **Settings** — LLM status badge, retention, framework integrations.
8. **Developer: model quality** (`/settings/model-quality`) — the classifier's
   held-out accuracy and per-category precision/recall.

---

## Security notes

- **Redaction everywhere.** API keys, bearer tokens, `sk-`/`gsk_` style keys,
  passwords and PII are redacted when an analysis is written, again when any
  record is serialised to the API, and once more for rows stored before
  redaction existed (a startup backfill). Provider error messages are redacted
  too: a rejected key is never echoed back into the database, an API response or
  a report.
- **The key never leaves the backend.** No endpoint returns `LLM_API_KEY`;
  `/api/settings` and `/api/llm/health` report state (`ready`, `key_missing`,
  `key_invalid`, …) and never the value.
- **Uploaded traces are data, never code.** Parsers read JSON/JSONL/text; nothing
  is executed. Generated fix snippets are shown as text with a Copy button and
  are never run by the app. Only the tests execute them, in-process, against
  stubs.
- **Upload filenames are reduced to a bare name**, so a client cannot get a local
  path stored in a record or printed in a report.
- **Backups.** Startup migrations write backups next to the database
  (`backend/data/backups/rows-*.json`, `backend/data/*.backup-*-pre-migration.db`).
  They can contain pre-redaction data: verify the migration, then delete them.
  They are git-ignored.
- **Never commit `.env`.** `.env` files are git-ignored; `.env.example` holds
  variable names and placeholder values only. Rotate any key that has been
  committed, pasted into a trace, or shared.
- **Auth and rate limits.** Set `API_TOKEN` before exposing the app beyond
  localhost; the rate limits are per client and configurable.

---

## API

All endpoints live under `/api`; interactive schema at `/docs`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | liveness + version |
| GET | `/api/formats` | supported trace formats and extensions |
| GET | `/api/demos` | built-in demo traces |
| POST | `/api/ingest/paste` | analyse a pasted trace `{text, agent_name?, use_llm?}` |
| POST | `/api/ingest/upload` | analyse an uploaded file (multipart) |
| POST | `/api/ingest/demo` | analyse a demo trace `{demo_id}` |
| GET | `/api/executions` | history: `q, status, category, severity, limit, offset, include_demo` |
| GET | `/api/executions/{id}` | execution + its analyses + the merged timeline |
| DELETE | `/api/executions/{id}` | delete an execution and its analyses |
| POST | `/api/executions/{id}/analyze` | re-run the diagnosis |
| GET | `/api/analyses/{id}` | one analysis |
| GET | `/api/dashboard` | counters and recent runs |
| GET | `/api/analytics` | category/severity trends |
| GET | `/api/compare?ids=1,2[,3]` | aligned step-by-step comparison (2–3 runs) |
| GET | `/api/reports/{id}.json` \| `.pdf` | export a report |
| GET/PUT | `/api/settings` | runtime settings (no secrets returned) |
| GET | `/api/llm/health` | real, cached provider check behind the badge |
| GET | `/api/ml/metrics` | classifier evaluation |
| GET | `/api/integrations` | LLM/integration status |
| POST | `/api/maintenance/purge` | retention cleanup |

Every error is JSON with a `detail` you can show a user: 404 names the path,
405 names the method problem, 422 names the offending field. Unknown `/api/*`
paths never fall through to the SPA.

---

## Tests

```bash
cd backend
DATABASE_URL="sqlite:////tmp/aa_test.db" .venv/bin/python -m pytest tests -q
```

| File | Covers |
| --- | --- |
| `test_ingest.py` | every parser, format detection, redaction, malformed input |
| `test_engine_and_api.py` | rules, severity, endpoints, error shapes |
| `test_workflow.py` | analyze → save → history → detail → export |
| `test_security_llm.py` | no key in any response or stored row, provider config, health probe |
| `test_diagnosis_quality.py` | diagnosis quality on the labelled corpus |
| `test_execution_detail.py` | the merged timeline and the recommended fixes |
| `test_snippet_sandbox.py` | every generated snippet is compiled and *executed* against a stub raising the traced exception |
| `test_compare_diff.py` | identical runs, divergence at step 1, different lengths, three-way, incomparable runs, unrecorded input |

Frontend checks:

```bash
cd frontend
npx tsc --noEmit        # types
npm run build           # production bundle
```

---

## Known limits

- The ML corpus is a small set of synthetic labelled examples, so
  `/settings/model-quality` is a sanity check, not a benchmark.
- Confidence is heuristic (rule strength + evidence), not a calibrated
  probability; the UI says so next to the number.
- Comparison aligns steps by step number. An inserted step shows as one
  divergence followed by a shifted column, not as a sequence alignment.
- Generated fix snippets are generic Python: they carry the trace's tool name,
  timeout and step number, and a note says which framework to adapt them to.
