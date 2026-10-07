"""Concurrency / latency test for the chat API.

    python -m scripts.load_test --users 20 --turns 5 [--base http://localhost:8000]

Each virtual user opens an authenticated session and runs a mixed workload (RAG, bank-data reads,
card status). Reports throughput, error rate and latency percentiles per request type. Voice latency
(STT/TTS/time-to-first-audio) is exported by the LiveKit worker as Prometheus metrics instead.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time
from collections import Counter, defaultdict

import httpx
import jwt

WORKLOAD = [
    ("rag", "What are home loan foreclosure charges?"),
    ("rag", "What is the minimum balance for a savings account?"),
    ("data", "What is my loan balance?"),
    ("data", "What is my account balance?"),
    ("data", "Mera credit card ka outstanding kitna hai?"),
    ("data", "Show my last transactions"),
]


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))] if s else 0.0


async def user(h: httpx.AsyncClient, turns: int, secret: str, lat: dict, errors: list) -> None:
    a = jwt.encode({"sub": "CUST1001", "aud": "demo-bank", "exp": int(time.time()) + 900, "amr": ["pwd"]}, secret, algorithm="HS256")
    r = await h.post("/sessions", json={"tenant": "demo-bank", "customer_assertion": a})
    if r.status_code != 201:
        errors.append(f"session {r.status_code}")
        return
    sid, tok = r.json()["session"]["session_id"], r.json()["session_token"]
    for _ in range(turns):
        kind, text = random.choice(WORKLOAD)
        t0 = time.perf_counter()
        try:
            r = await h.post("/chat/message", json={"session_id": sid, "message": text}, headers={"Authorization": f"Bearer {tok}"})
            if r.status_code != 200 or r.json().get("error"):
                errors.append(f"{kind} {r.status_code}")
                continue
        except httpx.HTTPError as e:
            errors.append(type(e).__name__)
            continue
        lat[kind].append((time.perf_counter() - t0) * 1000)


async def main(args) -> None:
    lat: dict[str, list[float]] = defaultdict(list)
    errors: list[str] = []
    started = time.perf_counter()
    async with httpx.AsyncClient(base_url=args.base, timeout=60, limits=httpx.Limits(max_connections=args.users * 2)) as h:
        await asyncio.gather(*(user(h, args.turns, args.secret, lat, errors) for _ in range(args.users)))
    wall = time.perf_counter() - started
    total = sum(len(v) for v in lat.values())
    print(f"users={args.users} turns/user={args.turns} completed={total} errors={len(errors)} wall={wall:.1f}s "
          f"throughput={total / wall:.1f} turns/s")
    for kind, v in sorted(lat.items()):
        print(f"  {kind:<5} n={len(v):<4} p50={statistics.median(v):7.1f}ms p95={pct(v, .95):7.1f}ms p99={pct(v, .99):7.1f}ms max={max(v):7.1f}ms")
    if errors:
        print("  errors:", dict(Counter(errors)))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--users", type=int, default=20)
    p.add_argument("--turns", type=int, default=5)
    p.add_argument("--secret", default="demo-bank-idp-shared-secret-32bytes!")
    asyncio.run(main(p.parse_args()))
