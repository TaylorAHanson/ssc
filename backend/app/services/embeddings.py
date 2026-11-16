"""Text embeddings for semantic Context Catalog search.

Calls an OpenAI-compatible embeddings API as the app's own identity, the same one the model
client uses. It is inference only: which documents a person may read is decided by the search
query, never by this identity. Two routes, chosen by the model name (``uses_gateway``):

* a Unity Catalog model reference such as ``system.ai.gte-large-en`` goes through the **AI Gateway**
  (``POST {host}/ai-gateway/mlflow/v1/embeddings`` with ``{"model": ref, "input": [...]}``), the route
  this platform sends all model traffic through (docs/ARCHITECTURE.md, "Model Control & Guardrails");
* a bare serving-endpoint name (a custom or provisioned endpoint, or the legacy ``databricks-*`` names,
  which the gateway no longer accepts) is called directly
  (``POST {host}/serving-endpoints/{name}/invocations`` with ``{"input": [...]}``).

Both answer ``{"data": [{"index": i, "embedding": [...]}]}``.

Vectors must have ``DIMS`` dimensions (the ``context_chunk_vectors.embedding`` column);
anything else raises ``EmbeddingError`` and nothing is written.

Sync on purpose: the agent's tools run in worker threads and the indexer runs in
``asyncio.to_thread``, so a blocking call with a short timeout is the simplest correct
shape. This deliberately does not reuse ``ModelServingClient``, whose retry policy
(five attempts, up to 30 s apart) is right for generation and wrong for a search box.

Tests install a deterministic fake with ``set_embedder_factory``; ``offline`` makes the
embedder "not configured" so no test ever reaches the network.

Never log document text, queries or tokens: only model names, counts and status codes.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Optional, Protocol
from urllib.parse import quote

from app.core.config import settings

logger = logging.getLogger(__name__)

DIMS = 1024
MAX_INPUT_CHARS = 8000  # per input; chunks are far smaller, this only guards odd documents
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,199}$")

# A search query must not wait on a slow endpoint, and a failing endpoint must not be
# retried on every keystroke or agent turn: after a failure the semantic leg is skipped
# for BREAKER_SECONDS and search answers from keywords.
QUERY_TIMEOUT_S = 1.5
BREAKER_SECONDS = 30.0
_QUERY_CACHE_MAX = 512
# Refresh an OAuth-derived token this often (well under its lifetime).
_TOKEN_TTL_S = 1800.0


class EmbeddingError(Exception):
    """An embedding call failed. ``message`` is safe to show; ``per_input`` means retrying won't help."""

    def __init__(self, message: str, *, status: Optional[int] = None, per_input: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.per_input = per_input


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str], *, timeout: float) -> list[list[float]]: ...


EmbedderFactory = Callable[[str], "Optional[Embedder]"]
_factory: Optional[EmbedderFactory] = None


def set_embedder_factory(factory: Optional[EmbedderFactory]) -> None:
    """Test seam: ``factory(model)`` returns an Embedder, or None for "not configured"."""
    global _factory
    _factory = factory
    with _state_lock:
        _query_cache.clear()
    reset_breaker()


def offline(_model: str) -> None:
    """An embedder factory for tests: semantic search is simply not configured."""
    return None


GATEWAY_EMBEDDINGS_ROUTE = "/ai-gateway/mlflow/v1/embeddings"


def valid_model(model: str) -> bool:
    """A usable model name: a Unity Catalog model reference or a serving-endpoint name."""
    return bool(MODEL_RE.match((model or "").strip()))


def uses_gateway(model: str) -> bool:
    """Whether ``model`` is a Unity Catalog model reference (``catalog.schema.model``), which is called through
    the AI Gateway. Serving-endpoint names can't contain a dot, so the dot is what tells the two apart."""
    return "." in (model or "").strip()


def _base_url() -> str:
    base = (settings.DATABRICKS_WORKSPACE_URL or settings.DATABRICKS_HOST or "").strip()
    if base and not base.startswith(("http://", "https://")):
        base = f"https://{base}"
    return base.rstrip("/")


def get_embedder(model: str) -> Optional[Embedder]:
    """The embedder for ``model``, or None when there is no workspace or the name isn't usable."""
    if not valid_model(model):
        return None
    if _factory is not None:
        return _factory(model)
    host = _base_url()
    return HttpEmbedder(host=host, model=model.strip()) if host else None


def vector_literal(vec: list[float]) -> str:
    """pgvector text input (``[1,2,3]``)."""
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def check_vectors(vectors: object, expected: int) -> list[list[float]]:
    """Validate an endpoint's answer: the right count, ``DIMS`` floats each, all finite."""
    if not isinstance(vectors, list) or len(vectors) != expected:
        raise EmbeddingError("The embedding endpoint returned an unexpected response.")
    out: list[list[float]] = []
    for v in vectors:
        if not isinstance(v, list) or len(v) != DIMS:
            n = len(v) if isinstance(v, list) else 0
            raise EmbeddingError(
                f"The embedding model returns {n}-dimensional vectors; search needs {DIMS}. "
                "Choose a 1024-dimension model in Admin > Settings > Context Catalog."
            )
        try:
            fv = [float(x) for x in v]
        except (TypeError, ValueError):
            raise EmbeddingError("The embedding endpoint returned an unexpected response.") from None
        if not all(math.isfinite(x) for x in fv):
            raise EmbeddingError("The embedding endpoint returned an unexpected response.")
        out.append(fv)
    return out


# ------------------------------------------------------------------- the HTTP client

_client = None
_token: Optional[str] = None
_token_at = 0.0
_state_lock = threading.Lock()


def _http():
    global _client
    import httpx

    with _state_lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client(limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))
        return _client


def close() -> None:
    global _client
    with _state_lock:
        if _client is not None and not _client.is_closed:
            _client.close()
        _client = None


def _bearer(host: str) -> str:
    """The app's bearer token, cached for ``_TOKEN_TTL_S`` (acquiring one can be a network round trip)."""
    global _token, _token_at
    with _state_lock:
        if _token and time.monotonic() - _token_at < _TOKEN_TTL_S:
            return _token
    from app.model_serving.client import acquire_token

    token = acquire_token(host)
    if not token:
        raise EmbeddingError("No credential is available for the embedding endpoint.")
    with _state_lock:
        _token, _token_at = token, time.monotonic()
    return token


def _map_status(status: int) -> EmbeddingError:
    if status in (401, 403):
        return EmbeddingError(
            "The app can't use the embedding model: it isn't permitted to, or the model name is no longer "
            "available. Check the model in Admin > Settings > Context Catalog and the app's access to it.",
            status=status)
    if status == 404:
        return EmbeddingError(
            "The embedding model wasn't found. Choose another model in Admin > Settings > Context Catalog.",
            status=status)
    if status == 429:
        return EmbeddingError("The embedding endpoint is busy; indexing will retry.", status=status)
    if status in (400, 413, 422):
        return EmbeddingError("The embedding endpoint couldn't process a passage.", status=status, per_input=True)
    return EmbeddingError("The embedding endpoint returned an error; indexing will retry.", status=status)


class HttpEmbedder:
    def __init__(self, *, host: str, model: str):
        self.host = host
        self.model = model

    def embed(self, texts: list[str], *, timeout: float) -> list[list[float]]:
        import httpx

        if not texts:
            return []
        token = _bearer(self.host)
        inputs = [t[:MAX_INPUT_CHARS] for t in texts]
        if uses_gateway(self.model):
            url = f"{self.host}{GATEWAY_EMBEDDINGS_ROUTE}"
            body = {"model": self.model, "input": inputs}
        else:
            url = f"{self.host}/serving-endpoints/{quote(self.model, safe='')}/invocations"
            body = {"input": inputs}
        try:
            res = _http().post(url, json=body, headers={"Authorization": f"Bearer {token}"},
                               timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)))
        except httpx.TimeoutException:
            raise EmbeddingError("The embedding endpoint timed out.") from None
        except httpx.HTTPError as exc:
            logger.info("Embedding request failed: %s", type(exc).__name__)
            raise EmbeddingError("Couldn't reach the embedding endpoint.") from None
        if res.status_code != 200:
            logger.info("Embedding endpoint %s returned HTTP %s", self.model, res.status_code)
            raise _map_status(res.status_code)
        try:
            data = res.json()["data"]
            ordered = sorted(data, key=lambda d: int(d.get("index", 0)))
            vectors = [d["embedding"] for d in ordered]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise EmbeddingError("The embedding endpoint returned an unexpected response.") from None
        return check_vectors(vectors, len(texts))


# ------------------------------------------------------------------- query embeddings

_query_cache: "OrderedDict[tuple[str, str], str]" = OrderedDict()
_down_until = 0.0


def reset_breaker() -> None:
    global _down_until
    with _state_lock:
        _down_until = 0.0


def _trip(reason: str) -> None:
    global _down_until
    with _state_lock:
        _down_until = time.monotonic() + BREAKER_SECONDS
    logger.info("Semantic search skipped for %ss: %s", int(BREAKER_SECONDS), reason)


def query_vector(model: str, query: str, *, timeout: Optional[float] = None) -> Optional[str]:
    """The query's vector as a pgvector literal, or None (not configured, failing, slow).

    Never raises: search falls back to keywords. A failure skips the semantic leg for
    ``BREAKER_SECONDS`` so chat stays responsive while the endpoint is down.
    """
    key = (model, query)
    with _state_lock:
        hit = _query_cache.get(key)
        if hit is not None:
            _query_cache.move_to_end(key)
            return hit
        if time.monotonic() < _down_until:
            return None
    embedder = get_embedder(model)
    if embedder is None:
        return None
    limit = QUERY_TIMEOUT_S if timeout is None else timeout
    try:
        (vec,) = embedder.embed([query], timeout=limit)
        literal = vector_literal(check_vectors([vec], 1)[0])
    except EmbeddingError as exc:
        _trip(exc.message)
        return None
    except Exception as exc:  # noqa: BLE001 - the semantic leg must never break search
        _trip(type(exc).__name__)
        return None
    with _state_lock:
        _query_cache[key] = literal
        if len(_query_cache) > _QUERY_CACHE_MAX:
            _query_cache.popitem(last=False)
    return literal
