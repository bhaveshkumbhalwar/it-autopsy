# Phase 2 — Data correctness and honest numbers

Phase 2 (remaining items) is done: **124 backend tests pass**, the frontend builds, and the
running app at http://127.0.0.1:8000 shows the new behaviour.

The two follow-ups from your Phase 1 approval are also in (section 0).

---

## 0. Phase 1 follow-ups

**(1) Backups are git-ignored and documented.** New root `.gitignore` (the repo had none):

```
backups/
*.backup-*.db
```

plus `*.db`, `.env`, `backend/.env`, `.venv/`, `node_modules/`, `dist/`, `__pycache__/`.

`README.md` gained a **Backups** section:

> Startup migrations write their backups next to the database
> (`backend/data/backups/rows-*.json` and `backend/data/*.backup-*-pre-migration.db`).
> **These contain pre-migration data — including rows that were merged away and values redaction
> had not yet processed — so delete them once you have verified the migration.** They are git-ignored.

**(2) Test that a provider error echoing the key is stored redacted.**
`tests/test_security_llm.py :: test_provider_error_echoing_the_key_is_stored_redacted`

It stubs the HTTP call to return a real Groq-style **401 body that quotes the key**
(`Incorrect API key provided: gsk_…`), runs a full `diagnose(..., capture_llm=True)`, and asserts:

* the failure is still reported (`"rejected the credentials"` / `401`);
* neither the key nor a 16-character fragment of it appears in `llm_error`;
* `[REDACTED]` is present;
* re-running the persistence redactor over that string changes nothing (idempotent).

---

## 1. Demo data is out of the numbers unless asked for

`include_demo` now defaults to **`false`** on `GET /api/executions`, `GET /api/dashboard` and
`GET /api/analytics` (it used to default to `true`, which is why the five sample runs dominated
every chart).

Fixed along the way: **analytics no longer counts analyses of filtered-out executions.** It
previously filtered `executions` by demo but then counted *all* analyses, so demo root causes
leaked back into the "Most common root causes" chart.

Verified live against your database:

| Request | Executions | Demo counted |
|---|---|---|
| `/api/dashboard` | **8** | 0 |
| `/api/dashboard?include_demo=true` | 13 | 5 |

**UI:** a new `Include demo data` switch (`src/components/DemoToggle.tsx`) sits in the header of
Dashboard, Failure Analytics, History and Compare. It persists in `localStorage` under
`agent-autopsy.include-demo` and is shared across pages through a window event, so flipping it on
one page updates the others. Off by default; reloading the page keeps the choice.

---

## 2. Low-sample tool rates are flagged

Each entry in `GET /api/analytics → failing_tools` now carries `calls` and `low_sample`
(`calls < 3`), and the response carries `low_sample_threshold: 3`.

In the UI a tool with fewer than 3 calls is rendered **greyed** (55 % opacity, muted bar) with an
`n=<count> (low sample)` chip next to its rate, and a tooltip explaining why. Your current data
shows the effect honestly — every tool has been called exactly once, so all of them are greyed:

```
fetch_page            1/1 · 100%   n=1 (low sample)
http_get              1/1 · 100%   n=1 (low sample)
...
```

---

## 3. `recovered` is visible and filterable

* **History** — status filter gained **Recovered**; the badge style is a new sky-blue entry in
  `STATUS_STYLES`, so `recovered` renders as a badge everywhere (History, Compare, detail page,
  Dashboard recent list) instead of falling back to no colour.
* **Analytics** — the dashboard totals gained `recovered`, and a new **"How executions ended"**
  card shows Success / Recovered / Partial / Failed counts from `status_counts`, with a one-line
  explanation that a recovered run failed and then completed anyway.
* **Dashboard** — the "Successful" stat card now reads e.g. `0 partial · 1 recovered`.
* Per-agent **recovery rate** now counts `recovered` runs as well as `partial` ones.

Verified live: a run with `429 → retry → success` comes back as `recovered`, appears under
`/api/executions?status=recovered` (total 1), and shows up in `status_counts.recovered = 1`.

---

## Files changed in Phase 2

**Backend**
* `app/api/executions.py` — `include_demo` default `False` (3 endpoints); analytics restricted to in-scope analyses; `low_sample` + `low_sample_threshold`; `status_counts`; `totals.recovered`; recovered-aware trend and recovery rate
* `tests/test_engine_and_api.py` — compare test now creates its own two runs instead of skipping once demos are excluded
* `tests/test_security_llm.py` — provider-echo redaction test; demo-exclusion, low-sample, recovered-visibility tests (+5)

**Frontend**
* `src/lib/demo.ts` *(new)* — `readIncludeDemo` / `writeIncludeDemo` / `useIncludeDemo` (localStorage + cross-page event)
* `src/components/DemoToggle.tsx` *(new)* — the "Include demo data" switch
* `src/lib/api.ts` — `dashboard(includeDemo)` / `analytics(includeDemo)`; `low_sample`, `status_counts`, `totals.recovered` in the types
* `src/lib/format.ts` — `STATUS_STYLES.recovered`
* `src/pages/DashboardPage.tsx`, `AnalyticsPage.tsx`, `HistoryPage.tsx`, `ComparePage.tsx` — toggle wired in; low-sample greying; "How executions ended" card; Recovered filter option

**Repo**
* `.gitignore` *(new)* — backups, databases, `.env`, build output
* `README.md` — one paragraph on backups containing pre-migration data

---

## How to test

```bash
# 1. Backend tests — should print: 124 passed
cd backend
DATABASE_URL="sqlite:////tmp/aa_test.db" .venv/bin/python -m pytest tests -q

# 2. The new ones on their own
DATABASE_URL="sqlite:////tmp/aa_test.db" .venv/bin/python -m pytest tests/test_security_llm.py -k "echoing or demo or low_sample or recovered" -v

# 3. Frontend build
cd ../frontend && npm run build

# 4. Run the app
cd ../backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then in the browser at http://127.0.0.1:8000:

1. **Dashboard** — executions count excludes the 5 demos (8, not 13). Flip **Include demo data**
   in the header: it jumps to 13 and the recent list shows the DEMO-badged runs. The switch stays
   on after a reload.
2. Go to **Failure Analytics** — the toggle is already on there (same setting). Turn it off: the
   root-cause chart drops the demo categories.
3. Still on Analytics, look at **Frequently failing tools** — each row is greyed with
   `n=1 (low sample)`; hover for the explanation.
4. **"How executions ended"** card shows Success / Recovered / Partial / Failed counts.
5. **History** — status dropdown has **Recovered**; pick it (nothing yet, unless you ingest a
   `429 → retry → success` trace, in which case the badge is sky-blue and readable at a glance).
6. `git status` — `backend/data/backups/` and `*.backup-*.db` no longer appear as untracked.

**Not started, as instructed:** Phase 3 (Execution Detail rebuild). The blocks still open are
Phase 3 (detail page layout), Phase 4 (Compare view: aligned timelines, first divergence,
searchable picker) and Phase 5 (ML eval moved to `/settings/model-quality`, chart axis labels,
consistent page width).
