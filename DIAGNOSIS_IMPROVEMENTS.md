# Diagnosis quality — what changed and why

Engine version **1.1**. Scope: the diagnosis pipeline only (rules → causal
analysis → ML → LLM → recommendations → confidence/severity). The API surface and
the UI layout are unchanged; a few fields were **added**, nothing was removed.

The six problems the brief named, and how each is handled now.

---

## 1. Duplicate evidence

**Before:** the same failure appeared once per step that mentioned it — a failed
tool span, the framework's error record and a summary row each produced a
separate evidence row, and rule + ML + LLM could add a fourth.

**Now:** every evidence row goes through `EvidenceBook` (`app/engine/evidence.py`),
which keys a row on *(where, error type, normalised message)*:

- a failed span and the error record the ingest layer creates for it are merged
  into **one** row (they share a span id, or the error record repeats the
  preceding step's error);
- a global error list restating a step's error merges into that step's row;
- the same error recurring on a *different* step is **kept**, because that is a
  separate occurrence in the execution (see the infinite-loop demo);
- when two rows describe one event, the richer quote, the strongest causal role
  and the merged category list survive.

Messages are normalised first (lowercased, ids/uuids/timestamps/numbers
collapsed) and truncated at the first clause, so `psycopg OperationalError:
connection refused (host db-1)` and the same error logged again elsewhere are one
event.

## 2. False-positive safety violations

**Before:** the safety rule matched the words *policy*, *safety*, *blocked* and
*refused* anywhere in a trace, so `connection refused` and `blocked by upstream
firewall` produced `safety_policy_violation`.

**Now:** `rule_safety` fires only on an explicit policy signal — content policy,
guardrails, moderation, `policy violation`, `blocked by (the) policy/safety/
guardrail/content`, `usage policy`, `requires human approval`, `approval denied`,
jailbreak, harmful content. Ordinary infrastructure wording no longer matches.

Also:

- a **policy block is not a tool failure**: a tool call that was blocked by policy
  no longer triggers `tool_execution_failure` (and the "called but no response"
  orphan rule skips it), so the two families cannot both be reported for one event;
- a **credential rejection** is its own family (`authentication_failure`), not a
  generic API failure — retrying a 401 is wrong advice;
- retry-style recommendations are **withheld** when a policy decision is involved.

## 3. Root cause accuracy

**Before:** the "root cause" was whichever rule fired first, and every failed span
counted as its own finding.

**Now:** `analyse_causality()` runs **before** the rules are ranked:

1. it collects every failure event in the trace;
2. links children to parents through `parent_id` / `span_id`;
3. groups repeats of the same signature;
4. the **earliest** failure not explained by an earlier one is the root cause; the
   rest are marked *downstream* and record which step caused them.

That ordering then decides the primary category, `failed_step_index`,
`root_cause_step`, and the role of every category and evidence row
(`root_cause` / `contributing` / `downstream` / `context`). The full chain is
returned as `causal_chain` and shown in the UI, so a downstream `ValueError: empty
response` is no longer reported as an independent root cause of an upstream 503.

## 4. Context-aware recommendations

**Before:** one flat catalogue; most of it came back for every failure.

**Now:** each catalogue entry **declares the categories it serves**
(`recommendations.CATALOGUE`) and `for_categories()` returns only entries matching
a detected category, specific advice before generic:

| Detected family | Advice family |
| --- | --- |
| database | connectivity, pooling, transactions, query budget |
| authentication | credential rotation, scopes, key validation |
| API / provider | retry with backoff, provider fallback, rate-limit budget |
| memory / context | context management, memory budgeting, validation |
| tool execution | tool error handling, argument validation, tool registry |
| policy / safety | guardrails, approval flow, policy tuning |
| timeout / loop / reasoning | timeout budget, execution limits, output verification |

`prevention` is derived from the same categories, and a test asserts that a
database failure can never return safety or context advice (and vice versa).

## 5. Confidence and severity

**Before:** confidence was a hand-tuned guess per rule; severity and confidence
were mixed together.

**Now:** `confidence_from_contributions()` starts from a base for *having located
a failure at all*, then adds points only for things that exist, and returns the
**arithmetic**: `confidence_factors = [{factor, detail, weight}]`.

```
located_failure            +0.30   the failure is localised to a step
root_cause_identified      +0.30   causal analysis isolated one root cause
explicit_error_text        +0.20   a row quotes a specific error message
reported_failure           +0.10   (instead of the above) trace reports it, no family named
causal_chain_consistent    +0.05   downstream failures are explained by the root cause
ml_agreement               +0.08   the classifier independently agrees
llm_corroboration          +0.05   a configured LLM layer corroborated
```

The score is the sum of the listed factors (asserted in tests), clamped to
`0.10–0.98`, and the UI prints the sum next to the number. The copy states
plainly that this measures *how well the evidence supports the diagnosis* and is
not a calibrated probability.

**Severity is separate**: `severity.assess()` still scores impact (failure class,
whether recovery was attempted, how many steps failed, whether the run failed) and
returns its own factor list. A certain diagnosis of a recovered rate-limit is LOW;
a well-evidenced database outage is CRITICAL. Both can have high confidence.

## 6. Testing

`backend/tests/test_diagnosis_quality.py` — 26 test cases (23 tests, 3 parametrised), one per claim above:

- dedupe: one event logged three ways → one row; repeats on different steps kept;
- policy precision: DB failure ≠ safety, firewall wording ≠ safety, explicit block
  = safety and *not* tool failure, model refusal = safety;
- root cause: earliest failure wins, `parent_id` links decide, repeats counted
  once, categories carry their causal role;
- recommendations: DB / API / auth / memory / policy / tool each get their own
  family and none of the others; every catalogue entry declares its categories;
- confidence: factors add up to the score, weaker evidence scores lower, severity
  and confidence move independently;
- end-to-end: `POST /api/ingest/paste` returns deduped evidence, a causal chain and
  the factor list, with no safety category on a database failure.

Full suite: **89 passed** (`DATABASE_URL="sqlite:////tmp/aa_test.db"
.venv/bin/python -m pytest tests -q`).

---

## Files touched

| File | Change |
| --- | --- |
| `app/engine/evidence.py` | **new** — error taxonomy, dedupe book, causal analysis, confidence arithmetic |
| `app/engine/analyzer.py` | wired to the above; confidence/severity separated; `causal_chain`, `confidence_factors` returned |
| `app/engine/rules.py` | narrowed policy regexes, new database rule, auth → `authentication_failure`, policy blocks excluded from tool failures |
| `app/engine/recommendations.py` | category-owned catalogue, `for_categories()`, `prevention_for()`, policy exclusion |
| `app/engine/severity.py` | base weights for the new families |
| `app/core/models.py` | `DATABASE`, `AUTH` categories + labels |
| `app/ml/corpus.py` | auth/database training examples |
| `app/db/database.py` | `confidence_factors`, `causal_chain`, `severity_detail` columns |
| `app/api/deps.py`, `app/api/ingest.py`, `app/api/executions.py` | persist and return the new fields |
| `frontend/src/lib/api.ts` | types for factors, causal nodes, evidence roles |
| `frontend/src/pages/AnalysisDetailPage.tsx` | "Causal chain" and "Why this confidence" cards, role badges on evidence and categories |

## Migration note

The three new columns are additive. An existing SQLite database needs them added
once (SQLAlchemy `create_all` does not alter tables):

```bash
sqlite3 backend/data/agent_autopsy.db \
  "ALTER TABLE analyses ADD COLUMN confidence_factors JSON NOT NULL DEFAULT '[]';
   ALTER TABLE analyses ADD COLUMN causal_chain JSON NOT NULL DEFAULT '[]';
   ALTER TABLE analyses ADD COLUMN severity_detail JSON NOT NULL DEFAULT '{}';"
```

Older analyses keep working: the UI shows a short "not recorded for this
analysis" note instead of an empty breakdown.
