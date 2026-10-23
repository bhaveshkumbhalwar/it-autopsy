# Research notes — Phase 0

Date: 2026-10-23
Purpose: record what was verified, what is uncertain, and which design decisions
follow. Nothing here is a performance claim.

> Historical record from Phase 0. The phases that were "deferred" below were
> completed later; see `PROJECT_STATUS.md` for actual commands and results and
> `docs/EVALUATION.md` for measured metrics.

## 1. Environment

| Item | Observed |
| --- | --- |
| OS | macOS 27.0.1 (build 26A434), arm64 |
| Working dir | `/Users/bhavesh/fai_mini_project` (empty at start; Git initialised, no commits) |
| Interpreters | `/usr/bin/python3` = 3.9.6; `~/anaconda3/bin/python` = 3.14.6 |
| Conda envs | only `base` (`/Users/bhavesh/anaconda3`) |
| Packages in conda base | scikit-learn 1.9.0, pandas 3.0.3, NumPy 2.4.6, Streamlit 1.58.0, pytest 9.0.3, requests 2.34.2, Plotly 6.7.0; `datasets` **not installed** |
| Git | 2.54.0 |

**Decision deferred to Phase 1:** the spec warns not to assume Python 3.14 is
compatible with every ML dependency. Two candidates:

1. a venv from `~/anaconda3/bin/python` (3.14.6) with pinned wheels;
2. a new conda env with Python 3.12, which has broader wheel support.

Phase 1 will test option 1 first (fast, no download of a new interpreter) and
fall back to option 2 if any dependency fails to install. Whichever is used,
the tested versions will be recorded. We will not silently rely on global
site-packages: all imports will be checked inside the chosen environment.

## 2. Dataset findings (verified by reading the files)

Full schema, labels, counts, duplicates and placeholder counts are in
`docs/SOURCES.md`. The points that change the design:

1. **Severe duplicate rate.** 610/1,572 train rows and 102/657 test rows are
   repeats. Accuracy on duplicated text is inflated because near-identical
   tickets appear many times. We will report the number of *unique* texts and
   also evaluate on a deduplicated test view, clearly labelled.
2. **33 exact texts appear in both train and test.** If we train on the
   released train split unchanged, those 33 test items are memorisable. We will
   remove those texts from the **training** set (never from test), and report
   both "official split, as released" and "leakage-reduced" results. The test
   split is otherwise untouched until final evaluation.
3. **One train text has two different labels.** It will be dropped from
   training (conflicting supervision cannot be learned reliably) and the
   decision logged.
4. **Class imbalance plus distribution shift.** Train is dominated by
   Fileservice (34.7%) and Support general (34.4%); test has far fewer
   Fileservice (21.0%) and more EOL (8.8% vs 2.9%). Macro-F1, per-class
   precision/recall and the confusion matrix matter more than accuracy.
   Majority baseline is 33.64% on test.
5. **No language field.** The corpus is multilingual (card says EN/DE/PT/ES plus
   others). Any language breakdown is a heuristic estimate; we will not claim
   per-language performance without a labelled language subset.
6. **Placeholders are part of the text.** `[TICKET ID]`, `[NAME]`, `[LOCATION]`
   etc. are frequent. They are useful structure, not secrets. We keep them as
   text; we do not attempt to recover masked values.
7. **No resolution outcome.** The dataset supports triage classification only.
   It cannot show that the agent fixes anything, and we will not imply it does.
8. **Scope mismatch.** Ticket categories (Active Directory, Fileservice, O365,
   EOL …) do not line up with the two permitted troubleshooting guides
   (macOS Wi-Fi, Windows DNS). Retrieval coverage will be deliberately narrow;
   "insufficient evidence" will be a common, correct outcome. We will not pad
   the knowledge base with invented articles to make coverage look better.

## 3. Source findings

See `docs/SOURCES.md` for URLs, dates, and licence status. Key constraints:

- Apple and Microsoft pages are **read-only reference material**. Their exact
  terms were not verified, so we store metadata plus our own concise factual
  summaries, not page copies.
- The Microsoft DNS instructions contain **state-changing commands**
  (`ipconfig /renew`, `ipconfig /flushdns`). These are guidance text only; they
  will not be exposed as callable tools.
- The Apple page names two real macOS diagnostics (Wi-Fi Recommendations,
  Wireless Diagnostics) that a user can run. We may mirror a subset with
  read-only commands in Phase 6, clearly labelled as real, with OS checks.
- OWASP is explicit that prompt filtering is not a security boundary. Our
  design therefore enforces authorization in code at the tool boundary, not in
  prompt text.

## 4. Proposed architecture (to be implemented phase by phase)

```text
app.py                  Streamlit UI
src/schemas.py          typed dataclasses: Ticket, Triage, Evidence, Plan, Step,
                        ToolCall, ToolResult, Verification, Escalation, Case
src/classifier.py       TF-IDF + linear classifier, persisted with metadata
src/retrieval.py        TF-IDF/cosine over a small curated source corpus,
                        dedup, min-relevance threshold, citations
src/planner.py          bounded structured plan using registered tool names only
src/policy.py           code-enforced allowlist, OS + risk checks, approval gate
src/tools.py            typed tool registry: simulated adapters + opt-in
                        read-only diagnostics (macOS), validation, timeouts
src/agent.py            bounded observe/decide/act controller
src/verification.py     verified_resolved / not_resolved / needs_user_confirmation
                        / escalated / unable_to_verify
src/escalation.py       evidence-rich handoff summary
src/database.py         SQLite cases + events, parameterised SQL, retention
src/evaluation.py       separate classification / retrieval / agent / security
                        / robustness reports
scripts/                download_dataset.py, audit_dataset.py,
                        evaluate_classifier.py
docs/                   SOURCES, RESEARCH_NOTES, ARCHITECTURE, EVALUATION,
                        SAFETY, DEMO_SCRIPT
tests/                  unit + adversarial tests, fixtures clearly labelled
```

Deliberate choices:

- **No agent framework.** Typed dataclasses and a small explicit loop. Easier to
  test and to explain, and avoids hiding policy inside a framework.
- **Deterministic baseline first.** No hosted LLM is required. If an API key is
  absent the app still triages, retrieves, plans, executes simulated/read-only
  tools, verifies and escalates. The UI will state "deterministic mode".
- **Priority is rule-based, never a trained model.** We have no labelled
  urgency data, so claiming a priority model would be dishonest. Rules use
  explicit signals (e.g. "all users", "cannot work", "security", "data loss")
  and are shown to the user.
- **Two evidence layers.** Retrieved official guidance, plus conservative
  safety rules that are explicitly marked as project rules, not as quotes.
- **Simulated by default.** Every simulated observation is tagged `SIMULATED`
  in the data model, the UI and the event log. Real diagnostics are opt-in.
- **Verification is separate from execution.** A tool succeeding means the
  command ran, not that the user's problem is fixed.
- **SQLite for cases and events**, with redaction before insert.

## 5. Risks and open questions

| Risk | Mitigation / status |
| --- | --- |
| Duplicate texts inflate metrics | Report unique-text and deduplicated results; document row counts |
| Train/test leakage (33 texts) | Remove those texts from training only; report both variants |
| Class imbalance / shift | Macro-F1, per-class table, confusion matrix, majority baseline |
| No language labels | Heuristic only; no per-language claims |
| No resolution labels | Never claim resolution accuracy from this dataset |
| Narrow KB vs broad categories | Expect and surface "insufficient evidence"; escalate |
| OS mismatch | Tools declare `os` compatibility; refuse to run Windows tools on macOS |
| Prompt injection in ticket text | Data/instruction separation + code-level tool authorization + adversarial tests |
| Python 3.14 wheel availability | Verify in Phase 1; fall back to Python 3.12 conda env |
| Source terms unclear | Store metadata + short derived summaries only |
| Small data for tuning | Nested/held-out discipline; no repeated tuning against test |

Open questions to settle with the student if needed:

- Whether to keep the seven original category names (proposed: yes, with the
  project's own user-facing labels mapped 1:1 and documented).
- Whether to add a hosted LLM later. Proposed: optional only, never required.

## 6. What this project will *not* claim

- It will not claim to fix real incidents.
- It will not claim a trained urgency model.
- It will not claim per-language parity.
- It will not claim the classifier proves resolution capability.
- It will not claim the retrieval corpus is complete.
