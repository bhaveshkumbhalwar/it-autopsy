# Autonomous IT Help Desk Agent

A local, deterministic **tool-using agent** for IT support tickets. It does not
just classify a ticket or show a dashboard: it retrieves cited guidance, forms a
bounded plan, passes every tool call through a code-enforced policy, executes
safe observations, uses the **observed results** to choose the next step, and
then asks for confirmation or escalates.

No API key is required. The same pipeline powers the tests, the evaluation
harness and the Streamlit UI.


```text
intake → triage → retrieve → plan → policy check → execute → observe
      → next step → verify or escalate
```

## Quick start

```bash
cd /Users/bhavesh/fai_mini_project

# 1. Environment (tested with Python 3.14.6; 3.10+ required)
~/anaconda3/bin/python -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Dataset and model (needed once on a fresh checkout; all git-ignored)
.venv/bin/python scripts/download_dataset.py
.venv/bin/python scripts/audit_dataset.py
.venv/bin/python -m scripts.evaluate_classifier

# 3. Tests
.venv/bin/python -m pytest -q

# 4. Regenerate all metrics
.venv/bin/python -m src.evaluation

# 5. UI
.venv/bin/python -m streamlit run app.py
```

If `models/classifier.joblib` is absent but the processed training split exists,
the first `Classifier` use trains it deterministically. The explicit command
above is still the documented way to rebuild it and write metadata/reports.
The dataset download needs network access; after that, training, tests and
evaluation are local and require no API key.

## What is real and what is simulated

| Part | Status |
| --- | --- |
| Ticket classifier | Real model trained on a public CC BY 4.0 dataset: TF-IDF word+char features + `LinearSVC`, plus a deterministic EOL keyword rule. Hybrid held-out accuracy **0.8356**, macro-F1 **0.8017**; base model alone **0.7473 / 0.6449** |
| Knowledge base | 10 concise derived summaries of **official** Apple Support and Microsoft Learn pages, with URLs and access dates |
| Retrieval | Real TF-IDF cosine search with a hard relevance threshold and OS filter; no answer → escalate |
| Diagnostics | **Default: simulated**, always labelled `SIMULATED`. Three real **read-only** macOS commands, off by default, requiring opt-in **and** per-call approval |
| Priority | Transparent keyword rules, not a trained model (the dataset has no urgency labels) |
| Agent loop | Real deterministic controller with a decision trace, observation-driven branching, loop prevention and a step budget |
| Persistence | SQLite `cases` + `events`, parameterized SQL, redacted at the persistence boundary |
| LLM | None in the default path |

## Measured results (actual runs)

| Suite | Result |
| --- | --- |
| Classification, hybrid (657 held-out test rows) | accuracy **0.8356**, macro-F1 **0.8017**, weighted-F1 0.8311; majority baseline 0.3364 |
| Classification, base model only (EOL rule disabled) | accuracy **0.7473**, macro-F1 **0.6449**, weighted-F1 0.7115 |
| Retrieval (10 answerable, 4 unanswerable queries) | Hit@1 1.0000, MRR 1.0000, false-positive rate 0.0000 |
| Agent scenarios | **7/7** |
| Security checks | **16/16** |
| Robustness cases | **12/12** |
| Tests | **78 passed** |

These are small, hand-written regression suites, not industry benchmarks. The
strict leakage-reduced training view removes both unique EOL texts (they also
occur in test), so the statistical model has no EOL examples. A deterministic
keyword rule handles phrases such as "End Of Life"; it is validated on training
data (precision 0.9783, recall 1.0000) and applied before the model. Because the
rule was introduced after the original EOL failure was observed, its 1.0000 test
F1 is optimistic, not independent evidence of generalisation. The base-model
macro-F1 is the honest statistical headline. Full tables live in
[`docs/EVALUATION.md`](docs/EVALUATION.md) and `reports/`.

## Safety posture

- **No arbitrary shell.** A closed allowlist of typed tools; parameters are
  validated (including hostnames) before execution.
- **No destructive actions.** No file deletion, DNS/firewall changes, software
  installation, service restarts, password resets, or admin commands.
- **Code is the authority.** Ticket text and retrieved documents are untrusted
  data. Prompt injection cannot add a tool, change a parameter type, grant
  opt-in, or grant approval.
- **Approval gate.** State-changing tools and real diagnostics require an
  explicit boolean approval supplied by the caller — never parsed from text.
- **Redaction.** Emails, MAC addresses, tokens and home usernames are redacted
  before display and again before storage.
- **Honest outcomes.** Tool success is not reported as resolution. The agent
  uses `needs_user_confirmation`, `escalated`, `unable_to_verify`, and only sets
  `verified_resolved` / `not_resolved` after a human reply.

Details and residual risks: [`docs/SAFETY.md`](docs/SAFETY.md).

## Repository layout

```text
app.py                     Streamlit UI (run, cases, evaluation, safety)
src/agent.py               observation-driven bounded controller
src/classifier.py          TF-IDF word+char LinearSVC, EOL rule, train/predict/evaluate
src/retrieval.py           KB search, threshold, OS filter, citations
src/policy.py              allowlist, OS support, typed params, approval
src/tools.py               simulated adapters, three real macOS read-only commands, redaction
src/verification.py        outcome rules + escalation handoff
src/database.py            SQLite persistence
src/evaluation.py          five independent evaluation suites + reports
data/knowledge/kb.json     tracked official-source summaries
data/eval/*.json           tracked hand-written eval sets
scripts/                   download, audit, train/evaluate
tests/                     78 tests (unit, security, DB, classifier, UI AppTest)
docs/                      architecture, evaluation, safety, data card, sources, demo
reports/                   generated metrics (git-ignored)
models/, data/raw/, data/processed/   generated artefacts (git-ignored)
```

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — loop, modules, decision points
- [`docs/EVALUATION.md`](docs/EVALUATION.md) — protocols, all metrics, what is not measured
- [`docs/SAFETY.md`](docs/SAFETY.md) — threat model, controls, residual risks
- [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) — 5-minute walkthrough with real commands
- [`docs/DATA_CARD.md`](docs/DATA_CARD.md) — dataset card generated from the files
- [`docs/SOURCES.md`](docs/SOURCES.md) — verified sources, licences, statistics
- [`docs/RESEARCH_NOTES.md`](docs/RESEARCH_NOTES.md) — early findings and decisions
- [`PROJECT_STATUS.md`](PROJECT_STATUS.md) — phase-by-phase commands and results
- [`data/README.md`](data/README.md) — data-handling policy

## Known limitations

- The dataset has **no resolution outcome**, so the project makes no claim about
  fixing real tickets.
- It contains duplicates and 33 exact train/test overlaps; overlap rows were
  removed from training only, and the test split stayed untouched. This removes
  the entire EOL class, which is why a separate deterministic rule exists.
- The base classifier has no EOL training examples and scores are not
  calibrated. The EOL rule matches the class-name phrase; it is not a general
  end-of-life lifecycle detector.
- Retrieval covers only Apple Wi-Fi and Windows DNS client guidance; many
  categories correctly escalate for lack of evidence.
- The agent's tools are simulated except three opt-in read-only macOS commands.
- The retrieval and agent evaluation sets are small and hand-written.
- The Streamlit app has no authentication; it is a local demo.
- No per-language performance claim is made (the dataset has no language field).

## Data attribution

> Leonardo Santiago Benitez Pereira. *Classification of IT Support Tickets.*
> Zenodo, 2022. DOI: [10.5281/zenodo.7648117](https://doi.org/10.5281/zenodo.7648117).
> Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

Accessed via <https://huggingface.co/datasets/tasksource/it-support-tickets>.

The knowledge base stores metadata and short derived summaries of Apple Support
and Microsoft Learn pages; see `docs/SOURCES.md` for exact URLs and access
dates. It does not redistribute article text.

## Status

All phases 0–12 and the final F1/audit pass are complete. See
`PROJECT_STATUS.md` for the exact commands run and the results observed. No
commit has been made; the working tree is untracked by design so the owner can
review before committing.
