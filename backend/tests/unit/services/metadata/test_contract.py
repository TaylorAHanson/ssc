"""The shared Metadata Manager contract (ADR-0006): object identity and the reconcile queue."""
import pytest

from app.db.metadata import MetadataReconcileQueueModel
from app.services.metadata.reconciler import enqueue
from app.services.metadata.types import ChangeOp, ObjectKey, TagFilter


def test_names_are_normalized_into_one_identity():
    key = ObjectKey.parse(" Sales_Dev.Bronze.Orders ", "Customer ID")

    assert key.fqn_key == "sales_dev.bronze.orders::customer id"
    assert key.display == "Sales_Dev.Bronze.Orders.Customer ID"
    assert key.level == "column"
    assert ObjectKey.from_fqn_key(key.fqn_key).fqn_key == key.fqn_key


def test_ancestors_run_nearest_first():
    key = ObjectKey.parse("c.s.t", "col")

    assert [a.fqn_key for a in key.ancestors()] == ["c.s.t", "c.s", "c"]
    assert key.relation_key().fqn_key == "c.s.t"


@pytest.mark.parametrize("name,column", [
    ("c.s.t; DROP TABLE x", None),
    ("c.s.`t`", None),
    ("c.s.t.u", None),
    ("c..t", None),
    ("c.s", "col"),
    ("c.s.t", "bad`col"),
])
def test_unsafe_or_malformed_names_are_refused(name, column):
    with pytest.raises(ValueError):
        ObjectKey.parse(name, column)


def test_key_only_tags_are_stored_as_none():
    op = ChangeOp(op="set_tags", name="c.s.t", tags={"pii": "", "owner": "team_a", " ": "x"})

    assert op.tags == {"pii": None, "owner": "team_a"}


@pytest.mark.parametrize("text,expected", [
    ("classification=restricted", ("classification", "eq", "restricted")),
    ("pii", ("pii", "exists", None)),
    ("!data_owner", ("data_owner", "missing", None)),
    ("pii=", ("pii", "eq", None)),
])
def test_tag_filters_parse(text, expected):
    f = TagFilter.parse(text)

    assert (f.key, f.op, f.value) == expected


def test_columns_are_queued_with_their_relation_once(db_session):
    keys = enqueue(db_session, ["c.s.t::a", "c.s.t::b", "c.s.t", "c.s"], reason="change", requested_by="jane")
    db_session.flush()
    enqueue(db_session, ["c.s.t"], reason="notify")
    db_session.flush()

    assert keys == ["c.s", "c.s.t"]
    rows = {r.fqn_key: r for r in db_session.query(MetadataReconcileQueueModel)}
    assert sorted(rows) == ["c.s", "c.s.t"]
    assert rows["c.s.t"].reason == "notify"
    assert rows["c.s.t"].requested_by == "jane"
