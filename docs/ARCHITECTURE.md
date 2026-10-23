# Architecture

## One-line summary

A deterministic, tool-using agent that turns a natural-language IT ticket into
a traced sequence of policy-checked observations, cited guidance, and an honest
verification or escalation decision.

There is no hosted LLM in the default path. The same pipeline runs in tests,
the evaluation harness and the Streamlit UI.

## Loop

```text
intake
  → triage (classifier + transparent priority rules)
  → retrieve (curated official-source KB, OS-filtered)
  → plan (bounded, tool names only)
  → policy check (allowlist, OS, typed params, opt-in, approval)
  → execute (simulated by default; labelled)
  → observe (structured result)
  → choose next step from the observation
  → repeat until done / failed / budget exhausted
  → verify or escalate (never claims resolution without user confirmation)
```

The decision points that matter are in `src/agent.py`:

- **No evidence → stop.** If retrieval returns nothing above
  `RETRIEVAL_MIN_SCORE`, the agent escalates instead of inventing a fix.
- **Security concern → stop before any tool.** Keywords such as `ransomware`,
  `breach`, `phishing` force priority `high`, `stopped_reason=security_concern`
  and escalation. No diagnostics run.
- **Observation-driven chain.** Wi-Fi observation → connected? then DNS.
  DNS present? then reachability. Reachability ok? then VPN. A failed
  observation stops the chain and escalates. This is not a fixed four-step
  script: tests inject failing/VPN-active/disconnected adapters and assert the
  later steps do **not** run.
- **Loop prevention and budget.** Identical `(tool, params)` calls are refused;
  `MAX_STEPS` is a hard stop.

## Modules

| Module | Responsibility |
| --- | --- |
| `src/config.py` | Paths, categories, OS constants, thresholds, step budget, DB path override (`HDA_DB_PATH`) |
| `src/schemas.py` | Stdlib dataclasses for Issue, TriageResult, Evidence, Plan, Step, ToolResult, Verification, Escalation, Case |
| `src/classifier.py` | Word+character TF-IDF + `LinearSVC` trained on the leakage-reduced split; deterministic EOL phrase rule runs first; `predict` returns label, name, and uncalibrated scores |
| `src/retrieval.py` | TF-IDF cosine search over `data/knowledge/kb.json`; threshold + optional OS filter; returns `Evidence` with citations |
| `src/policy.py` | Closed tool allowlist, OS support, typed parameter validation, opt-in, approval gate |
| `src/tools.py` | Registry, simulated adapters, three hard-coded read-only macOS commands, redaction, structured errors |
| `src/agent.py` | Intake, triage, retrieval, plan, bounded observation loop, trace, escalation flags |
| `src/verification.py` | Outcome rules (`needs_user_confirmation`, `escalated`, `unable_to_verify`, `verified_resolved`, `not_resolved`) and handoff builder |
| `src/database.py` | SQLite `cases` + `events`, parameterized SQL, redaction at the persistence boundary |
| `src/evaluation.py` | Five independent suites + aggregate/per-suite reports |
| `app.py` | Streamlit UI: run a case, browse stored cases, view metrics, inspect the allowlist |

## Priority rules (transparent, not trained)

The dataset has no urgency label, so priority is a small documented rule set in
`src/agent.py`:

1. **high** if a security keyword appears (`breach`, `hacked`, `ransomware`,
   `malware`, `phishing`, `stolen`, `compromised`) — and the run stops before any
   tool and escalates.
2. otherwise **high** for broad-impact phrases (`all users`, `everyone`,
   `outage`, `cannot work`, `data loss`, `down for`).
3. otherwise **medium** (default), with an `access-related issue` note when the
   text mentions password/login/access denied/locked.

The rationale list is returned with every triage result and shown in the UI, so
the rule that fired is visible.

## Data flow

```text
ticket text ──► classifier ──► category + uncalibrated scores
            └► retriever  ──► cited evidence (or none)
                              │
              priority rules ─┘
                     │
                     ▼
              bounded plan (tool names)
                     │
        policy.check(tool, params, approved)
                     │
             ToolRegistry.call(...)
                     │
        structured observation (data/summary/error)
                     │
          agent._decide_next(observation)
                     │
        verification.verify(evidence, results, run)
                     │
        needs confirmation / escalation / unable to verify
                     │
           SQLite event log (redacted)
```

## Why this design

- **Local and reproducible.** No API key, no network at inference time, no
  nondeterministic sampling in the agent path.
- **Code is the authority.** Ticket text and retrieved text can say anything;
  they cannot add a tool or loosen a parameter type.
- **Small surface.** Eight modules, stdlib dataclasses, one classifier pipeline,
  one SQLite file, one Streamlit app.
- **Testable decisions.** Observations and decision trace are plain dicts, so
  tests can assert exactly which step ran next.

## Known architectural limits

- Tool adapters are simulated except three macOS read-only commands. The agent
  therefore demonstrates the loop and safety boundary, not real remediation.
- The knowledge base covers Apple Wi-Fi and Windows DNS client troubleshooting
  only. Many ticket categories have no source and correctly escalate.
- Classification is a linear baseline. The strict leakage-reduced training set
  has no EOL rows, so EOL is handled by a deterministic phrase rule rather than
  the statistical model; see `docs/EVALUATION.md` for the caveat.
- Streamlit UI has no authentication; it is a local demo, not a multi-tenant
  service.
