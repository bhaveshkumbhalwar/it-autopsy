"""Streamlit UI for the Autonomous IT Help Desk Agent.

The UI is a thin shell over the same deterministic pipeline used by the tests
and evaluation. All rendering goes through Streamlit's escaping (no
``unsafe_allow_html``), and user/ticket text is never interpreted as markup or
instructions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from src import agent, config, database, policy, verification

st.set_page_config(page_title="Autonomous IT Help Desk Agent", layout="wide")


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


def _render_run(run: dict) -> None:
    triage = run["triage"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Category", triage["category"])
    c2.metric("Priority", triage["priority"])
    c3.metric("Escalated", "yes" if run["escalated"] else "no")
    st.caption(
        f"Priority rationale: {'; '.join(triage['priority_rationale'])}. "
        f"Classifier score type: {triage['confidence_label']}."
    )

    st.markdown("#### Evidence (official sources only)")
    if run["evidence"]:
        for e in run["evidence"]:
            st.markdown(
                f"- **{e['title']}** — {e['source']} — score {e['score']:.3f}  \n"
                f"  <{e['url']}>  \n  {e['citation']}",
                unsafe_allow_html=False,
            )
    else:
        st.info("No sufficiently relevant official source. The agent escalates instead of guessing.")

    st.markdown("#### Decision trace")
    for t in run["trace"]:
        tool = f" `{t['tool_name']}`" if t["tool_name"] else ""
        st.write(f"{t['step']}. {t['decision']}{tool}")

    st.markdown("#### Tool observations")
    if not run["results"]:
        st.caption("No tool was run.")
    for r in run["results"]:
        label = "SIMULATED" if r["simulated"] else "REAL (read-only)"
        icon = "✅" if r["ok"] else "❌"
        st.write(f"{icon} [{label}] `{r['tool_name']}` — {r.get('summary') or r.get('error', '')}")

    st.caption(f"Stopped because: {run['stopped_reason']}")


def _show_verification(db: database.DB, run: dict, ver: dict) -> None:
    st.markdown("#### Verification")
    st.write(f"**{ver['outcome']}** — {ver['reason']}")
    if ver.get("follow_up_question"):
        st.write(f"Follow-up: {ver['follow_up_question']}")

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


def new_case_tab() -> None:
    st.subheader("Submit an IT issue")
    issue_text = st.text_area(
        "Describe the issue in your own words",
        height=120,
        key="issue_text",
        placeholder="e.g. My Mac connects to Wi-Fi but web pages do not load",
    )
    col1, col2 = st.columns(2)
    os_choice = col1.selectbox("Operating system", ["unknown", "macos", "windows"], key="os_choice")
    allow_real = col2.checkbox(
        "Allow read-only real diagnostics (macOS, approval-gated)",
        value=False,
        key="allow_real",
    )
    approved = st.checkbox(
        "I approve state-changing tools for this run (demo only; not needed by the default plan)",
        value=False,
        key="approved",
    )

    if st.button("Run agent", type="primary"):
        if not issue_text.strip():
            st.warning("Please describe the issue.")
            return
        try:
            a = agent.Agent(os_name=os_choice, allow_real=allow_real)
            with st.spinner("Running intake → triage → retrieve → plan → execute → verify..."):
                run = a.run(issue_text, os_name=os_choice, approved=approved)
            ver = verification.Verifier().verify(run["evidence"], run["results"], run=run)
            esc = verification.Escalator().build(run, ver)
            db = database.DB()
            db.save_case(run["case_id"], issue_text, os_choice, run["triage"], ver["outcome"])
            _log_run(db, run, ver)
            st.session_state["run"] = run
            st.session_state["ver"] = ver
            st.session_state["esc"] = esc
        except Exception as exc:  # noqa: BLE001 - show a safe message, not a traceback
            st.error(f"Could not run the agent: {type(exc).__name__}")
            return

    run = st.session_state.get("run")
    if run:
        st.divider()
        flash = st.session_state.pop("flash", None)
        if flash:
            st.success(flash)
        st.caption(f"Case {run['case_id']}")
        _render_run(run)
        _show_verification(database.DB(), run, st.session_state["ver"])
        st.markdown("#### Escalation handoff")
        st.json(st.session_state["esc"])


def cases_tab() -> None:
    st.subheader("Recent cases")
    db = database.DB()
    rows = db.list_cases(limit=20)
    if not rows:
        st.info("No cases yet.")
        return
    for row in rows:
        with st.expander(f"{row['id'][:8]} · {row['outcome'] or 'pending'} · {row['issue_text'][:70]}"):
            st.write(f"Created: {row['created_at']} · OS: {row['os']}")
            for event in db.list_events(row["id"]):
                st.write(
                    f"- {event['ts']} · {event['event_type']} · "
                    f"{event['tool_name'] or ''} · {event['status']} · {event['result_summary']}"
                )


def evaluation_tab() -> None:
    st.subheader("Evaluation reports")
    report_path = config.REPORTS_DIR / "evaluation.json"
    if not report_path.exists():
        st.info("Run `python -m src.evaluation` to generate reports.")
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cm = report["classification"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Accuracy", f"{cm['accuracy']:.4f}")
    c2.metric("Macro-F1", f"{cm['f1_macro']:.4f}")
    c3.metric("Weighted-F1", f"{cm['f1_weighted']:.4f}")
    c4.metric("Majority baseline", f"{cm['majority_baseline']:.4f}")
    st.caption(
        "Held-out test split, untouched. Scores are not calibrated. "
        "Hybrid result: deterministic EOL rule first, statistical model otherwise. "
        f"Base model only: accuracy {cm['model_only']['accuracy']:.4f}, "
        f"macro-F1 {cm['model_only']['f1_macro']:.4f}."
    )
    st.dataframe(cm["classification_report"], use_container_width=True)
    st.caption(
        f"EOL rule validated on training data only: precision "
        f"{cm['eol_rule']['precision']:.4f}, recall {cm['eol_rule']['recall']:.4f} "
        f"({cm['eol_rule']['n_matched']} matches)."
    )

    st.markdown("#### Other suites")
    st.write(
        f"- Retrieval: Hit@1 {report['retrieval']['hit_at_1']:.4f}, "
        f"MRR {report['retrieval']['mrr']:.4f}, "
        f"false-positive rate {report['retrieval']['false_positive_rate_unanswerable']:.4f}"
    )
    st.write(
        f"- Agent scenarios: {report['agent']['passed']}/{report['agent']['n_scenarios']}"
    )
    st.write(
        f"- Security checks: {report['security']['passed']}/{report['security']['n_checks']}"
    )
    st.write(
        f"- Robustness cases: {report['robustness']['passed']}/{report['robustness']['n_cases']}"
    )
    with st.expander("Raw aggregate report"):
        st.json(report)


def safety_tab() -> None:
    st.subheader("Safety model")
    st.write(
        "The agent cannot execute free-form text. Every tool call is checked "
        "against a closed allowlist, typed parameters, OS support, opt-in for "
        "real diagnostics, and an approval gate for state-changing tools."
    )
    rows = []
    for name, spec in policy.ALLOWED_TOOLS.items():
        rows.append(
            {
                "tool": name,
                "simulated": spec["simulated"],
                "risk": spec["risk"],
                "approval": spec["requires_approval"],
                "os": ", ".join(spec["os"]),
            }
        )
    st.dataframe(rows, use_container_width=True)
    st.warning(
        "Output is redacted for emails, MAC addresses and tokens before display "
        "or storage. Prompt-injection text in tickets or retrieved documents is "
        "treated as untrusted data."
    )


def main() -> None:
    st.title("Autonomous IT Help Desk Agent")
    st.caption(
        "Intake → triage → retrieve → plan → policy → execute → observe → "
        "verify/escalate. Deterministic local baseline; no API key required."
    )
    tab_new, tab_cases, tab_eval, tab_safety = st.tabs(
        ["New case", "Cases", "Evaluation", "Safety"]
    )
    with tab_new:
        new_case_tab()
    with tab_cases:
        cases_tab()
    with tab_eval:
        evaluation_tab()
    with tab_safety:
        safety_tab()


if __name__ == "__main__":
    main()
