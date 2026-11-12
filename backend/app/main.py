"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import executions, ingest, reports
from app.core.config import settings
from app.db.database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("agent_autopsy")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database ready at %s", settings.database_url.split("///")[-1])
    # Warm the classifier in the background so the first analysis is fast.
    try:
        from app.ml.classifier import get_classifier

        get_classifier().pipeline  # noqa: B018 - triggers lazy training
        logger.info("ML failure classifier ready")
    except Exception as exc:  # pragma: no cover - training is best effort
        logger.warning("ML classifier unavailable: %s", exc)
    yield


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent Autopsy API",
        description=(
            "AI agent trace diagnostics: ingest execution logs, classify failures, "
            "find root causes and generate autopsy reports."
        ),
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        max_age=600,
    )

    @app.middleware("http")
    async def secure_headers(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:  # never leak stack traces to clients
            logger.exception("Unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error.", "error": type(exc).__name__},
            )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    # ----------------------------------------------------------------- errors
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        """Every HTTP error is JSON with an actionable message."""
        detail = exc.detail
        if exc.status_code == 405:
            detail = "Method not allowed for this endpoint. Check the request method (GET/POST/PUT/DELETE)."
        elif exc.status_code == 404 and isinstance(detail, str) and detail.strip().lower() == "not found":
            detail = f"No API route matches {_request.method} {_request.url.path}."
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail, "status": exc.status_code, "path": _request.url.path},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        """422s name the offending field instead of dumping a Pydantic trace."""
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        message = first.get("msg", "Invalid request body.")
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "message": f"{field}: {message}" if field else message,
                    "hint": "Check the request body against the API schema at /docs.",
                },
                "errors": jsonable_encoder(exc.errors()),
            },
        )

    # ------------------------------------------------------------------ routes
    routers = (ingest.router, executions.router, reports.router)
    for router in routers:
        app.include_router(router, prefix="/api")

    @app.get("/api")
    def root() -> dict[str, str]:
        return {"app": settings.app_name, "version": settings.app_version, "docs": "/docs"}

    # ------------------------------------------------------ static frontend
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            # /api/* is the API's alone: an unknown API path (or a wrong method
            # on one) must stay a JSON 404/405, never index.html with 200.
            if _is_api_path("/" + full_path):
                # Raised inside the app so the 405/404 exception handlers format it.
                raise HTTPException(status_code=405 if False else 404, detail="Not Found")
            candidate = dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
