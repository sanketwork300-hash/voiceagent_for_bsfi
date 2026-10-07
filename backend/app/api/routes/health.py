from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.deps import container
from app.observability.metrics import REGISTRY

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response, c=Depends(container)) -> dict:
    checks: dict[str, bool] = {}
    for name, probe in (("database", c.db.ping), ("state_store", c.store.ping), ("knowledge", c.knowledge_store.ping)):
        try:
            checks[name] = bool(await probe())
        except Exception:
            checks[name] = False
    ok = checks["database"] and checks["state_store"]  # RAG degradation is reported but doesn't fail readiness
    response.status_code = 200 if ok else 503
    return {"status": "ready" if ok else "degraded", "checks": checks}


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
