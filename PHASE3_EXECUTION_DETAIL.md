# Phase 3 — Execution Detail rebuild

The page at `/executions/:id` now answers one question in one screen: *what
broke, where, and what code stops it happening again.*

## What the page is now

| Region | Content |
|---|---|
| Header | agent name, status + severity badges, execution id, framework, step count, duration, timestamp, confidence %, `Recoverable: yes/no`, Re-analyze / PDF / JSON |
| Root cause card | one sentence, the failing step (number + name), the failure family, the impact |
| Left column — Timeline | one merged list of every step: task / plan / LLM call / tool call / retry / error / output; the failing step is highlighted as the root cause; each row expands to its raw input, raw output, tool arguments and raw error (all redacted) with the evidence rows that convicted it |
| Right column — Recommended fix | title, one-line why, the concrete "applies to" line (`fetch_page` (step 4) · timed out at 30s), and a code snippet with a Copy button. Text only — nothing is ever executed |
| How this was diagnosed | collapsed accordion: rule result, classifier guess (secondary hint with confidence), LLM result, severity point breakdown, confidence point breakdown |
| Prevention strategies | collapsed list at the bottom |

Removed from the main page: the separate *Severity assessment* card, the *Why
this confidence* card, the *Causal chain* card, the standalone *Supporting
evidence* card, the *ML classifier opinion* card and the raw *Normalized trace*
inspector. Those all still exist — the causal chain and evidence are folded into
the timeline, severity/confidence/ML into the accordion, and the LLM error banner
appears only when an LLM run actually failed.

Long traces (more than 8 steps) open collapsed to the failure path with a
"Show all 20 steps" control; short traces open straight on the failing step.

## Backend

| File | Change |
|---|---|
| `app/api/timeline.py` (new) | `build_timeline(trace, analysis)` — merges steps, causal chain and evidence into one ordered list. Parses step kinds, redacts every raw field through the same redactor as the rest of the API, attaches each evidence row to its own step, flags `is_root_cause` / `is_failure` / `causal_role`, marks evidence that merely repeats the step's own error, gives a stable `key` per row, and sets `collapsed_by_default` past 8 steps |
| `app/engine/snippets.py` (new) | `extract_facts` (timeout, suggested timeout, HTTP status, `retry-after`, host, context tokens, database/table, tool name, step number — falling back to the nearest earlier step when the failure is recorded one step after the call), `TEMPLATES` for 7 failure families (timeout, tool execution, API/429, infinite loop, context window, auth, database), `build_snippet`, `build_fix`, `fixes_for` (dedupes, limit 4, redacts every snippet) |
| `app/engine/analyzer.py` | the diagnosis now carries `fixes` (built without an LLM, so the page is useful with no provider key) and `primary_title` (the one-line family title) |
| `app/db/database.py`, `app/db/migrations.py` | `Analysis.fixes` (JSON) and `Analysis.primary_title` columns, with the matching register-on-startup migration |
| `app/api/deps.py` | `fixes` and `primary_title` serialised; `_primary_title` falls back to the stored primary category |
| `app/api/executions.py`, `app/api/ingest.py` | persist both fields on every write path (ingest, demo, re-analyze); `GET /executions/{id}` now returns `timeline` |
| `app/api/reports.py` | the JSON report includes `fixes` |

## Frontend

- `src/pages/ExecutionDetailPage.tsx` (new) — the page above.
- `src/App.tsx` — the `executions/:id` route points at it.
- `src/lib/api.ts` — types for `MergedTimeline`, `MergedTimelineItem`, `RecommendedFix`, `FixSnippet`, `TimelineEvidence`; `api.execution()` now types the `timeline` field.

`AnalysisDetailPage.tsx` is left on disk but no longer routed.

## Tests

`tests/test_execution_detail.py` — 19 new tests:

- timeline: 1-step, 2-step and 20-step traces; a 20-step trace collapses and
  still names step 18 as the root cause; every step kind is parsed and no row
  ever displays an "unknown" tag; every evidence row survives the merge and
  lands on its own step; secrets in input/output/error are redacted; a null
  analysis does not crash the builder.
- fixes: facts are read off the failing step (tool `fetch_page`, 30s observed →
  60s suggested, correct step number); all five demo traces produce at least one
  template fix with no LLM; the rate-limit fix honours `retry after 12s`; the
  timeout fix names the real tool and raises the read timeout; the loop fix is
  capped by `MAX_ITERATIONS` and names the repeated tool in its "applies to"
  line; snippets are redacted; a failing-free trace gets no invented fix; an
  unknown category produces no snippet; snippets contain no `eval(`/`exec(`.

Full suite: **143 passed** (was 124).

```
cd backend
DATABASE_URL="sqlite:////tmp/aa_test.db" .venv/bin/python -m pytest tests -q
```

## Verification on the live app

Started on `http://127.0.0.1:8000/`, re-analyzed and re-ingested demos, then
checked four traces end to end:

| Execution | Steps | Timeline | Fixes |
|---|---|---|---|
| `demo-exec-1001` tool timeout | 6 | expanded, root cause = step 4 `fetch_page`, 1 evidence row | 2 — raise the timeout & retry with backoff; return tool failures as data |
| `demo-exec-1003` infinite loop | 7 | expanded, root cause = step 7, repeated `http_get` ×5 | 1 — loop cap with progress check |
| `long-20-step` rate limit | 20 | **collapsed** to the failure path, root cause = step 18 | 2 — honour `retry-after: 12s`; structured tool errors |
| `context-overflow` demo | 4 | expanded, root cause = step 3 `read_csv` | 1 — context budget with summarising |

`GET /api/reports/{analysis}.json` includes the same fixes. No provider key
appears in any page payload; step input/output/error and every snippet pass
through the redactor.

## Not done in this phase

- The **Compare** page (Phase 4) and the cleanup list (Phase 5) are untouched.
- `reasoning_failure` has no code template — there is no mechanical patch for a
  wrong conclusion, so the page falls back to the written recommendations for
  that family. The Recommended fix card says so instead of inventing code.
- The old `AnalysisDetailPage.tsx` file can be deleted once the new page has
  been used for a while.
