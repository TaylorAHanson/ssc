"""Semantic search index for the Context Catalog: capabilities, vectors, indexer, query, status.

Search is keyword search plus, where the database allows it, a semantic leg that compares
the question's embedding with each passage's embedding (cosine distance, pgvector). The
two lists are fused in ``ContextCatalogService.search``. Anything that goes wrong here
means "keywords only"; it never fails a search and never blocks startup.

Capabilities. What the database can do decides the mode:

  semantic  PostgreSQL with the ``vector`` extension and the ``context_chunk_vectors`` table
  keyword   SQLite (local fallback), no pgvector, semantic search switched off, or no embedder

The extension may appear after the app first ran (an admin enables it later), so
``ensure_capabilities`` creates what the database allows, idempotently: at startup and every
``ENSURE_EVERY_S`` on the poller leader. A refused ``CREATE EXTENSION`` is logged and shown in
the status, never fatal. The extension may live outside this app's ``search_path`` schema, so
every use of the ``vector`` type and its operators is schema-qualified from the catalog.

Vectors are keyed by ``(chunk_hash, model)``, not by chunk row. A document edit deletes and
re-creates its chunks (new ids), but the hash of the text that gets embedded is the same for a
passage that didn't change, so an edit re-embeds only what changed and a model change re-embeds
everything, with no per-chunk bookkeeping. The table is not an ORM model: ``vector`` doesn't
exist on SQLite (see ``app/db/context_catalog.py``).

The indexer runs on the poller leader (``app/workers/tasks/context_search_index.py``): each pass
embeds up to ``PASS_CHUNKS`` passages that have no vector yet for the configured model. It backs
off 5 s -> 15 s -> 60 s -> 5 min while the endpoint fails; passages are still found by words
meanwhile. A passage the endpoint rejects outright is skipped until the process restarts.

Never log document text, queries or tokens: only model names, counts and status codes.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import bindparam, func, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.context_catalog import ContextChunkModel, ContextDocumentModel
from app.services import embeddings

logger = logging.getLogger(__name__)

VECTORS_TABLE = "context_chunk_vectors"
EMBED_BATCH = 16
EMBED_TIMEOUT_S = 30.0
PASS_CHUNKS = 64            # passages embedded per pass; a full pass runs again at once
BACKOFF_S = (5.0, 15.0, 60.0, 300.0)
IDLE_RECHECK_S = 30.0       # how soon an idle indexer looks for new passages
DETECT_TTL_S = 60.0
ENSURE_EVERY_S = 600.0
GC_EVERY_S = 3600.0
ORPHAN_GRACE = "1 day"      # an unreferenced vector is kept this long (a draft republished soon needs no re-embed)
_LOCK_KEY = 7_201_411_003   # advisory lock for the DDL (distinct from migrate.py's and the poller's)

SEMANTIC_MIN_LEN = 3        # queries shorter than this are keyword-only
# A passage only counts for the semantic leg when it has this many letters/digits. Near-empty passages
# embed close to short queries and surface as junk matches; they stay findable by words.
MIN_PASSAGE_CHARS = 24
_NONWORD_RE = r"[^[:alnum:]]"  # passed as a bind parameter: text() would read ":alnum" as a placeholder

# Largest cosine distance that still counts as a semantic match, per embedding model, so a nonsense
# query matches nothing instead of "the least-bad passage". Unknown models use the default.
#
# Measured live on 2026-11-13 against this catalog (9 passages) through the app's own indexer and HTTP
# client (through the AI Gateway; the legacy direct route gave the same figures), with 27 paraphrase queries sharing little or no wording with their passage and 24 junk queries
# (8 gibberish, 10 off-topic, 6 work-related but not in the catalog). Each value keeps every junk query out:
#
#   model                              intended passage    nearest junk   cutoff   paraphrases kept
#   system.ai.gte-large-en             0.28-0.60 (m 0.39)  0.460          0.43     19 / 27
#   system.ai.bge-large-en             0.26-0.48 (m 0.38)  0.442          0.43     23 / 27
#   system.ai.qwen3-embedding-0-6b     0.31-0.69 (m 0.47)  0.632          0.61     26 / 27
#
# The legacy databricks-* endpoint names (direct route, being retired) are the same models and keep the same cutoffs.
#
# These first matched the values collabmd measured on its own notes, so they were kept rather than loosened
# (GTE 0.45 or Qwen3 0.62 would keep only 2 or 0 more paraphrases and use up most of the margin). A nine-passage
# catalog flatters the margin: more passages put junk queries nearer to something. Re-measure as it grows.
SEMANTIC_MAX_DISTANCE = {
    "system.ai.gte-large-en": 0.43,
    "system.ai.bge-large-en": 0.43,
    "system.ai.qwen3-embedding-0-6b": 0.61,
    "databricks-gte-large-en": 0.43,
    "databricks-bge-large-en": 0.43,
    "databricks-qwen3-embedding-0-6b": 0.61,
}
DEFAULT_MAX_DISTANCE = 0.43


def max_distance(model: str) -> float:
    return SEMANTIC_MAX_DISTANCE.get(model, DEFAULT_MAX_DISTANCE)


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# ------------------------------------------------------------------ what gets embedded

def embed_text(title: str, chunk_index: int, content: str) -> str:
    """The text embedded for a passage. The first chunk already starts with the document title
    (``_rebuild_chunks`` prepends it); later chunks get it so a passage read alone says where it is from."""
    return content if chunk_index == 0 else f"{title}\n\n{content}"


def content_hash(title: str, chunk_index: int, content: str) -> str:
    """Key of a passage's vector: changes whenever the embedded text does (model is a separate key part)."""
    return hashlib.sha256(embed_text(title, chunk_index, content).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ capabilities

@dataclass(frozen=True)
class Capabilities:
    vector: bool = False                  # the vectors table exists with the right dimension
    vector_schema: Optional[str] = None   # schema holding the `vector` type and its operators
    note: Optional[str] = None            # user-safe reason a capability is missing, if we know one

    @property
    def mode(self) -> str:
        return "semantic" if self.vector else "keyword"


KEYWORD_ONLY = Capabilities()

_cache_lock = threading.Lock()
_caps: Optional[Capabilities] = None
_caps_at = 0.0
_ensured_at = 0.0
_forced: Optional[Capabilities] = None


def set_capabilities(caps: Optional[Capabilities]) -> None:
    """Test seam: pin the capabilities (None returns to detection)."""
    global _forced
    _forced = caps


def invalidate_capabilities() -> None:
    global _caps_at
    with _cache_lock:
        _caps_at = 0.0


def _is_postgres(bind: Any) -> bool:
    try:
        return bind.dialect.name == "postgresql"
    except AttributeError:
        return False


_DETECT_SQL = text(
    "SELECT "
    "  (SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
    "    WHERE e.extname = 'vector') AS vschema, "
    f"  (SELECT a.atttypmod FROM pg_attribute a WHERE a.attrelid = to_regclass('{VECTORS_TABLE}') "
    "      AND a.attname = 'embedding' AND NOT a.attisdropped) AS dims"
)


def _detect(conn: Any, note: Optional[str] = None) -> Capabilities:
    """Read-only catalog check (one cheap query) on a Connection or Session."""
    row = conn.execute(_DETECT_SQL).one()
    vschema, dims = row.vschema, row.dims
    dims = dims if dims is not None and dims > 0 else None
    vector = vschema is not None and dims == embeddings.DIMS
    if vschema is not None and dims not in (None, embeddings.DIMS):
        note = note or f"The embedding column has {dims} dimensions; search needs {embeddings.DIMS}."
    return Capabilities(vector=vector, vector_schema=vschema if vector else None, note=note)


def _set_cached(caps: Capabilities) -> None:
    global _caps, _caps_at
    with _cache_lock:
        if _caps is None or _caps.mode != caps.mode:
            logger.info("Context Catalog search mode: %s", caps.mode)
        _caps, _caps_at = caps, time.monotonic()


def capabilities(db: Session) -> Capabilities:
    """Cached capabilities; re-detected (read-only) when older than ``DETECT_TTL_S``. Never raises."""
    if _forced is not None:
        return _forced
    if not _is_postgres(db.get_bind()):
        return KEYWORD_ONLY
    with _cache_lock:
        if _caps is not None and time.monotonic() - _caps_at <= DETECT_TTL_S:
            return _caps
    try:
        with db.begin_nested():  # a failure rolls back this savepoint only, never the caller's transaction
            caps = _detect(db, _caps.note if _caps else None)
    except Exception as exc:  # noqa: BLE001 - search must keep working
        logger.warning("Context Catalog capability check failed: %s", type(exc).__name__)
        return _caps or KEYWORD_ONLY
    _set_cached(caps)
    return caps


def _step(conn: Any, label: str, sql: str) -> bool:
    """Run one DDL statement; a refusal is logged (server-side only) and reported as False."""
    try:
        conn.execute(text(sql))
        return True
    except SQLAlchemyError as exc:
        diag = getattr(getattr(getattr(exc, "orig", None), "diag", None), "message_primary", None) or ""
        logger.warning("Context Catalog search setup step %r skipped: %s %s", label, type(exc).__name__, diag)
        return False


def _extension_schema(conn: Any) -> Optional[str]:
    return conn.execute(text(
        "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
        "WHERE e.extname = 'vector'")).scalar()


def ensure_capabilities(engine: Engine) -> Capabilities:
    """Create what the database allows (idempotent), then detect. Never raises for a refused step."""
    global _ensured_at
    if _forced is not None:
        return _forced
    if not _is_postgres(engine):
        return KEYWORD_ONLY
    try:
        with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _LOCK_KEY})
            try:
                available = {r[0] for r in conn.execute(
                    text("SELECT name FROM pg_available_extensions WHERE name = 'vector'"))}
                vschema = _extension_schema(conn)
                refused = False
                if vschema is None and "vector" in available:
                    refused = not _step(conn, "vector extension", "CREATE EXTENSION IF NOT EXISTS vector")
                    vschema = _extension_schema(conn)
                table_failed = False
                if vschema is not None:
                    table_failed = not _step(
                        conn, "vectors table",
                        f"CREATE TABLE IF NOT EXISTS {VECTORS_TABLE} ("
                        "chunk_hash TEXT NOT NULL, model TEXT NOT NULL, "
                        f"embedding {qident(vschema)}.vector({embeddings.DIMS}) NOT NULL, "
                        "created_at TIMESTAMP NOT NULL DEFAULT now(), PRIMARY KEY (chunk_hash, model))")
                note = None
                if vschema is None and not available:
                    note = "This database doesn't offer the pgvector extension, so search uses words only."
                elif vschema is None and refused:
                    note = ("The app couldn't enable the pgvector extension (creating it was refused), "
                            "so search uses words only. An administrator can run CREATE EXTENSION vector.")
                elif table_failed:
                    note = ("The pgvector extension is installed, but the app couldn't create its vectors table "
                            "(it may need to run as the database role that owns the app's schema), "
                            "so search uses words only.")
                caps = _detect(conn, note)
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
    except Exception as exc:  # noqa: BLE001 - startup must never fail over search
        logger.warning("Context Catalog search setup failed: %s", type(exc).__name__)
        return _caps or KEYWORD_ONLY
    _ensured_at = time.monotonic()
    _set_cached(caps)
    return caps


# ------------------------------------------------------------------ effective target

@dataclass(frozen=True)
class Target:
    model: str       # the configured model ('' when the setting is unusable)
    semantic: bool   # embeddings are being produced and queried


def target(caps: Capabilities) -> Target:
    on = bool(getattr(settings, "CONTEXT_CATALOG_SEMANTIC_SEARCH", False))
    model = (getattr(settings, "CONTEXT_CATALOG_EMBEDDING_MODEL", "") or "").strip()
    if not embeddings.valid_model(model):
        model = ""
    semantic = bool(on and model and caps.vector and embeddings.get_embedder(model) is not None)
    return Target(model=model, semantic=semantic)


# ------------------------------------------------------------------ the indexer

class _IndexerState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.failures = 0
        self.retry_at = 0.0               # monotonic; no embedding calls before this
        self.failed_model: Optional[str] = None
        self.next_run_at = 0.0            # monotonic; the poller task skips cycles before this
        self.last_error: Optional[str] = None
        self.last_error_at: Optional[datetime] = None
        self.last_pass_at: Optional[datetime] = None
        self.rejected: Set[Tuple[str, str]] = set()   # (hash, model) the endpoint refuses outright
        self.last_gc_at = 0.0
        self.chunks_embedded = 0


_state = _IndexerState()


def reset_state() -> None:
    """Forget failures, back-off and cached capabilities (tests; a settings change)."""
    global _caps, _caps_at, _ensured_at
    _state.reset()
    with _cache_lock:
        _caps, _caps_at, _ensured_at = None, 0.0, 0.0


def kick() -> None:
    """A document changed: let the next poller cycle look for new passages now."""
    _state.next_run_at = 0.0


def due() -> bool:
    return time.monotonic() >= _state.next_run_at


def postpone(seconds: float) -> None:
    """Don't look again for ``seconds`` (the poller task calls this after a database error)."""
    _state.next_run_at = time.monotonic() + seconds


def ensure_due() -> bool:
    return time.monotonic() - _ensured_at > ENSURE_EVERY_S


def _fail(message: str, model: str) -> None:
    _state.failures += 1
    delay = BACKOFF_S[min(_state.failures, len(BACKOFF_S)) - 1]
    now = time.monotonic()
    _state.retry_at = now + delay
    _state.next_run_at = now + delay
    _state.failed_model = model
    _state.last_error = message
    _state.last_error_at = datetime.now(timezone.utc)


_REJECTED_MESSAGE = "Part of the catalog couldn't be embedded; it is found by words only."


def _recovered() -> None:
    """A pass finished without an endpoint failure: clear the back-off, keep any 'rejected passage' notice."""
    _state.failures, _state.retry_at, _state.failed_model = 0, 0.0, None
    _state.last_error = _REJECTED_MESSAGE if _state.rejected else None


def backfill_hashes(db: Session, limit: int = 500) -> int:
    """Hash chunks that predate the ``content_hash`` column (or were written without one)."""
    rows = (
        db.query(ContextChunkModel, ContextDocumentModel.title)
        .join(ContextDocumentModel, ContextChunkModel.document_id == ContextDocumentModel.id)
        .filter(ContextChunkModel.content_hash.is_(None))
        .limit(limit)
        .all()
    )
    for chunk, title in rows:
        chunk.content_hash = content_hash(title, chunk.chunk_index, chunk.content)
    if rows:
        db.commit()
    return len(rows)


_PENDING_SQL = text(
    "SELECT c.content_hash AS h, d.title AS title, c.chunk_index AS idx, c.content AS content "
    "FROM context_chunks c JOIN context_documents d ON d.id = c.document_id "
    f"LEFT JOIN {VECTORS_TABLE} v ON v.chunk_hash = c.content_hash AND v.model = :model "
    "WHERE d.status = 'published' AND c.content_hash IS NOT NULL AND v.chunk_hash IS NULL "
    "ORDER BY d.updated_at, c.chunk_index LIMIT :n"
)


def _store(db: Session, vs: str, model: str, hashes: List[str], vectors: List[List[float]]) -> None:
    db.execute(
        text(f"INSERT INTO {VECTORS_TABLE} (chunk_hash, model, embedding) "
             f"VALUES (:h, :m, CAST(:v AS {vs}.vector)) ON CONFLICT (chunk_hash, model) DO NOTHING"),
        [{"h": h, "m": model, "v": embeddings.vector_literal(v)} for h, v in zip(hashes, vectors)],
    )
    db.commit()


def _embed_singly(db: Session, vs: str, embedder: embeddings.Embedder, model: str,
                  batch: List[Tuple[str, str]]) -> int:
    """A batch had a passage the endpoint refused: embed one by one so only that passage is skipped."""
    done = 0
    refused: List[str] = []
    for h, body in batch:
        try:
            vec = embeddings.check_vectors(embedder.embed([body], timeout=EMBED_TIMEOUT_S), 1)
        except embeddings.EmbeddingError as exc:
            if exc.per_input:
                refused.append(h)
                continue
            _fail(exc.message, model)
            return done
        _store(db, vs, model, [h], vec)
        done += 1
    if refused and done == 0 and len(refused) >= 2:
        # Every passage was refused: that is the model (e.g. a chat model, not an embedding model), not the
        # passages. Back off with an accurate message instead of writing each one off.
        _fail("The embedding model refused the catalog's passages. Check that it is an embedding model in "
              "Admin > Settings > Context Catalog.", model)
        return 0
    for h in refused:
        _state.rejected.add((h, model))
    if refused:
        _state.last_error = _REJECTED_MESSAGE
    return done


def collect_garbage(db: Session, model: str) -> int:
    """Drop vectors for other models, and vectors no chunk references any more (after a grace period)."""
    res = db.execute(text(
        f"DELETE FROM {VECTORS_TABLE} v WHERE v.model <> :model OR (v.created_at < now() - interval '{ORPHAN_GRACE}' "
        "AND NOT EXISTS (SELECT 1 FROM context_chunks c WHERE c.content_hash = v.chunk_hash))"), {"model": model})
    db.commit()
    return res.rowcount or 0


def pass_once(db: Session, *, limit: int = PASS_CHUNKS) -> int:
    """Embed up to ``limit`` passages that have no vector yet. Returns how many were written. Never raises
    for an embedding failure (it backs off); a database error propagates to the caller."""
    caps = capabilities(db)
    tgt = target(caps)
    now = time.monotonic()
    if not tgt.semantic:
        _state.next_run_at = now + IDLE_RECHECK_S
        return 0
    if _state.failed_model != tgt.model:
        # The setting changed since the last failure: that back-off no longer applies.
        _state.failures, _state.retry_at, _state.failed_model = 0, 0.0, None
    if _state.retry_at > now:
        return 0
    backfill_hashes(db)
    rows = db.execute(_PENDING_SQL, {"model": tgt.model, "n": limit}).mappings().all()
    todo: Dict[str, str] = {}
    for r in rows:
        if r["h"] not in todo and (r["h"], tgt.model) not in _state.rejected:
            todo[r["h"]] = embed_text(r["title"], r["idx"], r["content"])
    if not todo:
        _recovered()
        _state.next_run_at = now + IDLE_RECHECK_S
        if now - _state.last_gc_at > GC_EVERY_S:
            _state.last_gc_at = now
            dropped = collect_garbage(db, tgt.model)
            if dropped:
                logger.info("Context Catalog search index cleaned up", extra={"data": {"vectors_removed": dropped}})
        _state.last_pass_at = datetime.now(timezone.utc)
        return 0
    embedder = embeddings.get_embedder(tgt.model)
    vs = qident(caps.vector_schema or "public")
    items = list(todo.items())
    written = 0
    for i in range(0, len(items), EMBED_BATCH):
        batch = items[i:i + EMBED_BATCH]
        try:
            vecs = embeddings.check_vectors(embedder.embed([t for _, t in batch], timeout=EMBED_TIMEOUT_S), len(batch))
        except embeddings.EmbeddingError as exc:
            if exc.per_input:
                written += _embed_singly(db, vs, embedder, tgt.model, batch)
                if _state.retry_at > time.monotonic():
                    break
                continue
            logger.info("Context Catalog embedding paused (HTTP %s): %s", exc.status, exc.message)
            _fail(exc.message, tgt.model)
            break
        except Exception as exc:  # noqa: BLE001 - a broken endpoint must not break the poller
            logger.info("Context Catalog embedding failed: %s", type(exc).__name__)
            _fail("The embedding endpoint returned an error; indexing will retry.", tgt.model)
            break
        _store(db, vs, tgt.model, [h for h, _ in batch], vecs)
        written += len(batch)
    else:
        _recovered()
    if written:
        _state.chunks_embedded += written
        logger.info("Context Catalog passages embedded", extra={"data": {"count": written, "model": tgt.model}})
    if _state.retry_at <= time.monotonic():
        # Nothing failed: a full batch means more may be waiting, so run again at once.
        _state.next_run_at = 0.0 if len(items) >= limit else time.monotonic() + IDLE_RECHECK_S
    _state.last_pass_at = datetime.now(timezone.utc)
    return written


# ------------------------------------------------------------------ the query side

def _semantic_sql(vs: str, with_domains: bool):
    dist = f"v.embedding OPERATOR({vs}.<=>) CAST(:qvec AS {vs}.vector)"
    domain = " AND c.domain_id IN :domain_ids" if with_domains else ""
    stmt = text(
        "SELECT id, dist FROM ("
        f" SELECT c.id AS id, {dist} AS dist,"
        "   length(regexp_replace(c.content, :nonword_re, '', 'g')) AS letters"
        " FROM context_chunks c JOIN context_documents d ON d.id = c.document_id"
        f" JOIN {VECTORS_TABLE} v ON v.chunk_hash = c.content_hash AND v.model = :model"
        f" WHERE d.status = 'published'{domain}) t"
        " WHERE dist <= :cutoff AND letters >= :min_chars"
        " ORDER BY dist, id LIMIT :cand"
    )
    return stmt.bindparams(bindparam("domain_ids", expanding=True)) if with_domains else stmt


def semantic_hits(db: Session, query: str, *, domain_ids: Optional[List[str]], cand: int) -> List[Tuple[str, float]]:
    """Chunk ids nearest the query by meaning, closest first, as ``(chunk_id, cosine_distance)``.

    Empty when semantic search is unavailable for any reason (SQLite, no pgvector, switched off, no
    embedder, endpoint down or slow, query too short, nothing within the model's cutoff). Never raises.
    """
    if len((query or "").strip()) < SEMANTIC_MIN_LEN:
        return []
    try:
        caps = capabilities(db)
        tgt = target(caps)
        if not tgt.semantic:
            return []
        qvec = embeddings.query_vector(tgt.model, query.strip())
        if qvec is None:
            return []
        params: Dict[str, Any] = {
            "qvec": qvec, "model": tgt.model, "cutoff": max_distance(tgt.model),
            "min_chars": MIN_PASSAGE_CHARS, "nonword_re": _NONWORD_RE, "cand": cand,
        }
        if domain_ids is not None:
            params["domain_ids"] = list(domain_ids) or [""]
        vs = qident(caps.vector_schema or "public")
        with db.begin_nested():
            rows = db.execute(_semantic_sql(vs, domain_ids is not None), params).all()
    except Exception as exc:  # noqa: BLE001 - the semantic leg must never break search
        logger.warning("Context Catalog semantic search skipped: %s", type(exc).__name__)
        invalidate_capabilities()
        return []
    return [(r.id, float(r.dist)) for r in rows]


# ------------------------------------------------------------------ status

def status(db: Session) -> Dict[str, Any]:
    """Search status for admins. User-safe strings only (no SQL)."""
    caps = capabilities(db)
    tgt = target(caps)
    total = (
        db.query(func.count(ContextChunkModel.id))
        .join(ContextDocumentModel, ContextChunkModel.document_id == ContextDocumentModel.id)
        .filter(ContextDocumentModel.status == "published")
        .scalar()
    ) or 0
    indexed = 0
    if caps.vector and tgt.model:
        try:
            with db.begin_nested():
                indexed = db.execute(text(
                    "SELECT count(*) FROM context_chunks c JOIN context_documents d ON d.id = c.document_id "
                    f"JOIN {VECTORS_TABLE} v ON v.chunk_hash = c.content_hash AND v.model = :model "
                    "WHERE d.status = 'published'"), {"model": tgt.model}).scalar() or 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("Context Catalog index status failed: %s", type(exc).__name__)
    if tgt.semantic:
        reason = None
    elif not bool(getattr(settings, "CONTEXT_CATALOG_SEMANTIC_SEARCH", False)):
        reason = "Semantic search is switched off in Admin > Settings > Context Catalog."
    elif not tgt.model:
        reason = "The embedding model setting isn't a usable endpoint name."
    elif not caps.vector:
        reason = caps.note or "This database has no pgvector support (local SQLite, or the extension isn't enabled)."
    else:
        reason = "No workspace is configured to reach the embedding endpoint."
    return {
        "mode": "semantic" if tgt.semantic else "keyword",
        "semantic": tgt.semantic,
        "model": tgt.model or None,
        "indexed": indexed,
        "total": int(total),
        "pending": max(0, int(total) - indexed) if tgt.semantic else 0,
        "reason": reason,
        "lastError": _state.last_error,
    }
