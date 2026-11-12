# Phase 4 — Compare : 

Two or three runs, their steps lined up by number, and one sentence saying why
they behaved differently.

## Endpoint

`GET /api/compare?ids=8,9` (2–3 ids; more are truncated) now returns the old
summary block plus a `comparison` object built by `app/api/diff.py`:

| Field | Meaning |
|---|---|
| `runs` | per execution: name, id, status, step count, latency, tool calls, categories, root cause |
| `rows` | one row per aligned step number; `cells` has one entry per run (`present: false` + `label: "no step"` when that run is shorter) |
| `rows[].differing_fields` | which of `kind`, `tool`, `status`, `duration_ms`, `tokens`, `args` differ |
| `first_divergence` | the earliest row that differs, or `null` |
| `identical` | true when every aligned step matches — "no differences found" |
| `summary` | one rule-based sentence, e.g. `Step 1 (a task step) was given different input: "summarise RAG papers" vs "find the pro plan price"` |
| `summary_source` | always `rules`; `llm_note` is an optional extra sentence from the model when one is configured |

Everything compared is redacted first: arguments, inputs and error text pass
through the same redactor as the rest of the API. Task and LLM steps are
compared on their *input* (what they were asked to do), tool steps on their
arguments.

## Page (`/compare`)

- **Searchable picker** replaces the `<select>`: typing filters by agent, execution
  id or status; each row shows agent name, execution id, status badge, failure
  family and relative time ("3 minutes ago", full timestamp on hover). Ticking
  is by click; 2–3 runs; the demo toggle is respected (demo traces are hidden
  until it is switched on).
- Selecting re-runs the comparison automatically; the URL keeps `?ids=`.
- **Summary card** at the top: the one-line reason, an optional model note, and a
  small block per run (status, root cause, steps, latency, tool calls).
- **Aligned timelines**: a grid with one column per run, aligned by step number.
  The first divergence row is highlighted with a "diverge" chip; differing
  values (latency, tokens, args) are shown in amber. "Only differences (N)"
  filters out the rows that match. A shorter run shows `no step` rather than
  being cropped. Identical runs say "These runs are identical step for step."

## Files changed

| File | Change |
|---|---|
| `backend/app/api/diff.py` (new) | `build_comparison(runs)` — alignment, per-field comparison, first divergence, rule-based summary |
| `backend/app/api/executions.py` | `/compare` accepts 2–3 ids, builds the diff from the stored traces, adds the optional `llm_note`, returns it through `sanitize()` |
| `backend/app/engine/llm.py` | new `complete(prompt, max_tokens)` helper for the one-line note; removed a duplicated `check_health` that survived an earlier edit (the copy the router did not use, and the one the fixture patch did not reach) |
| `backend/app/engine/snippets.py` | follow-up: every failure family now has a snippet (`generic` guard for reasoning / hallucination / planning / incorrect output / safety), the auth snippet no longer prints a rate-limit status as the rejection code, and each snippet carries `note: "Generic Python - adapt to your framework (<framework>)"` |
| `frontend/src/pages/ComparePage.tsx` | rewritten picker, summary card and aligned diff table |
| `frontend/src/pages/ExecutionDetailPage.tsx` | renders the snippet note as a caption under each fix |
| `frontend/src/lib/api.ts` | `CompareDiff`, `DiffRow`, `DiffCell`, `CompareResult`; `FixSnippet.note` |

Deleted: `frontend/src/pages/AnalysisDetailPage.tsx` (unrouted since Phase 3, no
imports left — `grep -rn AnalysisDetailPage frontend/src` is empty).

## Tests

New `backend/tests/test_compare_diff.py` — 19 tests:

- identical runs → `identical: true`, "no differences found"
- diverging at step 1 → that row is `first_divergence`, later matching rows are
  not flagged
- a later difference (step 3) leaves steps 1–2 clean
- different lengths → padded with `no step`, total = longest run, summary says
  the runs are not the same length
- 3-way compare → first step any run diverges at, three cells per row, and the
  two agreeing runs still agree
- more than three runs truncated; one run raises; empty/single-step runs safe
- latency + token differences, argument differences (inputs quoted in the
  summary), task steps compared on input
- secrets in arguments and error text are redacted
- two real demo traces compare; identical demo copies are identical
- the route: 200 with a `comparison`, 422 for one id or non-numeric ids, and no
  provider key in the response

Phase 3 follow-ups are covered in `backend/tests/test_execution_detail.py`:
auth and database templates exist and are distinct; all 12 failure families have
a snippet; the auth snippet fails fast and never prints a 429 as the rejection
code; the database snippet is bounded and retries only transient errors; every
snippet carries the framework note (langgraph / crewai / autogen / unspecified);
a reasoning failure is labelled `generic` and "not a mechanical patch".

Full suite: **172 passed**.

```
cd backend
DATABASE_URL="sqlite:////tmp/aa_test.db" .venv/bin/python -m pytest tests -q
```

## Verified on the live app

`http://127.0.0.1:8000/compare?ids=8,9`

| ids | result |
|---|---|
| `8,9` | `Step 1 (a task step) was given different input: "Summarise the three most recent papers on RAG…" vs "Find the Pro plan price on example.com".` — 7 rows, first divergence at step 1 |
| `8,8` | `No differences found in the 6 aligned steps of Research Agent vs Research Agent.` |
| `8,9,10` | 20 rows (longest run), three columns, first divergence at step 1 |

Execution detail still renders (HTTP 200) and its fixes now show
`Generic Python - adapt to your framework (langchain)`.

## Not done

- Phase 5 (cleanup: ML evaluation page, ML hint-only, chart axes, widths) — not
  started.
- The comparison aligns by step number, which suits the trace numbering every
  ingester produces; a run that inserts a step early shows as a divergence plus
  a shifted column rather than a sequence alignment.
