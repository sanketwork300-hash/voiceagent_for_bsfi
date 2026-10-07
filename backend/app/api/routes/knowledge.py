from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import container
from app.auth.authorization import StaffPrincipal, require
from app.auth.permissions import Permission
from app.domain import AuthState

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class SearchBody(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    product: str | None = None
    doc_type: str | None = None
    top_k: int = Field(5, ge=1, le=20)
    include_internal: bool = False


@router.post("/search")
async def search(body: SearchBody, p: StaffPrincipal = Depends(require(Permission.KNOWLEDGE_READ)), c=Depends(container)) -> dict:
    results = await c.rag.search(tenant_id=p.tenant_id, query=body.query, auth_state=AuthState.FULLY_AUTHENTICATED,
                                 product=body.product, doc_type=body.doc_type, top_k=body.top_k,
                                 include_internal=body.include_internal, channel="admin")
    return {"results": [r.model_dump() for r in results]}
