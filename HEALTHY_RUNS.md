# Honest health: why the dashboard said 100% failure, and what changed

The dashboard read **0 successful / 100% failure** because every stored run was
in fact a failed run. Nothing was mis-counted; the data was one-sided, and the
KPI presented that one-sided data as if it were a failure *rate*. Both are
fixed below, and the false positives found on the way are listed rather than
quietly patched.

Tests: **339 passed** (was 303). `tsc --noEmit` and `npm run build` clean.

---

## 1. Healthy traces

Three sample traces, in `backend/tests/test_healthy_traces.py`, each asserted
through the real ingest → analyze path:

| Trace | Expected | Result |
| --- | --- | --- |
| (a) clean successful run (5 steps, one tool call) | `success`, no failure category | `success`, `categories: []`, severity `NONE` |
| (b) 429/timeout retried then succeeded | `recovered` | `recovered`, one `timeout_failure`, `retry_count: 1` |
| (c) partial result (one of two exports missing) | `partial` | `partial`, one `tool_execution_failure`, severity `LOW` |

### False positives found — and fixed

1. **`severity: LOW` on a run with no failure at all.** `severity.assess()`
   returned `LOW` (score 0) whenever no category fired, so a perfectly healthy
   run carried a severity badge. A new `Severity.NONE` is returned in exactly
   that case; the dashboard, history, detail page and charts treat it as
   "healthy", not "low". No failure is not a low-severity failure.
2. **The recovered demo could never be `recovered`.** `_was_recovered()`
   decided this from `failed_step_index`, which is the *root cause* step — and
   the timeout rule can nominate a different step than the one the retry made
   good. `recoveries()` replaces it: a **later** step must call the **same
   tool** (same tool name, compatible arguments) and succeed. A failed step
   that names no tool (an LLM call, a plan) is recovered by a retry step after
   it or a step with the same name.

   Deliberately narrow — a different tool succeeding later is the agent moving
   on, not a recovery, and the run stays a failure:
   * a different tool succeeding afterwards → `failed`
   * a retry step that calls a different tool → `failed`
   * the same tool succeeding later with different arguments → `failed`
   * the same tool, same arguments, later → recovery counted (status becomes
     `recovered` only when the run itself finished successfully)
3. **`retry_count` was never stored.** The detail page had nothing to read for
   "recovered after N retries". `Execution.retry_count` (migration:
   `INTEGER NOT NULL DEFAULT 0`) is filled from `recoveries()` at analysis time.

No rule change was needed for (a): with no failed step and no error text, the
rules and the classifier both stay silent. The classifier is a hint only, so it
cannot manufacture a category on its own.

---

## 2. No-failure execution page

`ExecutionDetailPage.tsx` now branches on status:

* `success` / `recovered` → a green **"No failure detected"** card, or
  **"Recovered after N retries"** for a recovered run, with a sentence saying
  what happened. The **Root cause** card is not rendered, and **Recommended
  fix** is not rendered either: there is nothing to fix.
* The timeline is kept unchanged, so the failed attempt of a recovered run is
  still visible and highlighted.
* Only non-failure observations are shown, under "Worth knowing", from
  `timeline.build_observations()`: a retry, a slow step (≥ 5 s), a large payload
  (≥ 8 000 characters of output). Each is a fact from the trace, never advice.
* `partial` keeps the root-cause card: something did go wrong, and the run is
  not healthy in the "nothing to fix" sense.

Payload: `GET /api/executions/{id}` now also returns `observations` and
`execution.retry_count`. Tests: `backend/tests/test_no_failure_page.py`.

---

## 3. Demo data

* Restored **Coder Agent — 429 recovered by retry** (it was in the catalogue but
  not in the database).
* Added **Support agent — clean successful run** and **Report agent — partial
  result**.
* `app/samples/seed.py` seeds idempotently, keyed on each demo's own
  `execution_id`: a demo already present is left untouched (never re-analysed,
  never duplicated), a missing one is ingested and analysed. It runs at startup
  and is covered by `backend/tests/test_demo_seed.py` (seeding twice creates
  nothing; deleting one restores exactly that one).
* Seeding is switched off in tests (`tests/conftest.py`, `SEED_DEMOS=false`), so
  exact-count assertions elsewhere are unaffected.
* The "Try a demo trace" cards now lead with **Clean successful run**, then
  **429 recovered by retry** and **Partial result**, then the three failures.

---

## 4. Dashboard KPIs

* The **Failed** card no longer shows a bare rate. Its subtext is
  **"N of M ingested runs failed"**, with an info tooltip: *"This only covers
  runs you submitted for analysis, not your production failure rate."*
* The **Successful** card became **"Completed (success + recovered + partial)"**,
  subtext `1 success · 1 recovered · 1 partial`. It is not called "healthy": a
  partial run finished, it did not come out whole.
* With no runs at all, both subtexts read **"no runs yet"** — no `0%` is ever
  shown for a rate that has no denominator (`healthy_rate` is `null`, not `0`).

---

## 5. Layout

"Recent analysis history" is now its own full-width row on the Dashboard; the
`lg:grid-cols-3` wrapper that left two empty columns beside it is gone.

---

## 6. Snippet drift

The safest option was taken and made explicit: **the engine never puts LLM
output into a fix.** `analyzer.diagnose()` no longer calls `refine_fix()`; the
code shown is always the deterministic template built from the trace. LLM text
stays in the "why" fields (`problem`, `fix`, `reason`, `benefit`), where it is an
explanation, not something anyone runs. `validate_refined_code()` /
`refine_fix()` remain as the gate they were (parse, length, import allowlist
without `os`, no `eval`/`exec`/`open`/`system`), so any future path that does
accept model-written code still cannot accept a hostile one.

Test: `test_the_engine_never_puts_llm_code_in_a_fix` feeds a fix whose refined
code ignores the traced tool name and asserts the template — naming
`fetch_page` — is what comes back, with `source: "template"`.

**One HTTP library per page.** The timeout template was hard-wired to `httpx`
even when the trace raised `requests.exceptions.ReadTimeout`, which produced a
snippet importing both. `extract_facts()` now records `http_library` from the
exception the trace named, and the timeout snippet then uses
`requests.Session()` with a `(connect, read)` timeout tuple and
`from requests.exceptions import ReadTimeout` — no `httpx` import at all. An
`httpx` trace stays on `httpx`. Both variants are compiled in tests.

---

## 7. Compare text

The summary is one sentence per run, lowercase mid-sentence, inputs quoted:

> Data Analyst Agent failed because the model ran out of usable context at
> step 2 ('analyse rows'); Support Agent failed because the final answer is
> inconsistent with the information the agent collected during execution; the
> input at step 1 differed ("Analyse the attached 4000-row CSV and report the
> top 5 products by revenue." vs "Answer the customer question about the refund
> window."), which need not be the cause when the runs are answering different
> questions.

A bare category id (`timeout_failure`) or a root cause that just echoes the
category is turned into words ("failed because timeout failure") instead of
being pasted raw. Test: `test_the_compare_summary_reads_as_one_sentence`.

---

## Files changed

Backend
- `app/core/models.py` — `Severity.NONE`
- `app/engine/severity.py` — no-failure runs are `NONE`, not `LOW`
- `app/engine/analyzer.py` — `recoveries()` (same tool + compatible arguments,
  later, succeeding) replaces `_was_recovered()`, which is removed
- `app/engine/snippets.py` — `http_library` fact, requests/httpx timeout variants
- `app/db/database.py`, `app/db/migrations.py` — `Execution.retry_count`
- `app/api/ingest.py` — stores `retry_count`
- `app/api/deps.py` — returns `retry_count`
- `app/api/timeline.py` — `build_observations()`
- `app/api/executions.py` — `observations` in the detail payload; `healthy` /
  `healthy_rate` in the dashboard
- `app/api/diff.py` — outcome sentences, quoted inputs
- `app/samples/demos.py` — `clean-success`, `partial-result`
- `app/samples/seed.py` — idempotent seeding (new)
- `app/main.py`, `app/core/config.py` — seed at startup, `SEED_DEMOS`
- `tests/conftest.py` — seeding off under pytest
- `tests/test_healthy_traces.py` (new, 16 incl. the five recovery-heuristic
  cases), `tests/test_no_failure_page.py`
  (new, 10), `tests/test_demo_seed.py` (new, 7),
  `tests/test_snippet_sandbox.py`, `tests/test_compare_diff.py`,
  `tests/test_hardening.py` updated

Frontend
- `src/lib/api.ts` — `Severity.NONE`, `recovered`, `Observation`, `retry_count`,
  `healthy`/`healthy_rate`
- `src/lib/format.ts` — `NONE` styles
- `src/pages/DashboardPage.tsx` — healthy/failed KPIs, tooltip, full-width
  history, three healthy demo cards
- `src/pages/ExecutionDetailPage.tsx` — `NoFailureCard`, observations, no fix
  card for healthy runs

## Test steps

```bash
cd backend
.venv/bin/python -m pytest tests -q          # 339 passed

cd ../frontend
npx tsc --noEmit
npm run build
```
