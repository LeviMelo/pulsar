"""Local embedding providers.

PULSAR talks to exactly one embedding surface: an OpenAI-compatible
``POST /v1/embeddings`` endpoint served locally by LM Studio. Nothing else in the
codebase knows that LM Studio exists, so swapping in llama.cpp, Ollama or a
sentence-transformers process is a config change.

Requirements this abstraction is expected to meet (PLAN §27):
health check, loaded-model verification, model identity, batching, timeout and
retry, deterministic preprocessing, an on-disk cache, normalization, dimension
validation, re-indexing and provenance.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

from ..db import Database, utcnow
from .normalize import clean_text


class EmbeddingUnavailable(RuntimeError):
    """The configured embedding service is not reachable or not loaded."""


@dataclass(frozen=True, slots=True)
class EmbeddingHealth:
    reachable: bool
    model_present: bool
    model: str
    available_models: tuple[str, ...]
    dimension: int
    latency_ms: float
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.reachable and self.model_present and self.dimension > 0


CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_cache (
    provider_key VARCHAR,
    text_sha256  VARCHAR,
    dimension    BIGINT,
    vector       BLOB,
    created_at   VARCHAR,
    PRIMARY KEY (provider_key, text_sha256)
);
"""


class EmbeddingCache:
    """Content-addressed embedding cache kept inside the analytical store.

    Keyed by ``(provider identity, sha256 of the normalized text)``. Changing the
    model, its quantization or the preprocessing therefore misses the cache
    rather than silently mixing vector spaces.
    """

    def __init__(self, db: Database | None):
        self.db = db
        if db is not None:
            with db.connect() as con:
                con.execute(CACHE_SCHEMA)

    def get_many(self, provider_key: str, digests: Sequence[str]) -> dict[str, np.ndarray]:
        if self.db is None or not digests:
            return {}
        placeholders = ",".join("?" for _ in digests)
        with self.db.connect(read_only=True) as con:
            rows = con.execute(
                f"SELECT text_sha256, dimension, vector FROM embedding_cache "
                f"WHERE provider_key=? AND text_sha256 IN ({placeholders})",
                [provider_key, *digests],
            ).fetchall()
        return {r[0]: np.frombuffer(r[2], dtype=np.float32).reshape(int(r[1])) for r in rows}

    def put_many(self, provider_key: str, items: Iterable[tuple[str, np.ndarray]]) -> int:
        if self.db is None:
            return 0
        now = utcnow()
        n = 0
        with self.db.connect() as con:
            for digest, vector in items:
                vec = np.asarray(vector, dtype=np.float32)
                con.execute(
                    "INSERT OR REPLACE INTO embedding_cache VALUES (?,?,?,?,?)",
                    [provider_key, digest, int(vec.shape[0]), vec.tobytes(), now],
                )
                n += 1
        return n

    def clear(self, provider_key: str | None = None) -> int:
        if self.db is None:
            return 0
        with self.db.connect() as con:
            if provider_key:
                n = int(con.execute("SELECT COUNT(*) FROM embedding_cache WHERE provider_key=?", [provider_key]).fetchone()[0])
                con.execute("DELETE FROM embedding_cache WHERE provider_key=?", [provider_key])
            else:
                n = int(con.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0])
                con.execute("DELETE FROM embedding_cache")
        return n

    def stats(self) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        with self.db.connect(read_only=True) as con:
            return [
                {"provider_key": r[0], "vectors": int(r[1]), "dimension": int(r[2] or 0), "newest": r[3]}
                for r in con.execute(
                    "SELECT provider_key, COUNT(*), MAX(dimension), MAX(created_at) "
                    "FROM embedding_cache GROUP BY provider_key ORDER BY 2 DESC"
                ).fetchall()
            ]


class OpenAICompatibleEmbeddingProvider:
    """Batched embeddings from any OpenAI-compatible ``/v1/embeddings`` server."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:1234/v1",
        model: str = "text-embedding-qwen3-embedding-4b",
        api_key: str = "lm-studio",
        batch_size: int = 16,
        timeout: float = 300.0,
        max_retries: int = 3,
        max_chars: int = 6000,
        expected_dimension: int | None = None,
        cache: EmbeddingCache | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.batch_size = int(batch_size)
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.max_chars = int(max_chars)
        self.expected_dimension = expected_dimension
        self.cache = cache
        self._dimension = int(expected_dimension or 0)
        self._resolved_model: str | None = None

    # -- identity / provenance ----------------------------------------------

    def identity(self) -> dict[str, Any]:
        return {
            "provider": "openai_compatible",
            "base_url": self.base_url,
            "model": self._resolved_model or self.model,
            "max_chars": self.max_chars,
            "dimension": self._dimension,
        }

    @property
    def provider_key(self) -> str:
        payload = json.dumps(
            {k: v for k, v in self.identity().items() if k != "dimension"},
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @property
    def dimension(self) -> int:
        if not self._dimension:
            self._dimension = int(self.embed(["dimensão"]).shape[1])
        return self._dimension

    # -- transport -----------------------------------------------------------

    def _client(self):
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise EmbeddingUnavailable("httpx is required for the embedding provider") from exc
        return httpx.Client(timeout=self.timeout, headers={"Authorization": f"Bearer {self.api_key}"})

    def list_models(self) -> list[str]:
        with self._client() as client:
            resp = client.get(f"{self.base_url}/models")
            resp.raise_for_status()
            return [str(m.get("id")) for m in resp.json().get("data", [])]

    def health(self) -> EmbeddingHealth:
        started = time.perf_counter()
        try:
            models = self.list_models()
        except Exception as exc:
            return EmbeddingHealth(False, False, self.model, (), 0, (time.perf_counter() - started) * 1000,
                                   f"{type(exc).__name__}: {exc}")
        resolved = self._resolve_model(models)
        if resolved is None:
            return EmbeddingHealth(True, False, self.model, tuple(models), 0,
                                   (time.perf_counter() - started) * 1000,
                                   f"model {self.model!r} not served; available: {', '.join(models) or 'none'}")
        self._resolved_model = resolved
        try:
            dim = int(self._embed_batch(["verificação de integridade"]).shape[1])
        except Exception as exc:
            return EmbeddingHealth(True, True, resolved, tuple(models), 0,
                                   (time.perf_counter() - started) * 1000, f"{type(exc).__name__}: {exc}")
        if self.expected_dimension and dim != self.expected_dimension:
            return EmbeddingHealth(True, True, resolved, tuple(models), dim,
                                   (time.perf_counter() - started) * 1000,
                                   f"dimension mismatch: expected {self.expected_dimension}, server returned {dim}")
        self._dimension = dim
        return EmbeddingHealth(True, True, resolved, tuple(models), dim,
                               (time.perf_counter() - started) * 1000, "ok")

    def _resolve_model(self, models: Sequence[str]) -> str | None:
        """Accept the configured alias, an exact id, or an unambiguous substring."""
        if self.model in models:
            return self.model
        lowered = self.model.lower().replace("_", "-")
        exact = [m for m in models if m.lower() == lowered]
        if exact:
            return exact[0]
        partial = [m for m in models if lowered in m.lower() or m.lower() in lowered]
        return partial[0] if len(partial) == 1 else None

    def _embed_batch(self, texts: Sequence[str]) -> np.ndarray:
        payload = {"model": self._resolved_model or self.model, "input": list(texts)}
        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with self._client() as client:
                    resp = client.post(f"{self.base_url}/embeddings", json=payload)
                    resp.raise_for_status()
                    data = resp.json()["data"]
                data.sort(key=lambda d: int(d.get("index", 0)))
                return np.asarray([d["embedding"] for d in data], dtype=np.float32)
            except Exception as exc:  # network hiccup, model reload, OOM under load
                last = exc
                if attempt + 1 < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
        raise EmbeddingUnavailable(f"embedding request failed after {self.max_retries} attempts: {last}")

    # -- public API ----------------------------------------------------------

    def prepare(self, text: str) -> str:
        """Deterministic preprocessing. Identical text must hash identically."""
        cleaned = clean_text(text) or "pesquisa"
        return cleaned[: self.max_chars]

    def embed(self, texts: Sequence[str], *, progress=None) -> np.ndarray:
        prepared = [self.prepare(t) for t in texts]
        digests = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in prepared]
        key = self.provider_key

        cached: dict[str, np.ndarray] = {}
        if self.cache is not None:
            for i in range(0, len(digests), 400):
                cached.update(self.cache.get_many(key, list(dict.fromkeys(digests[i: i + 400]))))

        missing_order: list[str] = []
        missing_text: dict[str, str] = {}
        for digest, text in zip(digests, prepared):
            if digest not in cached and digest not in missing_text:
                missing_text[digest] = text
                missing_order.append(digest)

        if missing_order and self._resolved_model is None:
            health = self.health()
            if not health.ok:
                raise EmbeddingUnavailable(health.detail or "embedding service unavailable")

        fresh: list[tuple[str, np.ndarray]] = []
        for start in range(0, len(missing_order), self.batch_size):
            chunk = missing_order[start: start + self.batch_size]
            vectors = self._embed_batch([missing_text[d] for d in chunk])
            if self._dimension and vectors.shape[1] != self._dimension:
                raise EmbeddingUnavailable(
                    f"dimension drift: cache/model expected {self._dimension}, got {vectors.shape[1]}"
                )
            self._dimension = int(vectors.shape[1])
            for digest, vector in zip(chunk, vectors):
                cached[digest] = vector
                fresh.append((digest, vector))
            if progress is not None:
                progress(min(start + self.batch_size, len(missing_order)), len(missing_order))
        if fresh and self.cache is not None:
            self.cache.put_many(key, fresh)

        if not cached:
            return np.zeros((len(texts), self._dimension or 1), dtype=np.float32)
        dim = len(next(iter(cached.values())))
        return np.vstack([cached.get(d, np.zeros(dim, dtype=np.float32)) for d in digests])


def provider_from_config(config, db: Database | None = None) -> OpenAICompatibleEmbeddingProvider:
    """Build the configured provider. See ``[embedding]`` in config/default.toml."""
    section = config.raw.get("embedding", {}) if hasattr(config, "raw") else dict(config)
    return OpenAICompatibleEmbeddingProvider(
        base_url=section.get("base_url", "http://127.0.0.1:1234/v1"),
        model=section.get("model", "text-embedding-qwen3-embedding-4b"),
        api_key=section.get("api_key", "lm-studio"),
        batch_size=int(section.get("batch_size", 16)),
        timeout=float(section.get("timeout_seconds", 300.0)),
        max_retries=int(section.get("max_retries", 3)),
        max_chars=int(section.get("max_chars", 6000)),
        expected_dimension=section.get("expected_dimension") or None,
        cache=EmbeddingCache(db) if db is not None else None,
    )
