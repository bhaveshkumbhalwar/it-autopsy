"""Streamlit UI for the IT Autopsy Autonomous IT Help Desk Agent.

<<<<<<< HEAD
The UI is a thin shell over the same deterministic pipeline used by the tests
and evaluation. All rendering goes through Streamlit's escaping (no
``unsafe_allow_html``), and user/ticket text is never interpreted as markup or
instructions.....
=======
The UI is a thin shell over the deterministic agent pipeline. It uses the
existing backend, case store, retrieval system and evaluation reports.
>>>>>>> d4d3633 (updated ui)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from src import agent, config, database, llm_provider, policy, retrieval, tools, verification

st.set_page_config(
    page_title="IT Autopsy",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
        --ia-navy: #0f1f3d;
        --ia-blue: #2563eb;
        --ia-slate: #475569;
        --ia-border: #dbe3ef;
        --ia-surface: #ffffff;
        --ia-muted: #f5f7fb;
    }
    .stApp { background: linear-gradient(180deg, #f8fafc 0%, #eef3f9 100%); }
    [data-testid="stSidebar"] { background: var(--ia-navy); color: #e2e8f0; }
    [data-testid="stSidebar"] * { color: #e2e8f0; }
    .ia-title { font-size: 1.75rem; font-weight: 700; color: #0f172a; margin-bottom: 0.2rem; }
    .ia-subtitle { color: var(--ia-slate); margin-bottom: 1rem; }
    .ia-card {
        background: var(--ia-surface);
        border: 1px solid var(--ia-border);
        border-radius: 0.85rem;
        padding: 1rem 1.1rem;
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05);
        margin-bottom: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _safe_text(value, fallback: str = "") -> str:
    if value is None:
        return fallback
    text, _ = tools.redact(str(value))
    return text.strip() or fallback


def _case_status_for_outcome(outcome: str | None) -> str:
    return {
        config.OUTCOME_VERIFIED: "resolved",
        config.OUTCOME_NOT_RESOLVED: "escalated",
        config.OUTCOME_ESCALATED: "escalated",
        config.OUTCOME_NEEDS_CONFIRMATION: "awaiting_user",
        config.OUTCOME_UNABLE: "awaiting_user",
    }.get(outcome or "", "open")


def _assistant_summary(run: dict, ver: dict) -> str:
    triage = run.get("triage") or {}
    parts = [
        f"Category: {_safe_text(triage.get('category'), 'Unknown')}",
        f"Priority: {_safe_text(triage.get('priority'), 'Unknown')}",
        f"Outcome: {_safe_text(ver.get('outcome'), 'unknown')}",
    ]
    evidence = run.get("evidence") or []
    if evidence:
        parts.append("Evidence: " + ", ".join(_safe_text(e.get("title"), "source") for e in evidence if isinstance(e, dict)))
    if run.get("stopped_reason"):
        parts.append(f"Stopped: {_safe_text(run.get('stopped_reason'))}")
    return " | ".join(parts)


def _log_run(db: database.DB, run: dict, ver: dict) -> None:
    params_by_tool = {
        t["tool_name"]: t.get("params", {})
        for t in run.get("trace", [])
        if t.get("tool_name")
    }
    for result in run.get("results", []):
        db.log_event(
            run["case_id"],
            "tool_call",
            result["tool_name"],
            params_by_tool.get(result["tool_name"], {}),
            "ok" if result["ok"] else "failed",
            result.get("summary") or result.get("error", ""),
            [],
            {},
        )
    db.log_event(
        run["case_id"],
        "verification",
        None,
        {},
        ver["outcome"],
        ver["reason"],
        [e["doc_id"] for e in run.get("evidence", [])],
        {},
    )


def _render_sources(evidence: list[dict]) -> None:
    if not evidence:
        st.info("No sufficiently relevant official source was found.")
        return
    for item in evidence:
        if not isinstance(item, dict):
            continue
        title = _safe_text(item.get("title"), "Source")
        source = _safe_text(item.get("source"), "Official source").capitalize()
        url = _safe_text(item.get("url"))
        if url.startswith("https://"):
            st.markdown(f"- [{title}]({url}) — {source}")
        else:
            st.markdown(f"- **{title}** — {source}")
        snippet = _safe_text(item.get("snippet"))
        if snippet:
            st.caption(snippet)


def _tool_label(tool_name: str) -> str:
    labels = {
        "sim_get_wifi_status": "Check Wi-Fi status",
        "sim_get_dns_config": "Check DNS configuration",
        "sim_ping_host": "Test network reachability",
        "sim_check_vpn": "Check VPN status",
        "sim_get_system_resources": "Check system resources",
        "macos_wifi_status": "Read macOS Wi-Fi status",
        "macos_dns_config": "Read macOS DNS configuration",
        "macos_network_info": "Read macOS network information",
        "macos_system_resources": "Read macOS memory statistics",
    }
    return labels.get(tool_name, "Diagnostic action")


def _render_actions(results: list[dict]) -> None:
    if not results:
        st.caption("No diagnostic action was run.")
        return
    rows = []
    for result in results:
        if not isinstance(result, dict):
            continue
        detail = _safe_text(result.get("summary") or result.get("error"), "No result was returned.")
        if result.get("simulated"):
            detail = f"Simulated observation — {detail}"
        elif result.get("ok"):
            detail = f"Read-only observation — {detail}"
        rows.append(
            {
                "Action": _tool_label(_safe_text(result.get("tool_name"))),
                "Status": "Completed" if result.get("ok") else "Could not complete",
                "Result": detail,
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")


def _render_run(run: dict) -> None:
    triage = run.get("triage") or {}
    category = _safe_text(triage.get("category"), "Unknown")
    st.markdown("### Issue summary")
    issue_summary = _safe_text(run.get("issue"), "Issue description unavailable.")
    if len(issue_summary) > 240:
        issue_summary = issue_summary[:237].rsplit(" ", 1)[0] + "..."
    st.write(issue_summary)
    c1, c2 = st.columns(2)
    c1.metric("Category", category)
    c2.metric("Priority", _safe_text(triage.get("priority"), "Unknown"))
    category_explanation = config.CATEGORY_DESCRIPTIONS.get(category, "Assigned based on the ticket text.")
    rationale = "; ".join(_safe_text(item) for item in (triage.get("priority_rationale") or []) if item)
    explanation = f"Category: {category_explanation}"
    if rationale:
        explanation += f" Priority: {rationale}."
    st.caption(explanation)

<<<<<<< HEAD
    st.markdown("#### Evidence (official sources only)")
    if run["evidence"]:
        for e in run["evidence"]:
            st.markdown(
                f"- **{E['title']}** — {e['source']} — score {e['score']:.3f}  \n"
                f"  <{e['url']}>  \n  {e['citation']}",
                unsafe_allow_html=False,
            )
=======
    st.markdown("#### Relevant evidence")
    evidence = run.get("evidence") or []
    if not evidence:
        st.info("No sufficiently relevant official source was found.")
>>>>>>> d4d3633 (updated ui)
    else:
        for item in evidence:
            if not isinstance(item, dict):
                continue
            title = _safe_text(item.get("title"), "Source")
            source = _safe_text(item.get("source"), "Official source").capitalize()
            url = _safe_text(item.get("url"))
            if url.startswith("https://"):
                st.markdown(f"- [{title}]({url}) — {source}")
            else:
                st.markdown(f"- **{title}** — {source}")
            snippet = _safe_text(item.get("snippet"))
            if snippet:
                st.caption(snippet)

    _render_actions(run.get("results") or [])

    case_state = run.get("case_state") or {}
    hypotheses = case_state.get("hypotheses") or []
    if hypotheses:
        st.markdown("#### Current hypotheses")
        for hypothesis in hypotheses:
            if not isinstance(hypothesis, dict):
                continue
            text = _safe_text(hypothesis.get("hypothesis"))
            if text:
                st.write(f"- {text} (confidence: {_safe_text(hypothesis.get('confidence'), 'unknown')}, status: {_safe_text(hypothesis.get('status'), 'unknown')})")

    reused = run.get("reused_diagnostics") or []
    if reused:
        st.markdown("#### Reused diagnostics")
        for item in reused:
            if not isinstance(item, dict):
                continue
            label = _tool_label(_safe_text(item.get("tool_name")))
            reason = _safe_text(item.get("decision_reason"), "equivalent prior result reused")
            st.caption(f"{label}: {reason}")

    next_step = _safe_text(case_state.get("next_step"))
    if next_step:
        st.markdown("#### Next recommended step")
        st.write(next_step)

    st.markdown("#### Recommended next steps")
    if evidence:
        for item in evidence:
            if not isinstance(item, dict):
                continue
            snippet = _safe_text(item.get("snippet"))
            if snippet:
                st.markdown(f"- {snippet}")
    else:
        st.write("Contact support for guidance; no source-backed next step is available.")

    if run.get("stopped_reason") == "bluetooth_diagnostic_unavailable":
        st.info("No Bluetooth diagnostic tool is available. The guidance above comes from Apple's support article.")

    with st.expander("View technical details"):
        for entry in run.get("trace") or []:
            decision = _safe_text(entry.get("decision"))
            if decision:
                st.write(f"{entry.get('step', '')}. {decision}")


def _show_verification(db: database.DB, run: dict, ver: dict) -> None:
    st.markdown("#### Verification status")
    st.write(f"**{_safe_text(ver.get('outcome'), 'Not verified')}**")
    reason = _safe_text(ver.get("reason"))
    if reason:
        st.write(reason)
    if ver.get("follow_up_question"):
        st.info(_safe_text(ver["follow_up_question"]))
    if ver["outcome"] == config.OUTCOME_NEEDS_CONFIRMATION:
        st.caption("Tool success is not proof of resolution. Please confirm.")
        col1, col2 = st.columns(2)
        if col1.button("Yes, it is resolved", key="confirm_yes"):
            updated = verification.Verifier().confirm(ver, True)
            db.update_outcome(run["case_id"], updated["outcome"])
            db.log_event(run["case_id"], "user_confirmation", None, {}, updated["outcome"], updated["reason"], [], {})
            st.session_state["ver"] = updated
            st.session_state["flash"] = "Recorded as resolved by the user."
            st.rerun()
        if col2.button("No, still broken", key="confirm_no"):
            updated = verification.Verifier().confirm(ver, False)
            db.update_outcome(run["case_id"], updated["outcome"])
            db.log_event(run["case_id"], "user_confirmation", None, {}, updated["outcome"], updated["reason"], [], {})
            st.session_state["ver"] = updated
            st.session_state["flash"] = "Recorded as not resolved. Escalation handoff is below."
            st.rerun()


def _render_escalation(run: dict, esc: dict, ver: dict) -> None:
    run = run or {}
    esc = esc or {}
    ver = ver or {}
    st.markdown("### Escalation summary")
    summary = _safe_text(esc.get("summary"))
    if summary:
        st.write(summary)
    col1, col2, col3 = st.columns(3)
    col1.metric("Category", _safe_text(esc.get("category"), "Not provided"))
    col2.metric("Priority", _safe_text(esc.get("priority"), "Not provided"))
    col3.metric("To", _safe_text(esc.get("to"), "Support"))
    symptoms = _safe_text(esc.get("symptoms"))
    if symptoms:
        st.markdown("#### Reported symptoms")
        st.write(symptoms)
    _render_actions(run.get("results") or [])
    st.markdown("#### Relevant sources")
    _render_sources(run.get("evidence") or [])
    reason = _safe_text(esc.get("not_attempted_reason"), "Troubleshooting stopped before completion.")
    st.markdown("#### Why this was escalated")
    st.write(reason)
    question = _safe_text(ver.get("follow_up_question"))
    next_steps = []
    for step in esc.get("next_steps") or []:
        step_text = _safe_text(step)
        if step_text and step_text != question:
            next_steps.append(step_text)
    if next_steps:
        st.markdown("#### Recommended next steps")
        for step in next_steps:
            st.markdown(f"- {step}")
    if question:
        st.warning(question)


def _assistant_summary(run: dict, ver: dict) -> str:
    triage = run.get("triage") or {}
    parts = [
        f"Category: {_safe_text(triage.get('category'), 'Unknown')}",
        f"Priority: {_safe_text(triage.get('priority'), 'Unknown')}",
        f"Outcome: {_safe_text(ver.get('outcome'), 'unknown')}",
    ]
    evidence = run.get("evidence") or []
    if evidence:
        parts.append("Evidence: " + ", ".join(_safe_text(e.get("title"), "source") for e in evidence if isinstance(e, dict)))
    if run.get("stopped_reason"):
        parts.append(f"Stopped: {_safe_text(run.get('stopped_reason'))}")
    return " | ".join(parts)


def _execute_case(issue_text: str, os_choice: str, allow_real: bool, approved: bool, active_case_id: str | None) -> None:
    try:
        a = agent.Agent(os_name=os_choice, allow_real=allow_real)
        with st.spinner("Running intake → triage → retrieve → plan → execute → verify..."):
            run = a.run(issue_text, os_name=os_choice, approved=approved, case_id=active_case_id)
        ver = verification.Verifier().verify(run["evidence"], run["results"], run=run)
        esc = verification.Escalator().build(run, ver)
        db = database.DB()
        db.save_case(
            run["case_id"],
            issue_text,
            os_choice,
            run["triage"],
            ver["outcome"],
            status=_case_status_for_outcome(ver["outcome"]),
            analysis=run.get("analysis"),
            state=run.get("case_state"),
            symptoms=(run.get("analysis") or {}).get("symptoms", []),
        )
        _log_run(db, run, ver)
        db.update_conversation(
            run["case_id"],
            (st.session_state.get("conversation") or [])
            + [
                {"role": "user", "content": issue_text},
                {"role": "assistant", "content": _assistant_summary(run, ver)},
            ],
        )
        st.session_state["conversation"] = (
            (st.session_state.get("conversation") or [])
            + [
                {"role": "user", "content": issue_text},
                {"role": "assistant", "content": _assistant_summary(run, ver)},
            ]
        )
        st.session_state["run"] = run
        st.session_state["ver"] = ver
        st.session_state["esc"] = esc
        st.session_state["active_case_id"] = run["case_id"]
        st.session_state["original_issue"] = issue_text
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not run the agent: {type(exc).__name__}")


def _render_case_table(rows: list) -> None:
    if not rows:
        st.info("No cases match the current filters.")
        return
    enriched = []
    db = database.DB()
    for original in rows:
        case = db.get_case(original["id"])
        triage = (case or {}).get("triage") or {}
        enriched.append(
            {
                "Case": original["id"][:8],
                "Status": original["status"] or "open",
                "Priority": _safe_text(triage.get("priority"), "unknown"),
                "Outcome": original["outcome"] or "pending",
                "OS": original["os"] or "unknown",
                "Issue": (original["issue_text"] or "")[:90],
                "Created": original["created_at"],
            }
        )
    st.dataframe(enriched, hide_index=True, width="stretch")


def _chat_response(run: dict, ver: dict) -> str:
    triage = run.get("triage") or {}
    category = _safe_text(triage.get("category"), "Unknown")
    priority = _safe_text(triage.get("priority"), "Unknown")
    lines = [
        f"**Likely category:** {category}",
        f"**Priority:** {priority}",
    ]
    category_explanation = config.CATEGORY_DESCRIPTIONS.get(category)
    if category_explanation:
        lines.append(f"_{_safe_text(category_explanation)}_")

    evidence = run.get("evidence") or []
    if evidence:
        lines.append("")
        lines.append("**Relevant sources**")
        for item in evidence:
            if not isinstance(item, dict):
                continue
            title = _safe_text(item.get("title"), "Source")
            url = _safe_text(item.get("url"))
            if url.startswith("https://"):
                lines.append(f"- [{title}]({url})")
            else:
                lines.append(f"- {title}")
            snippet = _safe_text(item.get("snippet"))
            if snippet:
                lines.append(f"  - {snippet}")
    else:
        lines.append("")
        lines.append("No sufficiently relevant official source was found, so I will not guess a fix.")

    results = run.get("results") or []
    if results:
        lines.append("")
        lines.append("**Actions attempted**")
        for result in results:
            if not isinstance(result, dict):
                continue
            label = _tool_label(_safe_text(result.get("tool_name")))
            status = "completed" if result.get("ok") else "could not complete"
            detail = _safe_text(result.get("summary") or result.get("error"), "No result returned.")
            if result.get("simulated"):
                detail = f"Simulated observation — {detail}"
            elif result.get("ok"):
                detail = f"Read-only observation — {detail}"
            lines.append(f"- {label}: {status}. {detail}")
    else:
        lines.append("")
        lines.append("No diagnostic action was run. Tool observations are only used when they are relevant to the reported issue.")

    if run.get("stopped_reason") == "bluetooth_diagnostic_unavailable":
        lines.append("")
        lines.append(
            "No Bluetooth diagnostic tool is available here, so no Bluetooth device "
            "state was inspected. The steps above come from Apple’s guide."
        )

    case_state = run.get("case_state") or {}
    hypotheses = case_state.get("hypotheses") or []
    if hypotheses:
        lines.append("")
        lines.append("**Current hypotheses**")
        for hypothesis in hypotheses:
            if not isinstance(hypothesis, dict):
                continue
            text = _safe_text(hypothesis.get("hypothesis"))
            if text:
                lines.append(f"- {text} (confidence: {_safe_text(hypothesis.get('confidence'), 'unknown')})")

    reused = run.get("reused_diagnostics") or []
    if reused:
        lines.append("")
        lines.append("**Reused diagnostics**")
        for item in reused:
            if not isinstance(item, dict):
                continue
            label = _tool_label(_safe_text(item.get("tool_name")))
            reason = _safe_text(item.get("decision_reason"), "equivalent prior result reused")
            lines.append(f"- {label}: {reason}")

    next_step = _safe_text(case_state.get("next_step"))
    if next_step:
        lines.append("")
        lines.append(f"**Next recommended step:** {next_step}")

    next_steps = []
    for item in (run.get("evidence") or []):
        if not isinstance(item, dict):
            continue
        snippet = _safe_text(item.get("snippet"))
        if snippet:
            next_steps.append(snippet)
    if next_steps:
        lines.append("")
        lines.append("**Recommended next steps**")
        for step in next_steps:
            lines.append(f"- {step}")

    lines.append("")
    ver = run.get("verification") or {}
    lines.append(f"**Verification status:** {_safe_text(ver.get('outcome') if isinstance(ver, dict) else None, 'not verified')}")
    reason = _safe_text(ver.get("reason")) if isinstance(ver, dict) else ""
    if reason:
        lines.append(reason)
    question = _safe_text(ver.get("follow_up_question")) if isinstance(ver, dict) else ""
    if question:
        lines.append(f"**Please confirm:** {question}")

    return "\n".join(lines)




def page_help_desk() -> None:
    st.markdown('<div class="ia-title">AI Help Desk</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="ia-subtitle">Describe an IT issue. The agent will triage, retrieve cited guidance, run only relevant diagnostics, and ask for verification.</div>',
        unsafe_allow_html=True,
    )
    col1, col2 = st.columns([1.55, 1])
    with col1:
        st.text_area(
            "Describe the issue in your own words",
            height=110,
            key="issue_text",
            placeholder="e.g. My Mac connects to Wi-Fi but web pages do not load",
        )
        c1, c2 = st.columns(2)
        os_choice = c1.selectbox("Operating system", ["unknown", "macos", "windows"], key="os_choice", index=1)
        allow_real = c2.checkbox("Allow read-only real diagnostics (macOS, approval-gated)", key="allow_real")
        approved = st.checkbox("I approve state-changing tools for this run (demo only)", key="approved")
        if st.button("Run agent", type="primary", key="run_agent"):
            issue_text = st.session_state.get("issue_text", "")
            if not issue_text.strip():
                st.warning("Please describe the issue.")
            else:
                _execute_case(issue_text, os_choice, allow_real, approved, st.session_state.get("active_case_id"))

        prompt = st.chat_input("Or ask the help desk a question", key="help_chat_input")
        if prompt:
            _execute_case(prompt, os_choice, allow_real, approved, st.session_state.get("active_case_id"))
            run = st.session_state.get("run")
            ver = st.session_state.get("ver")
            if run and ver:
                with st.chat_message("assistant"):
                    st.markdown(_chat_response(run, ver))
                    _render_sources(run.get("evidence") or [])

        run = st.session_state.get("run")
        if run:
            st.divider()
            flash = st.session_state.pop("flash", None)
            if flash:
                st.success(flash)
            st.caption(f"Case {run['case_id']}")
            conversation = st.session_state.get("conversation") or []
            if conversation:
                with st.expander("Conversation history"):
                    for message in conversation:
                        role = _safe_text(message.get("role"), "user").capitalize()
                        st.write(f"**{role}:** {_safe_text(message.get('content'))}")
            analysis = run.get("analysis") or {}
            question = _safe_text(analysis.get("clarification_question"))
            if question:
                st.info(f"Follow-up question: {question}")
            _render_run(run)
            ver = st.session_state["ver"]
            _show_verification(database.DB(), run, ver)
            if run.get("escalated") or ver.get("outcome") in {
                config.OUTCOME_ESCALATED,
                config.OUTCOME_NOT_RESOLVED,
            }:
                _render_escalation(run, st.session_state.get("esc") or {}, ver)

            with st.form("follow_up_form"):
                follow_up = st.text_input("Add details or answer the follow-up question", key="follow_up_text")
                submitted = st.form_submit_button("Continue case")
            if submitted and follow_up.strip():
                original = st.session_state.get("original_issue") or run.get("issue") or ""
                combined = f"{original}\nFollow-up: {follow_up.strip()}"
                try:
                    a = agent.Agent(os_name=os_choice, allow_real=allow_real)
                    with st.spinner("Updating case with new details..."):
                        new_run = a.run(
                            combined,
                            os_name=os_choice,
                            approved=approved,
                            case_id=st.session_state.get("active_case_id"),
                        )
                    new_ver = verification.Verifier().verify(new_run["evidence"], new_run["results"], run=new_run)
                    new_esc = verification.Escalator().build(new_run, new_ver)
                    db = database.DB()
                    db.save_case(
                        new_run["case_id"],
                        combined,
                        os_choice,
                        new_run["triage"],
                        new_ver["outcome"],
                        status=_case_status_for_outcome(new_ver["outcome"]),
                        analysis=new_run.get("analysis"),
                        state=new_run.get("case_state"),
                        symptoms=(new_run.get("analysis") or {}).get("symptoms", []),
                    )
                    _log_run(db, new_run, new_ver)
                    st.session_state["conversation"] = st.session_state.get("conversation", []) + [
                        {"role": "user", "content": follow_up.strip()},
                        {"role": "assistant", "content": _assistant_summary(new_run, new_ver)},
                    ]
                    db.update_conversation(new_run["case_id"], st.session_state["conversation"])
                    st.session_state["run"] = new_run
                    st.session_state["ver"] = new_ver
                    st.session_state["esc"] = new_esc
                    st.session_state["original_issue"] = combined
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not update the case: {type(exc).__name__}")

    with col2:
        st.markdown("#### Live case context")
        active_case_id = st.session_state.get("active_case_id")
        if active_case_id:
            st.caption(f"Continuing case {active_case_id[:8]}")
        run = st.session_state.get("run")
        if run:
            case_state = run.get("case_state") or {}
            st.write(f"**Category:** {_safe_text((run.get('triage') or {}).get('category'), 'Unknown')}")
            st.write(f"**Priority:** {_safe_text((run.get('triage') or {}).get('priority'), 'Unknown')}")
            st.write(f"**Status:** {_safe_text(run.get('stopped_reason'), 'running')}")
            hypotheses = case_state.get("hypotheses") or []
            if hypotheses:
                st.markdown("**Hypotheses**")
                for hypothesis in hypotheses:
                    if isinstance(hypothesis, dict):
                        text = _safe_text(hypothesis.get("hypothesis"))
                        if text:
                            st.caption(f"- {text}")
            next_step = _safe_text(case_state.get("next_step"))
            if next_step:
                st.markdown("**Next step**")
                st.caption(next_step)
        else:
            st.caption("No active case yet. Submit an issue to begin.")


def page_cases() -> None:
    st.markdown('<div class="ia-title">Incident Cases</div>', unsafe_allow_html=True)
    st.markdown('<div class="ia-subtitle">Search, filter, reopen and continue persisted cases.</div>', unsafe_allow_html=True)
    db = database.DB()
    query = st.text_input("Search cases", key="case_search", placeholder="Search by issue, status, outcome or case ID")
    c1, c2 = st.columns(2)
    status_filter = c1.selectbox("Status", ["all", "open", "investigating", "awaiting_user", "resolved", "escalated"], key="case_status_filter")
    priority_filter = c2.selectbox("Priority", ["all", "high", "medium", "low", "unknown"], key="case_priority_filter")
    rows = db.search_cases(
        query=query,
        status=None if status_filter == "all" else status_filter,
        priority=None if priority_filter == "all" else priority_filter,
    )
    _render_case_table(rows)

    if not rows:
        return
    selected_id = st.selectbox("Open case", [row["id"] for row in rows], format_func=lambda value: value[:8], key="selected_case")
    case = db.get_case(selected_id)
    if not case:
        return
    st.markdown("#### Case details")
    st.write(f"**Issue:** {_safe_text(case.get('issue_text'))}")
    st.write(f"**OS:** {_safe_text(case.get('os'), 'unknown')}")
    st.write(f"**Status:** {_safe_text(case.get('status'), 'open')}")
    triage = case.get("triage") or {}
    st.write(f"**Category:** {_safe_text(triage.get('category'), 'Unknown')}")
    st.write(f"**Priority:** {_safe_text(triage.get('priority'), 'Unknown')}")
    state = case.get("state") or {}
    hypotheses = state.get("hypotheses") or []
    if hypotheses:
        st.markdown("#### Hypotheses")
        for hypothesis in hypotheses:
            if isinstance(hypothesis, dict):
                text = _safe_text(hypothesis.get("hypothesis"))
                if text:
                    st.write(f"- {text}")
    diagnostics = state.get("diagnostics") or []
    if diagnostics:
        st.markdown("#### Diagnostic history")
        st.dataframe(
            [
                {
                    "Tool": _tool_label(_safe_text(item.get("tool_name"))),
                    "Status": "Completed" if item.get("ok") else "Failed",
                    "Reused": "Yes" if item.get("reused") else "No",
                    "Result": _safe_text(item.get("summary") or item.get("error")),
                }
                for item in diagnostics
                if isinstance(item, dict)
            ],
            hide_index=True,
            width="stretch",
        )
    conversation = case.get("conversation") or []
    if conversation:
        st.markdown("#### Conversation")
        for message in conversation:
            if not isinstance(message, dict):
                continue
            role = _safe_text(message.get("role"), "user").capitalize()
            st.write(f"**{role}:** {_safe_text(message.get('content'))}")
    if st.button("Continue this case in Help Desk", key="continue_selected_case"):
        st.session_state["active_case_id"] = selected_id
        st.session_state["issue_text"] = case.get("issue_text") or ""
        st.session_state["conversation"] = conversation
        st.session_state["flash"] = f"Loaded case {selected_id[:8]} into AI Help Desk."
        st.rerun()


def page_diagnostics() -> None:
    st.markdown('<div class="ia-title">Diagnostics</div>', unsafe_allow_html=True)
    st.markdown('<div class="ia-subtitle">Registered tools, compatibility, latest results and diagnostic history.</div>', unsafe_allow_html=True)
    rows = []
    for name, spec in policy.ALLOWED_TOOLS.items():
        rows.append(
            {
                "Tool": name,
                "Topic": spec.get("topic", ""),
                "Risk": spec.get("risk", ""),
                "Simulated": "Yes" if spec.get("simulated") else "No",
                "Approval": "Required" if spec.get("requires_approval") else "No",
                "OS support": ", ".join(spec.get("os", [])),
                "Purpose": spec.get("purpose", ""),
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")

    st.markdown("#### Knowledge search")
    query = st.text_input("Search knowledge", key="kb_query", placeholder="e.g. Bluetooth headphones will not connect")
    os_choice = st.selectbox("Operating system", ["unknown", "macos", "windows"], key="kb_os", index=1)
    if query.strip():
        retriever = retrieval.Retriever()
        evidence = retriever.search(query, os_name=os_choice)
        if evidence:
            for item in evidence:
                title = _safe_text(item.title, "Source")
                url = _safe_text(item.url)
                if url.startswith("https://"):
                    st.markdown(f"- [{title}]({url})")
                else:
                    st.markdown(f"- **{title}**")
                snippet = _safe_text(item.snippet)
                if snippet:
                    st.caption(snippet)
        else:
            st.warning("No relevant knowledge was found for this query.")

    db = database.DB()
    cases = db.list_cases(limit=50)
    if not cases:
        st.info("No diagnostic history yet.")
        return
    selected_case = st.selectbox("Case", [row["id"] for row in cases], format_func=lambda value: value[:8], key="diagnostic_case")
    events = db.list_events(selected_case)
    tool_events = [event for event in events if event["event_type"] == "tool_call"]
    if tool_events:
        st.dataframe(
            [
                {
                    "Time": event["ts"],
                    "Tool": event["tool_name"],
                    "Status": event["status"],
                    "Result": _safe_text(event["result_summary"]),
                }
                for event in tool_events
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("No diagnostic events recorded for this case.")
    st.markdown("#### Recent failures")
    failures = [event for event in db.recent_events(50) if event["event_type"] == "tool_call" and event["status"] != "ok"]
    if failures:
        for event in failures:
            st.write(f"- {event['ts']} · {event['case_id'][:8]} · {event['tool_name']} · {event['result_summary']}")
    else:
        st.caption("No tool failures recorded.")


def page_operations() -> None:
    st.markdown('<div class="ia-title">Operations</div>', unsafe_allow_html=True)
    st.markdown('<div class="ia-subtitle">Compact case health, agent performance and administrative diagnostics.</div>', unsafe_allow_html=True)
    db = database.DB()
    summary = db.dashboard_summary()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total cases", summary["total_cases"])
    c2.metric("Resolved", summary["resolved_cases"])
    c3.metric("Escalated", summary["status_counts"].get("escalated", 0))
    c4.metric("Tool failure rate", f"{summary['tool_events']['failure_rate']:.1%}")
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Awaiting user", summary["status_counts"].get("awaiting_user", 0))
    c6.metric("Open", summary["status_counts"].get("open", 0))
    c7.metric("Investigating", summary["status_counts"].get("investigating", 0))
    c8.metric("Repeated issues", summary["repeated_issue_cases"])
    if summary["average_resolution_seconds"] is not None:
        st.caption(f"Average resolution time: {summary['average_resolution_seconds']:.0f} seconds")

    recent = db.list_cases(limit=8)
    if recent:
        st.markdown("#### Recent incidents")
        _render_case_table(recent)
    else:
        st.info("No incidents yet.")

    st.markdown("#### Agent evaluation")
    report_path = config.REPORTS_DIR / "evaluation.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        cm = report["classification"]
        st.write(
            f"Classification: accuracy {cm['accuracy']:.4f}, macro-F1 {cm['f1_macro']:.4f}, "
            f"weighted-F1 {cm['f1_weighted']:.4f}"
        )
        st.write(
            f"Retrieval: Hit@1 {report['retrieval']['hit_at_1']:.4f}, "
            f"MRR {report['retrieval']['mrr']:.4f}, "
            f"false-positive rate {report['retrieval']['false_positive_rate_unanswerable']:.4f}"
        )
        st.write(
            f"Agent: {report['agent']['passed']}/{report['agent']['n_scenarios']} · "
            f"Security: {report['security']['passed']}/{report['security']['n_checks']} · "
            f"Robustness: {report['robustness']['passed']}/{report['robustness']['n_cases']}"
        )
    else:
        st.info("Run `python -m src.evaluation` to generate reports.")

    st.markdown("#### Safety model")
    st.write("Tool calls use a closed allowlist, typed parameters, OS checks, redaction, approval gates and no arbitrary shell execution.")
    st.write(f"LLM mode: {llm_provider.get_provider().mode_label}")
    st.write(f"Database: `{config.get_db_path()}`")


def main() -> None:
    with st.sidebar:
        st.markdown("## IT Autopsy")
        st.caption("Autonomous IT Help Desk Agent")
        page = st.radio(
            "Navigation",
            ["AI Help Desk", "Cases", "Diagnostics", "Operations"],
            key="nav",
            label_visibility="collapsed",
        )
        st.divider()
        st.caption("Deterministic local baseline. No API key required.")

    if page == "AI Help Desk":
        page_help_desk()
    elif page == "Cases":
        page_cases()
    elif page == "Diagnostics":
        page_diagnostics()
    else:
        page_operations()


if __name__ == "__main__":
    main()
