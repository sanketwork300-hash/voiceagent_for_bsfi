"""Staff login and customer authentication-state transitions.

Customer authentication states (lowest -> highest):
  UNAUTHENTICATED -> IDENTIFIED (caller-id / customer ref) -> PARTIALLY_AUTHENTICATED (knowledge factor,
  voice biometric) -> FULLY_AUTHENTICATED (bank IdP assertion or login OTP) -> TRANSACTION_AUTHENTICATED
  (transaction OTP bound to one specific pending action; single use).

The OTP itself never touches our storage or the LLM: it is forwarded to the institution's verify API and
discarded. Only the institution's challenge id is kept.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

from sqlalchemy import select

from app.agents.state import AuthChallenge, SessionState
from app.auth.jwt import verify_customer_assertion
from app.database.models import Tenant, User
from app.database.session import Database
from app.domain import AuthMethod, AuthState
from app.observability import metrics
from app.security.audit import AuditLogger
from app.tools.router import ToolGateway
from app.tools.schemas import ToolContext


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)


class StaffAuthService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def authenticate(self, tenant_slug: str, email: str, password: str) -> tuple[User, Tenant] | None:
        async with self.db.session() as s:
            tenant = (await s.execute(select(Tenant).where(Tenant.slug == tenant_slug, Tenant.is_active.is_(True)))).scalar_one_or_none()
            if tenant is None:
                verify_password(password, hash_password("timing-equaliser"))
                return None
            user = (await s.execute(select(User).where(User.tenant_id == tenant.id, User.email == email.lower(),
                                                       User.is_active.is_(True)))).scalar_one_or_none()
        if user is None or not verify_password(password, user.password_hash):
            return None
        return user, tenant


@dataclass
class AuthOutcome:
    success: bool
    message: str
    locked: bool = False
    masked_destination: str | None = None


def _elevate(state: SessionState, to: AuthState, method: AuthMethod) -> None:
    if to.level > state.authentication_state.level:
        state.authentication_state = to
    if method.value not in state.auth_methods:
        state.auth_methods.append(method.value)


class CustomerAuthService:
    def __init__(self, db: Database, gateway: ToolGateway, audit: AuditLogger, assertion_secret: str, max_failures: int = 3) -> None:
        self.db = db
        self.gateway = gateway
        self.audit = audit
        self.assertion_secret = assertion_secret
        self.max_failures = max_failures

    def _ctx(self, state: SessionState) -> ToolContext:
        return ToolContext(tenant_id=state.tenant_id, session_id=state.session_id, conversation_id=state.conversation_id,
                           customer_id=state.customer_id, channel=state.channel, auth_state=state.authentication_state,
                           auth_methods=state.auth_methods)

    async def apply_assertion(self, state: SessionState, assertion: str, tenant_slug: str) -> AuthOutcome:
        claims = verify_customer_assertion(assertion, secret=self.assertion_secret, tenant_slug=tenant_slug)
        if state.customer_id and state.customer_id != claims["sub"]:
            return AuthOutcome(False, "assertion is for a different customer")
        state.customer_id = claims["sub"]
        amr = set(claims.get("amr", []))
        level = AuthState.FULLY_AUTHENTICATED if amr & {"pwd", "otp", "mfa", "bio_device", "mpin"} else AuthState.IDENTIFIED
        _elevate(state, level, AuthMethod.CUSTOMER_ASSERTION)
        await self._audit(state, "auth.assertion_accepted", "success", {"amr": sorted(amr), "level": level.value})
        metrics.auth_events.labels(state.channel.value, "customer_assertion", "success").inc()
        return AuthOutcome(True, "authenticated")

    async def identify(self, state: SessionState, *, phone: str | None = None, customer_id: str | None = None,
                       method: AuthMethod = AuthMethod.CALLER_ID) -> AuthOutcome:
        res = await self.gateway.execute_internal("lookup_customer", {k: v for k, v in {"phone": phone, "customer_ref": customer_id}.items() if v},
                                                  self._ctx(state))
        if not res.ok or not (res.data or {}).get("customer_id"):
            metrics.auth_events.labels(state.channel.value, method.value, "failure").inc()
            return AuthOutcome(False, "customer not found")
        state.customer_id = res.data["customer_id"]
        _elevate(state, AuthState.IDENTIFIED, method)
        await self._audit(state, "auth.identified", "success", {"method": method.value})
        metrics.auth_events.labels(state.channel.value, method.value, "success").inc()
        return AuthOutcome(True, "identified", masked_destination=res.data.get("phone_masked"))

    async def voice_biometric(self, state: SessionState, match_score: float, threshold: float = 0.85) -> AuthOutcome:
        """Voice biometrics can lift to PARTIALLY_AUTHENTICATED only — never enough for high-risk operations."""
        ok = state.customer_id is not None and match_score >= threshold
        if ok:
            _elevate(state, AuthState.PARTIALLY_AUTHENTICATED, AuthMethod.VOICE_BIOMETRIC)
        metrics.auth_events.labels(state.channel.value, "voice_biometric", "success" if ok else "failure").inc()
        await self._audit(state, "auth.voice_biometric", "success" if ok else "failure", {"score": round(match_score, 2)})
        return AuthOutcome(ok, "voice verified" if ok else "voice not verified")

    async def start_otp(self, state: SessionState, *, purpose: str, action_hash: str | None = None,
                        action_summary: str | None = None) -> AuthOutcome:
        if not state.customer_id:
            return AuthOutcome(False, "customer must be identified before OTP")
        if state.auth_failures >= self.max_failures:
            return AuthOutcome(False, "too many failed attempts", locked=True)
        res = await self.gateway.execute_internal("send_otp", {"purpose": purpose, "context": (action_summary or "")[:120]},
                                                  self._ctx(state))
        if not res.ok:
            return AuthOutcome(False, res.error or "could not send OTP")
        state.auth_challenge = AuthChallenge(challenge_id=res.data["challenge_id"], purpose=purpose, action_hash=action_hash,
                                             masked_destination=res.data.get("destination_masked"))
        await self._audit(state, "auth.otp_sent", "success", {"purpose": purpose})
        return AuthOutcome(True, "otp sent", masked_destination=res.data.get("destination_masked"))

    async def verify_otp(self, state: SessionState, code: str) -> AuthOutcome:
        ch = state.auth_challenge
        if ch is None:
            return AuthOutcome(False, "no OTP challenge in progress")
        res = await self.gateway.execute_internal("verify_otp", {"challenge_id": ch.challenge_id, "otp": code}, self._ctx(state))
        method = AuthMethod.TRANSACTION_OTP if ch.purpose == "transaction" else AuthMethod.OTP
        if res.ok and (res.data or {}).get("verified"):
            state.auth_challenge = None
            state.auth_failures = 0
            if ch.purpose == "transaction":
                _elevate(state, AuthState.TRANSACTION_AUTHENTICATED, method)
                state.txn_auth_action_hash = ch.action_hash
                if AuthMethod.OTP.value not in state.auth_methods:
                    state.auth_methods.append(AuthMethod.OTP.value)
            else:
                _elevate(state, AuthState.FULLY_AUTHENTICATED, method)
            metrics.auth_events.labels(state.channel.value, method.value, "success").inc()
            await self._audit(state, "auth.otp_verified", "success", {"purpose": ch.purpose})
            return AuthOutcome(True, "verified")
        state.auth_failures += 1
        ch.attempts += 1
        locked = state.auth_failures >= self.max_failures
        if locked:
            state.auth_challenge = None
        metrics.auth_events.labels(state.channel.value, method.value, "failure").inc()
        await self._audit(state, "auth.otp_failed", "failure", {"purpose": ch.purpose, "failures": state.auth_failures})
        return AuthOutcome(False, "incorrect OTP", locked=locked)

    @staticmethod
    def consume_transaction_auth(state: SessionState) -> None:
        """Transaction authentication is single-use: drop back to FULLY after the bound action executes."""
        if state.authentication_state == AuthState.TRANSACTION_AUTHENTICATED:
            state.authentication_state = AuthState.FULLY_AUTHENTICATED
        state.txn_auth_action_hash = None
        if AuthMethod.TRANSACTION_OTP.value in state.auth_methods:
            state.auth_methods.remove(AuthMethod.TRANSACTION_OTP.value)

    async def _audit(self, state: SessionState, event: str, outcome: str, payload: dict) -> None:
        await self.audit.record(state.tenant_id, event, actor_type="customer", actor_id=state.customer_id,
                                session_id=state.session_id, conversation_id=state.conversation_id,
                                channel=state.channel.value, outcome=outcome, payload=payload)
