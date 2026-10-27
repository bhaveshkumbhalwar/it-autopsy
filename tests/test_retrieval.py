"""Source-grounded retrieval tests."""

from __future__ import annotations

import json

from src import config, retrieval


def test_retriever_loads_kb():
    r = retrieval.Retriever()
    assert len(r.docs) >= 5


def test_every_kb_entry_has_source_metadata():
    docs = json.loads((config.KB_DIR / "kb.json").read_text(encoding="utf-8"))
    for doc in docs:
        for key in ("id", "source", "publisher", "url", "title", "text", "access_date"):
            assert doc.get(key), (doc.get("id"), key)
        assert doc["url"].startswith("https://")


def test_macos_wifi_query_returns_apple_evidence():
    ev = retrieval.Retriever().search(
        "My Mac connects to Wi-Fi but has no internet access", os_name="macos"
    )
    assert ev
    assert any(e.source == "apple" and e.os_scope == "macos" for e in ev)


def test_bluetooth_headphone_query_returns_bluetooth_guidance_only():
    ev = retrieval.Retriever().search(
        "My MacBook Bluetooth is on but wireless headphones are not connecting",
        os_name="macos",
    )
    assert ev
    assert [e.doc_id for e in ev] == ["apple-bluetooth-mac-headphones"]
    assert all("wifi" not in e.title.lower() for e in ev)
    assert "Sound > Output" in ev[0].snippet


def test_wifi_query_does_not_return_bluetooth_evidence():
    ev = retrieval.Retriever().search(
        "Mac Wi-Fi connected but no internet", os_name="macos"
    )
    assert ev
    assert all(e.doc_id != "apple-bluetooth-mac-headphones" for e in ev)


def test_windows_dns_query_returns_microsoft_evidence():
    ev = retrieval.Retriever().search(
        "Windows DNS client cannot resolve internal names", os_name="windows"
    )
    assert ev
    assert any(e.source == "microsoft" and e.os_scope == "windows" for e in ev)


def test_os_filter_excludes_cross_os_documents():
    ev = retrieval.Retriever().search(
        "Windows DNS client cannot resolve internal names", os_name="macos"
    )
    assert all(e.os_scope != "windows" for e in ev)


def test_unanswerable_query_does_not_fabricate():
    ev = retrieval.Retriever().search("my laptop fan is loud")
    assert ev == []


def test_all_scores_above_threshold():
    ev = retrieval.Retriever().search("Mac Wi-Fi no internet", os_name="macos")
    assert ev
    assert all(e.score >= config.RETRIEVAL_MIN_SCORE for e in ev)


def test_top_k_is_respected():
    ev = retrieval.Retriever().search("dns ipconfig windows", top_k=1)
    assert len(ev) <= 1
