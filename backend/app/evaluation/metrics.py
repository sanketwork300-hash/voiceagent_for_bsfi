from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any


@dataclass
class TurnResult:
    scenario: str
    channel: str
    turn: int
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    response: str = ""


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


def summarize(results: list[TurnResult]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for channel in sorted({r.channel for r in results}):
        rs = [r for r in results if r.channel == channel]
        dims: dict[str, list[bool]] = {}
        for r in rs:
            for k, v in r.checks.items():
                dims.setdefault(k, []).append(v)
        scen: dict[str, bool] = {}
        for r in rs:
            scen[r.scenario] = scen.get(r.scenario, True) and r.passed
        lat = [r.latency_ms for r in rs]
        out[channel] = {
            "turns": len(rs),
            "scenarios": len(scen),
            "scenario_pass_rate": round(sum(scen.values()) / max(1, len(scen)), 3),
            "turn_pass_rate": round(sum(r.passed for r in rs) / max(1, len(rs)), 3),
            "accuracy": {k: round(sum(v) / len(v), 3) for k, v in dims.items()},
            "latency_ms": {"p50": round(median(lat), 1) if lat else 0.0, "p95": round(_p95(lat), 1)},
        }
    return out
