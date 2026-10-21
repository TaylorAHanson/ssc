"""
Lightweight, idempotent startup migrations.

This project creates tables via ``Base.metadata.create_all`` (no Alembic). When
a model is renamed, ``create_all`` would otherwise create a fresh, empty table
and orphan the old data. These helpers run *before* ``create_all`` to rename
existing tables/columns in place so data survives the rename.

Each step is guarded by an inspector check so it is safe to run on every boot
and on fresh databases (where the old tables never existed).

``apply_replica_identity_full`` (opt-in Lakebase Change Data Feed readiness) is
the exception: it runs *after* ``create_all``, since it needs the tables to exist.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _rename_table(engine: Engine, old: str, new: str) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    if old in tables and new not in tables:
        with engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE {old} RENAME TO {new}'))
        logger.info("Migration: renamed table %s -> %s", old, new)


def _rename_column(engine: Engine, table: str, old: str, new: str) -> None:
    insp = inspect(engine)
    if table not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns(table)}
    if old in cols and new not in cols:
        with engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE {table} RENAME COLUMN {old} TO {new}'))
        logger.info("Migration: renamed %s.%s -> %s", table, old, new)


def _add_column(engine: Engine, table: str, column: str, ddl_type: str) -> None:
    """Add ``column`` to ``table`` if it doesn't exist yet (idempotent).

    ``create_all`` only creates missing *tables*, never missing *columns* on an
    existing table — so a new column on an existing model needs this. The DDL
    type string must be valid on both SQLite (local) and Postgres (Lakebase);
    ``INTEGER``, ``TIMESTAMP``, and ``TEXT`` all are.
    """
    insp = inspect(engine)
    if table not in set(insp.get_table_names()):
        return  # fresh DB: create_all will build the table with the column
    cols = {c["name"] for c in insp.get_columns(table)}
    if column in cols:
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
    logger.info("Migration: added column %s.%s", table, column)


def _add_index(engine: Engine, index_name: str, table: str, columns: list[str]) -> None:
    """Create ``index_name`` on ``table(columns)`` if missing (idempotent).

    ``create_all`` builds model-declared indexes only when it creates the table,
    so an index added to an existing model needs an explicit ``CREATE INDEX IF
    NOT EXISTS`` here. The name must match SQLAlchemy's generated name
    (``ix_<table>_<column>`` for single-column ``index=True``) so the two paths
    don't create duplicate indexes. ``IF NOT EXISTS`` is valid on SQLite and
    Postgres alike.
    """
    insp = inspect(engine)
    if table not in set(insp.get_table_names()):
        return  # fresh DB: create_all will build the index from the model
    existing = {ix.get("name") for ix in insp.get_indexes(table)}
    if index_name in existing:
        return
    cols = ", ".join(columns)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({cols})"))
    logger.info("Migration: created index %s on %s(%s)", index_name, table, cols)


def _make_metadata_idempotency_unique(engine: Engine) -> None:
    """Replace the plain (actor, idempotency_key) index on metadata_change_sets with a unique one.

    Two identical submits racing each other could otherwise both file an approval
    request. Existing duplicates keep the old index in place and log a warning.
    """
    table = "metadata_change_sets"
    insp = inspect(engine)
    if table not in set(insp.get_table_names()):
        return
    existing = {ix.get("name") for ix in insp.get_indexes(table)}
    if "uq_metadata_change_sets_idempotency" not in existing:
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_metadata_change_sets_idempotency "
                    f"ON {table} (actor, idempotency_key)"
                ))
        except Exception as e:  # noqa: BLE001 - duplicates already stored; keep the plain index
            logger.warning("Migration: couldn't make metadata idempotency keys unique (duplicates?): %s", e)
            return
        logger.info("Migration: created unique index uq_metadata_change_sets_idempotency")
    if "ix_metadata_change_sets_idempotency" in existing:
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX IF EXISTS ix_metadata_change_sets_idempotency"))


def _backfill_request_state_summary(engine: Engine) -> None:
    """Populate ``requests.state_summary`` for existing Sentinel rows (Postgres).

    Extracts just the small list-view subpaths server-side (``jsonb_build_object``
    over ``state_context``), so the huge blob is never transferred to the app.
    Only Sentinel rows carry a large ``state_context``, so we scope the (one-time,
    per-row detoast) update to them. SQLite (local dev) has tiny data and relies on
    the read-path fallback + new writes instead. Idempotent via the NULL guard.
    """
    if engine.dialect.name != "postgresql":
        return
    insp = inspect(engine)
    if "requests" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("requests")}
    if "state_summary" not in cols:
        return
    sql = text(
        """
        UPDATE requests
        SET state_summary = jsonb_build_object(
            'scan_stats',          state_context::jsonb -> 'scan_stats',
            'workspaces_scanned',  state_context::jsonb -> 'workspaces_scanned',
            'workspace_failures',  state_context::jsonb -> 'workspace_failures',
            'workspace',           state_context::jsonb -> 'workspace',
            'summary',             state_context::jsonb -> 'summary',
            'scan_error',          state_context::jsonb -> 'scan_error'
        )
        WHERE type = 'enforcement_sentinel'
          AND state_summary IS NULL
          AND state_context IS NOT NULL
        """
    )
    with engine.begin() as conn:
        result = conn.execute(sql)
    logger.info(
        "Migration: backfilled requests.state_summary for %s row(s)",
        getattr(result, "rowcount", "?"),
    )


def _prune_orphaned_workflow_children(engine: Engine) -> None:
    """Delete test cases, test runs, and versions whose workflow no longer exists.

    ``WorkflowService.delete`` used to remove only the workflow row, so every
    deleted workflow left its children behind. They're unreachable (every read
    path joins on ``workflow_id``) but they were confusing in aggregate: an admin
    who deleted a workflow and re-created the same key got a fresh uuid and an
    empty Tests tab, with the old cases still sitting in the table.

    Idempotent: after the first run these queries match nothing.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "workflows" not in tables:
        return
    total = 0
    with engine.begin() as conn:
        # Never prune against an empty parent table. This is an unconditional
        # DELETE keyed on the contents of ``workflows``, and it runs on every boot:
        # if the app ever starts while that table is empty but its children aren't
        # (a half-restored backup, a truncate-and-reseed), "orphaned" describes
        # every row — including the published version snapshots that are the only
        # rollback path. Same reasoning as the bundle import refusing an empty
        # bundle. When workflows exist, a genuinely orphaned child is still pruned.
        if not conn.execute(text("SELECT 1 FROM workflows LIMIT 1")).first():
            logger.info(
                "Migration: skipped orphan prune — the workflows table is empty, so "
                "every test/version row would look orphaned."
            )
            return
        for table in ("workflow_test_runs", "workflow_tests", "workflow_versions"):
            if table not in tables:
                continue
            result = conn.execute(
                text(
                    f"DELETE FROM {table} WHERE workflow_id NOT IN "
                    "(SELECT id FROM workflows)"
                )
            )
            total += result.rowcount or 0
    if total:
        logger.info(
            "Migration: pruned %d orphaned workflow test/version rows "
            "(their workflow was deleted)", total,
        )


def run_startup_migrations(engine: Engine) -> None:
    """Apply in-place schema renames. Safe to call on every startup."""
    try:
        # skills -> workflows terminology rename.
        _rename_table(engine, "skills", "workflows")
        _rename_table(engine, "skill_versions", "workflow_versions")
        _rename_column(engine, "workflow_versions", "skill_id", "workflow_id")
        _rename_column(engine, "workflow_versions", "skill_key", "workflow_key")
        # Workflows: operational "turn off" kill switch (hides a published workflow
        # from the agent without unpublishing/editing — the only lock-exempt way to
        # disable a workflow in a locked prod env).
        _bool_ddl = "BOOLEAN DEFAULT FALSE" if engine.dialect.name == "postgresql" else "INTEGER DEFAULT 0"
        _add_column(engine, "workflows", "disabled", _bool_ddl)
        _add_index(engine, "ix_workflows_disabled", "workflows", ["disabled"])
        # Workflow snapshots: distinguish released versions from pre-overwrite
        # draft backups. Publishing used to be the only thing that snapshotted, so
        # an authoring-assistant save over a draft was unrecoverable.
        _add_column(
            engine, "workflow_versions", "kind", "VARCHAR DEFAULT 'publish'",
        )
        _add_column(engine, "workflow_versions", "note", "VARCHAR")
        # Context Catalog retrieval-usage signal columns.
        _add_column(engine, "context_documents", "retrieval_count", "INTEGER DEFAULT 0")
        _add_column(engine, "context_documents", "last_retrieved_at", "TIMESTAMP")
        # Data Certification: full per-rule checklist cache on the data asset.
        # JSONB on Postgres (matches the model's JSONType) / TEXT on SQLite, which
        # stores the JSON string the JSONType serializes.
        _json_ddl = "JSONB" if engine.dialect.name == "postgresql" else "TEXT"
        _add_column(engine, "data_assets", "certification_rule_results", _json_ddl)
        # Data Certification detail view: per-table snapshot from the last scan,
        # and a lookup of one resource's findings across sentinel runs.
        _add_column(engine, "data_assets", "certification_assets", _json_ddl)
        _add_index(
            engine,
            "ix_sentinel_findings_resource_kind",
            "sentinel_findings",
            ["resource_id", "kind"],
        )
        # Metric Views / Domain hierarchy enrichment
        _add_column(engine, "data_assets", "subdomain", "VARCHAR")
        _add_index(engine, "ix_data_assets_subdomain", "data_assets", ["subdomain"])
        _add_column(engine, "data_assets", "kpis", _json_ddl)
        _add_column(engine, "data_assets", "upstream_tables", _json_ddl)
        _add_column(engine, "data_assets", "downstream_dashboards", _json_ddl)
        _add_column(engine, "data_assets", "legacy_mappings", _json_ddl)
        # Enforcement Sentinel: marks the scheduled run that emitted the daily
        # governance digest (anchored once-per-local-day dispatch).
        _add_column(engine, "requests", "digest_emitted_at", "TIMESTAMP")
        # List-view performance: a compact projection of state_context so the
        # requests list never loads the (hundreds-of-MB) blob, plus the composite
        # index the paginated list query needs (filter by type, sort by created_at).
        _req_json_ddl = "JSONB" if engine.dialect.name == "postgresql" else "TEXT"
        _add_column(engine, "requests", "state_summary", _req_json_ddl)
        _add_index(engine, "ix_requests_type", "requests", ["type"])
        _add_index(engine, "ix_requests_type_created_at", "requests", ["type", "created_at"])
        _backfill_request_state_summary(engine)
        # Multi-workspace Enforcement Sentinel: which workspace an audited
        # resource lives in, so the immediate-HIGH dedup can distinguish the same
        # resource_id across workspaces (job IDs / notebook paths repeat).
        _add_column(engine, "enforcement_audit", "workspace", "VARCHAR")
        _add_index(
            engine,
            "ix_enforcement_audit_workspace",
            "enforcement_audit",
            ["workspace"],
        )
        # Tool Registry: unified catalog rename to the three usage contexts.
        _rename_column(engine, "tool_registry", "enabled_for_edh", "enabled_for_main_agent")
        _rename_column(engine, "tool_registry", "enabled_for_workflow", "enabled_for_workflow_agent")
        _add_column(engine, "tool_registry", "enabled_for_workflow_execution", "INTEGER DEFAULT 0")
        _add_column(engine, "tool_registry", "exposed_via_mcp", "INTEGER DEFAULT 0")
        # Tool false-success detection: an author-declared success condition.
        _add_column(engine, "tool_registry", "success_predicate", "TEXT")
        # Scheduled reports: per-subscription timezone for cron evaluation.
        # Default mirrors the previously-hardcoded scheduling tz so existing
        # subscriptions keep running at the same wall-clock time.
        _add_column(
            engine,
            "report_subscriptions",
            "timezone",
            "VARCHAR DEFAULT 'America/Los_Angeles'",
        )
        # Performance: hot-path indexes for existing DBs (fresh DBs get these from
        # the models via create_all). Names mirror SQLAlchemy's generated names.
        _add_index(engine, "ix_events_request_id_type", "events", ["request_id", "event_type"])
        _add_index(engine, "ix_requests_status", "requests", ["status"])
        _add_index(engine, "ix_requests_requester_email", "requests", ["requester_email"])
        _add_index(engine, "ix_requests_locked_until", "requests", ["locked_until"])
        _add_index(engine, "ix_approvals_request_id", "approvals", ["request_id"])
        _add_index(engine, "ix_approvals_assigned_to_email", "approvals", ["assigned_to_email"])
        _add_index(engine, "ix_approvals_assigned_to_role", "approvals", ["assigned_to_role"])
        _add_index(engine, "ix_approvals_status", "approvals", ["status"])
        # Manual-task gates: the work instructions shown in the inbox, plus an
        # optional SLA so an indefinitely-parked task can be surfaced as overdue.
        _add_column(engine, "approvals", "instructions", "TEXT")
        _add_column(engine, "approvals", "due_at", "TIMESTAMP")
        # Preview features added by hand before Databricks lists them, and the
        # synced feature each one is later matched into.
        _add_column(engine, "preview_features", "origin", "VARCHAR")
        _add_column(engine, "preview_features", "created_by", "VARCHAR")
        _add_column(engine, "preview_features", "replaced_by", "VARCHAR")
        # Metadata Manager (ADR-0006): run items keep what each statement did.
        _add_column(engine, "metadata_reconcile_items", "securable_type", "VARCHAR")
        _add_column(engine, "metadata_reconcile_items", "removals", "INTEGER DEFAULT 0")
        _add_column(engine, "metadata_reconcile_items", "payload", "JSON")
        _add_index(engine, "ix_metadata_objects_securable_type", "metadata_objects", ["securable_type"])
        _add_index(engine, "ix_metadata_observed_securable_type", "metadata_observed", ["securable_type"])
        # Training courses that follow a Databricks Academy catalog course.
        _add_column(engine, "training_courses", "academy_course_id", "INTEGER")
        _add_index(engine, "ix_training_courses_academy_course_id", "training_courses", ["academy_course_id"])
        _make_metadata_idempotency_unique(engine)
        _prune_orphaned_workflow_children(engine)
    except Exception as e:  # noqa: BLE001 - never block startup on a migration
        logger.warning("Startup migration step failed (continuing): %s", e)


# --- Lakebase Change Data Feed readiness ------------------------------------

# What ``LAKEBASE_CDF_TABLES=default`` stands for. LangGraph's checkpoint tables
# share the app schema and are left out on purpose: they are large and rewritten
# on every graph step, and the feed skips any table without full replica identity.
DEFAULT_CDF_TABLES: tuple[str, ...] = (
    "requests",
    "events",
    "approvals",
    "enforcement_audit",
    "rbac_rules",
    "rbac_change_sets",
    "rbac_rule_events",
    "metadata_objects",
    "metadata_tags",
    "metadata_change_sets",
    "metadata_events",
)

_PG_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def resolve_cdf_tables(raw: str | None) -> list[str]:
    """Expand a ``LAKEBASE_CDF_TABLES`` value into an ordered, de-duplicated list.

    Comma-separated tokens: ``default`` expands to ``DEFAULT_CDF_TABLES``,
    ``none`` and blanks are ignored, anything else is a table name.
    """
    tables: list[str] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token or token.lower() == "none":
            continue
        for name in DEFAULT_CDF_TABLES if token.lower() == "default" else (token,):
            if name not in tables:
                tables.append(name)
    return tables


def apply_replica_identity_full(
    engine: Engine,
    tables_setting: str | None = None,
    schema: str | None = None,
) -> dict[str, list[str]]:
    """Set ``REPLICA IDENTITY FULL`` on the opted-in app tables (Postgres only).

    A Lakebase Change Data Feed only captures tables with full replica identity,
    and only a table's owner can change it — on a deployed app that is the app's
    own service principal, so the app does it at startup. Runs *after*
    ``create_all`` so a fresh database is covered on first boot. Idempotent and
    never raises: a table that can't be altered is logged and retried next boot.
    """
    from app.core.config import settings
    from app.db.session import get_db_schema

    result: dict[str, list[str]] = {
        "altered": [], "already_full": [], "missing": [], "failed": [], "invalid": [],
    }
    raw = settings.LAKEBASE_CDF_TABLES if tables_setting is None else tables_setting
    requested = resolve_cdf_tables(raw)
    if not requested:
        return result
    if engine.dialect.name != "postgresql":
        logger.info(
            "LAKEBASE_CDF_TABLES is set but the app database is %s, not Postgres; "
            "skipping REPLICA IDENTITY.", engine.dialect.name,
        )
        return result

    result["invalid"] = [t for t in requested if not _PG_IDENT_RE.match(t)]
    if result["invalid"]:
        logger.warning("Lakebase CDF: ignoring invalid table name(s): %s", result["invalid"])
    tables = [t for t in requested if t not in result["invalid"]]
    schema = schema or get_db_schema()

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT c.relname, c.relreplident FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema AND c.relkind = 'r'"
                ),
                {"schema": schema},
            ).fetchall()
    except Exception as e:  # noqa: BLE001 - never block startup
        logger.warning(
            "Lakebase CDF: could not read replica identity in schema %s (continuing): %s",
            schema, e,
        )
        result["failed"] = tables
        return result
    identity = {name: ident for name, ident in rows}

    for table in tables:
        if table not in identity:
            result["missing"].append(table)
        elif identity[table] == "f":
            result["already_full"].append(table)
        else:
            try:
                with engine.begin() as conn:
                    # ALTER TABLE queues behind every open transaction on the
                    # table; cap the wait so a busy table can't stall startup.
                    conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                    conn.execute(text(f'ALTER TABLE "{schema}"."{table}" REPLICA IDENTITY FULL'))
                result["altered"].append(table)
            except Exception as e:  # noqa: BLE001 - never block startup
                result["failed"].append(table)
                logger.warning(
                    "Lakebase CDF: could not set REPLICA IDENTITY FULL on %s.%s "
                    "(continuing; retried next boot): %s", schema, table, e,
                )

    logger.info(
        "Lakebase CDF: REPLICA IDENTITY FULL in schema %s: set now %s, already full %s",
        schema, result["altered"], result["already_full"],
    )
    if result["missing"]:
        logger.warning(
            "Lakebase CDF: table(s) not found in schema %s, skipped: %s",
            schema, result["missing"],
        )
    return result


# --- Metadata Manager search indexes (ADR-0006 D10) -------------------------

# Trigram GIN indexes for substring search on names and descriptions. Postgres
# only; SQLite search falls back to LIKE over the model's B-tree indexes.
_METADATA_TRGM_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_metadata_objects_fqn_trgm", "metadata_objects", "fqn_key"),
    ("ix_metadata_objects_comment_trgm", "metadata_objects", "comment"),
    # Search also matches Unity Catalog's comment and unmanaged objects from the snapshot.
    ("ix_metadata_observed_fqn_trgm", "metadata_observed", "fqn_key"),
    ("ix_metadata_observed_comment_trgm", "metadata_observed", "comment"),
)


def apply_metadata_search_indexes(engine: Engine, schema: str | None = None) -> bool:
    """Create ``pg_trgm`` and the trigram indexes behind Metadata Manager search.

    Returns True when every index exists. Never raises: without the extension
    (it needs CREATE on the database) search still works through ILIKE, only
    slower, and the next boot tries again.
    """
    if engine.dialect.name != "postgresql":
        return False
    from app.db.session import get_db_schema

    schema = schema or get_db_schema()
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except Exception as e:  # noqa: BLE001 - never block startup
        logger.warning("Metadata search: could not create the pg_trgm extension (search uses ILIKE): %s", e)
    try:
        with engine.connect() as conn:
            ext_schema = conn.execute(text(
                "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
                "WHERE e.extname = 'pg_trgm'"
            )).scalar()
    except Exception as e:  # noqa: BLE001 - never block startup
        logger.warning("Metadata search: could not look up pg_trgm (search uses ILIKE): %s", e)
        return False
    if not ext_schema or not _PG_IDENT_RE.match(ext_schema):
        logger.warning("Metadata search: pg_trgm is not installed; search uses ILIKE without trigram indexes.")
        return False

    ok = True
    for name, table, column in _METADATA_TRGM_INDEXES:
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                conn.execute(text(
                    f'CREATE INDEX IF NOT EXISTS "{name}" ON "{schema}"."{table}" '
                    f'USING gin ("{column}" "{ext_schema}".gin_trgm_ops)'
                ))
        except Exception as e:  # noqa: BLE001 - never block startup
            ok = False
            logger.warning("Metadata search: could not create index %s (continuing): %s", name, e)
    return ok
