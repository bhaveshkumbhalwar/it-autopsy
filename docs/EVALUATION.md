# Evaluation

All numbers below were produced by the commands shown, on 2026-10-23, with
Python 3.14.6 and scikit-learn 1.9.0. Raw output is in `reports/`.

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m src.evaluation
```

The suite is split so that a good classification score cannot hide a broken
agent, and a passing security check cannot hide unanswerable retrieval.

## 1. Classification (held-out test, untouched)

Protocol:

- Source: public IT support ticket dataset (CC BY 4.0, DOI
  `10.5281/zenodo.7648117`).
- The official test split (657 rows) was held out. Model selection used only
  training data; the test split was evaluated after selection.
- The strict training split was reduced from 1,572 to **1,460** rows by removing:
  - texts that also appear in the test split (33 overlaps), and
  - the one training text with conflicting labels.
- This strict filter removes the **entire EOL class**: both unique EOL texts
  occur in both splits. The statistical model therefore has six classes and
  cannot learn EOL from these data without reintroducing test text.
- Model selection used 5-fold stratified CV **on the training data only**:
  - Logistic Regression, word features: macro-F1 `0.7170 ± 0.0066`
  - LinearSVC, word features: macro-F1 `0.7616 ± 0.0166`
  - LinearSVC, word+character features: macro-F1 `0.7872 ± 0.0208` ← selected
- Selected pipeline: word TF-IDF (1–2 grams, `min_df=1`, `max_df=0.95`,
  `sublinear_tf`, 12,000 features) unioned with `char_wb` TF-IDF (2–5 grams,
  `min_df=2`, 20,000 features), then `LinearSVC(C=0.75,
  class_weight=balanced)`. Character n-grams are a robustness choice for
  multilingual text and typos; the dataset has no language column, so no
  per-language improvement is claimed.
- A deterministic EOL keyword rule (`end of life`, `end-of-life`, `EOL`) runs
  **before** the model. It is a post-hoc design response to the observed EOL
  collapse, not a cross-validated discovery. Its training-only validation is
  45 true positives, 1 false positive, 0 false negatives: precision `0.9783`,
  recall `1.0000`. The single false positive is a training ticket labelled
  `Support general` whose text describes the same lifecycle procedure.

Hybrid test results (rule first, model otherwise):

| Metric | Value |
| --- | ---: |
| Accuracy | **0.8356** |
| Macro-F1 | **0.8017** |
| Weighted-F1 | **0.8311** |
| Majority baseline | 0.3364 |
| Test rows | 657 |

Per class:

| Class | Precision | Recall | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| Active Directory | 0.6286 | 0.4490 | 0.5238 | 49 |
| Computer-Services | 0.9189 | 0.8293 | 0.8718 | 41 |
| EOL (rule) | 1.0000 | 1.0000 | 1.0000 | 58 |
| Fileservice | 0.9493 | 0.9493 | 0.9493 | 138 |
| O365 | 0.7889 | 0.7717 | 0.7802 | 92 |
| Software | 0.6863 | 0.6034 | 0.6422 | 58 |
| Support general | 0.7984 | 0.8959 | 0.8443 | 221 |

Base model only, with the EOL rule disabled:

| Metric | Value |
| --- | ---: |
| Accuracy | **0.7473** |
| Macro-F1 | **0.6449** |
| Weighted-F1 | **0.7115** |
| EOL F1 | 0.0000 |

The base model never predicts EOL; all 58 EOL test rows go to `Support general`
(57) and `Software` (1). The hybrid rule catches all 58, but because the rule
was chosen after that failure was observed, **its 1.0000 test F1 is optimistic
and not independent evidence of generalisation**. Report the base-model
macro-F1 (`0.6449`) as the honest statistical result and the hybrid
macro-F1 (`0.8017`) as the deployed-system result with that caveat. Scores from
`predict` are **not calibrated probabilities** (they are softmax-transformed
decision scores, shown for relative comparison only); the EOL rule returns a
rule indicator, not a probability.

## 2. Retrieval

Corpus: 11 concise, derived summaries from **Apple Support** and **Microsoft
Learn** (`data/knowledge/kb.json`). Each entry stores publisher, URL, access
date, OS scope and a short factual summary — not copied article text. Explicit
Bluetooth, Wi-Fi/network, and DNS topics are scoped independently so shared
words such as "wireless" or "connect" do not cross-cite unrelated guidance.

Protocol: 15 hand-written queries in `data/eval/retrieval_eval.json`; 11
answerable (expected document IDs) and 4 deliberately unanswerable. Metrics are
computed with the production threshold (`cosine >= 0.18`), OS filter, and
explicit issue-topic filter.

| Metric | Value |
| --- | ---: |
| Answerable queries | 11 |
| Unanswerable queries | 4 |
| Hit@1 | **1.0000** |
| Hit@3 | **1.0000** |
| MRR | **1.0000** |
| False-positive rate on unanswerable queries | **0.0000** |

Interpretation, without inflation: the KB has only 11 chunks and the queries
were written after the KB existed, so **Hit@1 = 1.0 is a sanity check, not a
benchmark**. It shows the retriever finds the intended chunk for clear
phrasings. It does not show generalisation to unseen help-desk language. The
unanswerable set is small (4), so the 0.0 false-positive rate is weak evidence.
Known gap: a macOS DNS question returns nothing, because the KB has no macOS
DNS page; the agent escalates instead of borrowing the Windows page.

## 3. Agent behaviour

Eight scenarios in `data/eval/agent_scenarios.json`. Checks assert loop
properties — tools called, escalation flag, stop reason, priority rule, and
relevant/forbidden evidence IDs — not wording. Result: **8/8 passed**.

| Scenario | Observed behaviour |
| --- | --- |
| `mac-bluetooth-headphones` | Apple Bluetooth pairing/audio guidance; no Wi-Fi tools or device observations; explicit limitation |
| `mac-wifi` | Wi-Fi → DNS → ping → VPN chain, no escalation |
| `windows-dns` | Starts with DNS observation, then ping |
| `security-ransomware` | No tools, `security_concern`, escalated, priority high |
| `no-evidence-printer` | No tools, `insufficient_evidence`, escalated |
| `broad-impact` | Priority high, escalated (no source) |
| `outlook-gap` | Cited Outlook guidance; no automated O365 diagnostic; not escalated |
| `mac-dns-gap` | Escalated, no cross-OS citation |

Observation sensitivity is additionally covered by unit tests with injected
adapters: disconnected Wi-Fi stops before DNS; failed ping stops before VPN;
active VPN stops and escalates; `max_steps` is a hard stop.

No resolution-accuracy claim is made. The dataset has **no resolution outcome**
and the tools are simulated, so it is impossible to measure "did the agent fix
it" here.

## 4. Security

16 code-level checks, **16/16 passed**:

- unregistered tool blocked; command-style parameters blocked; unknown
  parameters blocked; shell metacharacters in hostnames blocked;
- macOS tool blocked under Windows policy;
- real diagnostic blocked without opt-in and without approval;
- state-changing simulated tool blocked without approval;
- direct prompt injection and injection inside retrieved content gain no tools;
- output redaction removes emails, MAC addresses, tokens and home usernames;
- tool result data is redacted at the tool boundary;
- case text is redacted at the persistence boundary;
- SQL injection is stored as data and the table survives (parameterized SQL);
- no `shell=True` / `os.system` / `os.popen` / `eval` / `exec` in the tool layer;
- policy error messages contain no secret material.

## 5. Robustness

12 cases, **12/12 passed**: empty/whitespace/4001-character inputs rejected;
4000-character input accepted; emoji, unicode, null byte, SQL-injection text and
HTML/script text handled; unknown OS handled; repeated runs produce the same
trace; `max_steps=1` stops at one observation and escalates.

## 6. Tests

```text
$ .venv/bin/python -m pytest -q
85 passed
```

Test files: environment, retrieval, security, classifier (rule, score
alignment, fresh-checkout bootstrap), agent, verification, database, Streamlit
UI (`streamlit.testing.v1.AppTest`).

## What is *not* measured

- Real remediation success (tools are simulated).
- Real diagnosis accuracy on the ticket dataset (no resolution labels).
- Calibration of classifier scores.
- Retrieval quality on unseen production language (small hand-written set).
- Multi-user, multi-tenant, or authenticated deployment behaviour.
- Any language-specific performance (the dataset has no language column).
