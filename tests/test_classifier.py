"""Classifier tests: EOL rule, score alignment, and fresh-checkout bootstrap."""

from __future__ import annotations

import json

import pytest

from src import classifier, config


@pytest.fixture(scope="module")
def clf() -> classifier.Classifier:
    return classifier.Classifier()


def test_eol_rule_matches_common_phrasings():
    for text in (
        "The system must be set to End Of Life in Nexthink",
        "end-of-life hardware replacement",
        "Please retire this EOL laptop",
        "eol",
    ):
        assert classifier.eol_rule_matches(text), text


def test_eol_rule_does_not_match_unrelated_words():
    for text in ("eolian corrosion", "wifi drops", "printer jammed"):
        assert not classifier.eol_rule_matches(text), text


def test_eol_rule_returns_eol_before_the_model(clf):
    label_id, name, info = clf.predict(
        "The following system must be set to End Of Life in Nexthink"
    )
    assert label_id == classifier.EOL_LABEL_ID
    assert name == "EOL"
    assert info["scores"] == {"EOL": 1.0}
    assert "not a probability" in info["confidence_label"]


def test_model_predictions_align_with_estimator_classes(clf):
    label_id, name, info = clf.predict("VPN disconnects every few minutes")
    assert label_id in clf.classes
    assert name == config.CATEGORIES[label_id]
    assert name in info["scores"]
    # Scores must only name classes the estimator was trained on.
    assert set(info["scores"]).issubset(
        {config.CATEGORIES[class_id] for class_id in clf.classes}
    )


def test_strict_leakage_view_has_no_eol_training_rows(clf):
    # Both unique EOL texts occur in train and test, so the strict filter
    # removes the whole class. The rule exists because of this data property.
    assert classifier.EOL_LABEL_ID not in clf.classes


def test_rule_validation_uses_training_rows_only():
    import pandas as pd

    train = pd.read_csv(
        config.PROCESSED_DIR / "train_leakage_reduced.csv", keep_default_na=False
    )
    # The reduced file has no EOL rows, so we validate against the full training
    # split to measure the rule itself. This is still training data, not test.
    full = pd.read_csv(config.PROCESSED_DIR / "tickets.csv", keep_default_na=False)
    full_train = full[full["split"] == "train"]
    rule = classifier.rule_validation(full_train)
    assert rule["split"] == "train only"
    assert rule["true_positive"] > 0
    assert rule["precision"] >= 0.95
    assert rule["recall"] >= 0.95
    assert len(train) > 0


def test_auto_train_on_missing_default_model(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "models")
    model = classifier.Classifier(auto_train=True)
    assert (tmp_path / "models" / "classifier.joblib").exists()
    assert model.model_path == tmp_path / "models" / "classifier.joblib"
    assert model.predict("wifi drops")[1] in config.CATEGORIES
    metadata = json.loads(
        (tmp_path / "models" / "classifier_metadata.json").read_text()
    )
    # The rule must be measured on the full training split, not the reduced
    # view that intentionally contains no EOL rows.
    assert metadata["eol_rule"]["validation"]["n_rows"] == 1572
    assert metadata["eol_rule"]["validation"]["true_positive"] > 0


def test_missing_custom_model_is_not_silently_trained(tmp_path):
    with pytest.raises(FileNotFoundError):
        classifier.Classifier(model_path=tmp_path / "missing.joblib")
