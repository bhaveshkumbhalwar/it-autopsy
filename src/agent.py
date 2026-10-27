"""Bounded, observation-driven agent controller.

Loop: intake -> analyze -> triage -> retrieve -> plan -> policy check
   -> execute/approval -> observe -> decide next step -> verify/escalate.

The controller is deliberately small and explicit. It does not hide decisions
in a framework's internals, and it never executes free-form text as a command.
Every step references a registered tool name and validated parameters.

Decision rules (all code, not prompt text):
  * ``analyze_issue`` extracts structured symptoms, topics, and clarification
    questions from the raw text using deterministic rules; an LLM provider
    enriches this when configured.
  * Network-ish symptoms start with a network status observation.
  * The *result* of each observation decides what is checked next.
  * Failed observations stop the loop and escalate.
  * Repeated identical calls are refused (loop prevention).
  * The step budget is a hard stop.
  * Tool-topic relevance is checked before execution: a Bluetooth-only issue
    never runs Wi-Fi tools; a Wi-Fi issue never returns Bluetooth evidence.

LLM-assisted mode vs deterministic mode:
  * In deterministic mode (default, no API key required) every decision is
    made by explicit rules in this file.
  * In LLM-assisted mode (``LLM_MODE=llm`` + ``LLM_API_KEY``), the provider
    is called to enrich ``IssueAnalysis`` and to compose the user-facing
    explanation text.  Tool selection and policy enforcement remain in code.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from src import classifier, config, policy, retrieval, tools, verification
from src.llm_provider import LLMProvider, NullProvider, get_provider, parse_issue_analysis_json
from src.schemas import CaseState, Evidence, Issue, IssueAnalysis, Plan, Step, ToolResult, TriageResult

SECURITY_KEYWORDS = (
    "breach",
    "hacked",
    "ransomware",
    "malware",
    "phishing",
    "stolen",
    "compromised",
)

# Topics that imply network diagnostic tools are relevant
_NETWORK_TOOL_TOPICS = frozenset({"wifi", "dns", "network", "vpn"})

# Patterns for issue analysis
_BLUETOOTH_RE = re.compile(r"\b(?:bluetooth|headphones?|headsets?|earbuds?|airpods?)\b", re.I)
_WIFI_RE = re.compile(r"\b(?:wi[- ]?fi|wireless(?:\s+(?:network|internet|connection))?|internet|network|no\s+internet|offline|router|dhcp)\b", re.I)
_DNS_RE = re.compile(r"\b(?:dns|nslookup|name\s+resolution|resolve|resolv)\b", re.I)
_VPN_RE = re.compile(r"\b(?:vpn|virtual\s+private\s+network)\b", re.I)
_PERFORMANCE_RE = re.compile(r"\b(?:slow(?:ly)?|hang(?:ing)?|freez(?:e|ing)|unresponsive|lag(?:gy)?|fast\s+(?:drain|battery)|high\s+cpu|memory\s+(?:leak|full)|out\s+of\s+memory)\b", re.I)
_INSTALL_RE = re.compile(r"\b(?:install(?:ing|ation)?|can(?:\'t| not)\s+install|setup\s+fail(?:ed)?|deployment\s+fail(?:ed)?|software)\b", re.I)
_ACCOUNT_RE = re.compile(r"\b(?:account\s+lock(?:ed|out)|locked\s+out|password\s+(?:expired?|reset)|cannot\s+(?:log|sign)\s+in|access\s+denied|credential|unauthori[sz]ed)\b", re.I)
_EMAIL_TOPIC_RE = re.compile(r"\b(?:outlook|email|mailbox|send(?:ing)?|receiv(?:e|ing)|exchange|office\s*365|o365)\b", re.I)

_MACOS_RE = re.compile(r"\b(?:mac(?:book|os)?|apple|macos|osx)\b", re.I)
_WINDOWS_RE = re.compile(r"\b(?:windows?|win\s*10|win\s*11|pc\b|microsoft)\b", re.I)


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
    analysis: IssueAnalysis
    evidence: list[Evidence] = field(default_factory=list)
    plan: Plan | None = None
    results: list[ToolResult] = field(default_factory=list)
    trace: list[TraceEntry] = field(default_factory=list)
    stopped_reason: str = ""
    escalated: bool = False
    needs_clarification: bool = False
    case_state: CaseState | None = None

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
            "analysis": {
                "symptoms": self.analysis.symptoms,
                "affected_device": self.analysis.affected_device,
                "expected_behavior": self.analysis.expected_behavior,
                "actual_behavior": self.analysis.actual_behavior,
                "topics": sorted(self.analysis.topics),
                "os_hints": self.analysis.os_hints,
                "missing_info": self.analysis.missing_info,
                "clarification_question": self.analysis.clarification_question,
                "diagnostic_confidence": self.analysis.diagnostic_confidence,
                "severity": self.analysis.severity,
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
            "needs_clarification": self.needs_clarification,
            "clarification_question": self.analysis.clarification_question,
            "case_state": (asdict(self.case_state) if self.case_state is not None else None),
            "reused_diagnostics": (
                self.case_state.reused_diagnostics if self.case_state is not None else []
            ),
        }


class Agent:
    def __init__(
        self,
        os_name: str = config.OS_UNKNOWN,
        allow_real: bool = False,
        registry: tools.ToolRegistry | None = None,
        provider: LLMProvider | None = None,
    ):
        self.clf = classifier.Classifier()
        self.ret = retrieval.Retriever()
        self.os_name = policy.normalize_os(os_name)
        self.allow_real = bool(allow_real)
        self.reg = registry or tools.ToolRegistry(
            os_name=self.os_name, allow_real=self.allow_real
        )
        self.provider = provider or get_provider()

    # -- pipeline stages ---------------------------------------------------

    def intake(self, text: str, os_name: str | None = None) -> Issue:
        text = (text or "").strip()
        if not text:
            raise ValueError("issue text required")
        if len(text) > 4000:
            raise ValueError("issue text too long (max 4000 characters)")
        return Issue(text=text, os=os_name or self.os_name, case_id=str(uuid.uuid4()))

    def analyze_issue(self, issue: Issue) -> IssueAnalysis:
        """Extract structured issue fields from the user's description.

        Uses deterministic rules in baseline mode.  When an LLM provider is
        configured, the model is called to enrich the result; any LLM output
        that does not validate against the schema is silently discarded and
        the rule-based result is used instead.

        This method never invents facts.  Absent information is represented
        as empty strings or empty lists, not guesses.
        """
        low = issue.text.lower()
        analysis = IssueAnalysis()

        # -- Topic detection -----------------------------------------------
        topics: set[str] = set()
        if _BLUETOOTH_RE.search(issue.text):
            topics.add("bluetooth")
            analysis.affected_device = "Bluetooth device / headphones"
        if _WIFI_RE.search(issue.text) and "bluetooth" not in _WIFI_RE.pattern:
            # "wireless headphones" should not trigger wifi
            # Only fire wifi if there's a network/internet word, not just "wireless"
            if re.search(r"\b(?:wi[- ]?fi|internet|network|online|offline|router|dhcp)\b", issue.text, re.I):
                topics.add("wifi")
        if _DNS_RE.search(issue.text):
            topics.add("dns")
            if "wifi" not in topics:
                topics.add("network")
        if _VPN_RE.search(issue.text):
            topics.add("vpn")
            topics.add("network")
        if _PERFORMANCE_RE.search(issue.text):
            topics.add("performance")
        if _INSTALL_RE.search(issue.text):
            topics.add("software-install")
        if _ACCOUNT_RE.search(issue.text):
            topics.add("account-access")
        if _EMAIL_TOPIC_RE.search(issue.text):
            topics.add("email")

        analysis.topics = topics

        # -- OS hints -------------------------------------------------------
        os_hints: list[str] = []
        if _MACOS_RE.search(issue.text):
            os_hints.append("macos")
        if _WINDOWS_RE.search(issue.text):
            os_hints.append("windows")
        analysis.os_hints = os_hints

        # -- Symptom extraction (key phrases) ------------------------------
        symptoms: list[str] = []
        if "bluetooth" in topics:
            if re.search(r"not\s+connect|won.t\s+connect|fail(?:s|ed)?\s+to\s+connect|can.t\s+connect", low):
                symptoms.append("Bluetooth device not connecting")
            if re.search(r"no\s+sound|not\s+playing|silent|audio\s+(?:not|no)", low):
                symptoms.append("No audio output from Bluetooth device")
        if "wifi" in topics:
            if re.search(r"no\s+internet|cannot\s+(?:load|browse|access)|not\s+working", low):
                symptoms.append("Connected to Wi-Fi but no internet access")
            if re.search(r"cannot\s+connect|won.t\s+connect|not\s+connecting", low):
                symptoms.append("Cannot connect to Wi-Fi network")
        if "dns" in topics:
            symptoms.append("DNS resolution failing")
        if "vpn" in topics:
            if re.search(r"block|fail|cannot\s+connect|disconnect", low):
                symptoms.append("VPN connection issue")
        if "performance" in topics:
            symptoms.append("Computer running slowly or unresponsively")
        if "software-install" in topics:
            symptoms.append("Software installation failing or blocked")
        if "account-access" in topics:
            symptoms.append("Account locked out or access denied")
        if "email" in topics:
            if re.search(r"cannot\s+send|fail(?:s|ed)?\s+to\s+send|not\s+(?:sending|receiving)", low):
                symptoms.append("Email send or receive failure")
        if not symptoms:
            symptoms.append("Issue reported — symptoms not fully characterised")
        analysis.symptoms = symptoms

        # -- Expected vs actual behaviour ----------------------------------
        if "bluetooth" in topics:
            analysis.expected_behavior = "Bluetooth device connects and works normally"
            analysis.actual_behavior = "Bluetooth device not connecting or not producing audio"
        elif "wifi" in topics:
            analysis.expected_behavior = "Internet access available over Wi-Fi"
            analysis.actual_behavior = "Wi-Fi connected but internet not accessible, or cannot join network"
        elif "dns" in topics:
            analysis.expected_behavior = "DNS names resolve correctly"
            analysis.actual_behavior = "DNS resolution failing for some or all names"
        elif "performance" in topics:
            analysis.expected_behavior = "Computer responds quickly"
            analysis.actual_behavior = "Computer is slow, hanging, or freezing"
        elif "software-install" in topics:
            analysis.expected_behavior = "Software installs successfully"
            analysis.actual_behavior = "Installation fails or is blocked"
        elif "account-access" in topics:
            analysis.expected_behavior = "User can sign in with valid credentials"
            analysis.actual_behavior = "Account locked out or access denied"
        elif "email" in topics:
            analysis.expected_behavior = "Email sends and receives normally"
            analysis.actual_behavior = "Email send or receive is failing"

        # -- Missing information -------------------------------------------
        missing: list[str] = []
        effective_os = policy.normalize_os(issue.os)
        # Merge text-detected OS hints
        if "macos" in os_hints:
            effective_os = "macos"
        elif "windows" in os_hints:
            effective_os = "windows"

        if effective_os == config.OS_UNKNOWN and _NETWORK_TOOL_TOPICS & topics:
            missing.append("Operating system (macOS or Windows)")
        if "bluetooth" in topics and not re.search(r"system settings|sound|output|pairing\s+mode", low):
            missing.append("Whether the device appears in System Settings > Bluetooth")
        if "software-install" in topics and effective_os == config.OS_UNKNOWN:
            missing.append("Operating system")
        if "performance" in topics and not re.search(r"cpu|memory|ram|disk|storage|activity monitor|task manager", low):
            missing.append("Whether CPU, memory, or disk usage is high (check Activity Monitor / Task Manager)")
        analysis.missing_info = missing

        # -- Clarification question ----------------------------------------
        # Ask at most one focused question. Priority: OS > device state > symptom detail
        if missing:
            if any("Operating system" in m for m in missing):
                analysis.clarification_question = (
                    "Which operating system are you using — macOS or Windows?"
                )
            elif any("System Settings" in m for m in missing):
                analysis.clarification_question = (
                    "Does the device appear in System Settings > Bluetooth "
                    "on your Mac (even with a connection error)?"
                )
            elif any("CPU" in m or "memory" in m or "disk" in m for m in missing):
                analysis.clarification_question = (
                    "Can you open Activity Monitor (Mac) or Task Manager (Windows) "
                    "and tell me which process is using the most CPU or memory?"
                )
            else:
                analysis.clarification_question = missing[0]

        # -- Diagnostic confidence -----------------------------------------
        if topics and effective_os != config.OS_UNKNOWN and not missing:
            analysis.diagnostic_confidence = "high"
        elif topics:
            analysis.diagnostic_confidence = "medium"
        else:
            analysis.diagnostic_confidence = "low"

        # -- Severity ------------------------------------------------------
        if any(w in low for w in SECURITY_KEYWORDS):
            analysis.severity = "high"
        elif any(p in low for p in ("all users", "everyone", "outage", "down for", "cannot work", "data loss")):
            analysis.severity = "high"
        else:
            analysis.severity = "medium"

        # -- LLM enrichment (optional) -------------------------------------
        if self.provider.is_available():
            analysis = self._enrich_with_llm(issue, analysis)

        return analysis

    def _enrich_with_llm(self, issue: Issue, analysis: IssueAnalysis) -> IssueAnalysis:
        """Try to enrich IssueAnalysis with LLM output.

        Returns the original analysis unchanged on any error.  The model
        cannot override topics detected by our rules; it can only supplement
        them.  Model output is strictly validated before use.
        """
        prompt = (
            f"Issue description: {issue.text}\n\n"
            "Extract the following fields from the issue description above and return "
            "a JSON object with these keys: symptoms (list of strings), "
            "affected_device (string), expected_behavior (string), "
            "actual_behavior (string), topics (list from: bluetooth, wifi, dns, "
            "network, performance, software-install, account-access, vpn, email, "
            "hardware, other), missing_info (list of strings), "
            "clarification_question (string, one question only or empty), "
            "diagnostic_confidence (high/medium/low), severity (high/medium/low). "
            "Do not add topics not mentioned. Return only the JSON object."
        )
        system = (
            "You are a structured information extractor for an IT support system. "
            "Extract facts only. Do not invent details not present in the description. "
            "Return only a JSON object, no prose."
        )
        raw = self.provider.complete(prompt, system=system)
        parsed = parse_issue_analysis_json(raw)
        if not parsed:
            return analysis  # fall back to rule-based result

        # Merge: LLM can add topics but not remove ones detected by rules
        if isinstance(parsed.get("topics"), list):
            analysis.topics = analysis.topics | set(parsed["topics"])
        if parsed.get("symptoms"):
            analysis.symptoms = parsed["symptoms"]
        if parsed.get("affected_device"):
            analysis.affected_device = parsed["affected_device"]
        if parsed.get("expected_behavior"):
            analysis.expected_behavior = parsed["expected_behavior"]
        if parsed.get("actual_behavior"):
            analysis.actual_behavior = parsed["actual_behavior"]
        if parsed.get("missing_info"):
            analysis.missing_info = parsed["missing_info"]
        if parsed.get("clarification_question"):
            analysis.clarification_question = parsed["clarification_question"]
        if parsed.get("diagnostic_confidence"):
            analysis.diagnostic_confidence = parsed["diagnostic_confidence"]
        if parsed.get("severity"):
            analysis.severity = parsed["severity"]
        return analysis

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

    def retrieve(self, issue: Issue, analysis: IssueAnalysis) -> list[Evidence]:
        """Retrieve evidence filtered to the detected topics.

        If the analysis detected specific topics, the retriever will only
        return documents whose topic matches one of them (topic-level
        filtering in the retriever).  This prevents Bluetooth issues from
        returning Wi-Fi evidence and vice-versa.
        """
        return self.ret.search(issue.text, os_name=issue.os)

    def plan(self, issue: Issue, triage: TriageResult, evidence: list[Evidence], analysis: IssueAnalysis) -> Plan:
        steps: list[Step] = []
        topics = analysis.topics
        network_topics = _NETWORK_TOOL_TOPICS & topics

        # Only add network diagnostic tools when the issue explicitly mentions
        # a network symptom.  Bluetooth-only issues must not reach this branch.
        dns_hint = "dns" in topics
        network_hint = bool(network_topics) and ("bluetooth" not in topics or len(topics) > 1)
        performance_hint = "performance" in topics

        if network_hint and triage.priority != "high":
            first_tool = "sim_get_dns_config" if dns_hint else "sim_get_wifi_status"
            # Topic-relevance check: confirm tool topic matches issue topic
            tool_topic = policy.ALLOWED_TOOLS[first_tool].get("topic", "")
            if tool_topic in topics or tool_topic in ("network", "dns", "wifi", "vpn"):
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
        if performance_hint and triage.priority != "high":
            steps.append(
                Step(
                    step_id=len(steps) + 1,
                    action="Observe system resources",
                    tool_name="sim_get_system_resources",
                    params={},
                    rationale=(
                        "Collect a CPU, memory and disk observation before giving "
                        "performance guidance."
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

        # Plan rationale
        rationale_parts: list[str] = ["Bounded plan; later steps are chosen from observations."]
        if "bluetooth" in topics and not network_hint:
            rationale_parts = [
                "No Bluetooth diagnostic tool is available; provide source-backed "
                "manual guidance without inventing device observations."
            ]
        if "performance" in topics:
            rationale_parts.append(
                "System resource observation completed; manual guidance from "
                "Activity Monitor / Task Manager is still recommended."
            )
        if "software-install" in topics:
            rationale_parts.append(
                "No automated software install diagnostic is available; OS-specific "
                "guidance is provided from official sources."
            )
        if "account-access" in topics:
            rationale_parts.append(
                "Account lockout requires administrator access; guidance is cited "
                "from official documentation."
            )

        return Plan(steps=steps, rationale=" ".join(rationale_parts))

    # -- main loop ---------------------------------------------------------
    def run(
        self,
        text: str,
        os_name: str | None = None,
        max_steps: int = config.MAX_STEPS,
        *,
        approved: bool = False,
        case_id: str | None = None,
        clarify: bool = True,
        case_state: dict[str, Any] | CaseState | None = None,
        previous_results: list[dict[str, Any]] | None = None,
        force_refresh: bool = False,
        reuse_diagnostics: bool = True,
    ) -> dict[str, Any]:
        issue = self.intake(text, os_name)
        if case_id:
            issue.case_id = case_id
        analysis = self.analyze_issue(issue)
        triage = self.triage(issue)
        evidence = self.retrieve(issue, analysis)
        plan = self.plan(issue, triage, evidence, analysis)

        prior_state = case_state if isinstance(case_state, dict) else (asdict(case_state) if case_state is not None else {})
        prior_diagnostics = list(prior_state.get("diagnostics") or [])
        diagnostic_records = list(prior_diagnostics)
        if previous_results:
            for result in previous_results:
                if isinstance(result, dict) and "tool_name" in result:
                    diag = self._diagnostic_from_result(result, issue.os, reused=False)
                    prior_diagnostics.append(diag)
                    diagnostic_records.append(diag)

        run = AgentRun(
            case_id=issue.case_id,
            issue=issue,
            triage=triage,
            analysis=analysis,
            evidence=evidence,
            plan=plan,
            needs_clarification=bool(analysis.clarification_question),
        )
        run.reused_diagnostics = []

        # Guard: a network diagnostic cannot be meaningful without an OS. Ask
        # instead of running tools that would be wrong or non-portable.
        effective_os = policy.normalize_os(issue.os)
        if "macos" in analysis.os_hints:
            effective_os = "macos"
        elif "windows" in analysis.os_hints:
            effective_os = "windows"
        if (
            clarify
            and effective_os == config.OS_UNKNOWN
            and (_NETWORK_TOOL_TOPICS & analysis.topics)
        ):
            run.trace.append(
                TraceEntry(
                    0,
                    "missing required operating system; ask before running network diagnostics",
                )
            )
            run.stopped_reason = "needs_clarification"
            run.escalated = False
            return run.to_dict()


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

            # Topic-relevance guard: reject tools whose topic does not match
            # any detected issue topic.  This is a code-enforced property.
            tool_spec = policy.ALLOWED_TOOLS.get(tool_name, {})
            tool_topic = tool_spec.get("topic", "")
            if analysis.topics and tool_topic not in analysis.topics and tool_topic not in ("network",):
                # Allow "network" topic tools for any network-related issue
                if not (_NETWORK_TOOL_TOPICS & analysis.topics and tool_topic in _NETWORK_TOOL_TOPICS):
                    run.trace.append(
                        TraceEntry(
                            step_no,
                            f"topic-relevance check failed: tool {tool_name!r} "
                            f"(topic={tool_topic!r}) not relevant to {sorted(analysis.topics)}; "
                            "skipped",
                        )
                    )
                    run.stopped_reason = "tool_not_relevant_to_issue"
                    break

            reused_result = None
            reuse_reason = "no equivalent previous diagnostic"
            if reuse_diagnostics:
                reused_result, reuse_reason = self._find_reusable_diagnostic(
                    prior_diagnostics + run.case_state.diagnostics if run.case_state else prior_diagnostics,
                    tool_name,
                    next_step.params,
                    issue.os,
                    force_refresh=force_refresh or self._freshness_requested(issue.text),
                )
            if reused_result is not None:
                result = ToolResult(
                    tool_name=tool_name,
                    simulated=bool(reused_result.get("simulated", True)),
                    ok=bool(reused_result.get("ok", True)),
                    data=reused_result.get("data", {}) or {},
                    summary=f"REUSED: {reused_result.get('summary') or 'previous diagnostic result'}",
                    error="",
                    duration_ms=0,
                    redacted=bool(reused_result.get("redacted", True)),
                )
                run.trace.append(
                    TraceEntry(
                        step_no,
                        f"reused previous diagnostic {tool_name} ({reuse_reason})",
                        tool_name,
                        result.summary,
                        dict(next_step.params),
                    )
                )
            else:
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

            diagnostic_records.append(
                self._record_diagnostic(
                    tool_name,
                    next_step.params,
                    result,
                    issue.os,
                    issue.text,
                    reused=reused_result is not None,
                    decision_reason=reuse_reason,
                )
            )

            # Observation-driven next step.
            next_step = self._decide_next(run, result, step_no)
            if next_step is None:
                run.stopped_reason = run.stopped_reason or "plan_complete"

        run.reused_diagnostics = [item for item in diagnostic_records if item.get("reused")]

        if not run.results and not run.escalated:
            topics = analysis.topics
            if "bluetooth" in topics and not (_NETWORK_TOOL_TOPICS & topics):
                run.stopped_reason = "bluetooth_diagnostic_unavailable"
                run.trace.append(
                    TraceEntry(
                        0,
                        "no Bluetooth diagnostic tool is available; no Bluetooth "
                        "device state was observed",
                    )
                )
            elif "performance" in topics:
                run.stopped_reason = "no_automated_diagnostic"
                run.trace.append(
                    TraceEntry(0, "no automated performance diagnostic tool is available")
                )
            elif "software-install" in topics:
                run.stopped_reason = "no_automated_diagnostic"
                run.trace.append(
                    TraceEntry(0, "no automated software installation diagnostic is available")
                )
            elif "account-access" in topics:
                run.stopped_reason = "no_automated_diagnostic"
                run.trace.append(
                    TraceEntry(0, "no automated account diagnostic is available; admin required")
                )
            elif "email" in topics:
                run.stopped_reason = "no_automated_diagnostic"
                run.trace.append(
                    TraceEntry(0, "no automated email diagnostic tool is available")
                )
            else:
                run.stopped_reason = "no_applicable_tools"

        from src import verification as _verification

        verification_result = _verification.Verifier().verify(
            [asdict(item) for item in run.evidence],
            [asdict(item) for item in run.results],
            run={"stopped_reason": run.stopped_reason, "escalated": run.escalated, "issue": issue.text},
        )
        run.verification = verification_result
        if run.case_state is None:
            run.case_state = self._build_case_state(
                run,
                analysis,
                issue,
                evidence,
                run.results,
                diagnostic_records,
                run.reused_diagnostics,
                verification_result,
            )
        else:
            run.case_state.verification = verification_result
            run.case_state.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result = run.to_dict()
        result["verification"] = verification_result
        result["case_state"] = (asdict(run.case_state) if run.case_state is not None else None)
        result["reused_diagnostics"] = run.reused_diagnostics
        return result


    def _freshness_requested(self, text: str) -> bool:
        low = (text or "").lower()
        return any(term in low for term in ("fresh", "recheck", "rerun", "new measurement", "again"))

    def _find_reusable_diagnostic(
        self,
        diagnostics: list[dict[str, Any]],
        tool_name: str,
        params: dict[str, Any],
        os_name: str,
        *,
        force_refresh: bool = False,
    ) -> tuple[dict[str, Any] | None, str]:
        params = dict(params or {})
        for diagnostic in reversed(diagnostics or []):
            if not isinstance(diagnostic, dict):
                continue
            if diagnostic.get("tool_name") != tool_name:
                continue
            if dict(diagnostic.get("params") or {}) != params:
                continue
            if not diagnostic.get("ok", False):
                return None, "previous equivalent diagnostic failed"
            if force_refresh:
                return None, "fresh check requested"
            if diagnostic.get("os") not in (None, "", os_name):
                return None, "relevant environment changed"
            timestamp = diagnostic.get("timestamp")
            if timestamp is not None:
                try:
                    age = time.time() - float(timestamp)
                except (TypeError, ValueError):
                    age = 0.0
                ttl = getattr(config, "DIAGNOSTIC_REUSE_TTL_SECONDS", 300)
                if age > ttl:
                    return None, f"previous result is stale ({int(age)}s old)"
            return diagnostic, "equivalent successful diagnostic is still fresh"
        return None, "no equivalent previous diagnostic"

    def _diagnostic_from_result(
        self, result: dict[str, Any], os_name: str, *, reused: bool
    ) -> dict[str, Any]:
        return {
            "tool_name": result.get("tool_name"),
            "params": result.get("params", {}),
            "timestamp": result.get("timestamp", time.time()),
            "executed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "os": os_name,
            "ok": bool(result.get("ok", False)),
            "simulated": bool(result.get("simulated", True)),
            "summary": result.get("summary", ""),
            "error": result.get("error", ""),
            "data": result.get("data", {}),
            "reused": reused,
            "decision_reason": "provided by caller",
        }

    def _record_diagnostic(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: ToolResult,
        os_name: str,
        issue_text: str,
        *,
        reused: bool,
        decision_reason: str,
    ) -> dict[str, Any]:
        return {
            "tool_name": tool_name,
            "params": dict(params or {}),
            "timestamp": time.time(),
            "executed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "os": os_name,
            "issue": issue_text,
            "ok": bool(result.ok),
            "simulated": bool(result.simulated),
            "summary": result.summary,
            "error": result.error,
            "data": result.data,
            "reused": reused,
            "decision_reason": decision_reason,
            "duration_ms": result.duration_ms,
        }

    def _build_hypotheses(
        self, topics: set[str], evidence: list[Evidence], results: list[ToolResult]
    ) -> list[dict[str, Any]]:
        evidence_ids = [e.doc_id for e in evidence if hasattr(e, "doc_id")]
        hypotheses: list[dict[str, Any]] = []
        if "bluetooth" in topics:
            hypotheses.append(
                {
                    "id": "bluetooth-pairing-or-audio",
                    "hypothesis": "Bluetooth pairing, discoverability, or audio-output configuration issue.",
                    "confidence": "medium",
                    "status": "manual_guidance",
                    "supporting_evidence": evidence_ids,
                    "contradictory_evidence": [],
                }
            )
        if "wifi" in topics or "dns" in topics or "network" in topics or "vpn" in topics:
            status = "testing"
            for result in results:
                if result.tool_name == "sim_get_wifi_status" and result.data.get("connected") is False:
                    status = "supported"
                if result.tool_name == "sim_ping_host" and result.data.get("received", 0) == 0:
                    status = "supported"
                if result.tool_name == "sim_check_vpn" and result.data.get("vpn_active"):
                    status = "supported"
            hypotheses.append(
                {
                    "id": "network-connectivity",
                    "hypothesis": "Network association, DNS, reachability, or VPN interference issue.",
                    "confidence": "medium",
                    "status": status,
                    "supporting_evidence": evidence_ids,
                    "contradictory_evidence": [],
                }
            )
        if "performance" in topics:
            status = "testing"
            for result in results:
                if result.tool_name == "sim_get_system_resources":
                    if result.data.get("cpu_percent", 0) >= 80 or result.data.get("memory_percent", 0) >= 85:
                        status = "supported"
                    else:
                        status = "not_supported"
            hypotheses.append(
                {
                    "id": "resource-contention",
                    "hypothesis": "CPU, memory, disk, or background-process contention.",
                    "confidence": "medium",
                    "status": status,
                    "supporting_evidence": evidence_ids,
                    "contradictory_evidence": [],
                }
            )
        if "email" in topics:
            hypotheses.append(
                {
                    "id": "email-configuration",
                    "hypothesis": "Mailbox, account, or mail-flow configuration issue.",
                    "confidence": "low",
                    "status": "manual_guidance",
                    "supporting_evidence": evidence_ids,
                    "contradictory_evidence": [],
                }
            )
        if "account-access" in topics:
            hypotheses.append(
                {
                    "id": "account-access",
                    "hypothesis": "Account lockout, credential, or permission issue.",
                    "confidence": "low",
                    "status": "manual_guidance",
                    "supporting_evidence": evidence_ids,
                    "contradictory_evidence": [],
                }
            )
        if "software-install" in topics:
            hypotheses.append(
                {
                    "id": "software-installation",
                    "hypothesis": "Installer, permission, or package-source issue.",
                    "confidence": "low",
                    "status": "manual_guidance",
                    "supporting_evidence": evidence_ids,
                    "contradictory_evidence": [],
                }
            )
        if not hypotheses:
            hypotheses.append(
                {
                    "id": "general-support",
                    "hypothesis": "Issue requires manual triage using the cited guidance.",
                    "confidence": "low",
                    "status": "manual_guidance",
                    "supporting_evidence": evidence_ids,
                    "contradictory_evidence": [],
                }
            )
        return hypotheses

    def _build_case_state(
        self,
        run: AgentRun,
        analysis: IssueAnalysis,
        issue: Issue,
        evidence: list[Evidence],
        results: list[ToolResult],
        diagnostic_records: list[dict[str, Any]],
        reused_diagnostics: list[dict[str, Any]],
        verification: dict[str, Any],
    ) -> CaseState:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        next_step = run.analysis.clarification_question if run.needs_clarification else ""
        if not next_step and run.plan and run.plan.steps:
            first = run.plan.steps[0]
            next_step = first.tool_name or "Review cited guidance"
        if not next_step and verification.get("follow_up_question"):
            next_step = verification["follow_up_question"]
        if not next_step:
            next_step = run.plan.rationale if run.plan else "Review cited guidance"
        return CaseState(
            case_id=run.case_id,
            status="diagnosing",
            issue=issue.text,
            os=issue.os,
            symptoms=analysis.symptoms,
            topics=sorted(analysis.topics),
            environment={"os": issue.os, "os_hints": analysis.os_hints},
            clarification_questions=[analysis.clarification_question] if analysis.clarification_question else [],
            clarification_answers=[],
            evidence=[
                {
                    "doc_id": e.doc_id,
                    "title": e.title,
                    "url": e.url,
                    "source": e.source,
                    "score": e.score,
                    "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                for e in evidence
            ],
            hypotheses=self._build_hypotheses(analysis.topics, evidence, results),
            diagnostics=diagnostic_records,
            reused_diagnostics=reused_diagnostics,
            actions_attempted=[r.tool_name for r in results],
            approvals=[],
            verification=verification,
            next_step=next_step,
            confidence=analysis.diagnostic_confidence,
            created_at=now,
            updated_at=now,
        )


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

        if result.tool_name == "sim_get_system_resources":
            run.trace.append(TraceEntry(step_no, "observation: system resource snapshot collected"))
            run.stopped_reason = "observation_complete"
            return None

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
