# Phase 5 — cleanup and review fixes

Scope: the review fixes requested on Phases 3 and 4, then the Phase 5 cleanup
list. No new features, no redesign.

---

## Review fixes

### Execution page

1. **Snippet `except` clauses come from the trace.**
   - `app/engine/snippets.py` gained `_EXCEPTION_RE`, `EXCEPTION_ALIASES`,
     `_TIMEOUT_NAMES` and `_exception_facts()`. A traced `ReadTimeout` becomes
     `except httpx.ReadTimeout`, a traced `requests.exceptions.ReadTimeout`
     becomes `except requests.exceptions.ReadTimeout` with
     `from requests.exceptions import ReadTimeout` (plus `import requests` when
     the code touches `requests.exceptions`).
   - A trace that names no exception type gets `except Exception as exc:` at the
     tool boundary and decides inside — never a builtin `TimeoutError` standing
     in for `ReadTimeout`:
     ```python
     except Exception as exc:
         last_error = exc
         # Only a timeout is worth waiting out.
         if not any(w in type(exc).__name__.lower() for w in ("timeout", "timedout")):
             raise
     ```
   - The database snippet catches the traced DB exception; the tool snippet
     returns a structured failure instead of raising; the auth snippet no longer
     prints the literal `{the provider}` into an f-string.
   - `backend/tests/test_snippet_sandbox.py` (16 tests) compiles and **executes**
     each generated snippet against a stub raising the traced exception, and
     asserts it is caught and retried (`MAX_ATTEMPTS` calls), that a non-timeout
     error escapes the retry loop on the first attempt, that the tool snippet
     returns `{"ok": False, …}`, and that a secret in the trace never reaches the
     code. The UI never executes a snippet.
   - `requests` was added to `backend/requirements.txt` (tests only).

2. **No "unknown" step-type tag.** `timeline._status()` maps empty/`unknown`/
   `none` to `success`, so the tag is never rendered.

3. **Evidence rows collapse.** Each timeline item now carries
   `evidence_summary` (e.g. `2 evidence rows (rule +0.25, LLM)`) and every row
   carries `adds_information`; a row that only restates the step's own error is
   counted, not quoted. The page renders one expandable line
   (`EvidenceRows` in `ExecutionDetailPage.tsx`).

4. **Code blocks.** No nested scroll: the `<pre>` is as tall as the code and
   scrolls horizontally only; snippets over 26 lines fold behind
   *Show code (N more lines)*. Generated comment lines wrap at 70 columns
   (`_wrap_comment`, `_tidy`). The Copy button sits top-right on every fix card,
   and the internal `template: …` label is gone.

### Compare page

5. **Incomparable runs.** `diff.compare_basis()` reports `same_agent` /
   `same_task` from the runs' own `agent_name` and their first task text. When
   they differ the response carries `comparable: false`,
   `comparison_banner: "Different agents or tasks - the diff is limited"`, the
   reason, and `outcomes`; the summary leads with each run's outcome (its root
   cause, trimmed to its first clause) and only then names the first step-level
   difference. The page shows the banner above the summary.

6. **Unrecorded input.** A step with no arguments and no input shows `-`
   (`diff.MISSING`) and is never counted as a differing field, so it can never
   be the first divergence.

7. **Pluralization and the demo toggle.** `plural()` in `ComparePage.tsx` ("1
   tool call", "3 steps"), and `DemoToggle` now sits in the Compare header.

8. **Tests.** `test_compare_diff.py` grew from 19 to 26: different agents,
   different tasks, same agent + same task, one agent with two tasks, an
   unrecorded input shown as `-`, two runs with no input being identical, and a
   missing input never becoming the first divergence.

### Data

9. **Junk executions removed.** The dev database was backed up
   (`backend/data/agent_autopsy.backup-*-pre-junk-delete.db`) before deleting
   the three test runs ("hh", "Live Test", "Unnamed agent") through the API.
   Upload filenames are now reduced to a bare name
   (`ingest.safe_filename`), so a client cannot get a local path into a stored
   record or a report.

---

## Phase 5 cleanup

- **Model quality moved.** The classifier table left Settings for
  `frontend/src/pages/ModelQualityPage.tsx` at `/settings/model-quality`, linked
  from Settings under *Developer → Developer: model quality*.
- **Classifier is a hint only.** `analyzer.py` now records the classifier's
  category and confidence as a hint beside the rule result and says whether it
  agrees; it only becomes the diagnosis when no rule matched at all, and the
  note says so. `deps.py` exposes `ml.hint_only` and `ml.agrees`, and the detail
  page renders "agrees with the rule result" / "differs … the rules decide" /
  "no rule matched, so this is the only signal".
- **Chart labels.** Failure Analytics "Most common root causes" is now a
  horizontal bar chart with a 150 px category axis and ellipsised long labels —
  no rotated or clipped ticks.
- **One content width.** `Layout.tsx` centres every page in a single
  `max-w-[1280px]` box inside the area right of the sidebar; the double-reserved
  sidebar offset is gone, so no page has a wide empty strip on its left.
- **README.** Rewritten: setup, every environment variable by name (no values),
  the screenshot list, the security notes (redaction at write and at the API
  boundary, backup files, never commit `.env`, filenames, auth and rate limits),
  the API table, the test map and the known limits.

---

## Files changed

Backend
- `app/engine/snippets.py` — exception-derived catches, comment wrapping, imports
- `app/engine/analyzer.py` — classifier as a hint
- `app/api/timeline.py` — `_status`, `adds_information`, `evidence_summary`
- `app/api/diff.py` — `compare_basis`, outcome-led summary, `MISSING` input
- `app/api/deps.py` — `ml.hint_only` / `ml.agrees`
- `app/api/executions.py` — `agent_name` passed to the diff
- `app/api/ingest.py` — `safe_filename` on upload
- `app/ingest/__init__.py` — `safe_filename`
- `requirements.txt` — `requests` (tests)
- `tests/test_snippet_sandbox.py` — new, 16 tests
- `tests/test_compare_diff.py` — 7 new tests

Frontend
- `src/pages/ExecutionDetailPage.tsx` — evidence rows, code block, Copy, hint text
- `src/pages/ComparePage.tsx` — banner, pluralization, demo toggle
- `src/pages/ModelQualityPage.tsx` — new
- `src/pages/SettingsPage.tsx` — developer link, table removed
- `src/pages/AnalyticsPage.tsx` — horizontal bar chart
- `src/components/Layout.tsx` — one centred content width
- `src/App.tsx` — `/settings/model-quality` route
- `src/lib/api.ts` — new response fields

Docs
- `README.md` — rewritten
- `PHASE5_CLEANUP.md` — this file

---

## Test steps

```bash
cd backend
DATABASE_URL="sqlite:////tmp/aa_test.db" .venv/bin/python -m pytest tests -q
# 195 passed

cd ../frontend
npx tsc --noEmit     # no output
npm run build        # built
```

Checked in the running app (all HTTP 200): `/`, `/history`, `/compare`,
`/analytics`, `/settings`, `/settings/model-quality`, `/executions/8`.
`/api/compare?ids=8,10` returns the banner, the caveat and outcome-led summary.
