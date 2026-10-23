"""Agent loop tests: observations change the next step; the loop stops safely."""

from __future__ import annotations

from src import agent, config, tools


def _run(text, os_name="macos", registry=None, **kwargs):
    a = agent.Agent(os_name=os_name, registry=registry)
    return a.run(text, os_name=os_name, **kwargs)


def test_observation_chain_uses_result_to_choose_next_step():
    run = _run("My Mac connects to Wi-Fi but has no internet")
    tools_called = [r["tool_name"] for r in run["results"]]
    assert tools_called == [
        "sim_get_wifi_status",
        "sim_get_dns_config",
        "sim_ping_host",
        "sim_check_vpn",
    ]
    decisions = [t["decision"] for t in run["trace"]]
    assert any("next check DNS" in d for d in decisions)
    assert run["stopped_reason"] == "observations_complete"
    assert run["escalated"] is False


def test_bounded_loop_max_steps():
    run = _run("Wi-Fi connected but no internet", max_steps=2)
    assert len(run["results"]) <= 2
    assert run["escalated"] is True
    assert run["stopped_reason"] == "step_budget_exhausted"


def test_failed_reachability_stops_and_escalates():
    class FailingPing(tools.ToolRegistry):
        def _sim(self, name, params):
            data = super()._sim(name, params)
            if name == "sim_ping_host":
                data.update(received=0, sent=4, summary="SIMULATED: 100% packet loss")
            return data

    run = _run(
        "My Mac connects to Wi-Fi but has no internet",
        registry=FailingPing(os_name="macos"),
    )
    called = [r["tool_name"] for r in run["results"]]
    assert called[-1] == "sim_ping_host"
    assert "sim_check_vpn" not in called  # observation stopped the chain
    assert run["stopped_reason"] == "reachability_failed"
    assert run["escalated"] is True


def test_active_vpn_stops_and_escalates():
    class VpnOn(tools.ToolRegistry):
        def _sim(self, name, params):
            data = super()._sim(name, params)
            if name == "sim_check_vpn":
                data.update(vpn_active=True, summary="SIMULATED: VPN active")
            return data

    run = _run(
        "My Mac connects to Wi-Fi but has no internet",
        registry=VpnOn(os_name="macos"),
    )
    assert run["results"][-1]["tool_name"] == "sim_check_vpn"
    assert run["stopped_reason"] == "vpn_active"
    assert run["escalated"] is True


def test_disconnected_wifi_does_not_continue_to_dns():
    class Disconnected(tools.ToolRegistry):
        def _sim(self, name, params):
            data = super()._sim(name, params)
            if name == "sim_get_wifi_status":
                data.update(connected=False, summary="SIMULATED: Wi-Fi off")
            return data

    run = _run(
        "My Mac connects to Wi-Fi but has no internet access",
        registry=Disconnected(os_name="macos"),
    )
    assert [r["tool_name"] for r in run["results"]] == ["sim_get_wifi_status"]
    assert run["stopped_reason"] == "not_connected"
    assert run["escalated"] is False


def test_dns_query_starts_with_dns_observation():
    run = _run("Windows DNS resolution failing for internal site", os_name="windows")
    assert run["results"][0]["tool_name"] == "sim_get_dns_config"


def test_security_concern_stops_before_any_tool():
    run = _run("My laptop has ransomware and files are encrypted")
    assert run["results"] == []
    assert run["escalated"] is True
    assert run["stopped_reason"] == "security_concern"


def test_no_evidence_escalates_without_tools():
    run = _run("My office printer is jammed and needs toner", os_name="unknown")
    assert run["results"] == []
    assert run["escalated"] is True
    assert run["stopped_reason"] == "insufficient_evidence"


def test_unknown_os_does_not_run_macos_commands():
    run = _run("wifi problem", os_name=config.OS_UNKNOWN)
    for r in run["results"]:
        assert not r["tool_name"].startswith("macos_")


def test_evidence_citations_present_when_found():
    run = _run("My Mac connects to Wi-Fi but has no internet")
    assert run["evidence"]
    for e in run["evidence"]:
        assert e["citation"]
        assert e["url"].startswith("https://")


def test_simulated_results_are_labelled():
    run = _run("Network connectivity issue")
    for r in run["results"]:
        if r["tool_name"].startswith("sim_"):
            assert r["simulated"] is True


def test_empty_issue_rejected():
    import pytest

    with pytest.raises(ValueError):
        _run("   ")


def test_oversized_issue_rejected():
    import pytest

    with pytest.raises(ValueError):
        _run("x" * 4001)
