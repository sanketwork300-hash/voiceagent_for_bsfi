"""Bind a tenant's LiveKit phone numbers to the voice agent.

    python -m scripts.provision_telephony --tenant demo-bank --rule-id SDR_xxx          # adopt a dashboard-made rule
    python -m scripts.provision_telephony --tenant demo-bank --phone-number-id PN_PPN_xxx [--number +1415…]
    python -m scripts.provision_telephony --list                                        # show rules (read-only)

1. Get the number(s) first: buy a LiveKit Phone Number in LiveKit Cloud (dashboard / `lk` CLI), or bring a carrier SIP
   trunk into LiveKit SIP (create an inbound trunk for it). Check which countries LiveKit sells numbers in; for Indian
   numbers a carrier SIP trunk into LiveKit SIP is the usual route — the rest of this setup is identical.
2. This script creates/updates a SIP dispatch rule for those numbers: one *individual* room per caller, the voice agent
   (`LIVEKIT_AGENT_NAME`) dispatched into it, and `bfsi.tenant=<slug>` set on the caller's SIP participant.
3. It records the numbers in `tenant.settings.sip_numbers` (a fallback for resolving the institution by dialled number).

Uses LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET. Nothing here is hard-coded.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.channels.voice.telephony import TelephonyService, normalize_number
from app.config import get_settings
from app.database.models import Tenant
from app.database.session import Database


async def list_rules() -> None:
    from livekit import api

    lk = TelephonyService(get_settings())._api()
    try:
        for r in (await lk.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())).items:
            print(f"{r.sip_dispatch_rule_id}  {r.name!r}  phone/trunks={list(r.trunk_ids)} numbers={list(r.numbers)} "
                  f"agents={[a.agent_name for a in r.room_config.agents]} attributes={dict(r.attributes)}")
    finally:
        await lk.aclose()


async def main(tenant_slug: str, numbers: list[str], phone_number_ids: list[str], rule_id: str | None) -> None:
    settings = get_settings()
    numbers = [n for n in (normalize_number(x) for x in numbers) if n]
    if not (numbers or phone_number_ids or rule_id):
        raise SystemExit("give --rule-id, --phone-number-id and/or --number")
    db = Database(settings.database_url)
    try:
        async with db.session() as s:
            tenant = (await s.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one_or_none()
            if tenant is None:
                raise SystemExit(f"unknown tenant {tenant_slug}")
            if numbers:
                current = list((tenant.settings or {}).get("sip_numbers", []))
                tenant.settings = {**(tenant.settings or {}), "sip_numbers": sorted({*current, *numbers})}
        rid = await TelephonyService(settings).ensure_dispatch_rule(tenant_slug=tenant_slug, numbers=numbers,
                                                                    phone_number_ids=phone_number_ids, rule_id=rule_id)
        print(f"dispatch rule {rid} -> agent '{settings.livekit_agent_name}', callers tagged bfsi.tenant={tenant_slug}")
    finally:
        await db.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tenant")
    ap.add_argument("--number", action="append", default=[])
    ap.add_argument("--phone-number-id", action="append", default=[], help="LiveKit Phone Number id (PN_PPN_…)")
    ap.add_argument("--rule-id", help="adopt an existing dispatch rule")
    ap.add_argument("--list", action="store_true", help="list dispatch rules and exit (read-only)")
    a = ap.parse_args()
    if a.list:
        asyncio.run(list_rules())
    else:
        if not a.tenant:
            raise SystemExit("--tenant is required")
        asyncio.run(main(a.tenant, a.number, a.phone_number_id, a.rule_id))
