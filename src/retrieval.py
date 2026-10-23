"""Source-grounded retrieval over a small curated knowledge base.

The knowledge base contains concise, derived summaries of official sources
(Apple, Microsoft Learn) with full metadata and citations. It does **not** contain
held-out ticket examples. Low cosine similarity returns "insufficient evidence"
rather than a fabricated answer. Retrieval is TF-IDF + cosine similarity.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src import config
from src.schemas import Evidence


class Retriever:
    def __init__(self, kb_path: Path | None = None):
        self.kb_path = kb_path or (config.KB_DIR / "kb.json")
        self.docs = json.loads(self.kb_path.read_text(encoding="utf-8"))
        texts = []
        for doc in self.docs:
            # Combine title and body for retrieval without duplicating boilerplate.
            texts.append(f"{doc['title']}. {doc['text']}")
        self.vectorizer = TfidfVectorizer(
            lowercase=True, strip_accents="unicode", stop_words="english"
        )
        self.matrix = self.vectorizer.fit_transform(texts)

    def search(
        self,
        query: str,
        top_k: int = config.RETRIEVAL_TOP_K,
        os_name: str | None = None,
    ) -> list[Evidence]:
        """Return source-backed evidence above the relevance threshold.

        ``os_name`` optionally restricts results to documents whose ``os_scope``
        matches. This prevents citing a Windows-only page for a macOS issue.
        """
        qvec = self.vectorizer.transform([query])
        sims = cosine_similarity(qvec, self.matrix)[0]
        order = np.argsort(-sims)
        wanted = (os_name or "").strip().lower()
        results: list[Evidence] = []
        for idx in order:
            score = float(sims[idx])
            if score < config.RETRIEVAL_MIN_SCORE:
                # Sorted descending: nothing further can pass the threshold.
                break
            doc = self.docs[int(idx)]
            if wanted and wanted not in ("unknown", "") and doc.get("os_scope"):
                if doc["os_scope"].lower() != wanted:
                    continue
            citation = (
                f"{doc['publisher']}. {doc['title']}. {doc['url']} "
                f"(accessed {doc['access_date']})."
            )
            snippet = doc["text"]
            if len(snippet) > 220:
                snippet = snippet[:217] + "..."
            results.append(
                Evidence(
                    doc_id=doc["id"],
                    source=doc["source"],
                    title=doc["title"],
                    url=doc["url"],
                    snippet=snippet,
                    score=round(score, 4),
                    os_scope=doc.get("os_scope", ""),
                    citation=citation,
                )
            )
            if len(results) >= top_k:
                break
        return results
