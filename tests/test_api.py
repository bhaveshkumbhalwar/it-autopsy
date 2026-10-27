"""Smoke tests for the React-facing Starlette API."""

from __future__ import annotations

import asyncio
import json

import pytest


@pytest.mark.anyio
async def test_api_health(monkeypatch, tmp_path):
    monkeypatch.setenv("HDA_DB_PATH", str(tmp_path / "api.db"))
    from backend.api import health

    from starlette.requests import Request

    request = Request({"type": "http", "method": "GET", "path": "/api/health", "headers": []})
    response = await health(request)
    assert response.status_code == 200
    assert json.loads(response.body)["status"] == "ok"


@pytest.mark.anyio
async def test_api_create_and_get_case(monkeypatch, tmp_path):
    monkeypatch.setenv("HDA_DB_PATH", str(tmp_path / "api.db"))
    from backend.api import create_case, get_case

    from starlette.requests import Request

    body = b'{"issue":"My Mac connects to Wi-Fi but has no internet","os":"macos"}'

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/cases",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )
    response = await create_case(request)
    assert response.status_code == 201
    payload = json.loads(response.body)
    case_id = payload["run"]["case_id"]
    assert payload["verification"]["outcome"] in {
        "needs_user_confirmation",
        "escalated",
        "unable_to_verify",
    }

    get_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/api/cases/{case_id}",
            "path_params": {"case_id": case_id},
            "headers": [],
        }
    )
    get_response = await get_case(get_request)
    assert get_response.status_code == 200
    assert json.loads(get_response.body)["case"]["id"] == case_id
