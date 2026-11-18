"""Local free embeddings for the episodic index (definition 2.7, C54).

Two providers share one seam: any class with ``embed(text)`` and a
``dim`` attribute can be used (``EpisodicStore`` accepts an embedder
as a parameter, so swapping embedders never changes the schema;
the vector dimension is recorded in ``EMBEDDING_DIM`` and changing
it needs a migration of the ``vector(N)`` column).

- ``HashEmbedding`` (default fallback): deterministic token-hash
  embedding, no model, no network, no cost. Similarity measures
  word overlap, not meaning. Good enough for the 11-user MVP and
  it keeps the episodic write + read path testable end to end
  with no new dependency.
- ``FastEmbedEmbedding`` (preferred when available): the real
  BAAI/bge-small-en-v1.5 model through fastembed (MIT), loaded
  lazily and run fully locally, no network at inference time.
  Dimension 384, the same as ``EMBEDDING_DIM``, so it is a
  drop-in replacement for ``HashEmbedding``. Verified live in
  this environment on 18 Nov 2026: the model downloads from
  HuggingFace (about 65 MB, ONNX) and embeds locally; the
  cache was copied to ``~/.cache/fastembed`` so it survives
  restarts. If fastembed or the model is unavailable,
  ``make_embedder("auto")`` falls back to ``HashEmbedding``
  instead of failing.

Convention: English identifiers, English stored values (rev 7).
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path

#: Episodic vector dimension. Must match `vector(N)` in schema.sql.
EMBEDDING_DIM = 384

#: Default real embedding model (384 dimensions, MIT via fastembed).
DEFAULT_FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"

#: Cache directory name fastembed uses for this model.
_FASTEMBED_MODEL_CACHE_DIR = "models--Qdrant--bge-small-en-v1.5-onnx-Q"

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


class EmbeddingUnavailable(Exception):
    """A real embedding model was requested but cannot be loaded."""


def _default_fastembed_cache_dir():
    """Persistent cache dir for the fastembed model, if present.

    The ``FASTEMBED_CACHE_PATH`` env var wins. Otherwise use the
    persistent ``~/.cache/fastembed`` copy when it exists; return
    None to let fastembed use its own default cache.
    """
    env = os.environ.get("FASTEMBED_CACHE_PATH")
    if env:
        return env
    persistent = Path.home() / ".cache" / "fastembed"
    if (persistent / _FASTEMBED_MODEL_CACHE_DIR).exists():
        return str(persistent)
    return None


def _l2_normalize(vec):
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return [0.0] * len(vec)
    return [v / norm for v in vec]


class HashEmbedding:
    """Deterministic token-hash embedding, L2 normalized.

    Each token maps to one dimension through SHA-256, then the
    vector is normalized. The same text always gives the same
    vector; texts sharing words have high cosine similarity.
    """

    name = "hash"
    dim = EMBEDDING_DIM

    def embed(self, text: str) -> list[float]:
        """Return a vector of length `dim` for `text`."""
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        vec = [0.0] * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            vec[index] += 1.0
        return _l2_normalize(vec)


class FastEmbedEmbedding:
    """Real local embedding through fastembed (BAAI/bge-small-en-v1.5).

    The model loads lazily on the first ``embed`` call, so
    importing this module never downloads anything and never
    fails when fastembed is missing. Pass a ``model`` object
    (anything with an ``embed(list[str])`` method) to inject a
    stub in tests without touching the real model.
    """

    name = "fastembed"
    dim = EMBEDDING_DIM

    def __init__(self, model_name: str = DEFAULT_FASTEMBED_MODEL,
                 model=None, cache_dir: str | None = None):
        self.model_name = model_name
        self._model = model
        self._cache_dir = (
            cache_dir if cache_dir is not None
            else _default_fastembed_cache_dir()
        )

    @property
    def model(self):
        """Load (once) and return the underlying TextEmbedding."""
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise EmbeddingUnavailable(
                    "fastembed is not installed; install it with "
                    "`pip install fastembed` or use HashEmbedding"
                ) from exc
            try:
                if self._cache_dir:
                    self._model = TextEmbedding(
                        self.model_name, cache_dir=self._cache_dir)
                else:
                    self._model = TextEmbedding(self.model_name)
            except Exception as exc:  # noqa: BLE001 - any load failure
                raise EmbeddingUnavailable(
                    f"could not load embedding model {self.model_name!r}: {exc}"
                ) from exc
        return self._model

    def embed(self, text: str) -> list[float]:
        """Return an L2-normalized vector of length `dim` for `text`."""
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        vectors = list(self.model.embed([text]))
        if not vectors:
            return [0.0] * self.dim
        vec = [float(v) for v in vectors[0]]
        if len(vec) != self.dim:
            raise EmbeddingUnavailable(
                f"model {self.model_name!r} returned dim {len(vec)}, "
                f"expected {self.dim}"
            )
        return _l2_normalize(vec)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Embed several texts in one model call, L2 normalized."""
        if not all(isinstance(t, str) for t in texts):
            raise ValueError("all texts must be strings")
        return [
            _l2_normalize([float(v) for v in vec])
            for vec in self.model.embed(list(texts))
        ]


def make_embedder(kind: str = "auto", model=None,
                  model_name: str = DEFAULT_FASTEMBED_MODEL):
    """Build an embedder by kind: "auto", "fastembed" or "hash".

    - "fastembed": the real model; raises EmbeddingUnavailable
      when fastembed or the model cannot be loaded.
    - "hash": the deterministic stand-in, always available.
    - "auto": try the real model first, fall back to HashEmbedding
      when it is unavailable (safe fallback, never raises for a
      missing model). ``model`` injects a stub TextEmbedding for
      tests; it is only used with kind "fastembed" or "auto".
    """
    if kind == "hash":
        return HashEmbedding()
    if kind == "fastembed":
        return FastEmbedEmbedding(model_name=model_name, model=model)
    if kind == "auto":
        try:
            candidate = FastEmbedEmbedding(model_name=model_name, model=model)
            if model is not None:
                return candidate
            # Probe the real model once so "auto" really means
            # "real when available, hash otherwise".
            _ = candidate.model
            return candidate
        except EmbeddingUnavailable:
            return HashEmbedding()
    raise ValueError(f"unknown embedder kind: {kind!r}")


def get_default_embedder():
    """Preferred embedder for production: real model, hash fallback."""
    return make_embedder("auto")


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity of two vectors (assumed L2 normalized)."""
    if len(left) != len(right):
        raise ValueError("vector dimensions differ")
    return sum(a * b for a, b in zip(left, right))
