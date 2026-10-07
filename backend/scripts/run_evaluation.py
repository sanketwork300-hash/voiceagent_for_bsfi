"""Run the evaluation suite (chat + voice) against the configured backend.

Usage: python -m scripts.run_evaluation [--category security] [--channel chat]
"""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.container import Container
from app.database.models import Tenant
from app.domain import Channel
from app.evaluation.runner import EvaluationRunner
from scripts.seed import DEMO_SLUG, seed_demo


async def _main(args) -> None:
    settings = get_settings()
    c = Container(settings)
    await c.startup(create_schema=True)
    await seed_demo(c)
    async with c.db.session() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.slug == DEMO_SLUG))).scalar_one()

    async def reset() -> None:  # sandbox only: restore mock-bank fixtures between scenarios
        async with httpx.AsyncClient() as h:
            await h.post(f"{settings.mock_bank_url}/_admin/reset")

    report = await EvaluationRunner(c, reset_hook=reset).run(
        tenant.id, channels=[Channel(x) for x in args.channel] if args.channel else None,
        categories=args.category or None)
    print(json.dumps(report["metrics"], indent=2))
    for r in report["results"]:
        if not r["passed"]:
            print(f"FAIL {r['channel']}/{r['scenario']}#{r['turn']}: {r['failures']} :: {r['response'][:120]}")
    print(f"overall pass rate: {report['pass_rate']}")
    await c.shutdown()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--category", action="append")
    p.add_argument("--channel", action="append")
    asyncio.run(_main(p.parse_args()))
