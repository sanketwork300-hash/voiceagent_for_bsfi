"""Document management: versioned uploads, storage, (re)indexing."""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.database.models import Document, DocumentVersion
from app.database.session import Database
from app.domain import new_id
from app.knowledge.ingestion.metadata import (
    AccessLevel,
    DocumentMetadata,
    DocumentStatus,
    infer_doc_type,
    infer_product,
)
from app.knowledge.ingestion.parser import parse_document
from app.knowledge.ingestion.pipeline import IngestionPipeline
from app.knowledge.retrieval.base import KnowledgeStore

log = logging.getLogger(__name__)


class DocumentService:
    def __init__(self, db: Database, pipeline: IngestionPipeline, store: KnowledgeStore, storage_dir: str) -> None:
        self.db = db
        self.pipeline = pipeline
        self.store = store
        self.storage = Path(storage_dir)

    async def upload(self, tenant_id: str, *, data: bytes, filename: str, content_type: str | None, title: str | None = None,
                     document_id: str | None = None, version: str | None = None, product: str | None = None,
                     doc_type: str | None = None, language: str = "en", region: str | None = None,
                     access_level: str = "public", effective_from: date | None = None, effective_until: date | None = None,
                     tags: list[str] | None = None, index_now: bool = True) -> tuple[Document, DocumentVersion]:
        sha = hashlib.sha256(data).hexdigest()
        safe_name = Path(filename).name
        async with self.db.session() as s:
            doc = None
            if document_id:
                doc = (await s.execute(select(Document).where(Document.tenant_id == tenant_id, Document.id == document_id))).scalar_one_or_none()
                if doc is None:
                    raise LookupError("document not found")
            if doc is None:
                try:
                    preview = parse_document(data, safe_name, content_type).text[:3000]
                except Exception:  # noqa: BLE001 - parse errors are reported at ingestion
                    preview = ""
                doc = Document(id=new_id(), tenant_id=tenant_id, title=title or Path(safe_name).stem.replace("_", " ").title(),
                               doc_type=doc_type or infer_doc_type(safe_name, preview), product=product or infer_product(preview),
                               language=language, region=region, access_level=AccessLevel(access_level).value, tags=tags or [])
                s.add(doc)
                await s.flush()
            n_versions = len((await s.execute(select(DocumentVersion.id).where(DocumentVersion.document_id == doc.id))).all())
            ver = DocumentVersion(id=new_id(), tenant_id=tenant_id, document_id=doc.id, version=version or str(n_versions + 1),
                                  filename=safe_name, content_type=content_type or "application/octet-stream", storage_path="",
                                  sha256=sha, effective_from=effective_from, effective_until=effective_until)
            path = self.storage / tenant_id / doc.id / f"{ver.id}_{safe_name}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            ver.storage_path = str(path)
            s.add(ver)
        if index_now:
            ver = await self.index_version(tenant_id, ver.id)
        return doc, ver

    async def index_version(self, tenant_id: str, version_id: str) -> DocumentVersion:
        async with self.db.session() as s:
            ver = (await s.execute(select(DocumentVersion).where(DocumentVersion.tenant_id == tenant_id,
                                                                 DocumentVersion.id == version_id))).scalar_one()
            doc = await s.get(Document, ver.document_id)
            meta = DocumentMetadata(
                tenant_id=tenant_id, document_id=doc.id, version_id=ver.id, title=doc.title, doc_type=doc.doc_type,
                product=doc.product, language=doc.language, region=doc.region, effective_from=ver.effective_from,
                effective_until=ver.effective_until, version=ver.version,
                status=DocumentStatus.ACTIVE if doc.status == "active" else DocumentStatus.RETIRED,
                access_level=AccessLevel(doc.access_level), tags=doc.tags or [], source=ver.filename,
            )
            try:
                result = await self.pipeline.ingest(Path(ver.storage_path).read_bytes(), ver.filename, ver.content_type, meta)
                ver.status, ver.chunk_count, ver.page_count, ver.error = "indexed", result.chunk_count, result.page_count, None
                prior = (await s.execute(select(DocumentVersion).where(DocumentVersion.document_id == doc.id,
                                                                       DocumentVersion.id != ver.id,
                                                                       DocumentVersion.status == "indexed"))).scalars()
                for p in prior:
                    p.status = "superseded"
                doc.current_version_id = ver.id
            except Exception as e:  # noqa: BLE001
                log.exception("document ingestion failed")
                ver.status, ver.error = "failed", f"{type(e).__name__}: {e}"[:500]
        return ver

    async def reindex(self, tenant_id: str, document_id: str) -> DocumentVersion:
        async with self.db.session() as s:
            doc = (await s.execute(select(Document).where(Document.tenant_id == tenant_id, Document.id == document_id))).scalar_one_or_none()
        if doc is None or not doc.current_version_id:
            raise LookupError("document not found or never indexed")
        return await self.index_version(tenant_id, doc.current_version_id)

    async def list(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self.db.session() as s:
            docs = (await s.execute(select(Document).where(Document.tenant_id == tenant_id).order_by(Document.created_at))).scalars().all()
            vers = (await s.execute(select(DocumentVersion).where(DocumentVersion.tenant_id == tenant_id))).scalars().all()
        by_doc: dict[str, list[DocumentVersion]] = {}
        for v in vers:
            by_doc.setdefault(v.document_id, []).append(v)
        return [{
            "id": d.id, "title": d.title, "doc_type": d.doc_type, "product": d.product, "language": d.language,
            "access_level": d.access_level, "status": d.status, "current_version_id": d.current_version_id,
            "versions": [{"id": v.id, "version": v.version, "status": v.status, "chunks": v.chunk_count,
                          "effective_from": v.effective_from, "effective_until": v.effective_until, "error": v.error}
                         for v in sorted(by_doc.get(d.id, []), key=lambda v: v.created_at)],
        } for d in docs]
