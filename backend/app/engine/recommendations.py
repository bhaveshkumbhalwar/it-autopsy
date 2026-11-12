"""Recommendation catalogue.

Each recommendation is tied to the failure categories it addresses and carries
the fields the brief requires: problem, fix, reason, benefit and priority.

Two rules keep the output honest:

* **Category-owned advice.** Every entry declares exactly the categories it is
  valid for, and ``for_categories`` only ever returns entries that match the
  categories actually detected. A database failure cannot surface API advice,
  and a policy block cannot surface retry advice.
* **Specific before generic.** Category-specific entries are ordered ahead of
  the shared "observability" style entry, which is only appended when it does
  not crowd out something actionable.
"""

from __future__ import annotations

from typing import Any

from app.core.models import FailureCategory as F

CATALOGUE: list[dict[str, Any]] = [
    # ------------------------------------------------------------- database
    {
        "id": "db-connectivity",
        "categories": [F.DATABASE.value],
        "problem": "The agent cannot reach the database, so every query in the run fails.",
        "fix": "Check the connection string, host/port and credentials; add a bounded connect timeout and a health check before the agent starts working.",
        "reason": "Connection-level errors (refused, pool exhausted, DNS) are environment problems, not agent logic, and they fail every step that touches data.",
        "benefit": "Fails fast with a clear message instead of failing the whole task silently.",
        "priority": "critical",
    },
    {
        "id": "db-transactions",
        "categories": [F.DATABASE.value],
        "problem": "Writes conflict or violate constraints, so the transaction rolls back.",
        "fix": "Use a transaction per tool call, handle duplicate-key and deadlock errors explicitly, and retry deadlocks with jitter after rolling back.",
        "reason": "Deadlocks and integrity errors are recoverable at the transaction boundary but fatal if they escape as exceptions.",
        "benefit": "Fewer failed writes and no partial data left behind.",
        "priority": "high",
    },
    {
        "id": "db-query-budget",
        "categories": [F.DATABASE.value],
        "problem": "Queries are unbounded, so a single tool call can saturate the pool.",
        "fix": "Add LIMIT to every agent-issued query, cap statement timeouts, and return a structured 'too much data' result the agent can narrow.",
        "reason": "Agent-generated queries are the least predictable load on a database.",
        "benefit": "Predictable database load and readable failures.",
        "priority": "medium",
    },
    # ---------------------------------------------------- authentication
    {
        "id": "credential-rotation",
        "categories": [F.AUTH.value],
        "problem": "Credentials are missing, expired or lack the required scope.",
        "fix": "Verify keys and scopes in CI, surface auth errors separately from transient errors, and never retry them.",
        "reason": "Auth failures are permanent; retrying wastes time and hides the cause.",
        "benefit": "Immediate, actionable error messages.",
        "priority": "critical",
    },
    {
        "id": "auth-error-surface",
        "categories": [F.AUTH.value],
        "problem": "Auth failures are reported as generic tool or API errors.",
        "fix": "Map 401/403 to a dedicated error type, name the credential that failed, and stop the run with a remediation hint.",
        "reason": "The operator needs to know which key to rotate, not that 'a tool failed'.",
        "benefit": "Minutes instead of hours to recover from an expired key.",
        "priority": "high",
    },
    # --------------------------------------------------------------- API
    {
        "id": "retry-with-backoff",
        "categories": [F.TIMEOUT.value, F.API.value, F.TOOL_EXECUTION.value],
        "problem": "Transient provider or tool failures end the whole execution.",
        "fix": "Wrap every model and tool call in a retry policy with exponential backoff and jitter (e.g. 3 attempts, 0.5s → 4s).",
        "reason": "Timeouts, 429/5xx responses and connection resets are usually momentary; a second attempt often succeeds.",
        "benefit": "Large reduction in avoidable failures without changing agent logic.",
        "priority": "high",
    },
    {
        "id": "provider-fallback",
        "categories": [F.API.value],
        "problem": "One provider outage fails every run.",
        "fix": "Add a circuit breaker per provider and a fallback model or provider for non-critical calls.",
        "reason": "Provider 5xx and overload errors are outside your control; degrading beats failing.",
        "benefit": "Runs survive a provider incident.",
        "priority": "high",
    },
    {
        "id": "rate-limit-budget",
        "categories": [F.API.value, F.TIMEOUT.value],
        "problem": "The agent sends more requests than the provider's quota allows.",
        "fix": "Add a token/request budget per run and queue calls per provider key; read the Retry-After header instead of retrying immediately.",
        "reason": "429s are self-inflicted load, not bad luck.",
        "benefit": "Stable throughput without quota errors.",
        "priority": "medium",
    },
    # ------------------------------------------------------------- timeout
    {
        "id": "timeout-budget",
        "categories": [F.TIMEOUT.value, F.API.value],
        "problem": "Operations run without a time budget, or with a budget that is too tight for the slowest tool.",
        "fix": "Set an explicit per-call timeout and a whole-run deadline, then abort early with a clear error when either is reached.",
        "reason": "Unbounded waits stall the run; too-tight limits cause failures that are indistinguishable from provider errors.",
        "benefit": "Predictable latency and failures that are easy to diagnose.",
        "priority": "high",
    },
    # -------------------------------------------------------- tool failures
    {
        "id": "tool-error-handling",
        "categories": [F.TOOL_EXECUTION.value],
        "problem": "Tool errors surface as exceptions instead of information the agent can act on.",
        "fix": "Return structured tool errors ({ok:false, error_code, message, retryable}) and describe the error schema in the tool definition.",
        "reason": "Agents can only correct a tool call if they can read what went wrong.",
        "benefit": "More self-corrections, fewer dead ends.",
        "priority": "high",
    },
    {
        "id": "argument-validation",
        "categories": [F.TOOL_EXECUTION.value],
        "problem": "The model produces arguments that do not match the tool schema.",
        "fix": "Validate arguments with a JSON schema before execution and feed validation errors back to the model as a tool result.",
        "reason": "Most argument failures are recoverable in one turn when the model sees the schema violation.",
        "benefit": "Fewer failed tool calls and clearer error messages.",
        "priority": "medium",
    },
    {
        "id": "execution-limits",
        "categories": [F.INFINITE_LOOP.value],
        "problem": "The loop has no effective stopping condition.",
        "fix": "Cap iterations, detect repeated (tool, arguments) pairs and short-circuit with a partial result plus a reason.",
        "reason": "Identical repeated calls indicate no progress; continuing only burns tokens and time.",
        "benefit": "Bounded cost and a diagnosable outcome instead of a hang.",
        "priority": "high",
    },
    {
        "id": "state-change-check",
        "categories": [F.INFINITE_LOOP.value, F.PLANNING.value],
        "problem": "The agent cannot tell whether it is making progress.",
        "fix": "Track a progress signal (new information, changed state) and force a strategy change when two consecutive steps produce none.",
        "reason": "Progress detection is the general fix behind most loop symptoms.",
        "benefit": "Faster convergence and fewer wasted steps.",
        "priority": "medium",
    },
    # --------------------------------------------------- memory / context
    {
        "id": "context-management",
        "categories": [F.MEMORY_CONTEXT.value],
        "problem": "The conversation or tool output overflows the context window.",
        "fix": "Summarise older turns, truncate large tool outputs before inserting them, and pin critical instructions at the top of every prompt.",
        "reason": "Dropped context silently removes constraints the task depends on.",
        "benefit": "Stable behaviour on long tasks at lower token cost.",
        "priority": "high",
    },
    {
        "id": "memory-budgeting",
        "categories": [F.MEMORY_CONTEXT.value],
        "problem": "The process runs out of memory while holding tool results or embeddings.",
        "fix": "Stream large results instead of materialising them, cap in-memory caches, and move vector data to a store with a size limit.",
        "reason": "Memory exhaustion kills the process, so no error handling downstream can run.",
        "benefit": "Runs that survive large inputs.",
        "priority": "high",
    },
    {
        "id": "memory-validation",
        "categories": [F.MEMORY_CONTEXT.value, F.HALLUCINATION.value],
        "problem": "Retrieved or stored memory is used without checking relevance.",
        "fix": "Add a relevance check (score threshold, re-query) and refuse to answer when retrieval returns nothing usable.",
        "reason": "Empty or irrelevant retrieval is a common cause of fabricated answers.",
        "benefit": "Answers grounded in real context, or an honest refusal.",
        "priority": "medium",
    },
    # -------------------------------------------------------- reasoning etc.
    {
        "id": "grounded-answering",
        "categories": [F.HALLUCINATION.value],
        "problem": "The final answer contains claims that no tool result supports.",
        "fix": "Require citations to tool-call IDs in the final answer and verify each claim against those results before returning.",
        "reason": "Ungrounded claims are the defining symptom of hallucination.",
        "benefit": "Verifiable answers and easy post-hoc auditing.",
        "priority": "high",
    },
    {
        "id": "output-verification",
        "categories": [F.INCORRECT_OUTPUT.value, F.REASONING.value],
        "problem": "Output format or content is not checked before it is returned.",
        "fix": "Add a validator step (schema validation plus an LLM critic that checks the answer against the task and the evidence).",
        "reason": "Most malformed or incomplete outputs are caught by a cheap validation pass.",
        "benefit": "Higher task success rate with a small latency cost.",
        "priority": "high",
    },
    {
        "id": "system-prompt",
        "categories": [F.REASONING.value, F.PLANNING.value],
        "problem": "Instructions are ambiguous about how tool results must be used.",
        "fix": "State explicitly that the answer must be derived from tool results, define the stopping condition, and list the required output fields.",
        "reason": "Many reasoning failures are prompt-contract failures.",
        "benefit": "Fewer contradictions between observations and answers.",
        "priority": "medium",
    },
    {
        "id": "tool-result-reconciliation",
        "categories": [F.REASONING.value],
        "problem": "The final answer contradicts the tool output.",
        "fix": "Insert a reconciliation step that compares the draft answer with the collected observations before returning it.",
        "reason": "A second, cheaper pass catches contradictions the main loop missed.",
        "benefit": "Detects a class of failures that is invisible to format checks.",
        "priority": "high",
    },
    {
        "id": "planner-fallback",
        "categories": [F.PLANNING.value],
        "problem": "The agent stops when its plan is invalid or a subtask fails.",
        "fix": "Add a re-plan step on subtask failure and a fallback path (ask for clarification, return a partial result).",
        "reason": "A single failed subtask should not end the whole task.",
        "benefit": "Higher completion rate on multi-step tasks.",
        "priority": "medium",
    },
    # ------------------------------------------------------- policy / safety
    {
        "id": "safety-guardrails",
        "categories": [F.SAFETY.value],
        "problem": "The agent attempted an action a policy forbids.",
        "fix": "Add pre-execution checks for sensitive actions (write/delete/send), require human approval, and log every blocked attempt.",
        "reason": "Blocked actions are visible only after the fact without pre-checks.",
        "benefit": "Prevents unsafe side effects and gives an audit trail.",
        "priority": "critical",
    },
    {
        "id": "policy-approval-flow",
        "categories": [F.SAFETY.value],
        "problem": "A policy block or human-approval gate stops the run with no recovery path.",
        "fix": "Route blocked actions to an approval queue with the exact attempted action attached, and let the agent continue with an allowed alternative instead of retrying the blocked call.",
        "reason": "Policy decisions are deliberate: retrying the same action is both useless and audit-noise.",
        "benefit": "Compliant runs that still complete, plus an auditable decision trail.",
        "priority": "high",
    },
    {
        "id": "policy-tuning",
        "categories": [F.SAFETY.value],
        "problem": "Guardrails block legitimate work (false positives).",
        "fix": "Review blocked attempts against the intended policy, tighten the tool description and the system prompt, and keep an allow-list for destructive-but-approved tools.",
        "reason": "Over-blocking is a correctness problem for the user, not just a nuisance.",
        "benefit": "Fewer unjustified refusals without loosening real protections.",
        "priority": "medium",
    },
    # ---------------------------------------------------------------- shared
    {
        "id": "observability",
        "categories": [c.value for c in F],
        "problem": "Some failures are hard to localise from the trace alone.",
        "fix": "Emit one span per step with status, duration, model, token usage and tool arguments/results (OpenTelemetry GenAI attributes).",
        "reason": "Complete spans turn guesswork into evidence.",
        "benefit": "Faster root-cause analysis on every future failure.",
        "priority": "medium",
    },
]

PREVENTION: dict[str, list[str]] = {
    F.DATABASE.value: [
        "Alert on database connection errors and pool saturation separately from agent errors.",
        "Run migrations in CI against a scratch database so schema drift is caught before a run fails.",
        "Give every agent query a statement timeout and a row limit.",
    ],
    F.AUTH.value: [
        "Alert on any 401/403 immediately — it never recovers on its own.",
        "Rotate keys on a schedule and test the rotation path in staging.",
        "Fail the deployment if a required scope is missing for a configured tool.",
    ],
    F.TIMEOUT.value: [
        "Track p95 latency per tool and alert when it approaches the timeout.",
        "Run slow tools asynchronously and poll instead of blocking.",
    ],
    F.API.value: [
        "Add circuit breakers per provider so one outage does not fail every run.",
        "Configure a fallback model/provider for non-critical calls.",
    ],
    F.TOOL_EXECUTION.value: [
        "Contract-test every tool with malformed arguments.",
        "Log the exact arguments of each failed tool call for replay.",
    ],
    F.INFINITE_LOOP.value: [
        "Emit a metric for repeated-argument calls and alert above threshold.",
        "Include a hard max-steps budget in every deployment configuration.",
    ],
    F.MEMORY_CONTEXT.value: [
        "Log token counts per prompt to spot growth before it overflows.",
        "Keep a pinned, untruncated block for task-critical instructions.",
        "Track process RSS during runs and alert before the limit is hit.",
    ],
    F.HALLUCINATION.value: [
        "Sample completed runs and audit claims against tool results.",
        "Store the evidence set with every answer so it can be re-checked.",
    ],
    F.REASONING.value: [
        "Keep a regression suite of tasks with known-good answers.",
        "Record the observation the model had when it answered.",
    ],
    F.PLANNING.value: [
        "Test the planner on tasks with deliberately failing subtasks.",
        "Define an explicit success criterion for every plan node.",
    ],
    F.INCORRECT_OUTPUT.value: [
        "Validate output against a schema in CI with recorded traces.",
        "Fail loudly on schema mismatch instead of returning partial data.",
    ],
    F.SAFETY.value: [
        "Review blocked actions weekly for false positives and negatives.",
        "Keep an allow-list for destructive tools and require approval for the rest.",
    ],
}

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


# Advice that is actively wrong once a policy decision is involved: retrying a
# blocked or refused action is not a fix.
POLICY_EXCLUDED = {"retry-with-backoff", "timeout-budget", "rate-limit-budget", "tool-error-handling"}


def for_categories(categories: list[str], limit: int = 6) -> list[dict[str, Any]]:
    """Recommendations valid for the detected categories, specific ones first.

    An entry is returned only if one of its categories was detected, so advice
    from an unrelated failure family can never appear. When the primary failure
    is a policy decision, retry-style advice is withheld.
    """
    detected = [c for c in categories if c]
    if F.SAFETY.value in detected:
        detected = detected  # kept for readability; exclusion happens below
    chosen: list[tuple[int, int, dict[str, Any]]] = []
    for position, item in enumerate(CATALOGUE):
        matched = [c for c in detected if c in item["categories"]]
        if not matched:
            continue
        if F.SAFETY.value in detected and item["id"] in POLICY_EXCLUDED:
            continue
        entry = {k: v for k, v in item.items() if k != "categories"}
        entry["for_categories"] = matched
        # A category-specific entry outranks the catch-all observability entry.
        generic = 1 if item["id"] == "observability" else 0
        chosen.append((generic, PRIORITY_ORDER.get(item["priority"], 9), entry))

    chosen.sort(key=lambda row: (row[0], row[1], -len(row[2]["for_categories"])))
    ordered = [entry for _generic, _prio, entry in chosen]

    # Only append the generic entry when nothing specific exists at all.
    specific = [e for e in ordered if e["id"] != "observability"]
    if specific:
        return specific[:limit]
    return ordered[:limit]


def prevention_for(categories: list[str]) -> list[str]:
    out: list[str] = []
    for c in categories:
        for line in PREVENTION.get(c, []):
            if line not in out:
                out.append(line)
    if not out:
        out = ["Add regression traces for this agent so future failures can be compared.", "Review this trace with the agent owner."]
    return out
