"""FastAPI application: chat channel, admin/config APIs, health. Voice runs in the LiveKit worker."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    agents,
    audit,
    auth,
    chat,
    conversations,
    documents,
    evaluation,
    handoff,
    health,
    integrations,
    knowledge,
    mcp,
    monitoring,
    policies,
    sessions,
    tenants,
    tools,
    users,
    voice,
)
from app.config import Settings, get_settings
from app.container import Container
from app.observability.logging import setup_logging
from app.observability.tracing import setup_tracing

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, container: Container | None = None,
               container_factory: Callable[[Settings], Container] | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.log_json)
    setup_tracing(settings.otel_service_name, settings.otel_exporter_otlp_endpoint, settings.otel_enabled)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = container or (container_factory or Container)(settings)
        app.state.container = c
        await c.startup(create_schema=settings.db_auto_create)
        if settings.auto_seed:
            from scripts.seed import seed_demo

            await seed_demo(c)
        yield
        await c.shutdown()

    app = FastAPI(title="BFSI Agent Platform", version="0.1.0", lifespan=lifespan,
                  description="Multi-tenant chat + voice AI agent backend for banks, NBFCs, insurers and fintechs.")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready,metrics")
    except ImportError:
        pass

    @app.exception_handler(LookupError)
    async def not_found(_: Request, exc: LookupError):
        return JSONResponse(status_code=404, content={"detail": str(exc) or "not found"})

    @app.exception_handler(PermissionError)
    async def forbidden(_: Request, exc: PermissionError):
        return JSONResponse(status_code=403, content={"detail": str(exc) or "forbidden"})

    for module in (health, auth, tenants, users, agents, sessions, chat, voice, conversations, documents, knowledge,
                   integrations, mcp, tools, policies, handoff, audit, evaluation, monitoring):
        app.include_router(module.router)
    return app


app = create_app()
