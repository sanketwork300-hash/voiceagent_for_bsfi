from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import container
from app.auth.authorization import StaffPrincipal, require
from app.auth.permissions import Permission

router = APIRouter(prefix="/documents", tags=["knowledge"])
MAX_BYTES = 25 * 1024 * 1024
ALLOWED = (".pdf", ".txt", ".md", ".html", ".htm")


@router.post("", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    document_id: str | None = Form(None, description="Upload a new version of an existing document"),
    version: str | None = Form(None),
    product: str | None = Form(None),
    doc_type: str | None = Form(None),
    language: str = Form("en"),
    region: str | None = Form(None),
    access_level: str = Form("public"),
    effective_from: date | None = Form(None),
    effective_until: date | None = Form(None),
    p: StaffPrincipal = Depends(require(Permission.KNOWLEDGE_MANAGE)),
    c=Depends(container),
) -> dict:
    if not (file.filename or "").lower().endswith(ALLOWED):
        raise HTTPException(415, f"supported types: {', '.join(ALLOWED)}")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large")
    if access_level not in ("public", "customer", "internal"):
        raise HTTPException(400, "invalid access_level")
    doc, ver = await c.documents.upload(
        p.tenant_id, data=data, filename=file.filename or "document", content_type=file.content_type, title=title,
        document_id=document_id, version=version, product=product, doc_type=doc_type, language=language, region=region,
        access_level=access_level, effective_from=effective_from, effective_until=effective_until)
    await c.audit.record(p.tenant_id, "document.uploaded", actor_type="staff", actor_id=p.user_id, resource=doc.id,
                         payload={"version": ver.version, "status": ver.status, "sha256": ver.sha256})
    return {"document_id": doc.id, "version_id": ver.id, "version": ver.version, "status": ver.status,
            "chunks": ver.chunk_count, "error": ver.error}


@router.get("")
async def list_documents(p: StaffPrincipal = Depends(require(Permission.KNOWLEDGE_READ)), c=Depends(container)) -> list[dict]:
    return await c.documents.list(p.tenant_id)


@router.post("/{document_id}/reindex")
async def reindex(document_id: str, p: StaffPrincipal = Depends(require(Permission.KNOWLEDGE_MANAGE)), c=Depends(container)) -> dict:
    ver = await c.documents.reindex(p.tenant_id, document_id)
    await c.audit.record(p.tenant_id, "document.reindexed", actor_type="staff", actor_id=p.user_id, resource=document_id)
    return {"document_id": document_id, "version_id": ver.id, "status": ver.status, "chunks": ver.chunk_count, "error": ver.error}
