"""UI smoke tests using Streamlit's AppTest (no browser required)."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("HDA_DB_PATH", str(tmp_path / "ui.db"))
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def test_app_renders_without_exception(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    at.run()
    assert not at.exception
    assert "Autonomous IT Help Desk Agent" in at.title[0].value


def test_app_runs_a_case_and_persists(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    at.run()
    at.text_area(key="issue_text").set_value(
        "My Mac connects to Wi-Fi but has no internet access"
    )
    at.selectbox(key="os_choice").set_value("macos")
    at.button[0].click().run()
    assert not at.exception

    run = at.session_state["run"]
    assert run["triage"]["category"] in {
        "Active Directory",
        "Computer-Services",
        "EOL",
        "Fileservice",
        "O365",
        "Software",
        "Support general",
    }
    assert run["results"], "expected at least one observation"
    assert at.session_state["ver"]["outcome"] in {
        "needs_user_confirmation",
        "escalated",
        "unable_to_verify",
        "verified_resolved",
        "not_resolved",
    }

    from src import database

    db = database.DB()
    cases = db.list_cases()
    assert any(c["id"] == run["case_id"] for c in cases)
    events = db.list_events(run["case_id"])
    assert any(e["event_type"] == "tool_call" for e in events)
    assert any(e["event_type"] == "verification" for e in events)


def test_app_rejects_empty_issue(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    at.run()
    at.button[0].click().run()
    assert not at.exception
    assert at.warning, "expected a warning for an empty issue"


def test_app_user_confirmation_updates_outcome(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path)
    at.run()
    at.text_area(key="issue_text").set_value(
        "My Mac connects to Wi-Fi but has no internet access"
    )
    at.selectbox(key="os_choice").set_value("macos")
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state["ver"]["outcome"] == "needs_user_confirmation"

    labels = [b.label for b in at.button]
    index = labels.index("Yes, it is resolved")
    at.button[index].click().run()
    assert not at.exception
    assert at.session_state["ver"]["outcome"] == "verified_resolved"

    from src import database

    db = database.DB()
    cases = db.list_cases()
    match = [c for c in cases if c["id"] == at.session_state["run"]["case_id"]]
    assert match and match[0]["outcome"] == "verified_resolved"
