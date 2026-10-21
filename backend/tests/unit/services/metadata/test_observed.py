"""The observed snapshot (ADR-0006 D2): sweep, targeted refresh, new-object poll and reads.

UC is a fake ``statement_execution`` client answering from an in-memory metastore.
It applies the ``=``, ``IN``, ``NOT IN`` and ``TIMESTAMP`` filters the module
emits, and records every statement.
"""
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Dict, List, Optional

import pytest

from app.core.config import settings
from app.db.metadata import MetadataObservedModel, MetadataSyncStateModel
from app.services.metadata import observed
from app.services.metadata.types import ObjectKey
from app.workflows import tag_governed

T0 = "2026-01-01T00:00:00.000Z"


@dataclass
class FakeTable:
    name: str
    table_type: str = "MANAGED"
    owner: str = "etl_sp"
    comment: Optional[str] = None
    created: str = T0
    last_altered: str = T0
    tags: Dict[str, str] = field(default_factory=dict)
    # (name, full_data_type, comment, tags)
    columns: List[tuple] = field(default_factory=list)


@dataclass
class FakeSchema:
    owner: str = "owner_group"
    comment: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)
    tables: Dict[str, FakeTable] = field(default_factory=dict)


@dataclass
class FakeCatalog:
    owner: str = "admins"
    comment: Optional[str] = None
    catalog_type: str = "MANAGED_CATALOG"
    tags: Dict[str, str] = field(default_factory=dict)
    schemas: Dict[str, FakeSchema] = field(default_factory=dict)
    # information_schema answers but leaves the catalog's own row out (lost BROWSE).
    hidden: bool = False


_VIEW = re.compile(r"FROM\s+(`[^`]+`|system)\.information_schema\.(\w+)")
_IN = re.compile(r"(\w+) (NOT IN|IN) \(([^)]*)\)")
_EQ = re.compile(r"(\w+) = '([^']*)'")
_SINCE = re.compile(r"TIMESTAMP '([^']*)'")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _literals(text: str) -> List[str]:
    return re.findall(r"'([^']*)'", text)


class FakeUC:
    def __init__(self):
        self.catalogs: Dict[str, FakeCatalog] = {}
        self.failing: set = set()
        self.sql: List[str] = []
        self.governed: Dict[str, List[str]] = {}
        self.governed_error: Optional[Exception] = None
        # Results with more rows fail the way a warehouse's inline byte limit does.
        self.max_rows: Optional[int] = None
        self._lock = threading.Lock()

    # -- provider surface --
    @property
    def client(self):
        return SimpleNamespace(
            statement_execution=SimpleNamespace(
                execute_statement=self._execute,
                get_statement=None,
                get_statement_result_chunk_n=None,
            ),
            catalogs=SimpleNamespace(list=self._list_catalogs),
            tag_policies=SimpleNamespace(list_tag_policies=self._list_tag_policies),
        )

    def get_config(self, key, default=None):
        return {"warehouse_id": "wh-1"}.get(key, default)

    def _list_catalogs(self, include_browse=None):
        return [SimpleNamespace(name=n, catalog_type=c.catalog_type) for n, c in self.catalogs.items()]

    def _list_tag_policies(self, page_size=None):
        if self.governed_error:
            raise self.governed_error
        return [
            SimpleNamespace(tag_key=k, values=[SimpleNamespace(name=v) for v in vals], description="")
            for k, vals in self.governed.items()
        ]

    def _execute(self, statement, warehouse_id, wait_timeout=None):
        with self._lock:
            self.sql.append(statement)
        rows: List[list] = []
        for part in statement.split(" UNION ALL "):
            rows.extend(self._answer(part))
        if self.max_rows is not None and len(rows) > self.max_rows:
            error = (
                "ServiceError(error_code=<ServiceErrorCode.BAD_REQUEST: 'BAD_REQUEST'>, message='Inline byte limit "
                "exceeded. Statements executed with disposition=INLINE can have a result size of at most 26214400 "
                "bytes. Please execute the statement with disposition=EXTERNAL_LINKS if you want to download the "
                "full result.')"
            )
            return SimpleNamespace(
                statement_id=f"stmt-{len(self.sql)}",
                status=SimpleNamespace(state=SimpleNamespace(value="FAILED"), error=error),
                result=None,
            )
        return SimpleNamespace(
            statement_id=f"stmt-{len(self.sql)}",
            status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED"), error=None),
            result=SimpleNamespace(data_array=rows, next_chunk_index=None),
        )

    # -- a tiny information_schema --
    def _answer(self, sql: str) -> List[list]:
        source, view = _VIEW.search(sql).groups()
        where = sql.split(" WHERE ", 1)[1] if " WHERE " in sql else ""
        if source != "system":
            name = source.strip("`")
            if name in self.failing or name not in self.catalogs:
                raise RuntimeError(f"[CATALOG_NOT_FOUND] {name}")
        conditions = [(c, op, set(_literals(vals))) for c, op, vals in _IN.findall(where)]
        conditions += [(c, "=", {v}) for c, v in _EQ.findall(where) if c not in ("kind",)]
        for column, op, values in conditions:
            if column in ("catalog_name", "table_catalog") and op != "NOT IN" and values & self.failing:
                raise RuntimeError(f"[CATALOG_NOT_FOUND] {sorted(values & self.failing)}")
        since = [datetime.fromisoformat(s.replace("Z", "+00:00")) for s in _SINCE.findall(where)]

        def keep(row: dict) -> bool:
            for column, op, values in conditions:
                value = row.get(column)
                if op == "NOT IN" and value in values:
                    return False
                if op in ("IN", "=") and value not in values:
                    return False
            if since:
                stamps = [datetime.fromisoformat(row[c].replace("Z", "+00:00")) for c in ("created", "last_altered")]
                if not any(s > since[0] for s in stamps):
                    return False
            return True

        out = []
        for row, values in self._view_rows(view, sql):
            if not keep(row):
                continue
            if row["catalog_name"] in self.failing:
                raise RuntimeError(f"[INTERNAL_ERROR] {row['catalog_name']}")
            out.append(values)
        return out

    def _view_rows(self, view: str, sql: str):
        for cname, cat in self.catalogs.items():
            base = {"catalog_name": cname, "table_catalog": cname}
            if view == "catalogs" and not cat.hidden:
                yield base, ["catalog", cname, None, cat.owner, cat.comment, T0, T0]
            if view == "catalog_tags":
                for k, v in cat.tags.items():
                    yield base, ["catalog", None, None, None, k, v]
            for sname, sch in cat.schemas.items():
                srow = {**base, "schema_name": sname, "table_schema": sname}
                if view == "schemata":
                    yield srow, ["schema", cname, sname, sch.owner, sch.comment, T0, T0]
                if view == "schema_tags":
                    for k, v in sch.tags.items():
                        yield srow, ["schema", sname, None, None, k, v]
                for tname, t in sch.tables.items():
                    trow = {**srow, "table_name": tname, "created": t.created, "last_altered": t.last_altered}
                    if view == "tables" and sql.startswith("SELECT table_catalog"):
                        yield trow, [cname, sname, tname, t.created, t.last_altered]
                    elif view == "tables":
                        yield trow, [sname, tname, t.table_type, t.owner, t.comment, t.created, t.last_altered]
                    if view == "table_tags":
                        for k, v in t.tags.items():
                            yield trow, ["table", sname, tname, None, k, v]
                    for pos, (col, dtype, comment, ctags) in enumerate(t.columns):
                        if view == "columns":
                            yield trow, [sname, tname, col, dtype, str(pos), comment]
                        if view == "column_tags":
                            for k, v in ctags.items():
                                yield trow, ["column", sname, tname, col, k, v]


def _metastore() -> FakeUC:
    uc = FakeUC()
    orders = FakeTable(
        name="orders", comment="All orders", tags={"classification": "restricted", "flagged": ""},
        columns=[("Email", "string", "Customer email", {"pii": ""}), ("id", "bigint", "", {})],
    )
    bronze = FakeSchema(comment="Raw", tags={"layer": "bronze"}, tables={
        "orders": orders,
        "v_orders": FakeTable(name="v_orders", table_type="VIEW", columns=[("id", "bigint", None, {})]),
        "mv": FakeTable(name="mv", table_type="MATERIALIZED_VIEW"),
        "st": FakeTable(name="st", table_type="STREAMING_TABLE"),
        "kpis": FakeTable(name="kpis", table_type="METRIC_VIEW"),
        "ext": FakeTable(name="ext", table_type="EXTERNAL"),
        "__materialization_mat_kpis": FakeTable(name="__materialization_mat_kpis", columns=[("x", "int", None, {})]),
    })
    info = FakeSchema(tables={"tables": FakeTable(name="tables", table_type="VIEW")})
    uc.catalogs["sales"] = FakeCatalog(
        comment="Sales data", tags={"data_owner": "team_a"}, schemas={"bronze": bronze, "information_schema": info},
    )
    uc.catalogs["hr"] = FakeCatalog(schemas={"people": FakeSchema(tables={
        "staff": FakeTable(name="staff", columns=[("name", "string", None, {"pii": "true"})]),
    })})
    uc.governed = {"classification": ["public", "restricted"], "pii": []}
    return uc


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", [], raising=False)
    monkeypatch.setattr(settings, "SCAN_CATALOGS", "", raising=False)
    monkeypatch.setattr(tag_governed, "_keys_cache", {"at": 0.0, "keys": None})


def _rows(db) -> Dict[str, MetadataObservedModel]:
    db.expire_all()
    return {r.fqn_key: r for r in db.query(MetadataObservedModel)}


# --- scope ---------------------------------------------------------------------------


def test_scope_prefers_reconcile_catalogs_then_scan_catalogs_then_visible(monkeypatch):
    uc = _metastore()
    uc.catalogs["system"] = FakeCatalog(catalog_type="SYSTEM_CATALOG")
    uc.catalogs["shared"] = FakeCatalog(catalog_type="DELTASHARING_CATALOG")
    uc.catalogs["hive_metastore"] = FakeCatalog()

    assert observed.scope_catalogs(uc) == ["sales", "hr"]

    monkeypatch.setattr(settings, "SCAN_CATALOGS", " Finance , sales ")
    assert observed.scope_catalogs(uc) == ["finance", "sales"]

    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["Sandbox_Dev"])
    assert observed.scope_catalogs(uc) == ["sandbox_dev"]


def test_securable_types_follow_uc_table_types():
    assert observed.securable_type_for("MANAGED") == "table"
    assert observed.securable_type_for("EXTERNAL") == "table"
    assert observed.securable_type_for("FOREIGN") == "table"
    assert observed.securable_type_for(None) == "table"
    assert observed.securable_type_for("VIEW") == "view"
    assert observed.securable_type_for("MATERIALIZED_VIEW") == "materialized_view"
    assert observed.securable_type_for("materialized view") == "materialized_view"
    assert observed.securable_type_for("STREAMING_TABLE") == "streaming_table"
    assert observed.securable_type_for("METRIC_VIEW") == "metric_view"


# --- sweep ---------------------------------------------------------------------------


def test_sweep_snapshots_every_level_with_tags_and_comments(db_session):
    uc = _metastore()

    result = observed.sweep(db_session, provider=uc)

    assert result.catalogs == ["sales", "hr"]
    assert result.failed_catalogs == {}
    rows = _rows(db_session)
    assert result.objects_seen == len(rows)
    assert {k: r.securable_type for k, r in rows.items() if k.startswith("sales.bronze.") and "::" not in k} == {
        "sales.bronze.orders": "table",
        "sales.bronze.v_orders": "view",
        "sales.bronze.mv": "materialized_view",
        "sales.bronze.st": "streaming_table",
        "sales.bronze.kpis": "metric_view",
        "sales.bronze.ext": "table",
    }
    # information_schema and Databricks-managed materializations aren't snapshotted.
    assert not any("information_schema" in k or "__materialization" in k for k in rows)

    catalog = rows["sales"]
    assert (catalog.securable_type, catalog.owner, catalog.comment) == ("catalog", "admins", "Sales data")
    assert catalog.tags == {"data_owner": "team_a"}
    assert catalog.created == datetime(2026, 1, 1)
    assert rows["sales.bronze"].tags == {"layer": "bronze"}
    assert rows["sales.bronze"].parent_key == "sales"

    orders = rows["sales.bronze.orders"]
    assert orders.tags == {"classification": "restricted", "flagged": None}
    assert (orders.table_type, orders.owner, orders.parent_key) == ("MANAGED", "etl_sp", "sales.bronze")

    email = rows["sales.bronze.orders::email"]
    assert (email.securable_type, email.column_name, email.display_name) == ("column", "Email", "sales.bronze.orders.Email")
    assert email.fqn_key == ObjectKey.parse("sales.bronze.orders", "Email").fqn_key
    assert (email.data_type, email.ordinal_position, email.comment) == ("string", 0, "Customer email")
    assert email.tags == {"pii": None}
    assert email.parent_key == "sales.bronze.orders"
    # An empty comment is no description.
    assert rows["sales.bronze.orders::id"].comment is None
    assert all(r.present and r.missing_since is None for r in rows.values())

    assert observed.last_sweep_at(db_session) is not None
    assert observed.governed_tags(db_session) == {"classification": ["public", "restricted"], "pii": []}


def test_sweep_reads_the_system_information_schema_filtered_to_the_catalog(db_session):
    """<catalog>.information_schema needs USE CATALOG; the system views also list what's only BROWSEd."""
    uc = _metastore()

    observed.sweep(db_session, provider=uc, catalogs=["Sales"])

    assert len(uc.sql) == 5
    assert all("system.information_schema." in s and "`sales`" not in s for s in uc.sql)
    assert all("'sales'" in s for s in uc.sql)
    assert {r.catalog for r in _rows(db_session).values()} == {"sales"}


def _wide_catalog() -> FakeUC:
    """One catalog, four schemas of three two-column tables, each table and column tagged."""
    uc = FakeUC()
    uc.catalogs["big"] = FakeCatalog(schemas={
        f"s{i}": FakeSchema(tables={
            f"t{j}": FakeTable(
                name=f"t{j}", tags={"layer": "gold"},
                columns=[("a", "string", None, {"pii": ""}), ("b", "int", None, {"pii": ""})],
            )
            for j in range(3)
        })
        for i in range(4)
    })
    return uc


def test_a_result_over_the_inline_limit_is_read_in_parts(db_session):
    """A big catalog's tables/columns/tags overflow one inline result; halving by schema, then table, reads it all."""
    uc = _wide_catalog()
    observed.sweep(db_session, provider=uc)
    expected = {k: (r.present, dict(r.tags or {})) for k, r in _rows(db_session).items()}
    assert len(expected) == 1 + 4 + 12 + 24

    db_session.query(MetadataObservedModel).delete()
    db_session.commit()
    uc.sql.clear()
    uc.max_rows = 5  # fits the catalog and schema rows, not one schema's 6 columns or 9 tags: those split by table

    result = observed.sweep(db_session, provider=uc)

    assert result.failed_catalogs == {}
    assert {k: (r.present, dict(r.tags or {})) for k, r in _rows(db_session).items()} == expected
    assert any(" AND table_schema IN ('s0') AND table_name IN (" in s for s in uc.sql if ".columns " in s)
    assert sum(1 for s in uc.sql if ".tables " in s) == 1 + 2 + 4  # whole, two halves, four schemas


def test_a_single_table_over_the_inline_limit_fails_the_catalog(db_session):
    uc = _wide_catalog()
    uc.max_rows = 1

    result = observed.sweep(db_session, provider=uc)

    assert "Inline byte limit exceeded" in result.failed_catalogs["big"]
    assert _rows(db_session) == {}


def test_overlong_column_types_fall_back_to_the_bare_type_name():
    sql = observed._statements("system.information_schema", "big", observed._Targets())["columns"]
    assert f"CASE WHEN length(full_data_type) > {observed._DATA_TYPE_MAX} THEN data_type ELSE full_data_type END" in sql


def test_sweep_marks_dropped_objects_missing_and_leaves_failed_catalogs_untouched(db_session):
    uc = _metastore()
    observed.sweep(db_session, provider=uc)
    first = _rows(db_session)
    hr_seen_at = first["hr.people.staff"].observed_at

    del uc.catalogs["sales"].schemas["bronze"].tables["orders"]
    uc.failing.add("hr")
    result = observed.sweep(db_session, provider=uc)

    assert result.catalogs == ["sales"]
    assert set(result.failed_catalogs) == {"hr"}
    assert "CATALOG_NOT_FOUND" in result.failed_catalogs["hr"]
    assert result.objects_missing == 3  # the table and its two columns
    rows = _rows(db_session)
    for key in ("sales.bronze.orders", "sales.bronze.orders::email", "sales.bronze.orders::id"):
        assert rows[key].present is False
        assert rows[key].missing_since is not None
    assert rows["sales.bronze.v_orders"].present is True
    staff = rows["hr.people.staff"]
    assert staff.present is True and staff.observed_at == hr_seen_at
    missing_since = rows["sales.bronze.orders"].missing_since

    observed.sweep(db_session, provider=uc)
    assert _rows(db_session)["sales.bronze.orders"].missing_since == missing_since

    uc.catalogs["sales"].schemas["bronze"].tables["orders"] = FakeTable(name="orders", tags={"recreated": ""})
    observed.sweep(db_session, provider=uc)
    back = _rows(db_session)["sales.bronze.orders"]
    assert back.present is True and back.missing_since is None
    assert back.tags == {"recreated": None}


def test_a_catalog_whose_own_row_is_invisible_counts_as_failed(db_session):
    uc = _metastore()
    observed.sweep(db_session, provider=uc)
    uc.catalogs["hr"].hidden = True
    uc.catalogs["hr"].schemas.clear()

    result = observed.sweep(db_session, provider=uc)

    assert "hr" in result.failed_catalogs
    assert "BROWSE" in result.failed_catalogs["hr"]
    assert _rows(db_session)["hr.people.staff"].present is True


def test_governed_tag_failure_keeps_the_cache_and_the_sweep(db_session):
    uc = _metastore()
    observed.sweep(db_session, provider=uc)
    tag_governed._keys_cache.update(at=0.0, keys=None)
    uc.governed_error = RuntimeError("PERMISSION_DENIED")

    result = observed.sweep(db_session, provider=uc)

    assert result.failed_catalogs == {}
    assert observed.governed_tags(db_session)["classification"] == ["public", "restricted"]


def test_sweep_records_the_run_in_sync_state(db_session):
    uc = _metastore()
    uc.failing.add("hr")

    result = observed.sweep(db_session, provider=uc)

    state = db_session.get(MetadataSyncStateModel, observed.LAST_SWEEP_KEY).value
    assert state["catalogs"] == ["sales"]
    assert set(state["failed_catalogs"]) == {"hr"}
    assert state["objects_seen"] == result.objects_seen
    assert result.took_ms >= 0 and result.read_ms >= 0 and result.write_ms >= 0


def test_a_sweep_where_every_catalog_fails_keeps_the_last_sweep_time(db_session):
    uc = _metastore()
    observed.sweep(db_session, provider=uc)
    before = observed.last_sweep_at(db_session)
    uc.failing.update({"sales", "hr"})

    observed.sweep(db_session, provider=uc)

    assert observed.last_sweep_at(db_session) == before


# --- refresh -------------------------------------------------------------------------


def test_refresh_rereads_targets_and_their_columns(db_session):
    uc = _metastore()
    observed.sweep(db_session, provider=uc)
    bronze = uc.catalogs["sales"].schemas["bronze"]
    bronze.tables["orders"].tags = {"classification": "public"}
    bronze.tables["orders"].columns = [("Email", "string", "Email address", {})]
    del bronze.tables["v_orders"]
    uc.sql.clear()

    states = observed.refresh(db_session, [
        ObjectKey.parse("sales.bronze.orders"),
        ObjectKey.parse("sales.bronze.v_orders"),
        ObjectKey.parse("sales.bronze.never_existed"),
        ObjectKey.parse("sales.bronze"),
        ObjectKey.parse("newcat.raw.events"),
    ], provider=uc)

    assert states["sales.bronze.orders"].tags == {"classification": "public"}
    assert states["sales.bronze.orders::email"].comment == "Email address"
    assert states["sales.bronze.orders::id"].present is False
    assert states["sales.bronze.v_orders"].present is False
    assert states["sales.bronze.never_existed"].present is False
    assert states["sales.bronze"].tags == {"layer": "bronze"}
    assert states["newcat.raw.events"].present is False
    assert states["newcat.raw.events"].securable_type == "table"

    rows = _rows(db_session)
    assert rows["sales.bronze.orders"].tags == {"classification": "public"}
    assert rows["sales.bronze.orders::id"].present is False
    assert rows["sales.bronze.v_orders"].present is False
    assert rows["sales.bronze.v_orders::id"].present is False
    # A never-seen object gets no placeholder row; untouched objects keep theirs.
    assert "sales.bronze.never_existed" not in rows
    assert rows["sales.bronze.mv"].present is True

    assert all("system.information_schema" in s for s in uc.sql)
    tables_sql = next(s for s in uc.sql if ".tables " in s)
    assert "table_name IN ('never_existed', 'orders', 'v_orders')" in tables_sql
    assert "table_catalog = 'sales'" in tables_sql


def test_refresh_of_a_column_rereads_its_relation(db_session):
    uc = _metastore()
    observed.sweep(db_session, provider=uc)
    uc.catalogs["sales"].schemas["bronze"].tables["orders"].columns[0] = ("Email", "string", None, {"pii": "true"})

    states = observed.refresh(db_session, [ObjectKey.parse("sales.bronze.orders", "EMAIL")], provider=uc)

    assert states["sales.bronze.orders::email"].tags == {"pii": "true"}
    assert states["sales.bronze.orders"].present is True
    assert "sales.bronze.orders::id" in states


def test_refresh_of_a_catalog_reads_only_the_catalog(db_session):
    uc = _metastore()
    uc.catalogs["sales"].tags = {"data_owner": "team_b"}

    states = observed.refresh(db_session, [ObjectKey.parse("Sales")], provider=uc)

    assert list(states) == ["sales"]
    assert states["sales"].tags == {"data_owner": "team_b"}
    assert len(uc.sql) == 2  # containers + tags; no relations or columns


def test_refresh_leaves_out_keys_of_a_catalog_it_could_not_read(db_session):
    uc = _metastore()
    observed.sweep(db_session, provider=uc)
    uc.failing.add("hr")

    states = observed.refresh(db_session, [
        ObjectKey.parse("hr.people.staff"), ObjectKey.parse("sales.bronze.mv"),
    ], provider=uc)

    assert "hr.people.staff" not in states
    assert states["sales.bronze.mv"].present is True
    assert _rows(db_session)["hr.people.staff"].present is True


def test_refresh_reads_every_catalog_before_writing(db_session, monkeypatch):
    uc = _metastore()
    order = []
    real_read, real_upsert = observed._read, observed._upsert
    monkeypatch.setattr(observed, "_read", lambda *a, **k: order.append("read") or real_read(*a, **k))
    monkeypatch.setattr(observed, "_upsert", lambda *a, **k: order.append("write") or real_upsert(*a, **k))

    observed.refresh(db_session, [ObjectKey.parse("sales.bronze.orders"), ObjectKey.parse("hr.people.staff")],
                     provider=uc)

    assert order == ["read", "read", "write", "write"]


def test_refresh_chunks_large_in_lists(db_session, monkeypatch):
    uc = _metastore()
    monkeypatch.setattr(observed, "_REFRESH_CHUNK", 2)
    keys = [ObjectKey.parse(f"sales.bronze.{t}") for t in ("orders", "v_orders", "mv", "st", "kpis")]

    states = observed.refresh(db_session, keys, provider=uc)

    assert sum(1 for s in uc.sql if ".tables " in s) == 3
    assert all(states[k.fqn_key].present for k in keys)


# --- new-object poll -----------------------------------------------------------------


def _set_watermark(db, at: datetime):
    db.merge(MetadataSyncStateModel(key=observed.NEW_OBJECT_WATERMARK_KEY, value={"at": at.isoformat()}))
    db.flush()


def _watermark(db) -> datetime:
    db.expire_all()
    return datetime.fromisoformat(db.get(MetadataSyncStateModel, observed.NEW_OBJECT_WATERMARK_KEY).value["at"])


def test_first_poll_only_sets_the_watermark(db_session):
    uc = _metastore()
    before = _now()

    assert observed.poll_new_objects(db_session, provider=uc) == []

    assert uc.sql == []
    assert before <= _watermark(db_session) <= _now()


def test_poll_returns_relations_past_the_watermark_and_advances_it(db_session):
    uc = _metastore()
    bronze = uc.catalogs["sales"].schemas["bronze"]
    bronze.tables["fresh"] = FakeTable(name="fresh", created="2026-03-01T10:00:00.000Z",
                                      last_altered="2026-03-01T10:00:00.000Z", tags={"source": "pipeline"})
    bronze.tables["orders"].last_altered = "2026-03-02T08:30:00.500Z"
    bronze.tables["__materialization_mat_new"] = FakeTable(name="__materialization_mat_new",
                                                          created="2026-03-03T00:00:00.000Z")
    _set_watermark(db_session, datetime(2026, 2, 1))

    found = observed.poll_new_objects(db_session, provider=uc)

    assert found == ["sales.bronze.fresh", "sales.bronze.orders"]
    assert _watermark(db_session) == datetime(2026, 3, 2, 8, 30, 0, 500000)
    poll_sql = uc.sql[0]
    assert "system.information_schema.tables" in poll_sql
    assert "TIMESTAMP '2026-02-01T00:00:00.000000Z'" in poll_sql
    assert "table_catalog IN ('hr', 'sales')" in poll_sql
    rows = _rows(db_session)
    assert rows["sales.bronze.fresh"].tags == {"source": "pipeline"}
    assert rows["sales.bronze.orders::email"].present is True

    uc.sql.clear()
    assert observed.poll_new_objects(db_session, provider=uc) == []


def test_poll_never_moves_the_watermark_past_now(db_session):
    uc = _metastore()
    uc.catalogs["hr"].schemas["people"].tables["staff"].last_altered = "2099-01-01T00:00:00.000Z"
    _set_watermark(db_session, datetime(2026, 2, 1))

    assert observed.poll_new_objects(db_session, provider=uc) == ["hr.people.staff"]

    assert _watermark(db_session) <= _now()


# --- reads ---------------------------------------------------------------------------


def test_get_observed_and_columns_read_the_snapshot(db_session):
    uc = _metastore()
    uc.catalogs["sales"].schemas["bronze"].tables["orders"].columns.append(("zip", "string", None, {}))
    observed.sweep(db_session, provider=uc)
    del uc.catalogs["sales"].schemas["bronze"].tables["orders"].columns[1]
    observed.refresh(db_session, [ObjectKey.parse("sales.bronze.orders")], provider=uc)

    states = observed.get_observed(db_session, ["sales.bronze.orders", "sales.bronze.orders::email", "missing.key"])
    assert set(states) == {"sales.bronze.orders", "sales.bronze.orders::email"}
    state = states["sales.bronze.orders::email"]
    assert state.key.column_name == "Email" and state.key.fqn_key == "sales.bronze.orders::email"
    assert state.tags == {"pii": None}

    present = observed.get_columns(db_session, "Sales.Bronze.Orders")
    assert [c.key.column_name for c in present] == ["Email", "zip"]
    everything = observed.get_columns(db_session, "sales.bronze.orders", include_missing=True)
    assert [c.key.column_name for c in everything] == ["Email", "id", "zip"]


def test_reads_are_empty_before_any_sweep(db_session):
    assert observed.governed_tags(db_session) == {}
    assert observed.last_sweep_at(db_session) is None
    assert observed.get_observed(db_session, []) == {}
    assert observed.refresh(db_session, []) == {}


def test_sweep_upserts_in_chunks(db_session, monkeypatch):
    uc = _metastore()
    monkeypatch.setattr(observed, "_UPSERT_CHUNK", 3)

    result = observed.sweep(db_session, provider=uc)

    assert result.objects_seen == len(_rows(db_session)) > 3


def test_timestamps_parse_to_naive_utc():
    assert observed._ts("2026-03-01T10:00:00.123Z") == datetime(2026, 3, 1, 10, 0, 0, 123000)
    assert observed._ts("2026-03-01T12:00:00+02:00") == datetime(2026, 3, 1, 10, 0, 0)
    assert observed._ts("2026-03-01 10:00:00") == datetime(2026, 3, 1, 10, 0, 0)
    assert observed._ts("") is None and observed._ts("not a date") is None
    assert observed._ts(datetime(2026, 3, 1) + timedelta(hours=1)) == datetime(2026, 3, 1, 1)
