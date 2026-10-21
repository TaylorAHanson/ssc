"""Desired vs observed under full enforcement, the drawer's drift entries and the typed plan (ADR-0006 D3)."""
import pytest

from app.services.metadata import diff as d
from app.services.metadata.types import CommentState, DesiredState, ObjectKey, ObservedState

EXEMPT = ["system.certification_status"]


def desired(name="c.s.t", column=None, *, securable_type=None, tags=None, comment=None):
    key = ObjectKey.parse(name, column)
    stype = securable_type or {"catalog": "catalog", "schema": "schema", "relation": "table", "column": "column"}[key.level]
    return DesiredState(
        id=f"id-{key.fqn_key}", key=key, fqn_key=key.fqn_key, display_name=key.display, securable_type=stype,
        tags=tags or {}, comment=CommentState(value=comment),
    )


def observed(name="c.s.t", column=None, *, securable_type=None, table_type=None, tags=None, comment=None, present=True):
    key = ObjectKey.parse(name, column)
    stype = securable_type or {"catalog": "catalog", "schema": "schema", "relation": "table", "column": "column"}[key.level]
    return ObservedState(
        fqn_key=key.fqn_key, key=key, securable_type=stype, table_type=table_type, tags=tags or {},
        comment=comment, present=present,
    )


# --- diff ---------------------------------------------------------------------------------


def test_missing_and_changed_tags_are_set_and_extra_tags_unset():
    result = d.diff(
        desired(tags={"owner": "team_a", "cls": "restricted", "pii": None}),
        observed(tags={"owner": "team_b", "pii": "", "stale": "x", "Old": "y"}),
        exempt_keys=EXEMPT,
    )

    assert result.observed_present is True
    assert result.to_set == {"cls": "restricted", "owner": "team_a"}
    assert result.to_unset == ["Old", "stale"]
    assert result.comment_changed is False
    assert not result.in_sync


def test_exempt_keys_are_ignored_on_both_sides_case_insensitively():
    result = d.diff(
        desired(tags={"System.Certification_Status": "certified", "a": "1"}),
        observed(tags={"system.certification_status": "pending", "SYSTEM.CERTIFICATION_STATUS": "x", "a": "1"}),
        exempt_keys=["  SYSTEM.certification_status "],
    )

    assert result.in_sync


def test_tag_keys_are_otherwise_compared_exactly():
    result = d.diff(desired(tags={"PII": "true"}), observed(tags={"pii": "true"}), exempt_keys=[])

    assert result.to_set == {"PII": "true"}
    assert result.to_unset == ["pii"]


@pytest.mark.parametrize("want,have,changed", [
    (None, "", False),
    ("", None, False),
    (None, None, False),
    ("Orders", "Orders", False),
    ("Orders", None, True),
    (None, "Orders", True),
    ("Orders", "orders", True),
])
def test_comment_change_treats_empty_and_none_alike(want, have, changed):
    result = d.diff(desired(comment=want), observed(comment=have), exempt_keys=EXEMPT)

    assert result.comment_changed is changed
    if changed:
        assert result.comment_after == (want or None)
        assert result.comment_before == (have or None)


@pytest.mark.parametrize("obs", [None, "absent"])
def test_with_nothing_observed_everything_desired_is_set(obs):
    obs_row = observed(tags={"x": "1"}, comment="old", present=False) if obs else None
    result = d.diff(
        desired(tags={"a": "1", "pii": None}, comment="Orders"), obs_row, exempt_keys=EXEMPT,
    )

    assert result.observed_present is False
    assert result.to_set == {"a": "1", "pii": None}
    assert result.to_unset == []
    assert result.comment_changed and result.comment_after == "Orders" and result.comment_before is None


def test_tags_fall_back_to_tag_details():
    from app.services.metadata.types import TagAssignment

    row = desired()
    row.tag_details = [TagAssignment(key="a", value="1"), TagAssignment(key="pii", value="")]

    assert d.diff(row, observed(), exempt_keys=[]).to_set == {"a": "1", "pii": None}


# --- drift_entries ------------------------------------------------------------------------


def test_drift_entries_list_each_difference_and_flag_exempt_keys():
    entries = d.drift_entries(
        desired(tags={"a": "1", "b": "2", "c": None, "same": "s"}, comment="New"),
        observed(tags={"a": "1x", "c": "", "d": "4", "same": "s", "system.certification_status": "certified"},
                 comment="Old"),
        exempt_keys=EXEMPT,
    )

    assert [(e.field, e.key, e.kind, e.desired, e.observed, e.exempt) for e in entries] == [
        ("tag", "a", "changed", "1", "1x", False),
        ("tag", "b", "missing", "2", None, False),
        ("tag", "d", "extra", None, "4", False),
        ("tag", "system.certification_status", "extra", None, "certified", True),
        ("comment", None, "changed", "New", "Old", False),
    ]


@pytest.mark.parametrize("want,have,kind", [("New", None, "missing"), (None, "Old", "extra")])
def test_comment_drift_kinds(want, have, kind):
    [entry] = d.drift_entries(desired(comment=want), observed(comment=have), exempt_keys=EXEMPT)

    assert (entry.field, entry.kind) == ("comment", kind)


def test_in_sync_objects_have_no_drift_entries():
    assert d.drift_entries(
        desired(tags={"a": "1", "k": None}, comment=""), observed(tags={"a": "1", "k": ""}, comment=None),
        exempt_keys=EXEMPT,
    ) == []


def test_unobserved_object_shows_everything_desired_as_missing():
    entries = d.drift_entries(desired(tags={"a": "1"}, comment="x"), None, exempt_keys=EXEMPT)

    assert [(e.field, e.key, e.kind) for e in entries] == [("tag", "a", "missing"), ("comment", None, "missing")]


def test_unmanaged_object_has_no_drift():
    assert d.drift_entries(None, observed(tags={"a": "1"}), exempt_keys=EXEMPT) == []


# --- plan ---------------------------------------------------------------------------------


def test_plan_orders_unset_then_set_then_comment_and_counts_removals():
    want = desired(tags={"b": "2", "a": "1"}, comment="Orders")
    have = observed(tags={"z": "9", "y": "8"}, table_type="MANAGED")
    items = d.plan(d.diff(want, have, exempt_keys=EXEMPT), observed=have, object_id="obj-1")

    assert [(i.operation, i.sql, i.removals) for i in items] == [
        ("unset", "ALTER TABLE `c`.`s`.`t` UNSET TAGS ('y', 'z');", 2),
        ("set", "ALTER TABLE `c`.`s`.`t` SET TAGS ('a' = '1', 'b' = '2');", 0),
        ("comment", "COMMENT ON TABLE `c`.`s`.`t` IS 'Orders';", 0),
    ]
    assert all(i.object_id == "obj-1" and i.fqn_key == "c.s.t" and i.name == "c.s.t" for i in items)
    assert items[0].keys == ["y", "z"] and items[1].tags == {"a": "1", "b": "2"} and items[2].comment == "Orders"


def test_clearing_a_description_counts_as_a_removal():
    have = observed(comment="Orders placed online", table_type="MANAGED")
    [cleared] = d.plan(d.diff(desired(), have, exempt_keys=EXEMPT), observed=have)
    [changed] = d.plan(d.diff(desired(comment="Orders"), have, exempt_keys=EXEMPT), observed=have)

    assert (cleared.operation, cleared.comment, cleared.removals) == ("comment", None, 1)
    assert (changed.operation, changed.removals) == ("comment", 0)


@pytest.mark.parametrize("table_type,securable_type,keyword", [
    ("VIEW", "view", "VIEW"),
    ("METRIC_VIEW", "metric_view", "VIEW"),
    ("MATERIALIZED_VIEW", "materialized_view", "MATERIALIZED VIEW"),
    ("streaming table", "streaming_table", "STREAMING TABLE"),
    ("EXTERNAL", "table", "TABLE"),
])
def test_plan_is_typed_from_the_observed_table_type(table_type, securable_type, keyword):
    want = desired(securable_type="table", tags={"a": "1"})
    have = observed(table_type=table_type)
    [item] = d.plan(d.diff(want, have, exempt_keys=EXEMPT), observed=have)

    assert item.securable_type == securable_type
    assert item.sql == f"ALTER {keyword} `c`.`s`.`t` SET TAGS ('a' = '1');"


def test_plan_falls_back_to_the_desired_type_when_nothing_is_observed():
    want = desired(securable_type="streaming_table", tags={"a": "1"})
    [item] = d.plan(d.diff(want, None, exempt_keys=EXEMPT), observed=None)

    assert item.sql == "ALTER STREAMING TABLE `c`.`s`.`t` SET TAGS ('a' = '1');"


def test_plan_uses_uc_spelling_of_the_name():
    want = desired("C.S.T", tags={"a": "1"})
    have = observed("c.s.t")
    [item] = d.plan(d.diff(want, have, exempt_keys=EXEMPT), observed=have)

    assert item.name == "c.s.t"


def test_catalog_and_schema_plans():
    cat = d.plan(d.diff(desired("c", comment="Cat"), observed("c"), exempt_keys=EXEMPT), observed=observed("c"))
    sch = d.plan(
        d.diff(desired("c.s", tags={"a": None}), observed("c.s", tags={"b": "1"}), exempt_keys=EXEMPT),
        observed=observed("c.s"),
    )

    assert [i.sql for i in cat] == ["COMMENT ON CATALOG `c` IS 'Cat';"]
    assert [i.sql for i in sch] == [
        "ALTER SCHEMA `c`.`s` UNSET TAGS ('b');",
        "ALTER SCHEMA `c`.`s` SET TAGS ('a' = '');",
    ]
    assert {i.securable_type for i in cat + sch} == {"catalog", "schema"}


def test_table_column_plan_is_typed_from_the_parent_relation():
    want = desired("c.s.t", "Email", tags={"pii": None}, comment="Address")
    have = observed("c.s.t", "Email", tags={"old": "1"})
    parent = observed("c.s.t", table_type="MATERIALIZED_VIEW")
    items = d.plan(d.diff(want, have, exempt_keys=EXEMPT), observed=have, parent=parent)

    assert [(i.operation, i.sql, i.removals) for i in items] == [
        ("unset", "ALTER MATERIALIZED VIEW `c`.`s`.`t` ALTER COLUMN `Email` UNSET TAGS ('old');", 1),
        ("set", "ALTER MATERIALIZED VIEW `c`.`s`.`t` ALTER COLUMN `Email` SET TAGS ('pii' = '');", 0),
        ("comment", "COMMENT ON COLUMN `c`.`s`.`t`.`Email` IS 'Address';", 0),
    ]
    assert all(i.securable_type == "column" and i.column == "Email" and i.name == "c.s.t" for i in items)
    assert all(i.fqn_key == "c.s.t::email" for i in items)


def test_view_column_plan_unsets_a_changed_value_before_setting_it():
    want = desired("c.s.v", "col", tags={"cls": "secret", "new": None})
    have = observed("c.s.v", "col", tags={"cls": "public", "gone": "1"})
    parent = observed("c.s.v", table_type="VIEW")
    items = d.plan(d.diff(want, have, exempt_keys=EXEMPT), observed=have, parent=parent)

    assert [(i.operation, i.sql, i.removals) for i in items] == [
        ("unset", "UNSET TAG ON COLUMN `c`.`s`.`v`.`col` `cls`;", 0),
        ("unset", "UNSET TAG ON COLUMN `c`.`s`.`v`.`col` `gone`;", 1),
        ("set", "SET TAG ON COLUMN `c`.`s`.`v`.`col` `cls` = `secret`;", 0),
        ("set", "SET TAG ON COLUMN `c`.`s`.`v`.`col` `new`;", 0),
    ]
    assert sum(i.removals for i in items) == 1


def test_column_without_a_known_relation_type_is_a_table_column():
    want = desired("c.s.t", "x", tags={"a": "1"})
    [item] = d.plan(d.diff(want, None, exempt_keys=EXEMPT), observed=None)

    assert item.sql == "ALTER TABLE `c`.`s`.`t` ALTER COLUMN `x` SET TAGS ('a' = '1');"


def test_in_sync_object_plans_nothing():
    want = desired(tags={"a": "1"}, comment="x")
    have = observed(tags={"a": "1", "system.certification_status": "certified"}, comment="x")

    assert d.plan(d.diff(want, have, exempt_keys=EXEMPT), observed=have) == []


def test_plan_sort_key_groups_a_relation_with_its_columns():
    items = []
    for name, column in [("c.s.t2", None), ("c.s.t", "b"), ("c.s.t", None), ("c.s.t", "a")]:
        want = desired(name, column, tags={"k": "v"})
        items += d.plan(d.diff(want, None, exempt_keys=EXEMPT), observed=None)

    assert [i.fqn_key for i in sorted(items, key=d.plan_sort_key)] == ["c.s.t", "c.s.t::a", "c.s.t::b", "c.s.t2"]
