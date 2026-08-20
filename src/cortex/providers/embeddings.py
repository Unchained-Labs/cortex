"""OpenAI-compatible embeddings client.

Raw httpx: the surface is one endpoint, and vectors must come back in
order — a shifted batch silently corrupts every later chunk, so reassembly
is by the reported index with an explicit gap check.
"""

from __future__ import annotations

import httpx

from cortex.config import ProviderProfile

_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


class ProviderError(RuntimeError):
    pass


class Embedder:
    def __init__(self, profile: ProviderProfile):
        if not profile.base_url:
            raise ProviderError(f"provider {profile.name!r} has no base_url")
        if not profile.embed_model:
            raise ProviderError(f"provider {profile.name!r} has no embed_model")
        self.profile = profile
        self._base = profile.base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = dict(self.profile.headers)
        headers["Authorization"] = f"Bearer {self.profile.key() or 'not-needed'}"
        return headers

    async def embed(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": self.profile.embed_model, "input": texts}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                res = await client.post(
                    f"{self._base}/embeddings", json=payload, headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self._base} unreachable: {exc}") from exc
        if res.status_code != 200:
            raise ProviderError(f"{self._base} returned {res.status_code}: {res.text[:300]}")
        rows = res.json().get("data") or []
        if len(rows) != len(texts):
            raise ProviderError(f"asked for {len(texts)} embeddings, got {len(rows)}")
        vectors: list[list[float] | None] = [None] * len(texts)
        for row in rows:
            vectors[row["index"]] = row["embedding"]
        if any(v is None for v in vectors):
            raise ProviderError("embedding response left gaps in the batch")
        return vectors  # type: ignore[return-value]
