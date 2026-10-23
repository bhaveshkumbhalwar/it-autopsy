# Data handling policy

This folder holds data that is **not committed to Git** (see `.gitignore`).

## Layout

```text
data/
├── README.md        this policy
├── eval/            hand-written evaluation sets (tracked)
├── knowledge/       official-source summaries + metadata (tracked)
├── raw/             original downloaded files, unmodified (git-ignored)
└── processed/       derived copies, regenerable from raw (git-ignored)
```

## Rules

1. **Never edit files in `raw/`.** They are the provenance record. Checksums
   (SHA-256) and download URLs are written to `data/raw/SOURCES.json` by
   `scripts/download_dataset.py`.
2. **Derived files go in `processed/`.** Any cleaning, deduplication or split
   adjustment is a scripted, reproducible transformation.
3. **No fabricated data.** No synthetic tickets, labels, articles or outcomes
   are added to production data or to benchmarks. Small hand-written fixtures
   may live in `tests/fixtures/` and must be clearly named and documented as
   artificial test fixtures.
4. **No secrets or personal data.** The source dataset was PII-redacted by its
   authors; we still treat ticket text as sensitive and never log full text.
5. **No large binaries in Git.** Datasets and model artefacts stay local.
6. **Attribution.** The dataset is CC BY 4.0. Attribution is in `README.md` and
   `docs/SOURCES.md`; keep it if data is redistributed.

## Source dataset

- HuggingFace: `tasksource/it-support-tickets`
- DOI: `10.5281/zenodo.7648117`
- Licence: CC BY 4.0
- Observed size: 2,229 rows (train 1,572 / test 657), 2 columns (`text`, `label`)

See `docs/SOURCES.md` for verified statistics and known data-quality issues.
