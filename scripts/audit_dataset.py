#!/usr/bin/env python
"""Audit the downloaded dataset and write a data card from observed values.

Run after scripts/download_dataset.py:
    .venv/bin/python scripts/audit_dataset.py

Outputs:
    docs/DATA_CARD.md   committed, human-readable
    reports/audit.json  generated, git-ignored

Nothing in this script hard-codes expected metrics. Every number is computed
from the files; the only fixed values are provenance (URL/DOI/licence).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402

PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z0-9_ ]*\]")
TOKEN_RE = re.compile(r"[a-zà-ÿ]+")

# A deliberately simple, unvalidated language heuristic. It is reported as an
# estimate only. No per-language performance claim may be derived from it.
STOPWORDS = {
    "en": set(
        "the and is are not to of for my with you your have has can cannot on in "
        "it this that was were but from at i we be please".split()
    ),
    "de": set(
        "der die das und nicht ist mit für mein meine ich sie wir haben kann von "
        "zu den dem ein eine auf bitte".split()
    ),
    "pt": set(
        "o a os as de da do das dos não nao meu minha eu você voce preciso para "
        "com uma um em que é está esta foi".split()
    ),
    "es": set(
        "el la los las de del no mi mis yo usted para con una un en que es está "
        "esta fue por favor".split()
    ),
}


def guess_language(text: str) -> str:
    tokens = TOKEN_RE.findall(text.lower())
    if not tokens:
        return "other/unknown"
    scores = {lang: sum(1 for t in tokens if t in words) for lang, words in STOPWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other/unknown"


def main() -> int:
    config.ensure_directories()
    tickets_path = config.PROCESSED_DIR / "tickets.csv"
    if not tickets_path.exists():
        raise SystemExit("data/processed/tickets.csv missing; run scripts/download_dataset.py")

    frame = pd.read_csv(tickets_path, keep_default_na=False)
    train = frame[frame["split"] == "train"]
    test = frame[frame["split"] == "test"]

    names = list(config.CATEGORIES)
    label_rows = []
    for name in names:
        tr = int((train["label_name"] == name).sum())
        te = int((test["label_name"] == name).sum())
        label_rows.append(
            {
                "label": name,
                "train": tr,
                "train_pct": round(100 * tr / len(train), 1),
                "test": te,
                "test_pct": round(100 * te / len(test), 1),
            }
        )

    train_unique = int(train["text"].nunique())
    test_unique = int(test["text"].nunique())
    total_unique = int(frame["text"].nunique())
    overlap = sorted(set(train["text"]) & set(test["text"]))
    conflicts = train.groupby("text")["label"].nunique()
    conflict_texts = sorted(conflicts[conflicts > 1].index)

    placeholder_counts = Counter()
    for text in frame["text"]:
        for token in PLACEHOLDER_RE.findall(text):
            placeholder_counts[token] += 1
    common_placeholders = placeholder_counts.most_common(10)

    lang_counts = Counter(guess_language(text) for text in frame["text"])
    lengths = frame["text"].str.len()

    majority = int(test["label_name"].value_counts().iloc[0])
    majority_label = test["label_name"].value_counts().index[0]

    report = {
        "rows": {
            "total": int(len(frame)),
            "train": int(len(train)),
            "test": int(len(test)),
        },
        "schema": {
            "columns": [str(c) for c in frame.columns],
            "dtypes": {str(k): str(v) for k, v in frame.dtypes.items()},
        },
        "labels": {
            "names": names,
            "per_label": label_rows,
        },
        "missing": {str(k): int(v) for k, v in frame.isna().sum().items()},
        "empty_texts": int((frame["text"].astype(str).str.strip() == "").sum()),
        "duplicates": {
            "train_rows": int(len(train)),
            "train_unique_texts": train_unique,
            "train_duplicate_rows": int(len(train) - train_unique),
            "test_rows": int(len(test)),
            "test_unique_texts": test_unique,
            "test_duplicate_rows": int(len(test) - test_unique),
            "total_unique_texts": total_unique,
        },
        "leakage": {
            "exact_train_test_overlap_texts": len(overlap),
            "test_unique_texts": test_unique,
            "overlap_share_of_unique_test": round(len(overlap) / test_unique, 4),
            "train_rows_containing_overlap": int(train["text"].isin(overlap).sum()),
        },
        "label_conflicts": {
            "train_texts_with_multiple_labels": len(conflict_texts),
        },
        "length": {
            "min": int(lengths.min()),
            "median": int(lengths.median()),
            "mean": round(float(lengths.mean()), 1),
            "max": int(lengths.max()),
        },
        "placeholders": {
            "distinct": len(placeholder_counts),
            "top10": [{"token": t, "rows": c} for t, c in common_placeholders],
            "rows_without_placeholder": int(
                (~frame["text"].str.contains(PLACEHOLDER_RE, regex=True)).sum()
            ),
        },
        "language_heuristic": {
            "method": "stopword overlap; unvalidated estimate, not a measurement",
            "counts": dict(lang_counts.most_common()),
        },
        "baselines": {
            "test_majority_class": majority_label,
            "test_majority_count": majority,
            "test_majority_accuracy": round(majority / len(test), 4),
        },
    }

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPORTS_DIR / "audit.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    lines: list[str] = []
    add = lines.append
    add("# Data card — Classification of IT Support Tickets")
    add("")
    add("Generated by `scripts/audit_dataset.py` from the files actually downloaded.")
    add("Do not edit by hand; re-run the script instead.")
    add("")
    add("## Provenance")
    add("")
    add("- Title: *Classification of IT Support Tickets*")
    add("- Author: Leonardo Santiago Benitez Pereira (Federal Institute of Santa Catarina)")
    add("- DOI: [10.5281/zenodo.7648117](https://doi.org/10.5281/zenodo.7648117)")
    add("- Mirror used: [tasksource/it-support-tickets](https://huggingface.co/datasets/tasksource/it-support-tickets)")
    add("- Licence: CC BY 4.0")
    add("- Raw files: `data/raw/` (unmodified), processed copies: `data/processed/`")
    add("- Checksums: `data/raw/SOURCES.json`")
    add("")
    add("## Size")
    add("")
    add("| Split | Rows |")
    add("| --- | ---: |")
    add(f"| train | {len(train)} |")
    add(f"| test | {len(test)} |")
    add(f"| **total** | **{len(frame)}** |")
    add("")
    add("## Schema")
    add("")
    add("| Column | Type | Notes |")
    add("| --- | --- | --- |")
    add("| `row_id` | int | added by us; stable within this release |")
    add("| `split` | string | `train` or `test`, from the upstream files |")
    add("| `text` | string | ticket text, PII-masked by the authors |")
    add("| `label` | int | 0–6, upstream ClassLabel |")
    add("| `label_name` | string | decoded label name |")
    add("")
    add("The upstream schema has only `text` and `label`. It has **no language**,")
    add("**no OS**, and **no resolution outcome** field.")
    add("")
    add("## Label distribution (observed)")
    add("")
    add("| Label | Train | Train % | Test | Test % |")
    add("| --- | ---: | ---: | ---: | ---: |")
    for row in label_rows:
        add(f"| {row['label']} | {row['train']} | {row['train_pct']}% | {row['test']} | {row['test_pct']}% |")
    add("")
    add(f"Majority-class baseline on test: **{majority_label}**, {majority}/{len(test)} = "
        f"**{100 * majority / len(test):.2f}%**.")
    add("")
    add("## Data quality (observed)")
    add("")
    add(f"- Train: {len(train)} rows, {train_unique} unique texts "
        f"({len(train) - train_unique} duplicate rows).")
    add(f"- Test: {len(test)} rows, {test_unique} unique texts "
        f"({len(test) - test_unique} duplicate rows).")
    add(f"- Total unique texts: {total_unique} of {len(frame)} rows.")
    add(f"- **Exact texts present in both train and test: {len(overlap)}** "
        f"({100 * len(overlap) / test_unique:.1f}% of unique test texts). "
        f"These occur in {int(train['text'].isin(overlap).sum())} training rows.")
    add(f"- Train texts with more than one label: {len(conflict_texts)}.")
    add(f"- Missing values: {sum(report['missing'].values())}. "
        f"Empty texts: {report['empty_texts']}.")
    add(f"- Text length: min {int(lengths.min())}, median {int(lengths.median())}, "
        f"mean {float(lengths.mean()):.1f}, max {int(lengths.max())} characters.")
    add("")
    add("### PII placeholders")
    add("")
    add("| Placeholder | Rows |")
    add("| --- | ---: |")
    for token, count in common_placeholders:
        add(f"| `{token}` | {count} |")
    add("")
    add(f"Rows with no placeholder tag: {report['placeholders']['rows_without_placeholder']}.")
    add("")
    add("### Language (heuristic estimate — not a measurement)")
    add("")
    add("The dataset has no language column, so the following was estimated with a")
    add("small stopword-overlap script. It is **not** a validated language classifier.")
    add("")
    add("| Estimate | Rows | Share |")
    add("| --- | ---: | ---: |")
    for lang, count in lang_counts.most_common():
        add(f"| {lang} | {count} | {100 * count / len(frame):.1f}% |")
    add("")
    add("## Split handling")
    add("")
    add("- The upstream train/test split is preserved exactly and is never reshuffled.")
    add("- `data/processed/test_untouched.csv` is the test set. It is never modified.")
    add("- `data/processed/train_leakage_reduced.csv` removes, from **training only**,")
    add("  texts that also occur in test and texts with conflicting labels.")
    add("- Both training variants are evaluated and reported; the untouched official")
    add("  split is reported alongside the leakage-reduced one.")
    eol_train = set(train.loc[train["label_name"] == "EOL", "text"])
    eol_test = set(test.loc[test["label_name"] == "EOL", "text"])
    add(
        f"- The EOL class has {len(eol_train)} unique training texts and "
        f"{len(eol_test)} unique test texts; all {len(eol_train & eol_test)} "
        "training texts also occur in test. Strict overlap removal therefore "
        "leaves **zero EOL training rows**. The classifier uses a deterministic "
        "phrase rule for explicit EOL wording and reports the base model "
        "separately."
    )
    add("")
    add("## Known limitations")
    add("")
    add("- Duplicate tickets mean row-level accuracy over-weights repeated wording.")
    add("- There is train/test leakage in the upstream split (see numbers above).")
    add("- The EOL class is template-identical across splits and disappears under")
    add("  strict leakage removal; no zero-shot EOL generalisation is claimed.")
    add("- No language labels exist; per-language performance cannot be claimed.")
    add("- No OS field and no resolution outcome: the data supports triage only.")
    add("- Categories are the source authors' seven labels, not our invention.")
    add("")
    (config.DOCS_DIR / "DATA_CARD.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {config.DOCS_DIR / 'DATA_CARD.md'}")
    print(f"wrote {config.REPORTS_DIR / 'audit.json'}")
    print(f"total={len(frame)} train={len(train)} test={len(test)} "
          f"overlap={len(overlap)} conflicts={len(conflict_texts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
