# Project Status — Autonomous IT Help Desk Agent

Last updated: 2026-10-23  
Overall status: **Phases 0–12 complete; Bluetooth routing and UI follow-up fixes verified. No commit has been made.**

## Objective

Build a tool-using IT Help Desk agent whose loop is:

```text
intake → triage → retrieve evidence → plan → policy check → approval if needed
       → tool execution → observe result → next step → verify or escalate
```

The agent must use returned observations to decide the next step, and the
deterministic local baseline must work without a hosted LLM or API key.

## Phase progress

| Phase | Status | Main evidence |
| --- | --- | --- |
| 0 — Inspect and research | COMPLETE | `docs/SOURCES.md`, `docs/RESEARCH_NOTES.md` |
| 1 — Environment | COMPLETE | `.venv`, pinned `requirements.txt`, initial pytest |
| 2 — Dataset acquisition and audit | COMPLETE | `docs/DATA_CARD.md`, `reports/audit.json` |
| 3 — Classifier baseline | COMPLETE | hybrid acc 0.8356, macro-F1 0.8017; base model 0.7473 / 0.6449 |
| 4 — Source-grounded retrieval | COMPLETE | `data/knowledge/kb.json`, retrieval eval |
| 5 — Agent controller and planning | COMPLETE | observation-driven loop + tests |
| 6 — Safe tools | COMPLETE | `src/policy.py`, `src/tools.py`, security tests |
| 7 — Verification and escalation | COMPLETE | `src/verification.py`, outcome tests |
| 8 — Persistence and observability | COMPLETE | SQLite schema, redacted events, DB tests |
| 9 — Streamlit UI | COMPLETE | AppTest + live HTTP 200 / health `ok` |
| 10 — Evaluation and security | COMPLETE | five suites, per-suite reports |
| 11 — Documentation and demo | COMPLETE | README + five docs + demo script |
| 12 — Final acceptance | COMPLETE | clean-venv run (below) |

---

## Phase 0 — Inspect and research

- Deleted the previous unrelated project at the user's explicit instruction
  (scope confirmed, including two virtual environments).
- Initialised a fresh Git repository on `main` (still zero commits).
- Verified dataset schema, labels, licence and statistics, plus Apple,
  Microsoft, scikit-learn, OWASP and NIST sources.
- Wrote `README.md`, `PROJECT_STATUS.md`, `docs/SOURCES.md`,
  `docs/RESEARCH_NOTES.md`, `data/README.md`.

Environment observed: macOS 27.0.1 arm64, `~/anaconda3/bin/python` 3.14.6,
scikit-learn 1.9.0, pandas 3.0.3, NumPy 2.4.6, Streamlit 1.58.0, pytest 9.0.3.

## Phase 1 — Environment

Commands:

```text
~/anaconda3/bin/python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          → 5 passed
```

Added `.gitignore`, `.env.example`, `src/__init__.py`, `src/config.py`,
`tests/test_environment.py`. Pinned versions were all installable on Python
3.14.6; no fallback interpreter was needed.

## Phase 2 — Dataset acquisition and audit

Commands:

```text
.venv/bin/python scripts/download_dataset.py
.venv/bin/python scripts/audit_dataset.py
```

Observed facts (generated from the files, not hard-coded):

- 2,229 rows: 1,572 train / 657 test; 7 category labels.
- 33 exact texts shared between train and test; 610 duplicate train rows and
  102 duplicate test rows; 1 conflicting label in train.
- Test majority baseline 221/657 = **0.3364**.
- No language column and **no resolution outcome**.
- Data card: `docs/DATA_CARD.md`; raw audit: `reports/audit.json`.

## Phase 3 — Classifier baseline

Commands:

```text
.venv/bin/python -m scripts.evaluate_classifier
```

- 5-fold CV on training data only (word features): LogisticRegression 0.7170,
  LinearSVC 0.7616; word+char LinearSVC 0.7872 → selected word+char LinearSVC.
- Removed 33 test-overlapping texts and 1 conflicting-label text from the
  **training** set only: 1,572 → 1,460 rows. Test split untouched. This removes
  both unique EOL texts (they occur in both splits), so the base model has six
  classes and no EOL examples.
- Added a deterministic EOL keyword rule before the model. Training-only
  validation: precision 0.9783, recall 1.0000 (45 TP, 1 FP, 0 FN).
- Held-out test, hybrid: accuracy **0.8356**, macro-F1 **0.8017**, weighted-F1
  0.8311. Base model only: accuracy **0.7473**, macro-F1 **0.6449**,
  weighted-F1 **0.7115**, EOL F1 0.0000.
- Caveat: the EOL rule was introduced after the original EOL failure was
  observed, so its 1.0000 test F1 is optimistic; the base-model macro-F1 is the
  honest statistical headline.
- Scores are explicitly uncalibrated; the EOL rule returns a rule indicator.

Artefacts: `models/classifier.joblib`, `reports/classification_metrics.{json,md}`.

## Phase 4 — Source-grounded retrieval

- Curated 10 chunks from verified Apple Support and Microsoft Learn pages in
  `data/knowledge/kb.json`; metadata + short derived summaries only.
- TF-IDF + cosine similarity, threshold 0.18, top-k 3, optional OS filter.
- Low similarity returns no evidence; the agent escalates instead of answering.
- Hand-written eval: 10 answerable + 4 unanswerable queries (see
  `docs/EVALUATION.md`). Observed Hit@1 1.0000, MRR 1.0000, false-positive rate
  0.0000 — a small sanity check, not a benchmark.

## Phase 5 — Agent controller and planning

- `src/agent.py` implements intake → triage → retrieve → plan → policy →
  execute → observe → decide next → stop.
- Initial tool is chosen from the symptom (DNS hint vs general network hint).
- The **next** tool is chosen from the result: connected → DNS present → ping
  success → VPN check. A failed observation stops the chain.
- Guards: security keywords stop before any tool; no evidence stops before any
  tool; repeated `(tool, params)` refused; `MAX_STEPS` hard stop.
- Every run returns a decision trace with step, decision, tool, observation and
  parameters.

## Phase 6 — Safe tools

- Closed allowlist in `src/policy.py` (8 tools), per-tool OS support, typed
  parameters, hostname validation, opt-in for real diagnostics, explicit
  approval gate.
- `src/tools.py`: deterministic simulated adapters; three hard-coded read-only
  macOS commands (`system_profiler`, `scutil --dns`, `networksetup -getinfo`);
  timeout; structured errors; output redaction (emails, MACs, tokens, home
  usernames).
- No `shell=True`, `os.system`, `os.popen`, `eval` or `exec` in the tool layer;
  a static test enforces this.

## Phase 7 — Verification and escalation

- All five outcomes implemented: `verified_resolved`, `not_resolved`,
  `needs_user_confirmation`, `escalated`, `unable_to_verify`.
- Tool success alone → `needs_user_confirmation`; only an explicit user reply
  can produce `verified_resolved` or `not_resolved`.
- Escalation handoff includes category, priority, symptoms, citations, attempted
  actions, observed results, why the agent stopped, and next steps.
- Tests in `tests/test_verification.py`.

## Phase 8 — Persistence and observability

- SQLite `cases` + `events` (`src/database.py`), created idempotently.
- All SQL parameterized; hostile text stored as data (tested with
  `'; DROP TABLE cases; --`).
- Events store tool name, redacted arguments, status, summary, source IDs and
  approval flag. Redaction is applied again at the persistence boundary.
- `HDA_DB_PATH` overrides the database path for tests and demos.
- Tests in `tests/test_database.py`.

## Phase 9 — Streamlit UI

`app.py` has four tabs: New case, Cases, Evaluation, Safety.

- New case: run the agent, show category/priority rationale, cited evidence,
  decision trace, labelled observations, confirmation buttons, escalation JSON.
- Cases: recent cases and their redacted event logs.
- Evaluation: real metrics and the full aggregate JSON.
- Safety: live allowlist table with simulated/risk/approval/OS columns.

Verification:

```text
.venv/bin/python -m pytest -q
  includes tests/test_app.py (AppTest: render, run a case, persist events,
  reject empty input) → all passed

.venv/bin/python -m streamlit run app.py --server.headless true --server.port 8599
curl http://localhost:8599/                 → 200
curl http://localhost:8599/_stcore/health   → ok
```

The server was stopped after the check.

## Phase 10 — Evaluation and security

Separate suites (no tuning on the classification test split):

| Suite | Command | Result |
| --- | --- | --- |
| Classification | `.venv/bin/python -m scripts.evaluate_classifier` | hybrid acc 0.8356, macro-F1 0.8017; base model 0.7473 / 0.6449 |
| Retrieval | `.venv/bin/python -m src.evaluation` | Hit@1 1.0000, MRR 1.0000, FP 0.0000 |
| Agent | same | 8/8 scenarios |
| Security | same | 16/16 checks |
| Robustness | same | 12/12 cases |
| Tests | `.venv/bin/python -m pytest -q` | 78 passed |

Reports: `reports/{evaluation,retrieval_metrics,agent_metrics,security_metrics,robustness_metrics}.{json,md}`
and `reports/classification_metrics.{json,md}`.

## Phase 11 — Documentation and demo

Written/updated:

- `README.md` — overview, quick start, real metrics, safety, limitations.
- `docs/ARCHITECTURE.md` — loop, module map, decision rules, limits.
- `docs/EVALUATION.md` — protocols, full metric tables, what is not measured.
- `docs/SAFETY.md` — threat model, controls, allowlist, residual risks.
- `docs/DEMO_SCRIPT.md` — 5-minute walkthrough with actual commands.
- `docs/DATA_CARD.md`, `docs/SOURCES.md`, `docs/RESEARCH_NOTES.md` — updated
  where later phases added evidence.

## Phase 12 — Final acceptance

A separate clean virtual environment was created and the full dependency set
installed from `requirements.txt`. The existing `models/` and `reports/`
artefacts were moved aside first, so this run also exercised the fresh-checkout
bootstrap (git-ignored model absent, auto-trained on first use).

```text
~/anaconda3/bin/python -m venv .venv-acceptance
.venv-acceptance/bin/pip install -q -r requirements.txt
.venv-acceptance/bin/python -m pytest -q
  → 78 passed in 18.01s
.venv-acceptance/bin/python -m scripts.evaluate_classifier
  → trained 1,460 rows, best_estimator linearsvc_word_char, CV macro-F1 0.7872
  → hybrid: accuracy 0.8356164383561644, macro_f1 0.8016644361679968,
    weighted_f1 0.8311102921311898
  → model-only: accuracy 0.7473363774733638, macro_f1 0.6449021265047526,
    weighted_f1 0.7115369790509121
  → EOL rule on train: precision 0.9783, recall 1.0, matches 46
.venv-acceptance/bin/python -m src.evaluation
  → classification acc=0.8356, retrieval hit@1=1.0000,
    agent 8/8, security 16/16, robustness 12/12
```

The clean environment produced the same metrics as the development environment.
The acceptance environment was then removed; `.venv` remains for normal use.
Both environments are git-ignored.

## Conversation and case-continuity follow-up

Added lightweight multi-turn case support on top of the existing deterministic
pipeline:

- `Agent.run(..., case_id=...)` can continue an existing case instead of
  creating a new one.
- The UI saves analysis, redacted conversation history, and a workflow status
  (`open`, `investigating`, `awaiting_user`, `resolved`, `escalated`) for each
  case.
- Cases can be reopened from the Cases tab and continued in New case.
- Analysis clarification questions and verification follow-up questions are shown
  prominently; case status is derived from verification outcome, not tool success.

Commands and results:

```text
.venv/bin/python -m pytest -q
  -> 86 passed in 10.17s
.venv/bin/python -m src.evaluation
  -> classification acc=0.8356, retrieval hit@1=1.0000,
     agent 8/8, security 16/16, robustness 12/12
.venv/bin/python -m streamlit run app.py --server.headless true --server.port 8599
  -> curl / = 200, curl /_stcore/health = ok
```

Limitations: conversation context is appended as text rather than a structured
dialogue planner; follow-up runs may rerun relevant diagnostics; no LLM is used
by default; case continuity is local SQLite only.

## Final audit — F1 and integrity fixes

The attached final-audit instruction file was not readable from this machine
(`~/.Trash` is protected by macOS privacy controls). The audit below follows
the stated objective and the project's own acceptance rules; it does not claim
to have read that file.

Commands run after the changes:

```text
.venv/bin/python -m pytest -q
  → 78 passed in 8.75s
.venv/bin/python -m src.evaluation
  → classification acc=0.8356, retrieval hit@1=1.0000,
    agent 8/8, security 16/16, robustness 12/12
.venv/bin/python -m streamlit run app.py --server.headless true --server.port 8599
  → curl / = 200, curl /_stcore/health = ok
```

What changed:

1. **EOL collapse fixed without feeding test text into training.** Strict
   leakage removal had deleted all 45 EOL training rows. A deterministic
   `end of life`/`EOL` phrase rule now runs before the statistical model. It is
   validated on the training split only (precision 0.9783, recall 1.0000).
2. **Model improved through training-only CV.** Word+character TF-IDF plus
   `LinearSVC(C=0.75, class_weight=balanced)` raised CV macro-F1 from 0.7427
   (old word-only baseline) to 0.7872. The base model's held-out macro-F1 rose
   from 0.5947 to 0.6449 without the rule.
3. **Score alignment fixed.** Decision scores are mapped through the
   estimator's own `classes_`, not positional label names. This matters because
   the strict model has six classes, not seven.
4. **Redaction claims made true.** `ToolResult.data` is now redacted at the tool
   boundary, and `cases.issue_text` at the SQLite boundary. Two security checks
   cover these paths (14 → 16 checks).
5. **Fresh-checkout bootstrap fixed.** `Classifier` auto-trains the default
   model once if it is missing; the acceptance run above deleted `models/` first
   and still passed. Tests now include classifier/rule/score/bootstrap coverage
   (68 → 78).

Honest caveat: the EOL rule was chosen after the original EOL failure was
observed. Its 1.0000 held-out F1 is therefore optimistic, not independent
evidence. The base-model macro-F1 (0.6449) is the honest statistical result.

---

## Problems found during development and how they were fixed

| Problem | Fix |
| --- | --- |
| `LinearSVC.decision_function` returned 6 columns while 7 label names were indexed | Guarded score mapping with `min(len(labels), len(scores))` |
| Agent treated the tool registry's dict as a `ToolResult` | Explicit dict → `ToolResult` conversion; optional registry injection for tests |
| Test suite assumed old verification/agent APIs | Rewrote tests around `PolicyViolation`, `AgentRun`, run-aware verification |
| UI logged `r["status"]`, which tool results do not have | Log status from `ok`, log from the decision trace, recompute outcome on confirmation |
| Robustness case used 571 × 8 = 4,568 chars and was correctly rejected | Corrected to exactly 4,000 chars |
| Cross-OS citation (Windows page for a macOS query) | Added OS filter in retrieval, used by the agent |
| Simulated-only tool layer could not demonstrate the approval gate | Added approval-gated simulated state-changing tool; real tools also require approval |
| DB could receive unredacted summaries from future call sites | Redaction moved into `DB.log_event` as defence in depth |
| Strict leakage removal deleted the entire EOL class, making EOL impossible to predict | Added a deterministic EOL keyword rule, validated on training only and reported separately from the base model |
| Base-model score names could be misaligned when a class was absent | Map decision scores through the estimator's own `classes_` instead of positional label names |
| README claimed redaction "before storage" but `cases.issue_text` stored raw text | Redact case text at `DB.save_case`; also redact `ToolResult.data` at the tool boundary |
| A fresh checkout had no model (git-ignored), so tests/UI failed before training | `Classifier` auto-trains the default model once on first use; explicit training is still documented |
| Tests did not cover the classifier at all | Added EOL-rule, score-alignment, class-coverage and bootstrap tests |

## Known limitations (unchanged and explicit)

- No resolution labels → no claim that the agent fixes real tickets.
- The base statistical model has no EOL training examples after strict leakage
  removal; EOL is handled by a deterministic phrase rule. That rule's test F1
  is optimistic because it was added after the failure was observed.
- Classifier scores are not calibrated.
- Knowledge base covers Apple Bluetooth/Wi-Fi and Windows DNS client guidance.
- There is no Bluetooth diagnostic tool; Bluetooth cases show cited manual
  guidance and explicitly state that no device state was observed.
- Only three real commands exist, all macOS and read-only; everything else is
  simulated and labelled.
- Retrieval/agent evaluation sets are small and hand-written.
- Streamlit UI has no authentication; it is a local demo.
- No per-language performance claim is made.

## Follow-up behavior and UI improvements — 2026-10-23

- Root cause fixed: the planner treated the substring "connect" as network
  intent, and TF-IDF matched shared "wireless" wording to Apple Wi-Fi
  diagnostics. Topic scoping now keeps Bluetooth and network evidence distinct;
  Wi-Fi tools require explicit network symptoms.
- Added Apple Mac Bluetooth pairing/audio-output guidance. Bluetooth-only cases
  run no tools, report that Bluetooth diagnostics are unavailable, and ask the
  user to verify manually. No Bluetooth state or fix is fabricated.
- Replaced raw escalation JSON with a readable, redacted handoff, action/result
  table, plain-English stopping reason, source links, next steps, and follow-up
  question. Routine results prioritize issue summary, evidence, actions,
  guidance, and verification.
- Primary navigation is **New case**, **Cases**, and **Admin / Diagnostics**;
  Evaluation and Safety remain available within Admin / Diagnostics.
- Final verification: `.venv/bin/python -m pytest -q` → **86 passed**;
  `.venv/bin/python -m src.evaluation` → retrieval **Hit@1 1.0000**, agent
  **8/8**, security **16/16**, robustness **12/12**. Bluetooth and Wi-Fi repros
  were run independently; the mixed Bluetooth + Wi-Fi case also runs only the
  network diagnostics relevant to its explicit Wi-Fi symptoms.

## Suggested commit message

```text
feat: implement deterministic autonomous IT help desk agent

- add leakage-reduced TF-IDF word+char LinearSVC classifier with training-validated EOL rule (hybrid acc 0.8356, macro-F1 0.8017; base model 0.7473/0.6449)
- add cited retrieval over 11 official Apple/Microsoft summaries with OS and topic filtering
- add observation-driven bounded agent loop with decision trace and escalation guards
- enforce closed tool allowlist, typed params, opt-in and approval at the tool boundary
- redact identifiers in tool output, case text and SQLite event logs; parameterize all SQL
- add Streamlit UI, five evaluation suites and 86 tests
- document architecture, evaluation, safety, demo, data card and limitations
```
