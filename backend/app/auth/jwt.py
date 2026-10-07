"""JWTs: staff access tokens, customer session tokens, and verification of bank-issued customer assertions."""

from __future__ import annotations

import time
from typing import Any

import jwt


class TokenError(PermissionError):
    pass


class JWTService:
    def __init__(self, secret: str, algorithm: str = "HS256", issuer: str = "bfsi-agent-platform") -> None:
        self.secret = secret
        self.algorithm = algorithm
        self.issuer = issuer

    def issue(self, claims: dict[str, Any], ttl_seconds: int, token_type: str) -> str:
        now = int(time.time())
        payload = {**claims, "iss": self.issuer, "iat": now, "nbf": now, "exp": now + ttl_seconds, "typ": token_type}
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def verify(self, token: str, token_type: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(token, self.secret, algorithms=[self.algorithm], issuer=self.issuer,
                                options={"require": ["exp", "iat", "typ"]})
        except jwt.PyJWTError as e:
            raise TokenError(f"invalid token: {type(e).__name__}") from e
        if claims.get("typ") != token_type:
            raise TokenError("wrong token type")
        return claims

    def staff_token(self, *, user_id: str, tenant_id: str, roles: list[str], ttl: int) -> str:
        return self.issue({"sub": user_id, "tenant_id": tenant_id, "roles": roles}, ttl, "access")

    def session_token(self, *, session_id: str, tenant_id: str, ttl: int) -> str:
        return self.issue({"sub": session_id, "tenant_id": tenant_id}, ttl, "session")


def verify_customer_assertion(token: str, *, secret: str, tenant_slug: str) -> dict[str, Any]:
    """Validate a customer assertion issued by the institution's own IdP after app/netbanking login.

    Prototype: HS256 shared secret. Production: RS256/ES256 verified against the institution's JWKS.
    Required claims: sub (bank customer id), aud (tenant slug), exp, amr (auth methods used).
    """
    try:
        return jwt.decode(token, secret, algorithms=["HS256", "RS256", "ES256"], audience=tenant_slug,
                          options={"require": ["sub", "exp", "aud"]})
    except jwt.PyJWTError as e:
        raise TokenError(f"invalid customer assertion: {type(e).__name__}") from e
