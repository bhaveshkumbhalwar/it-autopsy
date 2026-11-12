# Phase 1 — Security, secrets and LLM configuration

Everything here is done and tested. **119 backend tests pass** (`119 passed`), the frontend
builds, and the running app at http://127.0.0.1:8000 reports `LLM ready` from a real call.

---

## 1. Redaction: no key ever leaves or rests in the app

Redaction now happens in **two** places, because either one alone has a hole:

| Where | What it covers | Code |
|---|---|---|
| **At write time** | Every text/JSON field of an `Analysis` before `db.commit()` | `app/api/deps.py :: redact_analysis_on_write()`, called from `ingest.py` and `executions.py` |
| **At the API boundary** | Every response body built by the routers (executions, analyses, dashboard, analytics, reports, settings, ingest results) | `app/api/deps.py :: sanitize()`, applied to every router return |
| **At startup (once)** | Rows already stored before this change — `llm_error`, `llm_prompt`, `llm_raw_response`, `root_cause`, `what_happened`, `impact`, `evidence`, `notes`, `raw_trace` | `app/db/migrations_data.py :: redact_stored_secrets()` |

Patterns covered (`app/core/redact.py`): `sk-`, `sk-proj-`, `sk-ant-`, `gsk_`, `AIza`, `hf_`,
`AKIA`, `ghp_`, `xox?-`, JWTs, `Bearer …`, `api_key=`/`key=`/`token=` query params, private
key blocks, e-mail addresses, phone numbers, `password: …` fields — **plus the exact value of
the configured key**, so a provider that quotes it in a different shape is still caught.

### The test that proves it

`backend/tests/test_security_llm.py :: test_no_key_in_any_api_response_or_stored_row`

It injects a fake key `gsk_…` into an ingested trace's error text, then:

1. calls `/api/executions`, `/api/executions/{id}`, `/api/analyses/{id}`, `/api/dashboard`,
   `/api/analytics`, `/api/settings`, `/api/health`, `/api/formats`, `/api/reports/{id}.json`;
2. reads **every stored row** back out of the database — including `llm_error`,
   `llm_raw_response`, `llm_prompt`, `evidence`, `detection_notes` and `raw_trace`;

and asserts the key string appears in **none** of them.

---

## 2. LLM configuration: OpenAI **and** Groq

`app/core/config.py :: resolve_llm()` resolves provider → base URL → model:

| Provider | Default base URL |
|---|---|
| `openai` | `https://api.openai.com/v1` |
| `groq` | `https://api.groq.com/openai/v1` |
| `anthropic` | `https://api.anthropic.com/v1` |

* `LLM_PROVIDER` picks the provider; **empty means "infer it from the key prefix"**
  (`gsk_…` → groq, `sk-ant-…` → anthropic, otherwise openai).
* `LLM_BASE_URL` overrides the provider default (for a proxy or a self-hosted endpoint).
* `LLM_MODEL` must be a model that provider serves.

A **mismatched key** (`gsk_…` with `LLM_PROVIDER=openai`) produces a clear message —
*"Key prefix `gsk_` is a Groq key but LLM_PROVIDER=openai…"* — which **never echoes the key**
(`llm_config_problem()`, asserted in `test_mismatched_provider_is_reported_without_the_key`).

### Model quirk: no JSON mode for `gpt-oss-20b`

`openai/gpt-oss-20b` on Groq returns an empty `content` when `response_format=json_object`
is set on a short prompt (it fails with `400 Failed to validate JSON`). So:

* `llm.supports_json_mode()` is **false** for `gpt-oss`, `mixtral` and `gemma` models;
* for those the prompt itself asks for JSON, the answer is parsed, and a **malformed answer
  triggers exactly one retry** with an extra "return only the JSON object" instruction
  (`_post_chat_json()`);
* other models still use real JSON mode.

This is why LLM analysis now works end to end on the configured model — verified live:

```
llm_used True | model openai/gpt-oss-20b | llm_error None
prompt_len 2921 | raw_len 1975
```

---

## 3. "LLM ready" is a real check, cached 5 minutes

`GET /api/llm/health` (and `?refresh=true`) makes a genuine 1-token call to the provider and
maps the outcome to one of seven states — never "ready" from key presence alone:

`ready` · `key_missing` · `key_invalid` (401/403) · `model_unavailable` (404) ·
`rate_limited` (429) · `unreachable` (network/timeout/5xx) · `misconfigured` (provider/key
prefix mismatch).

The result is cached for `LLM_HEALTH_CACHE_SECONDS` (default **300**).

The Settings page badge now reads that state and has a **Check now** button:

```
[🔑 LLM ready]  provider: groq  openai/gpt-oss-20b  [Check now]
groq answered with model openai/gpt-oss-20b. (1006 ms)
```

---

## 4. Duplicate submissions and merged history

* Every ingested trace gets a **content hash** — `app/ingest/util.py :: trace_content_hash()`,
  which hashes the trace *without* `execution_id`, `started_at`, `ended_at` (and never hashes
  itself). The same run submitted as JSON, JSONL or a demo therefore hashes identically.
* Submitting it again **updates** the existing execution instead of adding a row, and adds a
  new `Analysis` (`analysis_count` counts them). Verified live: the same paste twice
  returned execution **id 14** both times.
* `Execution.analysis_count` and `Execution.content_hash` are new columns.

### The merge migration (with backup, as you asked)

On startup `init_db()` → `app/db/migrations.py :: run_migrations()`:

1. adds any missing columns (idempotent `ALTER TABLE`);
2. **only if a data migration could actually change something**, writes two backups next to
   the database:
   * `data/backups/rows-<timestamp>.json` — a portable row-level dump;
   * `data/agent_autopsy.backup-<timestamp>-pre-migration.db` — a full file copy;
3. re-redacts stored secrets;
4. merges duplicate executions, keeping the oldest row (the id already linked in the UI) and
   re-pointing its newest analysis if it had none;
5. **logs how many rows were merged**, per group and in total.

Actual run on your database:

```
Row-level backup written to data/backups/rows-20261112-134944.json (15 executions, 18 analyses)
Database file backed up to data/agent_autopsy.backup-20261112-134944-pre-migration.db
Merged 1 duplicate execution(s) of 'exec-1ab648b73eaa' into pk=14 (removed pk=[15])
Merged 1 duplicate execution(s) of 'exec-b71414aa7eb7' into pk=16 (removed pk=[17])
Duplicate merge complete: 2 group(s), 2 row(s) removed
```

A second run merges nothing and writes **no** new backup (`test_duplicate_merge_backs_up_first_and_reports_rows`).

---

## 5. `recovered` status

A run that hit a failure and still produced its answer (a retry step after the failure, or a
failed step in a run that ended successfully) is now `recovered`, not `success` — the
incident stays visible in History and Analytics. `rate-limit-recovered` is the reference case.

---

## 6. Dashboard

The **"Most common failure categories"** card is removed from the Dashboard; "Recent analysis
history" is now the full-width card. "Most common root causes" stays on the Failure Analytics
page (that was your instruction #1).

---

## Files changed in Phase 1

**Backend**
* `app/core/redact.py` — pattern set, `redact_secret`, `redact_obj`
* `app/core/config.py` — provider defaults/prefixes, `resolve_llm()`, `llm_config_problem()`, `llm_health_cache_seconds`, `LLM_BASE_URL`
* `app/engine/llm.py` — one httpx client for OpenAI/Groq/Anthropic, `supports_json_mode()`, `_post_chat_json()` retry, `check_health()`, `config_status()`, `analyze_full()`
* `app/engine/analyzer.py` — `capture_llm` (prompt + raw response), `recovered` status, `_was_recovered()`
* `app/api/deps.py` — `sanitize()`, `redact_analysis_on_write()`, ml fields, `content_hash` in the execution payload
* `app/api/ingest.py` — content-hash dedupe (`_find_duplicate`, `_refresh_duplicate`), write-time redaction, `analysis_count`, ML + LLM-capture persistence
* `app/api/executions.py` — write-time redaction, ML + LLM-capture persistence, sanitised responses
* `app/api/reports.py` — `GET /api/llm/health`, real AI status in `/api/settings`, sanitised JSON report
* `app/db/database.py` — `content_hash`, `analysis_count`, `ml_*`, `llm_prompt`, `llm_raw_response` columns; `init_db()` runs migrations
* `app/db/migrations.py` *(new)* — backup (file + rows), column adds, orchestration
* `app/db/migrations_data.py` *(new)* — duplicate merge, stored-secret redaction
* `app/ingest/util.py`, `app/ingest/__init__.py`, `app/core/models.py` — `trace_content_hash()`, hash on ingest, `Status.RECOVERED`
* `tests/test_security_llm.py` *(new, 30 tests)* — redaction, provider config, health states + caching, no-leak proof, dedupe, `recovered`, backup-and-merge

**Frontend**
* `src/lib/api.ts` — `LlmHealth` type, `api.llmHealth()`, extended `AppSettings.ai`
* `src/pages/SettingsPage.tsx` — real state badge + **Check now** + problem line
* `src/pages/DashboardPage.tsx` — categories card removed, recent history full width

---

## How to test

```bash
# 1. Backend tests (should print: 119 passed)
cd backend
DATABASE_URL="sqlite:////tmp/aa_test.db" .venv/bin/python -m pytest tests -q

# 2. The no-leak proof on its own
DATABASE_URL="sqlite:////tmp/aa_test.db" .venv/bin/python -m pytest tests/test_security_llm.py -k no_key -v

# 3. Frontend build
cd ../frontend && npm run build

# 4. Run the app (backend serves the built SPA on one port)
cd ../backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then, in the browser at http://127.0.0.1:8000:

1. **Settings** → the AI card shows a green **LLM ready** badge with the measured latency;
   press **Check now** to re-probe (each probe is a real call, cached for 5 minutes).
2. **Analyze** → paste any trace with *Use LLM* on: the analysis detail page shows
   `llm_model: openai/gpt-oss-20b`, no error banner, and "How this was diagnosed" carries the
   model's reasoning.
3. **Analyze** → paste the *same* trace again: you land on the **same execution** (the list
   count does not grow) and the analysis history on that execution gains an entry.
4. **Dashboard** → no "Most common failure categories" card; "Recent analysis history" spans
   the full width.
5. Restart the server and check `backend/data/backups/`: a new `rows-*.json` and `*.db` copy
   appear **only** if there was something to migrate.

Known-unchanged: nothing in Phase 1 touches the detail page layout, the Compare view, the ML
eval placement or the chart axes — those are Phases 2–5.
