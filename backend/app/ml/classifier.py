"""ML failure classifier: TF-IDF + Linear SVM (with calibrated probabilities).

The pipeline is intentionally small and modular:
    features  -> TfidfVectorizer(word + char n-grams)
    model     -> CalibratedClassifierCV(LinearSVC) with a LogisticRegression
                 fallback when calibration data is too thin.

`get_classifier()` lazily trains the model the first time it is needed and
caches it on disk (data/ml/failure_classifier.joblib). Training is deterministic,
so the artefact is reproducible.

Metrics reported by `evaluate()` are measured on a held-out split of the labeled
corpus — they are never hardcoded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.ml.corpus import training_texts_and_labels

MODEL_VERSION = "1.0.0"


@dataclass
class Prediction:
    label: str
    confidence: float
    top: list[tuple[str, float]]


def build_pipeline():
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline
    from sklearn.svm import LinearSVC

    features = FeatureUnion(
        [
            ("word", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1, lowercase=True)),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=1, lowercase=True)),
        ]
    )
    base = LinearSVC(C=1.0, class_weight="balanced", max_iter=5000, random_state=42)
    try:
        clf = CalibratedClassifierCV(base, method="sigmoid", cv=3)
        pipeline = Pipeline([("features", features), ("clf", clf)])
        pipeline.fit(*training_texts_and_labels())
        return pipeline
    except Exception:
        pipeline = Pipeline(
            [("features", features), ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=42))]
        )
        pipeline.fit(*training_texts_and_labels())
        return pipeline


class FailureClassifier:
    def __init__(self, pipeline=None):
        self._pipeline = pipeline
        self._loaded = pipeline is not None

    @property
    def pipeline(self):
        if not self._loaded:
            self._pipeline = load_or_train()
            self._loaded = True
        return self._pipeline

    def predict(self, text: str, top_k: int = 3) -> Prediction | None:
        if not text or not text.strip():
            return None
        try:
            pipeline = self.pipeline
            proba = pipeline.predict_proba([text])[0]
            classes = list(pipeline.classes_)
            ranked = sorted(zip(classes, proba), key=lambda x: x[1], reverse=True)
            best_label, best_p = ranked[0]
            return Prediction(label=str(best_label), confidence=float(best_p), top=[(str(c), float(p)) for c, p in ranked[:top_k]])
        except Exception:
            return None


def _model_path() -> Path:
    return Path(settings.ml_model_path)


def load_or_train(force: bool = False) -> Any:
    path = _model_path()
    meta_path = path.with_suffix(".json")
    if path.exists() and not force:
        try:
            import joblib

            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            if meta.get("version") == MODEL_VERSION:
                return joblib.load(path)
        except Exception:
            pass

    pipeline = build_pipeline()
    try:
        import joblib

        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(pipeline, path)
        metrics = evaluate(pipeline)
        meta_path.write_text(json.dumps({"version": MODEL_VERSION, "metrics": metrics}, indent=2))
    except Exception:
        pass
    return pipeline


_CLASSIFIER: FailureClassifier | None = None


def get_classifier() -> FailureClassifier:
    global _CLASSIFIER
    if _CLASSIFIER is None:
        _CLASSIFIER = FailureClassifier()
    return _CLASSIFIER


def evaluate(pipeline=None, test_size: float = 0.3) -> dict[str, Any]:
    """Stratified train/test evaluation with real metrics."""
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
    from sklearn.model_selection import StratifiedKFold, cross_val_predict, train_test_split

    texts, labels = training_texts_and_labels()
    if pipeline is None:
        pipeline = build_pipeline()

    X_train, X_test, y_train, y_test = train_test_split(
        texts, labels, test_size=test_size, random_state=42, stratify=labels
    )

    fresh = build_pipeline()
    from sklearn.base import clone

    model = clone(fresh)
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)

    classes = sorted(set(labels))
    # Cross-validated estimate over the whole corpus (more stable than one split).
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_pred = cross_val_predict(clone(fresh), texts, labels, cv=cv)

    return {
        "model": "TF-IDF (word 1-2 + char 3-5) + calibrated LinearSVC",
        "dataset": {"examples": len(texts), "classes": len(classes), "source": "synthetic labeled corpus (app/ml/corpus.py)"},
        "holdout": {
            "test_size": test_size,
            "samples": len(y_test),
            "accuracy": round(float(accuracy_score(y_test, y_pred)), 4),
            "macro_f1": round(float(f1_score(y_test, y_pred, average="macro")), 4),
            "weighted_f1": round(float(f1_score(y_test, y_pred, average="weighted")), 4),
            "per_class": {
                k: {m: round(float(v), 3) for m, v in row.items() if m in {"precision", "recall", "f1-score"}}
                for k, row in classification_report(y_test, y_pred, output_dict=True, zero_division=0).items()
                if k in classes
            },
            "confusion_matrix": {
                "labels": classes,
                "matrix": confusion_matrix(y_test, y_pred, labels=classes).tolist(),
            },
        },
        "cross_validation": {
            "folds": 5,
            "accuracy": round(float(accuracy_score(labels, cv_pred)), 4),
            "macro_f1": round(float(f1_score(labels, cv_pred, average="macro")), 4),
        },
        "caveat": "Metrics come from a small synthetic corpus; treat them as a sanity check, not a production guarantee.",
    }
