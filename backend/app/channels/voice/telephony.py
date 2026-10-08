"""Phone calls over LiveKit Phone Numbers / LiveKit SIP: inbound call identification, provisioning, transfer, outbound.

    PSTN caller ─► LiveKit phone number (or carrier SIP trunk) ─► LiveKit SIP ─► dispatch rule (one room per caller,
    dispatches the voice agent) ─► voice worker ─► VoiceSessionService ─► AgentRuntime

This module only deals with telephony identity and LiveKit's SIP API. It never decides anything about banking:
a caller's number is an *identity lookup signal*, never authentication.

Phone-number hygiene: the raw caller number is read from the SIP participant once, used for the customer lookup,
and otherwise only kept as a masked display value (`XXXXXX3210`) and a keyed hash (`caller_ref`) for correlating
calls. It is never put in session state, room names we persist, logs or prompts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.config import Settings
from app.database.models import Tenant
from app.database.session import Database

log = logging.getLogger(__name__)

# LiveKit SIP participant attributes (set by LiveKit for every SIP participant)
ATTR_CALLER = "sip.phoneNumber"  # the caller (inbound) / callee (outbound)
ATTR_DIALED = "sip.trunkPhoneNumber"  # the LiveKit phone number / trunk number that was called
ATTR_CALL_ID = "sip.callID"
ATTR_TRUNK_ID = "sip.trunkID"
ATTR_RULE_ID = "sip.ruleID"
ATTR_CALL_STATUS = "sip.callStatus"  # active | automation | dialing | ringing | hangup
# Set by our own dispatch rule (server-side configuration, see `TelephonyService.ensure_dispatch_rule`)
ATTR_TENANT = "bfsi.tenant"

_DIGITS = re.compile(r"\d")


def normalize_number(number: str | None, default_country: str = "91") -> str | None:
    """E.164-ish normalisation: "+91 98765-43210", "09876543210", "9876543210" -> "+919876543210"."""
    if not number:
        return None
    raw = number.strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if raw.startswith("+"):
        return "+" + digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"+{default_country}{digits}"
    return "+" + digits


def mask_number(number: str | None) -> str | None:
    digits = re.sub(r"\D", "", number or "")
    return ("X" * 6 + digits[-4:]) if len(digits) >= 4 else None


def mask_digits(text: str | None) -> str | None:
    """Mask phone-like digit runs inside identifiers LiveKit derives from the number (room names, `sip_+91...`)."""
    if text is None:
        return None
    return re.sub(r"\+?\d{6,}", lambda m: "X" * 6 + m.group(0)[-4:], text)


def caller_ref(number: str | None, key: str) -> str | None:
    """Stable internal caller identity (keyed hash): correlate calls without storing or passing the number."""
    norm = normalize_number(number)
    if not norm:
        return None
    return "ph_" + hmac.new(key.encode(), norm.encode(), hashlib.sha256).hexdigest()[:24]


def sip_caller_number(participant_attributes: dict[str, str]) -> str | None:
    return participant_attributes.get(ATTR_CALLER)


def sip_dialed_number(participant_attributes: dict[str, str]) -> str | None:
    return participant_attributes.get(ATTR_DIALED)


@dataclass
class InboundCall:
    """What LiveKit tells us about one inbound SIP participant. `caller_number` stays in memory only."""

    room_name: str
    participant_identity: str
    caller_number: str | None = None
    dialed_number: str | None = None
    sip_call_id: str | None = None
    trunk_id: str | None = None
    rule_id: str | None = None
    tenant_hint: str | None = None
    attributes: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_participant(cls, room_name: str, identity: str, attributes: dict[str, str]) -> InboundCall:
        a = dict(attributes)
        return cls(room_name=room_name, participant_identity=identity, caller_number=a.get(ATTR_CALLER),
                   dialed_number=a.get(ATTR_DIALED), sip_call_id=a.get(ATTR_CALL_ID), trunk_id=a.get(ATTR_TRUNK_ID),
                   rule_id=a.get(ATTR_RULE_ID), tenant_hint=a.get(ATTR_TENANT), attributes=a)

    def __repr__(self) -> str:  # never print the raw caller number
        return (f"InboundCall(room={mask_digits(self.room_name)!r}, caller={mask_number(self.caller_number)!r}, "
                f"dialed={self.dialed_number!r}, sip_call_id={self.sip_call_id!r})")


async def resolve_tenant(db: Database, call: InboundCall) -> Tenant | None:
    """Institution for an inbound call: the tenant attribute our dispatch rule sets, else the dialled number
    listed in `tenant.settings.sip_numbers` (compared in normalised form)."""
    async with db.session() as s:
        tenants = (await s.execute(select(Tenant).where(Tenant.is_active.is_(True)))).scalars().all()
    if call.tenant_hint:
        hit = next((t for t in tenants if t.slug == call.tenant_hint or t.id == call.tenant_hint), None)
        if hit is not None:
            return hit
    dialed = normalize_number(call.dialed_number)
    if not dialed:
        return None
    for t in tenants:
        if dialed in {normalize_number(n) for n in (t.settings or {}).get("sip_numbers", [])}:
            return t
    return None


async def tenant_for_dialed_number(db: Database, dialed: str | None) -> Tenant | None:
    """Backwards-compatible helper: resolve by dialled number only."""
    return await resolve_tenant(db, InboundCall(room_name="", participant_identity="", dialed_number=dialed))


class TelephonyService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _api(self):
        from livekit import api

        return api.LiveKitAPI(url=self.settings.livekit_url.replace("ws", "http", 1),
                              api_key=self.settings.livekit_api_key, api_secret=self.settings.livekit_api_secret)

    async def ensure_dispatch_rule(self, *, tenant_slug: str, numbers: list[str] | None = None,
                                   phone_number_ids: list[str] | None = None, rule_id: str | None = None,
                                   name: str | None = None) -> str:
        """Create, or adopt and update, the inbound dispatch rule for a tenant.

        * `rule_id`: adopt an existing rule (e.g. one made in the LiveKit dashboard). Its room settings, numbers and
          trunks are kept; we only make sure it dispatches the voice agent and tags callers with `bfsi.tenant`.
        * otherwise a rule is created (or updated by name) for `phone_number_ids` — LiveKit Phone Numbers are attached
          to a rule by their `PN_PPN_…` id — and/or `numbers` (E.164; carrier SIP trunks).

        Result: one *individual* room per caller, `LIVEKIT_AGENT_NAME` dispatched into it, and `bfsi.tenant=<slug>` on
        the caller's SIP participant so the worker knows the institution without trusting anything the caller controls."""
        from livekit import api

        agent = self.settings.livekit_agent_name
        lk = self._api()
        try:
            existing = (await lk.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())).items
            rule_name = f"{name or self.settings.livekit_sip_dispatch_rule_name}-{tenant_slug}"
            current = next((r for r in existing if (rule_id and r.sip_dispatch_rule_id == rule_id) or
                            (not rule_id and r.name == rule_name)), None)
            if rule_id and current is None:
                raise LookupError(f"dispatch rule {rule_id} not found")
            if current is not None:
                info = api.SIPDispatchRuleInfo()
                info.CopyFrom(current)
                info.attributes[ATTR_TENANT] = tenant_slug
                if not any(a.agent_name == agent for a in info.room_config.agents):
                    info.room_config.agents.append(api.RoomAgentDispatch(
                        agent_name=agent, metadata=json.dumps({"tenant": tenant_slug, "source": "sip"})))
                info.trunk_ids.extend(t for t in (phone_number_ids or []) if t not in info.trunk_ids)
                info.numbers.extend(n for n in (normalize_number(x) or x for x in numbers or []) if n not in info.numbers)
                updated = await lk.sip.update_dispatch_rule(current.sip_dispatch_rule_id, info)
                return updated.sip_dispatch_rule_id
            if not (numbers or phone_number_ids):
                raise ValueError("give phone_number_ids (PN_PPN_…) and/or numbers, or an existing rule_id")
            info = api.SIPDispatchRuleInfo(
                name=rule_name, trunk_ids=list(phone_number_ids or []), numbers=[normalize_number(n) or n for n in numbers or []],
                rule=api.SIPDispatchRule(dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                    room_prefix=self.settings.livekit_sip_room_prefix)),
                attributes={ATTR_TENANT: tenant_slug},
                room_config=api.RoomConfiguration(agents=[api.RoomAgentDispatch(
                    agent_name=agent, metadata=json.dumps({"tenant": tenant_slug, "source": "sip"}))]),
            )
            created = await lk.sip.create_dispatch_rule(api.CreateSIPDispatchRuleRequest(dispatch_rule=info))
            return created.sip_dispatch_rule_id
        finally:
            await lk.aclose()

    async def transfer_to_human(self, *, room: str, participant_identity: str, transfer_to: str | None = None) -> bool:
        """SIP REFER the caller to the contact-centre queue (cold transfer). Context travels via the handoff record."""
        from livekit import api

        lk = self._api()
        try:
            await lk.sip.transfer_sip_participant(api.TransferSIPParticipantRequest(
                room_name=room, participant_identity=participant_identity,
                transfer_to=transfer_to or self.settings.sip_human_transfer_uri, play_dialtone=True))
            return True
        except Exception:
            log.exception("SIP transfer failed")
            return False
        finally:
            await lk.aclose()

    async def hang_up(self, room: str) -> None:
        """End a phone call: deleting the per-call room disconnects the SIP participant."""
        from livekit import api

        lk = self._api()
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room))
        except Exception:
            log.warning("could not delete call room", extra={"room": mask_digits(room)})
        finally:
            await lk.aclose()

    async def place_outbound_call(self, *, room: str, phone_number: str, identity: str, metadata: dict[str, Any] | None = None) -> None:
        """Outbound call (e.g. callback after handoff, payment reminder) dialled into a LiveKit room."""
        from livekit import api

        trunk = self.settings.livekit_sip_outbound_trunk_id
        if not trunk:
            raise RuntimeError("LIVEKIT_SIP_OUTBOUND_TRUNK_ID not configured")
        lk = self._api()
        try:
            await lk.sip.create_sip_participant(api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk, sip_call_to=phone_number, room_name=room,
                participant_identity=identity, participant_metadata=json.dumps(metadata or {}), wait_until_answered=True))
        finally:
            await lk.aclose()
