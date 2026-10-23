"""Bounded, observation-driven agent controller.

Loop: intake -> triage -> retrieve -> plan -> policy check -> execute/approval
-> observe -> decide next step -> verify/escalate.

The controller is deliberately small and explicit. It does not hide decisions in
a framework's internals, and it never executes free-form text as a command.
Every step references a registered tool name and validated parameters.

Decision rules (all code, not prompt text):
  * network-ish symptoms start with a status observation;
  * the *result* of each observation decides what is checked next;
  * failed observations stop the loop and escalate;
  * repeated identical calls are refused (loop prevention);
  * the step budget is a hard stop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from src import classifier, config, policy, retrieval, tools
from src.schemas import Evidence, Issue, Plan, Step, ToolResult, TriageResult

SECURITY_KEYWORDS = (
    "breach",
    "hacked",
    "ransomware",
    "malware",
    "phishing",
    "stolen",
    "compromised",
)


@dataclass
class TraceEntry:
    step: int
    decision: str
    tool_name: str | None = None
    observed: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRun:
    case_id: str
    issue: Issue
    triage: TriageResult
    evidence: list[Evidence] = field(default_factory=list)
    plan: Plan | None = None
    results: list[ToolResult] = field(default_factory=list)
    trace: list[TraceEntry] = field(default_factory=list)
    stopped_reason: str = ""
    escalated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "issue": self.issue.text,
            "os": self.issue.os,
            "triage": {
                "category": self.triage.category,
                "scores": self.triage.scores,
                "priority": self.triage.priority,
                "priority_rationale": self.triage.priority_rationale,
                "confidence_label": self.triage.confidence_label,
            },
            "evidence": [
                {
                    "doc_id": e.doc_id,
                    "title": e.title,
                    "url": e.url,
                    "source": e.source,
                    "score": e.score,
                    "snippet": e.snippet,
                    "os_scope": e.os_scope,
                    "citation": e.citation,
                }
                for e in self.evidence
            ],
            "plan": {"rationale": self.plan.rationale if self.plan else "", "steps": len(self.plan.steps) if self.plan else 0},
            "results": [
                {
                    "tool_name": r.tool_name,
                    "simulated": r.simulated,
                    "ok": r.ok,
                    "data": r.data,
                    "summary": r.summary,
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                    "redacted": r.redacted,
                }
                for r in self.results
            ],
            "trace": [
                {
                    "step": t.step,
                    "decision": t.decision,
                    "tool_name": t.tool_name,
                    "observed": t.observed,
                    "params": t.params,
                }
                for t in self.trace
            ],
            "stopped_reason": self.stopped_reason,
            "escalated": self.escalated,
        }


class Agent:
    def __init__(
        self,
        os_name: str = config.OS_UNKNOWN,
        allow_real: bool = False,
        registry: tools.ToolRegistry | None = None,
    ):
        self.clf = classifier.Classifier()
        self.ret = retrieval.Retriever()
        self.os_name = policy.normalize_os(os_name)
        self.allow_real = bool(allow_real)
        self.reg = registry or tools.ToolRegistry(
            os_name=self.os_name, allow_real=self.allow_real
        )

    # -- pipeline stages ---------------------------------------------------
    def intake(self, text: str, os_name: str | None = None) -> Issue:
        text = (text or "").strip()
        if not text:
            raise ValueError("issue text required")
        if len(text) > 4000:
            raise ValueError("issue text too long (max 4000 characters)")
        return Issue(text=text, os=os_name or self.os_name, case_id=str(uuid.uuid4()))

    def triage(self, issue: Issue) -> TriageResult:
        _, label_name, info = self.clf.predict(issue.text)
        low = issue.text.lower()
        priority = "medium"
        rationale: list[str] = []
        if any(word in low for word in SECURITY_KEYWORDS):
            priority = "high"
            rationale.append("security keyword detected: handled as high priority and escalated")
        elif any(
            phrase in low
            for phrase in ("all users", "everyone", "outage", "cannot work", "data loss", "down for")
        ):
            priority = "high"
            rationale.append("broad-impact phrase detected")
        elif any(word in low for word in ("password", "login", "access denied", "locked")):
            rationale.append("access-related issue")
        else:
            rationale.append("default rule: no high-impact signal")
        return TriageResult(
            category=label_name,
            scores=info.get("scores", {}),
            priority=priority,
            priority_rationale=rationale,
            confidence_label=info.get("confidence_label", "score (not calibrated)"),
        )

    def retrieve(self, issue: Issue) -> list[Evidence]:
        return self.ret.search(issue.text, os_name=issue.os)

    def plan(self, issue: Issue, triage: TriageResult, evidence: list[Evidence]) -> Plan:
        steps: list[Step] = []
        low = issue.text.lower()
        dns_hint = any(w in low for w in ("dns", "resolve", "nslookup", "name resolution"))
        network_hint = dns_hint or any(
            w in low
            for w in (
                "wifi",
                "wi-fi",
                "internet",
                "network",
                "connect",
                "vpn",
                "ping",
                "offline",
            )
        )
        if network_hint and triage.priority != "high":
            first_tool = "sim_get_dns_config" if dns_hint else "sim_get_wifi_status"
            steps.append(
                Step(
                    step_id=1,
                    action="Observe current network state",
                    tool_name=first_tool,
                    params={},
                    rationale=(
                        "Start with the observation most relevant to the reported "
                        "symptom before giving advice."
                    ),
                )
            )
        steps.append(
            Step(
                step_id=len(steps) + 1,
                action="Check whether official evidence covers this issue",
                tool_name=None,
                params={},
                rationale=f"{len(evidence)} source(s) above the relevance threshold.",
            )
        )
        return Plan(steps=steps, rationale="Bounded plan; later steps are chosen from observations.")

    # -- main loop ---------------------------------------------------------
    def run(
        self,
        text: str,
        os_name: str | None = None,
        max_steps: int = config.MAX_STEPS,
        *,
        approved: bool = False,
    ) -> dict[str, Any]:
        issue = self.intake(text, os_name)
        triage = self.triage(issue)
        evidence = self.retrieve(issue)
        plan = self.plan(issue, triage, evidence)
        run = AgentRun(case_id=issue.case_id, issue=issue, triage=triage, evidence=evidence, plan=plan)

        # Guard: security concern -> escalate immediately, no diagnostics.
        if triage.priority == "high" and any(w in issue.text.lower() for w in SECURITY_KEYWORDS):
            run.trace.append(TraceEntry(0, "security concern detected; stop and escalate"))
            run.stopped_reason = "security_concern"
            run.escalated = True
            return run.to_dict()

        # Guard: no evidence -> do not guess; escalate.
        if not evidence:
            run.trace.append(TraceEntry(0, "no relevant official evidence; escalate instead of guessing"))
            run.stopped_reason = "insufficient_evidence"
            run.escalated = True
            return run.to_dict()

        next_step: Step | None = plan.steps[0] if plan.steps and plan.steps[0].tool_name else None
        visited: set[tuple[str, str]] = set()
        step_no = 0

        while next_step is not None:
            if step_no >= max_steps:
                run.stopped_reason = "step_budget_exhausted"
                run.escalated = True
                run.trace.append(TraceEntry(step_no, "stop: step budget exhausted"))
                break
            step_no += 1
            tool_name = next_step.tool_name
            if tool_name is None:
                break
            key = (tool_name, str(sorted(next_step.params.items())))
            if key in visited:
                run.stopped_reason = "loop_prevented"
                run.trace.append(TraceEntry(step_no, f"stop: repeated call to {tool_name}", tool_name))
                break
            visited.add(key)

            try:
                policy.Policy(os_name=issue.os, allow_real=self.allow_real).check(
                    tool_name, next_step.params, approved=approved
                )
                raw = self.reg.call(tool_name, next_step.params, approved=approved)
                result = ToolResult(
                    tool_name=raw["tool_name"],
                    simulated=raw["simulated"],
                    ok=raw["ok"],
                    data=raw.get("data", {}),
                    summary=raw.get("summary", ""),
                    error=raw.get("error", ""),
                    duration_ms=raw.get("duration_ms", 0),
                    redacted=raw.get("redacted", False),
                )
            except policy.PolicyViolation as exc:
                result = ToolResult(
                    tool_name=tool_name,
                    simulated=False,
                    ok=False,
                    error=f"policy_blocked: {exc}",
                    redacted=True,
                )
            run.results.append(result)
            run.trace.append(
                TraceEntry(
                    step_no,
                    f"executed {tool_name} ({'simulated' if result.simulated else 'real'})",
                    tool_name,
                    result.summary or result.error,
                    dict(next_step.params),
                )
            )

            # Observation-driven next step.
            next_step = self._decide_next(run, result, step_no)
            if next_step is None:
                run.stopped_reason = run.stopped_reason or "plan_complete"

        if not run.results and not run.escalated:
            run.stopped_reason = "no_applicable_tools"
        return run.to_dict()

    def _decide_next(self, run: AgentRun, result: ToolResult, step_no: int) -> Step | None:
        """Choose the next step from the *observed* result, not a fixed script."""
        if not result.ok:
            run.stopped_reason = f"tool_failed:{result.tool_name}"
            run.escalated = True
            return None

        data = result.data
        if result.tool_name == "sim_get_wifi_status":
            if data.get("connected") is False:
                run.trace.append(TraceEntry(step_no, "observation: not connected; stop (needs physical/network access)"))
                run.stopped_reason = "not_connected"
                return None
            run.trace.append(TraceEntry(step_no, "observation: wifi connected; next check DNS configuration"))
            return Step(
                step_id=step_no + 1,
                action="Observe DNS configuration",
                tool_name="sim_get_dns_config",
                params={},
                rationale="Connected but possibly no name resolution.",
            )

        if result.tool_name == "sim_get_dns_config":
            servers = data.get("dns_servers") or []
            if not servers:
                run.trace.append(TraceEntry(step_no, "observation: no DNS servers configured; stop"))
                run.stopped_reason = "no_dns_servers"
                return None
            run.trace.append(TraceEntry(step_no, "observation: DNS servers present; next test reachability"))
            return Step(
                step_id=step_no + 1,
                action="Test reachability",
                tool_name="sim_ping_host",
                params={"host": "example.com"},
                rationale="Confirm whether packets leave the host.",
            )

        if result.tool_name == "sim_ping_host":
            if data.get("received", 0) == 0:
                run.trace.append(TraceEntry(step_no, "observation: reachability failed; stop and escalate"))
                run.stopped_reason = "reachability_failed"
                run.escalated = True
                return None
            run.trace.append(TraceEntry(step_no, "observation: reachability ok; check VPN interference"))
            return Step(
                step_id=step_no + 1,
                action="Check VPN/proxy interference",
                tool_name="sim_check_vpn",
                params={},
                rationale="VPN software commonly blocks or reroutes traffic.",
            )

        if result.tool_name == "sim_check_vpn":
            if data.get("vpn_active"):
                run.trace.append(TraceEntry(step_no, "observation: VPN active; stop and escalate for human review"))
                run.stopped_reason = "vpn_active"
                run.escalated = True
                return None
            run.stopped_reason = "observations_complete"
            return None

        # Real diagnostics: one observation is enough; verification asks the user.
        run.stopped_reason = "observation_complete"
        return None
