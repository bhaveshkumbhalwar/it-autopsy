# Sources and provenance

Last updated: 2026-10-23 (all links accessed on this date)
Status: Phase 0 verification complete. Every claim below was observed directly
(dataset files were read; pages were fetched). Anything not verified is marked
**unverified**. This file is the historical verification record; all phases are
now complete (see `PROJECT_STATUS.md`), and later measurements live in
`docs/EVALUATION.md`.

---

## 1. IT Support Tickets dataset — classification data

**Primary access (used for inspection)**

- HuggingFace: <https://huggingface.co/datasets/tasksource/it-support-tickets>
- API metadata: <https://huggingface.co/api/datasets/tasksource/it-support-tickets>
- Parquet files (public, not gated, no auth required):
  - `https://huggingface.co/datasets/tasksource/it-support-tickets/resolve/main/data/train-00000-of-00001.parquet`
  - `https://huggingface.co/datasets/tasksource/it-support-tickets/resolve/main/data/test-00000-of-00001.parquet`
- Repository revision observed: `e0bbdcb96c84feaa394aaae9c48f8ba4b818451b`
  (dataset card `lastModified` 2026-09-23)

**Original record**

- Title: *Classification of IT Support Tickets*
- Author: Leonardo Santiago Benitez Pereira (Federal Institute of Santa Catarina)
- DOI: <https://doi.org/10.5281/zenodo.7648117> (Zenodo, v2, published 2022-12-01)
- License: **CC BY 4.0** (stated on both Zenodo and HuggingFace)
- Zenodo files: `X_train.csv`, `X_test.csv`, `y_train.csv`, `y_test.csv`,
  `code.py`, `metrics.txt`, `distributions.txt`, `confusion_matrix.png`,
  `training.png`, `vlatest_model.h5`, `vlatest_tokenizer.pickle`,
  `vlatest_encoder.pickle`

**Verified schema (read from the parquet files, not from the card)**

| Split | Rows | Columns | dtypes |
| --- | --- | --- | --- |
| train | 1,572 | `text`, `label` | string, int64 |
| test | 657 | `text`, `label` | string, int64 |
| total | 2,229 | | |

- Labels are integer-encoded `ClassLabel` values 0–6 with these names, in order:
  `Active Directory`, `Computer-Services`, `EOL`, `Fileservice`, `O365`,
  `Software`, `Support general`.
- **No language column.** The schema contains only `text` and `label`.
- No null values; no empty/whitespace-only texts.
- Text length: min 8, median 72, mean 164.1, max 1,856 characters.

**Verified label distribution**

| Label | Train | Train % | Test | Test % |
| --- | ---: | ---: | ---: | ---: |
| Fileservice | 546 | 34.7% | 138 | 21.0% |
| Support general | 541 | 34.4% | 221 | 33.6% |
| Software | 164 | 10.4% | 58 | 8.8% |
| O365 | 127 | 8.1% | 92 | 14.0% |
| Active Directory | 84 | 5.3% | 49 | 7.5% |
| Computer-Services | 65 | 4.1% | 41 | 6.2% |
| EOL | 45 | 2.9% | 58 | 8.8% |
| **Total** | **1,572** | | **657** | |

Majority-class baseline on the test split: **221 / 657 = 33.64%**.

**Verified data-quality issues (important)**

- Duplicate texts are common:
  - train: 962 unique texts out of 1,572 rows (610 duplicate rows)
  - test: 555 unique texts out of 657 rows (102 duplicate rows)
  - combined: 1,484 unique texts out of 2,229 rows
- **Exact text overlap between train and test: 33 texts** (5.9% of the 555
  unique test texts). This is leakage if the official split is used unchanged.
- One train text carries more than one label (conflicting annotation).
  No such conflict in the test split.
- Placeholder tokens present (PII masking by the authors), e.g.:
  `[TICKET ID]` 1,465 rows, `[LOCATION]` 1,072, `[COMPANY]` 1,015,
  `[NAME]` 917, `[SERVER]` 378, `[USERNAME]` 328, `[EMAIL]` 304,
  `[AWS_ACCESS_KEY]` 52, `[PASSWORD]` 22. 249 rows contain no placeholder tag.

**Language distribution — heuristic only, not verified ground truth**

The dataset has no language field, so this was estimated with a simple
stopword-counting script (our own, unvalidated) over lowercased word tokens:

| Guess | Rows | Share |
| --- | ---: | ---: |
| English | 992 | 44.5% |
| Other / unknown | 718 | 32.2% |
| Portuguese | 422 | 18.9% |
| German | 54 | 2.4% |
| Spanish | 43 | 1.9% |

**Caveat:** this is a heuristic, not a language classifier. Short tickets made
only of placeholders and product names fall into "other/unknown". Do not
report these as measured language shares. The dataset card states the corpus is
"mainly in English, German, Portuguese and Spanish, with other languages also
present"; HuggingFace tags list `en`, `de`, `pt`, `es`.

**Scope mismatch to remember**

The tickets are generic IT service requests (access requests, Active Directory,
file shares, O365/email, software installation, end-of-life hardware). They
carry **no OS field**, and most do not describe an operating-system fault.
The retrieval sources below are macOS/Windows troubleshooting guides. Therefore:

- the dataset is suitable for **triage classification only**;
- it says nothing about whether an agent can resolve an incident;
- it contains **no resolution outcome** field.

**Permitted use and obligations**

- CC BY 4.0 permits reuse and redistribution with attribution.
- We will attribute the author, title, DOI and licence in `README.md` and
  `docs/EVALUATION.md`.
- Raw files stay under `data/raw/` and are git-ignored; processed copies under
  `data/processed/`. We do not commit the dataset.
- We preserve the official train/test split. Duplicate texts that appear in
  test will be removed from the **training** set only (never the reverse), and
  both "as-released" and "leakage-reduced" numbers will be reported.

---

## 2. Apple Support — Mac Wi-Fi troubleshooting

- URL: <https://support.apple.com/en-in/101588>
- Publisher: Apple Inc.
- Page title: "If your Mac isn't connecting to the internet over Wi-Fi"
- Published date shown on page: March 19, 2025
- Access: public HTML, no login
- OS scope: macOS (current macOS UI)
- Sections observed on the page:
  1. Check the Wi-Fi connection
  2. Restart your Mac (and renew the DHCP lease)
  3. Check date and time
  4. Update macOS
  5. Check VPN or other security software
  6. Check the Wi-Fi router (firmware, recommended router settings)
  7. Use the built-in diagnostic tools: **Wi-Fi Recommendations** and
     **Wireless Diagnostics** (Option-click the Wi-Fi menu bar icon)
  8. Try a different network or contact the ISP
- Notable limitation from the page: Wi-Fi Recommendations is not available for
  personal hotspots or some enterprise security protocols (WPA/WPA2 Enterprise).
- **Licence/terms: not verified.** Apple's site carries a copyright notice and
  Terms of Use. We will therefore store **metadata + our own short factual
  summary** of each step, not a copy of the page text.

## 3. Microsoft Learn — Troubleshooting DNS clients

- URL: <https://learn.microsoft.com/en-us/windows-server/networking/dns/troubleshoot/troubleshoot-dns-client>
- Publisher: Microsoft
- Page metadata: `ms.date` 2019-08-08, `updated_at` 2025-09-22,
  `ms.service: windows-server`, `ms.subservice: networking`
- OS scope: **Windows Server / Windows client**. Must never be executed on macOS.
- Steps observed:
  - `ipconfig /all` — verify IP address, subnet mask, gateway, DNS servers, DNS suffix
  - `ipconfig /renew` — renew DHCP configuration
  - `ping <DNS server IP>` — reachability check (ICMP must be allowed)
  - `nslookup <client>` / `nslookup <fqdn>.` / `nslookup <DNS server>` /
    `nslookup <failed record>` / `nslookup <external name>` — DNS query tests
  - `ipconfig /displaydns` — inspect the client resolver cache
  - `ipconfig /flushdns` — clear the resolver cache
- Safety note: `ipconfig /renew` and `ipconfig /flushdns` **change state**. We
  will present them as documented manual guidance, **not** expose them as
  executable tools. Only read-only steps can become tools, and only on Windows.
- **Licence/terms: not verified.** Microsoft Learn content is generally
  CC BY 4.0, but we have not confirmed it for this page, so we store metadata +
  short derived summary only.

## 4. scikit-learn documentation (implementation reference)

- TF-IDF / text feature extraction:
  <https://scikit-learn.org/stable/modules/feature_extraction.html>
- Cosine similarity:
  <https://scikit-learn.org/stable/modules/metrics.html#cosine-similarity>
- Publisher: scikit-learn developers, BSD licence (documentation site footer:
  "© Copyright 2007 - 2026, scikit-learn developers (BSD License)")
- Use: implementation reference only. Facts used: `TfidfVectorizer` produces
  L2-normalised rows by default, so cosine similarity equals the linear kernel;
  `cosine_similarity` accepts sparse matrices.

## 5. OWASP — prompt injection

- LLM01:2025 Prompt Injection: <https://genai.owasp.org/llmrisk/llm01-prompt-injection/>
- Prevention Cheat Sheet:
  <https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html>
- Publisher: OWASP Foundation. Cheat sheet is CC BY-SA 4.0.
- Verified guidance adopted:
  - prompt injection cannot be fully prevented by prompt wording; input
    filtering is a layer, not a boundary;
  - enforce permissions and parameter validation **in code at the tool
    boundary**;
  - apply least privilege;
  - require human approval for high-risk actions, verified against the exact
    proposed action;
  - treat retrieved documents, tool output and conversation history as
    untrusted;
  - test direct and indirect injection with instrumented tool substitutes and
    record outcomes per security objective, not a single score.

## 6. NIST — AI RMF Generative AI Profile

- URL: <https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence>
- Full citation: Autio, C., Schwartz, R., Dunietz, J., Jain, S., Stanley, M.,
  Tabassi, E., Hall, P., Roberts, K. (2024). *Artificial Intelligence Risk
  Management Framework: Generative Artificial Intelligence Profile.*
  NIST Trustworthy and Responsible AI 600-1.
  DOI: <https://doi.org/10.6028/NIST.AI.600-1>
- Published 2024-07-26; page updated 2026-04-08.
- Use: voluntary risk-management framing for documenting risks, testing, and
  limitations. Not a compliance claim.

---

## Not used, and why

- No synthetic or fabricated tickets, labels, articles, or outcomes will be
  added to production data or benchmarks. Small hand-written fixtures are
  allowed only under `tests/fixtures/` and labelled as such.
- No scraping of support sites. Only the specific pages above, and only
  metadata plus short derived summaries.
- No resolution logs were found in the public dataset; we will not invent them.
