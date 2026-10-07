"""Runs the shared scenario suite on BOTH chat and voice; every scenario must pass on both channels."""

from __future__ import annotations

import pytest

from app.domain import Channel
from app.evaluation.runner import EvaluationRunner
from app.evaluation.scenarios import SCENARIOS
from mock_bank import data as bank_data


async def _reset() -> None:
    bank_data.reset()


@pytest.mark.parametrize("channel", [Channel.CHAT, Channel.VOICE])
async def test_suite_passes_on_channel(container, tenant, channel):
    report = await EvaluationRunner(container, reset_hook=_reset).run(tenant.id, channels=[channel])
    failures = [f"{r['scenario']}#{r['turn']}: {r['failures']} -> {r['response'][:160]}" for r in report["results"] if not r["passed"]]
    assert not failures, "\n".join(failures)
    m = report["metrics"][channel.value]
    assert m["scenarios"] == len(SCENARIOS)
    assert m["scenario_pass_rate"] == 1.0


async def test_same_behaviour_across_channels(container, tenant):
    """Chat and voice must make identical decisions for identical input (same runtime, same policy)."""
    report = await EvaluationRunner(container, reset_hook=_reset).run(tenant.id, channels=[Channel.CHAT, Channel.VOICE])
    by_key: dict[tuple, dict] = {}
    for r in report["results"]:
        by_key.setdefault((r["scenario"], r["turn"]), {})[r["channel"]] = r["passed"]
    assert all(v["chat"] == v["voice"] for v in by_key.values())
