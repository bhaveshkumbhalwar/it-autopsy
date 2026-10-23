"""Ticket classifier: TF-IDF + linear classifier, fit on train only.

Fit vectorizer and classifier on the training partition only. The test split is
kept untouched until final evaluation. Duplicate texts and train/test overlap
are handled explicitly (see scripts/download_dataset.py and scripts/audit_dataset.py).

The strict leakage-reduced training view removes every text that also occurs in
test.  In this dataset that removes the *entire* EOL class: both unique EOL
texts occur in both splits.  To keep the class usable without feeding test text
back into training, a small deterministic EOL keyword rule runs *before* the
statistical model.  The rule is validated on the training split and reported
separately; it is not a calibrated probability.

This is a deterministic, local baseline. It does not require an LLM.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.svm import LinearSVC

from src import config

# The EOL label is the source dataset's end-of-life lifecycle class.  The
# phrase is the class's own name, not a pattern discovered by looking at test
# predictions.  It is validated on the training split in :func:`rule_validation`.
EOL_RULE = re.compile(r"\bend[\s_-]*of[\s_-]*life\b|\beol\b", re.IGNORECASE)
EOL_LABEL_ID = config.CATEGORIES.index("EOL")
LABEL_TO_ID = {name: index for index, name in enumerate(config.CATEGORIES)}
ID_TO_LABEL = {index: name for index, name in enumerate(config.CATEGORIES)}


def eol_rule_matches(text: str) -> bool:
    """Return True when the deterministic EOL rule fires."""
    return bool(EOL_RULE.search(text or ""))


def _word_vectorizer(min_df: int = 1) -> TfidfVectorizer:
    return TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        analyzer="word",
        ngram_range=(1, 2),
        min_df=min_df,
        max_df=0.95,
        sublinear_tf=True,
        max_features=12000,
    )


def _char_vectorizer() -> TfidfVectorizer:
    return TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        analyzer="char_wb",
        ngram_range=(2, 5),
        min_df=2,
        max_df=0.95,
        sublinear_tf=True,
        max_features=20000,
    )


def build_pipeline(estimator_name: str = "linearsvc_word_char"):
    """Build one candidate pipeline.

    The word+character union is a representation choice, selected only through
    cross-validation on the training split.  Character n-grams add robustness to
    multilingual text and typos; the dataset has no language labels, so no
    per-language claim is made.
    """
    if estimator_name == "logreg_word":
        estimator = LogisticRegression(
            C=2.0,
            max_iter=3000,
            solver="lbfgs",
            class_weight="balanced",
            random_state=config.RANDOM_SEED,
        )
        features = _word_vectorizer()
    elif estimator_name == "linearsvc_word":
        estimator = LinearSVC(
            C=1.0,
            class_weight="balanced",
            random_state=config.RANDOM_SEED,
        )
        features = _word_vectorizer()
    elif estimator_name == "linearsvc_word_char":
        estimator = LinearSVC(
            C=0.75,
            class_weight="balanced",
            random_state=config.RANDOM_SEED,
        )
        features = FeatureUnion(
            [
                ("word", _word_vectorizer()),
                ("char", _char_vectorizer()),
            ]
        )
    else:
        raise ValueError(f"unknown estimator: {estimator_name}")

    return Pipeline([("features", features), ("clf", estimator)])


def _load_split(split: str) -> pd.DataFrame:
    path = config.PROCESSED_DIR / "tickets.csv"
    frame = pd.read_csv(path, keep_default_na=False)
    return frame[frame["split"] == split].copy()


def select_estimator(X_train, y_train) -> tuple[str, dict]:
    """Compare candidates with stratified CV on training data only."""
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=config.RANDOM_SEED)
    results = {}
    for name in ("logreg_word", "linearsvc_word", "linearsvc_word_char"):
        pipe = build_pipeline(name)
        scores = cross_val_score(
            pipe, X_train, y_train, cv=skf, scoring="f1_macro", n_jobs=1
        )
        results[name] = {
            "f1_macro_mean": float(scores.mean()),
            "f1_macro_std": float(scores.std()),
            "scores": scores.tolist(),
        }
    best = max(results.items(), key=lambda item: item[1]["f1_macro_mean"])[0]
    return best, results


def rule_validation(frame: pd.DataFrame) -> dict[str, Any]:
    """Measure the EOL keyword rule on the split supplied (training only)."""
    labels = frame["label"].astype(int)
    matches = frame["text"].astype(str).map(eol_rule_matches)
    true_eol = labels == EOL_LABEL_ID
    tp = int((matches & true_eol).sum())
    fp = int((matches & ~true_eol).sum())
    fn = int((~matches & true_eol).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "split": "train only",
        "n_rows": int(len(frame)),
        "n_matched": int(matches.sum()),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "note": (
            "Rule is applied before the model. It uses the class name phrase and "
            "is validated on training data only; it is not a probability."
        ),
    }


def train(use_leakage_reduced: bool = True) -> dict[str, Any]:
    config.ensure_directories()
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)

    train_path = config.PROCESSED_DIR / (
        "train_leakage_reduced.csv" if use_leakage_reduced else "tickets.csv"
    )
    if not train_path.exists():
        raise FileNotFoundError(train_path)
    train_frame = pd.read_csv(train_path, keep_default_na=False)
    if "split" not in train_frame.columns and use_leakage_reduced:
        # leakage-reduced contains only train rows
        pass
    elif not use_leakage_reduced:
        train_frame = train_frame[train_frame["split"] == "train"]

    X_train = train_frame["text"].astype(str).tolist()
    y_train = train_frame["label"].astype(int).tolist()

    best_name, cv_results = select_estimator(X_train, y_train)
    pipe = build_pipeline(best_name)
    pipe.fit(X_train, y_train)

    model_path = config.MODELS_DIR / "classifier.joblib"
    label_path = config.MODELS_DIR / "label_mapping.json"
    meta_path = config.MODELS_DIR / "classifier_metadata.json"

    joblib.dump(pipe, model_path)
    label_mapping = {
        "labels": list(config.CATEGORIES),
        "num_classes": len(config.CATEGORIES),
    }
    label_path.write_text(json.dumps(label_mapping, indent=2) + "\n", encoding="utf-8")

    # Validate the rule on the full training split, not the leakage-reduced
    # view: the reduced view intentionally has no EOL rows, so it cannot measure
    # EOL recall.
    rule_frame = _load_split("train") if (config.PROCESSED_DIR / "tickets.csv").exists() else train_frame

    metadata = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": __import__("sys").version,
        "sklearn": __import__("sklearn").__version__,
        "estimator_selected": best_name,
        "cv": cv_results,
        "training": {
            "source_file": str(train_path),
            "use_leakage_reduced": bool(use_leakage_reduced),
            "rows": int(len(train_frame)),
            "classes_seen": [
                ID_TO_LABEL.get(int(value), f"label-{value}")
                for value in sorted(set(y_train))
            ],
            "class_counts": {
                ID_TO_LABEL.get(int(key), f"label-{key}"): int(value)
                for key, value in sorted(pd.Series(y_train).value_counts().items())
            },
        },
        "vectorizer": {
            "word_ngram_range": [1, 2],
            "word_min_df": 1,
            "char_ngram_range": [2, 5],
            "char_min_df": 2,
            "max_df": 0.95,
            "sublinear_tf": True,
            "word_max_features": 12000,
            "char_max_features": 20000,
        },
        "eol_rule": {
            "pattern": EOL_RULE.pattern,
            "label": "EOL",
            "position": "before model prediction",
            "validation": rule_validation(rule_frame),
        },
        "label_order": list(config.CATEGORIES),
        "note": "Scores are not calibrated probabilities.",
    }
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    return {
        "model_path": str(model_path),
        "rows": int(len(train_frame)),
        "best_estimator": best_name,
        "cv": cv_results[best_name],
    }


class Classifier:
    def __init__(self, model_path: Path | None = None, auto_train: bool = True):
        default_path = config.MODELS_DIR / "classifier.joblib"
        self.model_path = Path(model_path) if model_path else default_path
        self.labels = list(config.CATEGORIES)
        self.id_to_name = {index: name for index, name in enumerate(self.labels)}
        if not self.model_path.exists():
            if not auto_train:
                raise FileNotFoundError(
                    f"{self.model_path} is missing; run "
                    "`.venv/bin/python -m scripts.evaluate_classifier`."
                )
            if self.model_path != default_path:
                raise FileNotFoundError(
                    f"{self.model_path} is missing and auto-training only writes "
                    f"the default model path ({default_path})."
                )
            # A fresh checkout has no model because models/ is git-ignored.
            # Training is deterministic and small; do it once on first use.
            train(use_leakage_reduced=True)
        self.pipe = joblib.load(self.model_path)

    @property
    def classes(self) -> list[int]:
        return [int(value) for value in self.pipe.named_steps["clf"].classes_]

    def predict(self, text: str) -> tuple[int, str, dict]:
        """Return ``(label_id, label_name, score_info)``.

        The deterministic EOL rule is checked first.  Otherwise the statistical
        model predicts one of the classes seen during training.  Scores are
        aligned with the estimator's own ``classes_``; they are not calibrated.
        """
        if eol_rule_matches(text):
            return (
                EOL_LABEL_ID,
                "EOL",
                {
                    "scores": {"EOL": 1.0},
                    "confidence_label": "deterministic EOL keyword rule (not a probability)",
                },
            )

        raw_label = int(self.pipe.predict([text])[0])
        clf = self.pipe.named_steps["clf"]
        classes = self.classes
        score_map: dict[str, float] = {}
        if hasattr(clf, "predict_proba"):
            probs = self.pipe.predict_proba([text])[0]
            score_map = {
                self.id_to_name[class_id]: float(prob)
                for class_id, prob in zip(classes, probs)
            }
            confidence_label = "confidence score (not calibrated)"
        elif hasattr(clf, "decision_function"):
            dec = np.asarray(self.pipe.decision_function([text]))
            if dec.ndim == 1:
                # Binary estimator: decision value is for the second class.
                value = float(dec[0])
                score_map = {
                    self.id_to_name[class_id]: (
                        value if class_id == classes[-1] else -value
                    )
                    for class_id in classes
                }
            else:
                raw = dec[0]
                # softmax for readability only; explicitly not calibrated
                expd = np.exp(raw - raw.max())
                probs = expd / expd.sum()
                score_map = {
                    self.id_to_name[class_id]: float(prob)
                    for class_id, prob in zip(classes, probs)
                }
            confidence_label = (
                "relative score (softmax of decision function, not calibrated)"
            )
        else:
            confidence_label = "unavailable"

        return raw_label, self.id_to_name[raw_label], {
            "scores": score_map,
            "confidence_label": confidence_label,
        }


def _metric_block(y_true, y_pred) -> dict[str, Any]:
    report = classification_report(
        y_true,
        y_pred,
        labels=list(range(len(config.CATEGORIES))),
        target_names=config.CATEGORIES,
        output_dict=True,
        zero_division=0,
    )
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "f1_macro": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "f1_weighted": float(
            f1_score(y_true, y_pred, average="weighted", zero_division=0)
        ),
        "classification_report": report,
        "confusion_matrix": confusion_matrix(
            y_true, y_pred, labels=list(range(len(config.CATEGORIES)))
        ).tolist(),
    }


def evaluate() -> dict[str, Any]:
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    clf = Classifier()
    test = _load_split("test")
    X_test = test["text"].astype(str).tolist()
    y_true = test["label"].astype(int).tolist()

    # Final system prediction: deterministic EOL rule first, model otherwise.
    y_pred = [clf.predict(text)[0] for text in X_test]
    # Base pipeline prediction, reported separately for transparency.
    y_pred_model = [int(value) for value in clf.pipe.predict(X_test)]

    hybrid = _metric_block(y_true, y_pred)
    model_only = _metric_block(y_true, y_pred_model)

    train = _load_split("train")
    rule = rule_validation(train)

    result = {
        "split": "test (untouched)",
        "protocol": (
            "hybrid: deterministic EOL keyword rule first; "
            "TF-IDF word+char LinearSVC otherwise"
        ),
        "n_test": int(len(test)),
        "accuracy": hybrid["accuracy"],
        "f1_macro": hybrid["f1_macro"],
        "f1_weighted": hybrid["f1_weighted"],
        "majority_baseline": round(
            test["label_name"].value_counts().max() / len(test), 4
        ),
        "classification_report": hybrid["classification_report"],
        "confusion_matrix": hybrid["confusion_matrix"],
        "labels": list(config.CATEGORIES),
        "model_only": model_only,
        "eol_rule": rule,
    }
    (config.REPORTS_DIR / "classification_metrics.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )

    # Human-readable
    lines = ["# Classification metrics (held-out test, untouched)", ""]
    lines.append(f"Accuracy: {hybrid['accuracy']:.4f}")
    lines.append(f"Macro-F1: {hybrid['f1_macro']:.4f}")
    lines.append(f"Weighted-F1: {hybrid['f1_weighted']:.4f}")
    lines.append(
        f"Majority baseline (Support general): {result['majority_baseline']:.4f}"
    )
    lines.append("")
    lines.append(
        "Protocol: deterministic EOL keyword rule first; the statistical model "
        "handles the other classes. Scores are not calibrated."
    )
    lines.append("")
    lines.append("## Per-class")
    lines.append("")
    lines.append("| Class | Precision | Recall | F1 | Support |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for cls in config.CATEGORIES:
        r = hybrid["classification_report"][cls]
        lines.append(
            f"| {cls} | {r['precision']:.4f} | {r['recall']:.4f} | "
            f"{r['f1-score']:.4f} | {int(r['support'])} |"
        )
    lines.append("")
    lines.append("## Confusion matrix (rows=true, cols=pred)")
    lines.append("")
    lines.append("| | " + " | ".join(config.CATEGORIES) + " |")
    lines.append("|---" * (len(config.CATEGORIES) + 1) + "|")
    for i, cls in enumerate(config.CATEGORIES):
        lines.append(
            f"| {cls} | "
            + " | ".join(str(x) for x in hybrid["confusion_matrix"][i])
            + " |"
        )
    lines.append("")
    lines.append("## EOL rule validation (training split only)")
    lines.append("")
    lines.append(
        f"Matched {rule['n_matched']} rows: {rule['true_positive']} true positives, "
        f"{rule['false_positive']} false positives, {rule['false_negative']} false negatives. "
        f"Precision {rule['precision']:.4f}, recall {rule['recall']:.4f}."
    )
    lines.append("")
    lines.append("## Base model only (rule disabled)")
    lines.append("")
    lines.append(f"- Accuracy: {model_only['accuracy']:.4f}")
    lines.append(f"- Macro-F1: {model_only['f1_macro']:.4f}")
    lines.append(f"- Weighted-F1: {model_only['f1_weighted']:.4f}")
    lines.append(
        f"- EOL F1: {model_only['classification_report']['EOL']['f1-score']:.4f} "
        "(the class has no training rows after strict leakage removal)"
    )
    lines.append("")
    (config.REPORTS_DIR / "classification_metrics.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return result
