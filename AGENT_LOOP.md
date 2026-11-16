# The diagnostic agent loop

This is the semi-autonomous layer added on top of the diagnosis engine. The
engine answers *what failed*; the agent answers **what to do about it, whether it
is allowed to do it, and whether it actually worked** — and it never claims the
last one without evidence.

## The loop

```
Observe  ->  Analyze  ->  Decide  ->  Act  ->  Verify  ->  Learn
   ^                                                          |
   +------------------------ verification failed -------------+
```

| Phase | Module | What it produces |
|---|---|---|
| **Observe** | `app/agent/observe.py` | A flat observation of the trace: failed steps with their error text, tool calls, retries, the engine's categories, evidence quotes, and the classifier's hint kept separate. |
| **Analyze** | `app/agent/decide.py` | One decision: is there *enough* evidence to name a cause, which category, what confidence, is a retry appropriate. When evidence is thin it returns `insufficient_evidence` with what is missing — it does not guess. |
| **Decide** | `app/agent/remediate.py` | A plan from a fixed allowlist, carrying problem, root cause, proposed solution, why this action, expected outcome, risk, whether approval is required, and the verification criteria. |
| **Act** | `app/agent/remediate.py::execute` | Runs the action **only** if the allowlist says it may run and, for approval actions, only after a human approved it. Everything else returns a refusal with a reason. |
| **Verify** | `app/agent/verify.py` | Compares the original run with a follow-up trace and returns `VERIFIED_RESOLVED`, `PARTIALLY_RESOLVED`, `STILL_FAILING`, `NEW_FAILURE` or `INCONCLUSIVE`, with the checks that produced it. |
| **Learn** | `app/agent/session.py` | Writes the outcome back: on a verified fix the original run becomes `recovered` with a recorded `resolution` (action, mode, lesson); on an unresolved one the failed action is remembered and the next attempt starts from the new trace. |

## Action categories

Every action declares its category, and the category is a property of the
action — no code path can promote a recommendation into an execution.

**Automatic (safe)** — in-process, no external effect:
`reanalyze-rules`, `check-tool-availability`.

**Needs approval** — cannot run until a human approves it:
`reanalyze-with-llm`.

**Recommendation only** — never executed by the agent, instructions + verification plan only:
`bounded-retry`, `rate-limit-backoff`, `verify-credentials`, `check-db-configuration`,
`prepare-arguments`, `escalate-to-human`, `collect-better-trace`.

The full catalogue is exposed read-only at `GET /api/agent/actions`, so the UI
shows what the agent is and is not allowed to do.

## Verification honesty

* Verification is **simulated** by default: the app does not run the agent under
  test, so it grades a follow-up trace you submit, and the verdict says
  `mode: "simulated"` and keeps the evidence (both executions, both failing
  steps, the same-failure check).
* The follow-up is ingested and diagnosed as its own run — it appears in history
  and in the comparison like any other execution.
* A follow-up that never reaches the failing step is `INCONCLUSIVE`, not
  "resolved".
* Error signatures ignore numbers, so "read timeout=30" and "read timeout=60" are
  the same failure; two genuinely different errors are not.

## The loop's stopping conditions

The session stops, and says why, when:

* the fix is **verified** (`session-stopped`: the fix was verified);
* the **attempt limit** is reached (`AGENT_MAX_ATTEMPTS`, default 3);
* the operator **rejects** the plan;
* verification is **inconclusive** (nothing is changed on the original run);
* an **earlier plan is still open**, so no second plan is stacked on top of it.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/agent/actions` | The action allowlist and what each category means. |
| GET | `/api/agent/executions/{pk}` | The whole session: attempts, decisions, events. |
| POST | `/api/agent/executions/{pk}/start` | Observe → analyze → decide → propose the next plan. |
| GET | `/api/agent/remediations/{id}` | One attempt with its plan, action result and verification. |
| POST | `/api/agent/remediations/{id}/approve` | Approve (`approved: true`) or reject (`false`). Add `execute_if_ready: true` to approve **and** run in one call (never runs a recommendation-only plan). Rejection stops the session. |
| POST | `/api/agent/remediations/{id}/execute` | Run an automatic or approved action; anything else is refused with a code. |
| POST | `/api/agent/remediations/{id}/verify` | Submit a follow-up trace, get the graded verdict and the next attempt. |
| GET | `/api/agent/verification-statuses` | What each verdict means, for the UI. |
| GET | `/api/executions/{pk}` | Now also carries `agent` (the session) and `execution.resolution`. |

## Database

New tables (created idempotently at startup, no existing row is touched):

* `remediations` — one row per attempt: decision, plan, approval, action result,
  verification, and the follow-up execution it was verified against.
* `agent_events` — one row per loop step (`observe` / `analyze` / `decide` /
  `act` / `verify` / `learn`), which is what the activity timeline renders.

New column: `executions.resolution` (JSON, default `{}`), added by the existing
migration runner.

## Frontend

`frontend/src/components/AgentPanel.tsx`, mounted on the execution detail page:

* **Diagnostic agent** — state, attempts used / limit, the six phases, stop reason.
* **Agent decision** — verdict, evidence quotes, retry appropriateness, classifier
  hint (secondary), or the explicit uncertainty when evidence is insufficient.
* **Remediation plan** — problem, root cause, solution, why, expected outcome,
  verification criteria, risk, instructions, and working **Approve / Reject /
  Run action** buttons wired to real endpoints. The verification form accepts a
  follow-up trace.
* **Verification** — verdict, what was checked, the run it was compared with.
* **Agent activity** — the event log; **Attempts** — every attempt with its
  outcome.

## Tests

`backend/tests/test_agent_loop.py` (25 tests): a failing trace yields a decision
with evidence; a plan carries every required field; automatic actions execute and
are recorded; an approval action is refused until approved; rejection stops the
session; successful verification marks the run `recovered` with its resolution;
an unsuccessful one starts the next attempt and does not re-propose the failed
action; a new failure is reported as such; the attempt limit is enforced; thin
evidence is reported as such; a healthy run is not remediated; history shows all
six phases; the allowlist refuses unknown and recommendation-only actions; a
credential failure is never a retry; verification refuses to call an unreached
step resolved; secrets in a follow-up trace are redacted.

Full suite: **364 passed** (`cd backend && .venv/bin/python -m pytest tests -q`).
Frontend: `npx tsc --noEmit && npm run build` clean.

## The Approve button

Three states, and the panel offers only what the backend will honour:

* **Approve & run** (one click) — for a plan the allowlist lets a human execute
  (automatic, or an already-approvable action). The approve endpoint takes
  `execute_if_ready: true`, records the approval and runs the action in the same
  request; the response says `executed: true/false`.
* **Approve** — for a recommendation-only plan, or when you want the decision
  recorded before the action runs. Approving never silently executes anything.
* **Reject** — stops the session, as before.

Fixes behind it: approving an approval-gated plan used to reset its status to
`proposed`, which erased the gate and left Run disabled forever. Approval now
changes *who may proceed*, not *what the plan is*: an approval action stays
`awaiting_approval` until it runs, an automatic one stays `proposed`, and a
refused attempt stays open so it can be approved and run.

## Limits (stated, not hidden)

* The agent does not run your agent. Actions that would touch a system outside
  this app (rotate a key, change a database, retry your process) are
  recommendation-only by design.
* Verification is only as good as the follow-up trace you submit.
* The attempt limit is per execution; a new session can be started again.
