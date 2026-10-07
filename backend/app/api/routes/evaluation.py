from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import container
from app.auth.authorization import StaffPrincipal, require
from app.auth.permissions import Permission
from app.database.models import EvaluationRun
from app.domain import Channel

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


class RunRequest(BaseModel):
    channels: list[Channel] = [Channel.CHAT, Channel.VOICE]
    categories: list[str] | None = None  # functional | security


@router.post("/runs", status_code=201)
async def run(body: RunRequest, p: StaffPrincipal = Depends(require(Permission.EVALUATION_RUN)), c=Depends(container)) -> dict:
    from app.evaluation.runner import EvaluationRunner

    report = await EvaluationRunner(c).run(p.tenant_id, channels=body.channels, categories=body.categories)
    return report


@router.get("/runs/{run_id}")
async def get_run(run_id: str, p: StaffPrincipal = Depends(require(Permission.EVALUATION_RUN)), c=Depends(container)) -> dict:
    async with c.db.session() as s:
        r = await s.get(EvaluationRun, run_id)
    if r is None or r.tenant_id != p.tenant_id:
        raise HTTPException(404, "run not found")
    return {"id": r.id, "status": r.status, "channels": r.channels, "pass_rate": r.pass_rate, "metrics": r.metrics, "results": r.results}
