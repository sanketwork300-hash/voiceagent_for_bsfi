"""Embedding providers: OpenAI-compatible endpoint, or deterministic feature hashing for offline use."""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod

import httpx


class EmbeddingProvider(ABC):
    dimensions: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


class OpenAICompatibleEmbeddings(EmbeddingProvider):
    def __init__(self, base_url: str, api_key: str | None, model: str, dimensions: int, batch_size: int = 64) -> None:
        self.dimensions = dimensions
        self.model = model
        self.batch_size = batch_size
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=30)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            r = await self._client.post("/embeddings", json={"model": self.model, "input": texts[i : i + self.batch_size],
                                                             "dimensions": self.dimensions})
            r.raise_for_status()
            out.extend(d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"]))
        return out


class HashingEmbeddings(EmbeddingProvider):
    """Signed feature hashing over word unigrams/bigrams and character trigrams, L2 normalised.

    Not semantic like a neural model, but deterministic, dependency-free and good enough for offline
    demos/tests; it also gives partial credit for transliteration variants via char n-grams.
    """

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def _features(self, text: str) -> list[str]:
        words = re.findall(r"\w+", text.lower())
        feats = list(words) + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
        for w in words:
            padded = f"#{w}#"
            feats += [padded[i : i + 3] for i in range(len(padded) - 2)]
        return feats

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for f in self._features(text):
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            vec[h % self.dimensions] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]
