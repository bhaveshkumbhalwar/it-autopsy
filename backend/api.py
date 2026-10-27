"""Small Starlette API exposing the existing IT Autopsy agent.

The API reuses the Python agent, SQLite store, retrieval system and policy
checks. It does not duplicate diagnostic logic in the frontend.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from src import agent, config, database, policy, retrieval, verification


def _json(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code)


def _clean(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return value


def _case_row(row) -> dict[str, Any]:
    case = database.DB().get_case(row["id"]) if False else None
    return {}


def _case_dict(case_id: str) -> dict[str, Any] | None:
    db = database.DB()
    case = db.get_case(case_id)
    if case is None:
        return None
    return case


def _run_case(issue_text: str, os_name: str, allow_real: bool, approved: bool, case_id: str | None = None) -> tuple[dict, dict, dict]:
    ag = agent.Agent(os_name=os_name, allow_real=allow_real)
    run = ag.run(issue_text, os_name=os_name, approved=approved, case_id=case_id)
    ver = verification.Verifier().verify(run["evidence"], run["results"], run=run)
    esc = verification.Escalator().build(run, ver)
    db = database.DB()
    db.save_case(
        run["case_id"],
        issue_text,
        os_name,
        run["triage"],
        ver["outcome"],
        status=_status_for_outcome(ver["outcome"]),
        analysis=run.get("analysis"),
        state=run.get("case_state"),
        symptoms=(run.get("analysis") or {}).get("symptoms", []),
    )
    # Best-effort event logging; failures must not hide the completed run.
    try:
        db.log_event(
            run["case_id"],
            "api_run",
            None,
            {"os": os_name},
            ver["outcome"],
            "Agent run completed through API",
            [e.get("doc_id") for e in run.get("evidence", [])],
            {},
        )
    except Exception:
        pass
    db.update_conversation(
        run["case_id"],
        [
            {"role": "user", "content": issue_text},
            {"role": "assistant", "content": _assistant_summary(run, ver)},
        ],
    )
    return run, ver, esc


def _status_for_outcome(outcome: str | None) -> str:
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
        f"Category: {triage.get('category', 'Unknown')}",
        f"Priority: {triage.get('priority', 'Unknown')}",
        f"Outcome: {ver.get('outcome', 'unknown')}",
    ]
    evidence = run.get("evidence") or []
    if evidence:
        parts.append("Evidence: " + ", ".join(e.get("title", "source") for e in evidence if isinstance(e, dict)))
    if run.get("stopped_reason"):
        parts.append(f"Stopped: {run.get('stopped_reason')}")
    return " | ".join(parts)


async def health(request: Request) -> Response:
    return _json({"status": "ok", "service": "it-autopsy-api", "version": "0.1.0"})


async def list_cases(request: Request) -> Response:
    db = database.DB()
    query = request.query_params.get("query", "")
    status = request.query_params.get("status")
    priority = request.query_params.get("priority")
    limit = int(request.query_params.get("limit", "100"))
    rows = db.search_cases(query=query, status=status, priority=priority, limit=limit)
    return _json({"cases": [dict(row) for row in rows]})


async def get_case(request: Request) -> Response:
    case_id = request.path_params["case_id"]
    db = database.DB()
    case = db.get_case(case_id)
    if case is None:
        return _json({"error": "case not found"}, 404)
    case["events"] = [dict(row) for row in db.list_events(case_id)]
    case["diagnostics"] = db.get_diagnostics(case_id)
    return _json({"case": case})


async def create_case(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    issue_text = str(payload.get("issue") or payload.get("issue_text") or "").strip()
    if not issue_text:
        return _json({"error": "issue is required"}, 400)
    os_name = payload.get("os") or payload.get("os_name") or "unknown"
    allow_real = bool(payload.get("allow_real", False))
    approved = bool(payload.get("approved", False))
    run, ver, esc = _run_case(issue_text, os_name, allow_real, approved)
    return _json(
        {
            "case": _case_dict(run["case_id"]),
            "run": run,
            "verification": ver,
            "escalation": esc,
        },
        201,
    )


async def continue_case(request: Request) -> Response:
    case_id = request.path_params["case_id"]
    db = database.DB()
    existing = db.get_case(case_id)
    if existing is None:
        return _json({"error": "case not found"}, 404)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    message = str(payload.get("message") or payload.get("issue") or "").strip()
    if not message:
        return _json({"error": "message is required"}, 400)
    original = existing.get("issue_text") or ""
    combined = f"{original}\nFollow-up: {message}" if original else message
    os_name = payload.get("os") or payload.get("os_name") or existing.get("os") or "unknown"
    allow_real = bool(payload.get("allow_real", False))
    approved = bool(payload.get("approved", False))
    case_state = existing.get("state")
    run, ver, esc = _run_case(
        combined,
        os_name,
        allow_real,
        approved,
        case_id=case_id,
        case_state=case_state,
        force_refresh=bool(payload.get("force_refresh", False)),
    )
    return _json({"case": _case_dict(run["case_id"]), "run": run, "verification": ver, "escalation": esc})


async def confirm_case(request: Request) -> Response:
    case_id = request.path_params["case_id"]
    db = database.DB()
    case = db.get_case(case_id)
    if case is None:
        return _json({"error": "case not found"}, 404)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    resolved = bool(payload.get("resolved", True))
    # Reconstruct a minimal verification object from the stored outcome if needed.
    ver = {"outcome": case.get("outcome") or "", "reason": "", "follow_up_question": ""}
    updated = verification.Verifier().confirm(ver, resolved)
    db.update_outcome(case_id, updated["outcome"])
    db.update_status(case_id, "resolved" if updated["outcome"] == config.OUTCOME_VERIFIED else "escalated")
    db.log_event(case_id, "user_confirmation", None, {}, updated["outcome"], updated["reason"], [], {})
    return _json({"case": db.get_case(case_id), "verification": updated})


async def list_events(request: Request) -> Response:
    case_id = request.path_params.get("case_id") or request.query_params.get("case_id")
    if not case_id:
        return _json({"error": "case_id is required"}, 400)
    db = database.DB()
    if db.get_case(case_id) is None:
        return _json({"error": "case not found"}, 404)
    events = [dict(row) for row in db.list_events(case_id)]
    return _json({"events": events})


async def list_tools(request: Request) -> Response:
    tools = []
    for name, spec in policy.ALLOWED_TOOLS.items():
        tools.append(
            {
                "name": name,
                "topic": spec.get("topic", ""),
                "risk": spec.get("risk", ""),
                "simulated": bool(spec.get("simulated", False)),
                "requires_approval": bool(spec.get("requires_approval", False)),
                "os": spec.get("os", []),
                "purpose": spec.get("purpose", spec.get("description", "")),
            }
        )
    return _json({"tools": tools})


async def search_knowledge(request: Request) -> Response:
    query = request.query_params.get("q") or request.query_params.get("query") or ""
    os_name = request.query_params.get("os") or request.query_params.get("os_name") or "unknown"
    retriever = retrieval.Retriever()
    evidence = retriever.search(query, os_name=os_name)
    by_id = {doc.get("id"): doc for doc in json.loads((config.KB_DIR / "kb.json").read_text(encoding="utf-8"))}
    results = []
    for item in evidence:
        doc = by_id.get(item.doc_id, {})
        results.append(
            {
                "doc_id": item.doc_id,
                "title": item.title,
                "source": item.source,
                "url": item.url,
                "snippet": item.snippet,
                "score": item.score,
                "os_scope": item.os_scope,
                "citation": item.citation,
                "text": doc.get("text", ""),
                "tags": doc.get("tags", []),
            }
        )
    return _json({"results": results})


async def operations_summary(request: Request) -> Response:
    db = database.DB()
    summary = db.dashboard_summary()
    return _json({"summary": summary})


async def operations_volume(request: Request) -> Response:
    db = database.DB()
    return _json({"volume": db.case_volume_by_date()})


async def operations_failures(request: Request) -> Response:
    db = database.DB()
    failures = [dict(row) for row in db.recent_events(100) if row["event_type"] == "tool_call" and row["status"] != "ok"]
    return _json({"failures": failures})


async def evaluation_report(request: Request) -> Response:
    path = config.REPORTS_DIR / "evaluation.json"
    if not path.exists():
        return _json({"error": "evaluation report not generated"}, 404)
    return _json(json.loads(path.read_text(encoding="utf-8")))


async def settings(request: Request) -> Response:
    from src import llm_provider

    provider = llm_provider.get_provider()
    return _json(
        {
            "database": str(config.get_db_path()),
            "knowledge_base": str(config.KB_DIR / "kb.json"),
            "reports": str(config.REPORTS_DIR),
            "llm_mode": provider.mode_label,
            "real_diagnostics_require_approval": True,
            "secrets_exposed": False,
        }
    )


routes = [
    Route("/api/health", health, methods=["GET"]),
    Route("/api/cases", list_cases, methods=["GET"]),
    Route("/api/cases", create_case, methods=["POST"]),
    Route("/api/cases/{case_id}", get_case, methods=["GET"]),
    Route("/api/cases/{case_id}/messages", continue_case, methods=["POST"]),
    Route("/api/cases/{case_id}/confirm", confirm_case, methods=["POST"]),
    Route("/api/cases/{case_id}/events", list_events, methods=["GET"]),
    Route("/api/diagnostics/tools", list_tools, methods=["GET"]),
    Route("/api/diagnostics/events", list_events, methods=["GET"]),  # case_id via query? see below
    Route("/api/knowledge/search", search_knowledge, methods=["GET"]),
    Route("/api/operations/summary", operations_summary, methods=["GET"]),
    Route("/api/operations/volume", operations_volume, methods=["GET"]),
    Route("/api/operations/failures", operations_failures, methods=["GET"]),
    Route("/api/evaluation", evaluation_report, methods=["GET"]),
    Route("/api/settings", settings, methods=["GET"]),
]

# Backwards-friendly aliases without /api.
for route in list(routes):
    if route.path.startswith("/api/"):
        routes.append(Route(route.path.removeprefix("/api"), route.endpoint, methods=route.methods))

app = Starlette(routes=routes)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dist = ROOT / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
