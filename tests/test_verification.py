"""Verification and escalation tests.

Tool success is never reported as resolution. Only an explicit user
confirmation can produce verified_resolved / not_resolved.
"""

from __future__ import annotations

from src import config, verification

EV = [{"doc_id": "apple-wifi-101588-restart"}]


def test_tool_success_is_not_resolution():
    res = verification.Verifier().verify(
        EV, [{"ok": True, "tool_name": "sim_get_wifi_status"}]
    )
    assert res["outcome"] == config.OUTCOME_NEEDS_CONFIRMATION


def test_no_evidence_escalates():
    res = verification.Verifier().verify([], [{"ok": True}])
    assert res["outcome"] == config.OUTCOME_ESCALATED
    assert res["evidence_ids"] == []


def test_all_tools_failed_escalates():
    res = verification.Verifier().verify(EV, [{"ok": False}, {"ok": False}])
    assert res["outcome"] == config.OUTCOME_ESCALATED


def test_mixed_results_unable_to_verify():
    res = verification.Verifier().verify(EV, [{"ok": True}, {"ok": False}])
    assert res["outcome"] == config.OUTCOME_UNABLE


def test_no_results_unable_to_verify():
    res = verification.Verifier().verify(EV, [])
    assert res["outcome"] == config.OUTCOME_UNABLE


def test_run_escalation_flag_is_respected():
    run = {"escalated": True, "stopped_reason": "security_concern", "results": []}
    res = verification.Verifier().verify(EV, [], run=run)
    assert res["outcome"] == config.OUTCOME_ESCALATED
    assert "security_concern" in res["reason"]


def test_user_confirmation_sets_verified_resolved():
    v = verification.Verifier()
    pending = v.verify(EV, [{"ok": True}])
    confirmed = v.confirm(pending, resolved=True)
    assert confirmed["outcome"] == config.OUTCOME_VERIFIED


def test_user_denial_sets_not_resolved():
    v = verification.Verifier()
    pending = v.verify(EV, [{"ok": True}])
    denied = v.confirm(pending, resolved=False)
    assert denied["outcome"] == config.OUTCOME_NOT_RESOLVED


def test_confirmation_cannot_upgrade_an_escalation():
    v = verification.Verifier()
    escalated = v.verify([], [])
    assert v.confirm(escalated, resolved=True)["outcome"] == config.OUTCOME_ESCALATED


def test_escalation_handoff_has_required_fields():
    run = {
        "issue": "wifi drops",
        "triage": {"category": "Support general", "priority": "medium"},
        "evidence": [{"citation": "Apple. Page. https://example (accessed 2026-10-23)."}],
        "results": [{"tool_name": "sim_get_wifi_status", "summary": "SIMULATED: ok", "error": ""}],
        "stopped_reason": "observations_complete",
    }
    ver = verification.Verifier().verify(run["evidence"], run["results"], run=run)
    esc = verification.Escalator().build(run, ver)
    for key in (
        "summary",
        "category",
        "priority",
        "symptoms",
        "sources",
        "actions_attempted",
        "results",
        "not_attempted_reason",
        "next_steps",
        "to",
    ):
        assert key in esc
    assert esc["sources"] and esc["to"] == "human support"
