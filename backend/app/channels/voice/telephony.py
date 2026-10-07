"""Phone/SIP: inbound call identification, outbound calls, and call transfer to the contact centre."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.config import Settings
from app.database.models import Tenant
from app.database.session import Database

log = logging.getLogger(__name__)


def sip_caller_number(participant_attributes: dict[str, str]) -> str | None:
    return participant_attributes.get("sip.phoneNumber")


def sip_dialed_number(participant_attributes: dict[str, str]) -> str | None:
    return participant_attributes.get("sip.trunkPhoneNumber")


async def tenant_for_dialed_number(db: Database, dialed: str | None) -> Tenant | None:
    """Each institution's IVR numbers are configured in `tenant.settings.sip_numbers`."""
    if not dialed:
        return None
    async with db.session() as s:
        for t in (await s.execute(select(Tenant).where(Tenant.is_active.is_(True)))).scalars():
            if dialed in (t.settings or {}).get("sip_numbers", []):
                return t
    return None


class TelephonyService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _api(self):
        from livekit import api

        return api.LiveKitAPI(url=self.settings.livekit_url.replace("ws", "http", 1),
                              api_key=self.settings.livekit_api_key, api_secret=self.settings.livekit_api_secret)

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

    async def place_outbound_call(self, *, room: str, phone_number: str, identity: str, metadata: dict[str, Any] | None = None) -> None:
        """Outbound call (e.g. callback after handoff, payment reminder) dialled into a LiveKit room."""
        import json

        from livekit import api

        if not self.settings.sip_trunk_id:
            raise RuntimeError("SIP_TRUNK_ID not configured")
        lk = self._api()
        try:
            await lk.sip.create_sip_participant(api.CreateSIPParticipantRequest(
                sip_trunk_id=self.settings.sip_trunk_id, sip_call_to=phone_number, room_name=room,
                participant_identity=identity, participant_metadata=json.dumps(metadata or {}), wait_until_answered=True))
        finally:
            await lk.aclose()
