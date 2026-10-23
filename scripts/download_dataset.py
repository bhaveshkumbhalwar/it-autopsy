#!/usr/bin/env python
"""Download the public IT support ticket dataset (CC BY 4.0).

Why parquet from HuggingFace: the dataset repository is public and not gated,
the two files are tiny (~98 KB total), and they preserve the source authors'
official train/test split. No authentication is used.

Run:
    .venv/bin/python scripts/download_dataset.py

The script is idempotent: existing files are re-hashed and skipped when intact.
Raw files are written to data/raw/ and never modified afterwards.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402

BASE = "https://huggingface.co/datasets/tasksource/it-support-tickets/resolve/main/data/"
FILES = {
    "train": "train-00000-of-00001.parquet",
    "test": "test-00000-of-00001.parquet",
}

SOURCE_META = {
    "title": "Classification of IT Support Tickets",
    "author": "Leonardo Santiago Benitez Pereira",
    "publisher": "Zenodo / HuggingFace (tasksource mirror)",
    "doi": "10.5281/zenodo.7648117",
    "license": "CC BY 4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "dataset_page": "https://huggingface.co/datasets/tasksource/it-support-tickets",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 64):
                if chunk:
                    handle.write(chunk)


def main() -> int:
    config.ensure_directories()
    raw_dir = config.RAW_DIR
    processed_dir = config.PROCESSED_DIR
    processed_dir.mkdir(parents=True, exist_ok=True)

    records = {}
    frames = {}
    for split, filename in FILES.items():
        target = raw_dir / filename
        url = BASE + filename
        if target.exists():
            print(f"exists, verifying {target.name}")
        else:
            print(f"downloading {url}")
            download(url, target)

        frame = pd.read_parquet(target)
        frames[split] = frame
        records[split] = {
            "url": url,
            "file": f"data/raw/{filename}",
            "sha256": sha256(target),
            "bytes": target.stat().st_size,
            "rows": int(len(frame)),
            "columns": [str(c) for c in frame.columns],
        }
        print(f"  {split}: {len(frame)} rows, sha256 {records[split]['sha256'][:16]}…")

    # Fail loudly rather than continuing with a surprising shape.
    expected = {"train": 1572, "test": 657}
    for split, rows in expected.items():
        if len(frames[split]) != rows:
            raise SystemExit(
                f"unexpected row count for {split}: {len(frames[split])} (expected {rows}). "
                "The upstream files may have changed; re-audit before training."
            )

    # Processed view: one file, explicit split column, labels decoded.
    frames["train"]["split"] = "train"
    frames["test"]["split"] = "test"
    combined = pd.concat([frames["train"], frames["test"]], ignore_index=True)
    combined.insert(0, "row_id", range(len(combined)))
    combined["label_name"] = combined["label"].map(
        dict(enumerate(config.CATEGORIES))
    )
    if combined["label_name"].isna().any():
        raise SystemExit("unknown label id encountered; aborting")
    combined = combined[["row_id", "split", "text", "label", "label_name"]]
    processed = processed_dir / "tickets.csv"
    combined.to_csv(processed, index=False)
    print(f"wrote {processed} ({len(combined)} rows)")

    # Leakage-reduced training view:
    #  * drop texts that also occur in test (never touch test itself)
    #  * drop texts carrying conflicting labels
    train = combined[combined["split"] == "train"]
    test_texts = set(combined.loc[combined["split"] == "test", "text"])
    conflicting = train.groupby("text")["label"].nunique()
    conflicting_texts = set(conflicting[conflicting > 1].index)
    reduced = train[
        ~train["text"].isin(test_texts) & ~train["text"].isin(conflicting_texts)
    ].copy()
    reduced.to_csv(processed_dir / "train_leakage_reduced.csv", index=False)
    combined[combined["split"] == "test"].to_csv(
        processed_dir / "test_untouched.csv", index=False
    )
    print(
        f"wrote leakage-reduced train ({len(reduced)} rows; "
        f"removed {len(train) - len(reduced)} rows)"
    )

    manifest = {
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE_META,
        "files": records,
        "processed": {
            "tickets.csv": {"rows": int(len(combined)), "sha256": sha256(processed)},
            "train_leakage_reduced.csv": {
                "rows": int(len(reduced)),
                "sha256": sha256(processed_dir / "train_leakage_reduced.csv"),
            },
            "test_untouched.csv": {
                "rows": int(len(combined) - len(train)),
                "sha256": sha256(processed_dir / "test_untouched.csv"),
            },
        },
        "notes": [
            "Raw files are unmodified upstream copies.",
            "Test rows are never removed or relabelled.",
            "Leakage reduction removes test-overlapping and conflicting texts from training only.",
        ],
    }
    manifest_path = raw_dir / "SOURCES.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
