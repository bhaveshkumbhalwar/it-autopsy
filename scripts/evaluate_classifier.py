"""Train and evaluate the classifier.

Usage:
    .venv/bin/python -m scripts.evaluate_classifier
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import classifier


def main() -> int:
    info = classifier.train(use_leakage_reduced=True)
    print("trained", info)
    metrics = classifier.evaluate()
    print(
        "hybrid:",
        "accuracy",
        metrics["accuracy"],
        "macro_f1",
        metrics["f1_macro"],
        "weighted_f1",
        metrics["f1_weighted"],
    )
    model_only = metrics["model_only"]
    print(
        "model-only (no EOL rule):",
        "accuracy",
        model_only["accuracy"],
        "macro_f1",
        model_only["f1_macro"],
        "weighted_f1",
        model_only["f1_weighted"],
    )
    rule = metrics["eol_rule"]
    print(
        "EOL rule on train:",
        "precision",
        rule["precision"],
        "recall",
        rule["recall"],
        "matches",
        rule["n_matched"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
