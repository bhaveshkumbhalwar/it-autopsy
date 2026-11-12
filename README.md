# Agent Autopsy

Trace debugging and root-cause analysis for AI agents. Paste or upload an agent
trace (OpenAI, Anthropic, LangSmith, OTLP/GenAI, MCP or generic JSON/JSONL), get a
diagnosis — failure category, severity, root cause, evidence, fixes — and keep it
in a searchable history with JSON/PDF reports.

**Rules are authoritative, ML is supplementary, the LLM layer is optional.**
Nothing in the analysis path executes the uploaded code or commands: traces are
parsed as data only. Secrets and PII are redacted before storage.

---

## Layout

```
backend/    FastAPI + SQLAlchemy + SQLite (Postgres-ready), analysis engine, tests
frontend/   React + Vite + TypeScript + Tailwind (dark/light, responsive)
```

## Run it

### Production-style (one port, one origin)

```bash
cd backend
cp .env.example .env            # optional: set LLM_API_KEY, API_TOKEN, DATABASE_URL
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

cd ../frontend && npm install && npm run build   # writes frontend/dist
cd ../backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>. The API serves the built SPA, so `/`, `/history`,
`/analytics` and every `/api/*` route share one origin and one port.

### Development (hot reload)

```bash
cd backend  && ./run.sh            # API on :8000
cd frontend && npm run dev         # SPA on :5173, /api proxied to :8000
```

The proxy target is configurable: `VITE_API_TARGET=http://127.0.0.1:8000` in
`frontend/.env.local` (see `frontend/.env.example`). The client itself only ever
calls `/api/...` (`VITE_API_BASE` overrides that for a split-origin deployment).

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
| GET | `/api/executions` | history: `q, status, category, severity, limit, offset` |
| GET | `/api/executions/{id}` | execution + its analyses + normalized trace |
| DELETE | `/api/executions/{id}` | delete an execution and its analyses |
| POST | `/api/executions/{id}/analyze` | re-run the diagnosis |
| GET | `/api/analyses/{id}` | one analysis |
| GET | `/api/dashboard` | counters and recent runs |
| GET | `/api/analytics` | category/severity trends |
| GET | `/api/compare?ids=1,2` | side-by-side comparison |
| GET | `/api/reports/{id}.json` \| `.pdf` | export a report |
| GET/PUT | `/api/settings` | runtime settings (no secrets returned) |
| GET | `/api/ml/metrics` | classifier evaluation |
| GET | `/api/integrations` | LLM/integration status |
| POST | `/api/maintenance/purge` | retention cleanup |

Every error is JSON with a `detail` you can show a user: 404 names the path,
405 names the method problem, 422 names the offending field. Unknown `/api/*`
paths never fall through to the SPA.

## Tests

```bash
cd backend
DATABASE_URL="sqlite:////tmp/aa_test.db" .venv/bin/python -m pytest tests -q
```

`tests/test_ingest.py` (parsers, redaction), `tests/test_engine_and_api.py`
(rules, severity, endpoints) and `tests/test_workflow.py` (the full
analyze → save → history → detail → export path, plus malformed input).

## Configuration

See `backend/.env.example`. No key is required to run: without `LLM_API_KEY` the
engine uses deterministic rules plus the local classifier and says so in the UI.
`API_TOKEN` adds bearer auth to every API call.

## Backups

Startup migrations write their backups next to the database (`backend/data/backups/rows-*.json`
and `backend/data/*.backup-*-pre-migration.db`). **These contain pre-migration data — including
rows that were merged away and values redaction had not yet processed — so delete them once you
have verified the migration.** They are git-ignored.

## Known limits

- The ML corpus is ~100 synthetic labelled examples, so `/api/ml/metrics` is a
  sanity check, not a benchmark.
- Confidence is heuristic (rule strength + ML agreement + evidence), not a
  calibrated probability; the UI states this next to the number.
- Comparison is side-by-side, not statistical.
