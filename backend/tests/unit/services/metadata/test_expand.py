"""Name patterns in a change expand into one op per known object."""
import itertools
from typing import Optional

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.db.metadata import MetadataObjectModel, MetadataObservedModel
from app.services.metadata import changes, expand
from app.services.metadata.errors import ChangeBlocked, MetadataError
from app.services.metadata.types import Actor, ChangeOp, ChangePreview, ChangeRequest, ChangeResult, ObjectKey

ALICE = Actor(id="alice@example.com", email="alice@example.com", groups=["team_a"])
STEWARD = Actor(id="steward@example.com", email="steward@example.com", roles=["Data Steward"])
_ids = itertools.count(1)
_LEVEL_TYPES = {"catalog": "catalog", "schema": "schema", "relation": "table", "column": "column"}


def _key(name, column=None):
    key = ObjectKey.parse(name, column)
    parent = key.parent()
    return key, dict(
        fqn_key=key.fqn_key, catalog=key.catalog, schema_name=key.schema_name, object_name=key.object_name,
        column_name=key.column_name, parent_key=parent.fqn_key if parent else None, display_name=key.display,
    )


def _snap(db, name, column=None, *, stype: Optional[str] = None, present=True):
    key, cols = _key(name, column)
    cols["catalog"] = cols["catalog"].lower()
    db.add(MetadataObservedModel(securable_type=stype or _LEVEL_TYPES[key.level], tags={}, present=present, **cols))


def _desired(db, name, column=None, *, stype: Optional[str] = None, state="pending", status="active"):
    key, cols = _key(name, column)
    db.add(MetadataObjectModel(id=f"obj-{next(_ids)}", securable_type=stype or _LEVEL_TYPES[key.level],
                               status=status, object_state=state, **cols))


@pytest.fixture
def world(db_session):
    db = db_session
    for name in ("sales_dev", "sales_dev.bronze", "sales_dev.silver", "salesxdev", "salesxdev.bronze"):
        _snap(db, name)
    _snap(db, "sales_dev.bronze.orders")
    _snap(db, "sales_dev.bronze.orders", "email")
    _snap(db, "sales_dev.bronze.orders", "customer_email")
    _snap(db, "sales_dev.bronze.v_orders", stype="view")
    _snap(db, "sales_dev.bronze.dropped", present=False)
    _snap(db, "sales_dev.silver.customers")
    _snap(db, "sales_dev.silver.customers", "email")
    _snap(db, "salesxdev.bronze.orders")
    _desired(db, "sales_dev.bronze.future_table")          # declared before it exists
    _desired(db, "sales_dev.bronze.gone", state="orphaned")
    _desired(db, "sales_dev.bronze.deleted", state="exists", status="deleted")
    _desired(db, "sales_dev.bronze.orders", state="exists")  # also in the snapshot
    db.flush()
    return db


def _names(matches):
    return [(m.name, m.column) if m.column else m.name for m in matches]


def test_relation_pattern_matches_snapshot_and_declared_objects(world):
    assert _names(expand.match_objects(world, "sales_dev.bronze.*")) == [
        "sales_dev.bronze.future_table", "sales_dev.bronze.orders", "sales_dev.bronze.v_orders",
    ]


def test_underscore_is_literal_and_case_is_ignored(world):
    assert _names(expand.match_objects(world, "SALES_DEV.*.*")) == [
        "sales_dev.bronze.future_table", "sales_dev.bronze.orders", "sales_dev.bronze.v_orders",
        "sales_dev.silver.customers",
    ]
    assert _names(expand.match_objects(world, "sales?dev.bronze.orders")) == [
        "sales_dev.bronze.orders", "salesxdev.bronze.orders",
    ]


def test_catalog_and_schema_levels(world):
    assert _names(expand.match_objects(world, "sales*")) == ["sales_dev", "salesxdev"]
    assert _names(expand.match_objects(world, "sales_dev.*")) == ["sales_dev.bronze", "sales_dev.silver"]


def test_column_pattern(world):
    assert _names(expand.match_objects(world, "sales_dev.*.*", column="*email")) == [
        ("sales_dev.bronze.orders", "customer_email"), ("sales_dev.bronze.orders", "email"),
        ("sales_dev.silver.customers", "email"),
    ]
    assert _names(expand.match_objects(world, "sales_dev.*.*", column="email")) == [
        ("sales_dev.bronze.orders", "email"), ("sales_dev.silver.customers", "email"),
    ]


def test_type_filter(world):
    assert _names(expand.match_objects(world, "sales_dev.bronze.*", types=["view"])) == ["sales_dev.bronze.v_orders"]
    with pytest.raises(MetadataError, match="can't match catalog"):
        expand.match_objects(world, "sales_dev.bronze.*", types=["catalog"])


@pytest.mark.parametrize("pattern,column", [
    ("a.b.c.d", None), ("a..c", None), ("", None), ("a.b c.*", None), ("a.'b'.*", None), ("a.b", "email"),
])
def test_bad_patterns_are_refused(world, pattern, column):
    with pytest.raises(MetadataError):
        expand.match_objects(world, pattern, column=column)


def test_too_many_matches(world):
    with pytest.raises(MetadataError, match="more than 2"):
        expand.match_objects(world, "sales_dev.*.*", limit=2)


@pytest.fixture
def two_envs(world, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_ENVIRONMENTS", [
        {"key": "dev", "catalog_pattern": "{base}_dev"},
        {"key": "prd", "catalog_pattern": "{base}_prd"},
    ])
    for name in ("sales_prd", "sales_prd.bronze", "sales_prd.bronze.orders", "hr_prd", "hr_prd.bronze.staff"):
        _snap(world, name)
    _desired(world, "sales_prd.bronze.planned")
    world.flush()
    return world


def test_braced_catalog_matches_that_catalog_in_every_environment(two_envs):
    assert _names(expand.match_objects(two_envs, "{sales}.bronze.*")) == [
        "sales_dev.bronze.future_table", "sales_dev.bronze.orders", "sales_dev.bronze.v_orders",
        "sales_prd.bronze.orders", "sales_prd.bronze.planned",
    ]
    assert _names(expand.match_objects(two_envs, "{SALES}")) == ["sales_dev", "sales_prd"]
    assert _names(expand.match_objects(two_envs, "{*}.bronze.orders")) == [
        "sales_dev.bronze.orders", "sales_prd.bronze.orders",
    ]
    # salesxdev isn't named by an environment pattern, so no braced pattern reaches it.
    assert _names(expand.match_objects(two_envs, "{sales*}.bronze.orders")) == [
        "sales_dev.bronze.orders", "sales_prd.bronze.orders",
    ]
    assert expand.match_objects(two_envs, "{finance}.*") == []


@pytest.mark.parametrize("pattern", ["{sales}_dev.bronze.*", "sales_dev.{bronze}.*", "{}.bronze", "{sales.bronze}"])
def test_braces_only_wrap_the_whole_catalog_part(two_envs, pattern):
    with pytest.raises(MetadataError, match="braces go around the whole catalog part"):
        expand.match_objects(two_envs, pattern)


def test_op_needs_exactly_one_of_name_and_match():
    with pytest.raises(ValidationError):
        ChangeOp(op="set_tags", tags={"a": "b"})
    with pytest.raises(ValidationError):
        ChangeOp(op="set_tags", name="a.b.c", match="a.b.*", tags={"a": "b"})
    assert ChangeOp(op="set_tags", match="a.b.*", tags={"a": "b"}).name == ""


def test_expand_request_keeps_named_ops_and_reports_counts(world):
    ask = ChangeRequest(ops=[
        ChangeOp(op="set_tags", name="hr.people.staff", tags={"domain": "hr"}),
        ChangeOp(op="set_tags", match="sales_dev.bronze.*", tags={"domain": "sales"}),
        ChangeOp(op="unset_tags", match="nothing.*.*", keys=["x"]),
    ], reason="Meeting outcome")
    out = expand.expand_request(world, ask)

    assert [(o.name, o.match, o.securable_type) for o in out.request.ops] == [
        ("hr.people.staff", None, None),
        ("sales_dev.bronze.future_table", None, "table"),
        ("sales_dev.bronze.orders", None, "table"),
        ("sales_dev.bronze.v_orders", None, "view"),
    ]
    assert all(o.tags == {"domain": "sales"} for o in out.request.ops[1:])
    assert out.request.reason == "Meeting outcome"
    assert out.matched == {"sales_dev.bronze.*": 3, "nothing.*.*": 0}
    assert [(f.code, f.severity) for f in out.findings] == [("pattern_matched", "info"), ("pattern_no_match", "warn")]


def test_matches_outside_the_managed_catalogs_are_left_out(world, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["sales_dev"])
    ask = ChangeRequest(ops=[ChangeOp(op="set_tags", match="sales?dev.bronze.orders", tags={"k": "v"})])

    out = expand.expand_request(world, ask)

    assert [o.name for o in out.request.ops] == ["sales_dev.bronze.orders"]
    assert out.matched == {"sales?dev.bronze.orders": 1}
    left_out = [f for f in out.findings if f.code == "pattern_outside_managed_catalogs"]
    assert len(left_out) == 1 and "salesxdev" in left_out[0].message

    monkeypatch.setattr(settings, "METADATA_RECONCILE_CATALOGS", ["hr_*"])
    with pytest.raises(MetadataError, match="manages only catalogs matching hr_"):
        expand.expand_request(world, ask)


def test_expand_request_without_patterns_is_untouched(world):
    ask = ChangeRequest(ops=[ChangeOp(op="set_tags", name="a.b.c", tags={"k": "v"})])
    assert expand.expand_request(world, ask).request is ask


def test_a_change_that_matches_nothing_is_refused(world):
    with pytest.raises(MetadataError, match="No known object matches 'nothing.\\*'"):
        expand.expand_request(world, ChangeRequest(ops=[ChangeOp(op="set_tags", match="nothing.*", tags={"k": None})]))


def test_patterns_cant_carry_expected_versions(world):
    ask = ChangeRequest(ops=[ChangeOp(op="set_tags", match="sales_dev.*", tags={"k": "v"},
                                      expected_versions={"k": 1})])
    with pytest.raises(MetadataError, match="expected versions"):
        expand.expand_request(world, ask)


def test_preview_and_submit_see_expanded_ops_and_carry_the_counts(world, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_BROWSE_ROLES", ["Data Steward"])
    seen = {}

    def fake_preview(db, request, actor):
        seen["preview"] = [o.name for o in request.ops]
        return ChangePreview(object_count=len(request.ops))

    def fake_submit(db, request, actor, *, source):
        seen["submit"] = ([o.name for o in request.ops], source)
        return ChangeResult(status="committed", preview=ChangePreview())

    monkeypatch.setattr(changes, "preview", fake_preview)
    monkeypatch.setattr(changes, "submit", fake_submit)
    ask = ChangeRequest(ops=[ChangeOp(op="set_tags", match="sales_dev.silver.*", tags={"k": "v"})])

    pv = expand.preview(world, ask, STEWARD)
    assert seen["preview"] == ["sales_dev.silver.customers"]
    assert pv.findings[0].message == "'sales_dev.silver.*' matched 1 object(s)."

    result = expand.submit(world, ask, STEWARD, source="ui")
    assert seen["submit"] == (["sales_dev.silver.customers"], "ui")
    assert result.preview.findings[0].code == "pattern_matched"


def test_blocked_submit_keeps_the_counts(world, monkeypatch):
    monkeypatch.setattr(settings, "METADATA_BROWSE_ROLES", ["Data Steward"])

    def blocked(db, request, actor, *, source):
        raise ChangeBlocked(ChangePreview(blocked=True))

    monkeypatch.setattr(changes, "submit", blocked)
    ask = ChangeRequest(ops=[ChangeOp(op="set_tags", match="sales_dev.silver.*", tags={"k": "v"})])
    with pytest.raises(ChangeBlocked) as exc:
        expand.submit(world, ask, STEWARD, source="api")
    assert exc.value.preview.findings[0].code == "pattern_matched"


def test_many_unmanaged_warnings_collapse_into_one(db_session):
    for t in range(10):
        _, cols = _key(f"dev.raw.t{t}")
        db_session.add(MetadataObservedModel(securable_type="table", tags={"legacy": "x"}, present=True, **cols))
    db_session.flush()
    admin = Actor(id="admin@example.com", email="admin@example.com", roles=["Platform Admin"], is_admin=True)
    ask = ChangeRequest(ops=[ChangeOp(op="set_tags", match="dev.raw.*", tags={"domain": "raw"})], reason="Bulk")

    pv = expand.preview(db_session, ask, admin)

    codes = [f.code for f in pv.findings]
    assert codes.count("unmanaged_object") == expand.UNMANAGED_SHOWN
    summary = next(f for f in pv.findings if f.code == "unmanaged_objects")
    assert summary.message.startswith("7 more object(s)")
    assert pv.object_count == 10
