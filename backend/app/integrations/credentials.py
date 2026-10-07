"""Integration credentials: encrypted at rest, resolved per call into outbound auth headers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.auth.oauth import ClientCredentialsTokenProvider
from app.database.models import Integration
from app.database.session import Database
from app.security.secrets import SecretBox, resolve_secret_ref


@dataclass
class IntegrationConfig:
    id: str
    tenant_id: str
    name: str
    kind: str
    base_url: str | None
    auth_type: str
    credentials: dict[str, Any]
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


class CredentialManager:
    def __init__(self, db: Database, box: SecretBox, oauth: ClientCredentialsTokenProvider, ttl: float = 60.0) -> None:
        self.db = db
        self.box = box
        self.oauth = oauth
        self._cache: dict[tuple[str, str], tuple[float, IntegrationConfig]] = {}
        self._ttl = ttl

    def encrypt(self, credentials: dict[str, Any]) -> str:
        return self.box.encrypt(credentials)

    def invalidate(self, tenant_id: str, integration_id: str) -> None:
        self._cache.pop((tenant_id, integration_id), None)

    async def get(self, tenant_id: str, integration_id: str) -> IntegrationConfig:
        key = (tenant_id, integration_id)
        if (hit := self._cache.get(key)) and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        async with self.db.session() as s:
            row = (await s.execute(select(Integration).where(
                Integration.tenant_id == tenant_id, Integration.id == integration_id))).scalar_one_or_none()
        if row is None:
            raise LookupError("integration not found for tenant")
        cfg = IntegrationConfig(
            id=row.id, tenant_id=row.tenant_id, name=row.name, kind=row.kind, base_url=row.base_url,
            auth_type=row.auth_type, credentials={k: resolve_secret_ref(v) for k, v in self.box.decrypt(row.credentials_encrypted).items()},
            config=row.config or {}, enabled=row.is_enabled,
        )
        self._cache[key] = (time.monotonic(), cfg)
        return cfg

    async def auth_headers(self, cfg: IntegrationConfig) -> dict[str, str]:
        c = cfg.credentials
        match cfg.auth_type:
            case "api_key":
                return {c.get("header", "X-API-Key"): c["api_key"]}
            case "bearer":
                return {"Authorization": f"Bearer {c['token']}"}
            case "oauth2_client_credentials":
                tok = await self.oauth.token(token_url=c["token_url"], client_id=c["client_id"],
                                             client_secret=c["client_secret"], scope=c.get("scope"))
                return {"Authorization": f"Bearer {tok}"}
            case _:
                return {}
