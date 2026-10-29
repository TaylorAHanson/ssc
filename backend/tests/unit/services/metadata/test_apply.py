"""Executing planned statements: type mix-up retry, missing-tag no-op and per-object failure isolation."""
import logging
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.metadata import apply
from app.services.metadata.sql import build_tag_statement
from app.services.metadata.types import PlanItem


def _resp(state, message=None, statement_id="stmt-1"):
    error = SimpleNamespace(message=message) if message else None
    return SimpleNamespace(statement_id=statement_id, status=SimpleNamespace(state=SimpleNamespace(value=state), error=error))


class FakeStatements:
    """Records every statement; ``failures`` maps a SQL prefix to UC's error message."""

    def __init__(self, failures=None, *, as_status=False, states=None):
        self.executed = []
        self.warehouses = []
        self.failures = failures or {}
        self.as_status = as_status
        self.states = list(states or [])
        self.polled = []
        self.cancelled = []

    def execute_statement(self, statement, warehouse_id, wait_timeout):
        self.executed.append(statement)
        self.warehouses.append(warehouse_id)
        for prefix, message in self.failures.items():
            if statement.startswith(prefix):
                if self.as_status:
                    return _resp("FAILED", message)
                raise RuntimeError(message)
        return _resp(self.states.pop(0) if self.states else "SUCCEEDED")

    def get_statement(self, statement_id):
        self.polled.append(statement_id)
        return _resp(self.states.pop(0) if self.states else "SUCCEEDED")

    def cancel_execution(self, statement_id):
        self.cancelled.append(statement_id)


class FakeProvider:
    def __init__(self, statements):
        self.client = SimpleNamespace(statement_execution=statements)


def item(fqn_key, operation, sql, *, securable_type="table", name=None, column=None, tags=None, keys=None, **extra):
    return PlanItem(
        fqn_key=fqn_key, name=name or fqn_key.split("::")[0], column=column, securable_type=securable_type,
        operation=operation, sql=sql, tags=tags or {}, keys=keys or [], **extra,
    )


@pytest.fixture(autouse=True)
def _warehouse(monkeypatch):
    monkeypatch.setattr(settings, "DATABRICKS_WAREHOUSE_ID", "wh-1")


def test_statements_run_in_order_and_are_applied():
    statements = FakeStatements()
    items = [
        item("c.s.t", "unset", "ALTER TABLE `c`.`s`.`t` UNSET TAGS ('x');", keys=["x"]),
        item("c.s.t", "set", "ALTER TABLE `c`.`s`.`t` SET TAGS ('a' = '1');", tags={"a": "1"}),
    ]

    outcomes = apply.execute(FakeProvider(statements), items)

    assert statements.executed == [i.sql for i in items]
    assert statements.warehouses == ["wh-1", "wh-1"]
    assert [o.status for o in outcomes] == ["applied", "applied"]
    assert outcomes[0].keys == ["x"] and outcomes[1].tags == {"a": "1"}


def test_the_providers_own_warehouse_wins():
    statements = FakeStatements()
    provider = FakeProvider(statements)
    provider.get_config = lambda key: "wh-gov" if key == "warehouse_id" else None

    apply.execute(provider, [item("c.s.t", "set", "ALTER TABLE `c`.`s`.`t` SET TAGS ('a' = '1');", tags={"a": "1"})])

    assert statements.warehouses == ["wh-gov"]


@pytest.mark.parametrize("as_status", [False, True])
def test_a_table_statement_on_a_view_is_retried_as_alter_view(as_status):
    statements = FakeStatements({
        "ALTER TABLE `c`.`s`.`v`": "[WRONG_COMMAND_FOR_OBJECT_TYPE] The operation ALTER TABLE requires a TABLE. "
                                   "But `c`.`s`.`v` is a VIEW. Use ALTER VIEW instead.",
    }, as_status=as_status)

    [outcome] = apply.execute(FakeProvider(statements), [
        item("c.s.v", "set", "ALTER TABLE `c`.`s`.`v` SET TAGS ('a' = '1');", tags={"a": "1"}),
    ])

    assert statements.executed[-1] == "ALTER VIEW `c`.`s`.`v` SET TAGS ('a' = '1');"
    assert outcome.status == "applied"
    assert outcome.sql == "ALTER VIEW `c`.`s`.`v` SET TAGS ('a' = '1');"
    assert outcome.securable_type == "view"
    assert outcome.detail == "Applied as ALTER VIEW."


@pytest.mark.parametrize("message,retried", [
    ("[EXPECT_VIEW_NOT_TABLE.NO_ALTERNATIVE] 'ALTER VIEW' expects a view but `c`.`s`.`t` is a table.",
     "ALTER TABLE `c`.`s`.`t` UNSET TAGS ('a');"),
    ("[WRONG_COMMAND_FOR_OBJECT_TYPE] requires a VIEW. But `c`.`s`.`t` is a MATERIALIZED_VIEW.",
     "ALTER MATERIALIZED VIEW `c`.`s`.`t` UNSET TAGS ('a');"),
])
def test_a_view_statement_on_another_relation_type_is_retried(message, retried):
    statements = FakeStatements({"ALTER VIEW": message})

    [outcome] = apply.execute(FakeProvider(statements), [
        item("c.s.t", "unset", "ALTER VIEW `c`.`s`.`t` UNSET TAGS ('a');", securable_type="view", keys=["a"]),
    ])

    assert statements.executed == ["ALTER VIEW `c`.`s`.`t` UNSET TAGS ('a');", retried]
    assert outcome.status == "applied" and outcome.sql == retried


def test_a_failed_retry_reports_both_errors():
    statements = FakeStatements({
        "ALTER TABLE": "[EXPECT_TABLE_NOT_VIEW] `c`.`s`.`v` is a view.",
        "ALTER VIEW": "[PERMISSION_DENIED] User does not have APPLY TAG on `c`.`s`.`v`.",
    })

    [outcome] = apply.execute(FakeProvider(statements), [
        item("c.s.v", "set", "ALTER TABLE `c`.`s`.`v` SET TAGS ('a' = '1');", tags={"a": "1"}),
    ])

    assert outcome.status == "failed"
    assert "EXPECT_TABLE_NOT_VIEW" in outcome.detail
    assert "Retried as ALTER VIEW: [PERMISSION_DENIED]" in outcome.detail
    assert outcome.sql == "ALTER TABLE `c`.`s`.`v` SET TAGS ('a' = '1');"


def test_column_and_comment_statements_are_not_retried():
    statements = FakeStatements({"ALTER TABLE": "`c`.`s`.`v` is a VIEW", "COMMENT ON": "`c`.`s`.`v` is a VIEW"})

    outcomes = apply.execute(FakeProvider(statements), [
        item("c.s.v::x", "set", "ALTER TABLE `c`.`s`.`v` ALTER COLUMN `x` SET TAGS ('a' = '1');",
             securable_type="column", name="c.s.v", column="x", tags={"a": "1"}),
        item("c.s.v", "comment", "COMMENT ON TABLE `c`.`s`.`v` IS 'x';"),
    ])

    assert len(statements.executed) == 2
    assert [o.status for o in outcomes] == ["failed", "failed"]


@pytest.mark.parametrize("message", [
    "[TAG_NOT_FOUND] Tag 'pii' not found on `c`.`s`.`t`.",
    "The tag `pii` does not exist on this object.",
])
def test_unsetting_a_tag_that_is_already_gone_is_a_noop(message):
    statements = FakeStatements({"ALTER TABLE": message})

    [outcome] = apply.execute(FakeProvider(statements), [
        item("c.s.t", "unset", "ALTER TABLE `c`.`s`.`t` UNSET TAGS ('pii');", keys=["pii"]),
    ])

    assert outcome.status == "noop"
    assert outcome.detail == "The tag was already gone."


def test_a_missing_table_on_unset_is_a_failure_not_a_noop():
    statements = FakeStatements({
        "ALTER TABLE": "[TABLE_OR_VIEW_NOT_FOUND] The table or view `c`.`s`.`t` cannot be found. SQLSTATE: 42P01\n"
                       "== SQL ==\nALTER TABLE `c`.`s`.`t` UNSET TAGS ('pii')",
    })

    [outcome] = apply.execute(FakeProvider(statements), [
        item("c.s.t", "unset", "ALTER TABLE `c`.`s`.`t` UNSET TAGS ('pii');", keys=["pii"]),
    ])

    assert outcome.status == "failed"


def test_a_missing_tag_error_on_set_is_a_failure():
    statements = FakeStatements({"ALTER TABLE": "[TAG_NOT_FOUND] Governed tag 'cls' does not exist."})

    [outcome] = apply.execute(FakeProvider(statements), [
        item("c.s.t", "set", "ALTER TABLE `c`.`s`.`t` SET TAGS ('cls' = 'x');", tags={"cls": "x"}),
    ])

    assert outcome.status == "failed"


def _object_items():
    return [
        item("c.s.a", "unset", "ALTER TABLE `c`.`s`.`a` UNSET TAGS ('x');", keys=["x"]),
        item("c.s.a", "set", "ALTER TABLE `c`.`s`.`a` SET TAGS ('y' = '1');", tags={"y": "1"}),
        item("c.s.a::col", "set", "ALTER TABLE `c`.`s`.`a` ALTER COLUMN `col` SET TAGS ('p' = '');",
             securable_type="column", name="c.s.a", column="col", tags={"p": None}),
        item("c.s.b", "set", "ALTER TABLE `c`.`s`.`b` SET TAGS ('y' = '1');", tags={"y": "1"}),
        item("c.s.a", "comment", "COMMENT ON TABLE `c`.`s`.`a` IS 'x';"),
    ]


def test_a_refused_statement_doesnt_hold_back_the_objects_other_statements(caplog):
    statements = FakeStatements({"ALTER TABLE `c`.`s`.`a` UNSET": "[PERMISSION_DENIED] no APPLY TAG"})
    items = _object_items()

    with caplog.at_level(logging.WARNING, logger="app.services.metadata.apply"):
        outcomes = apply.execute(FakeProvider(statements), items)

    assert [(o.fqn_key, o.status) for o in outcomes] == [
        ("c.s.a", "failed"), ("c.s.a", "applied"), ("c.s.a::col", "applied"), ("c.s.b", "applied"),
        ("c.s.a", "applied"),
    ]
    assert outcomes[0].detail == "[PERMISSION_DENIED] no APPLY TAG"
    assert sorted(statements.executed) == sorted(i.sql for i in items)
    assert any("c.s.a" in r.getMessage() for r in caplog.records)


def test_a_missing_object_skips_the_rest_of_that_object_but_not_other_objects():
    statements = FakeStatements({"ALTER TABLE `c`.`s`.`a` UNSET": "[TABLE_OR_VIEW_NOT_FOUND] `c`.`s`.`a` cannot be found."})
    items = _object_items()

    outcomes = apply.execute(FakeProvider(statements), items)

    assert [(o.fqn_key, o.status) for o in outcomes] == [
        ("c.s.a", "failed"), ("c.s.a", "skipped"), ("c.s.a::col", "applied"), ("c.s.b", "applied"),
        ("c.s.a", "skipped"),
    ]
    assert statements.executed == [items[0].sql, items[2].sql, items[3].sql]


def test_a_refused_multi_tag_set_is_retried_one_tag_at_a_time():
    statements = FakeStatements({
        "ALTER TABLE `c`.`s`.`t` SET TAGS ('a' = '', ": "[INVALID_PARAMETER_VALUE] 'bad' isn't allowed for governed tag b",
        "ALTER TABLE `c`.`s`.`t` SET TAGS ('b'": "[INVALID_PARAMETER_VALUE] 'bad' isn't allowed for governed tag b",
    })
    tags = {"a": None, "b": "bad", "c": "1"}
    items = [
        item("c.s.t", "set", "ALTER TABLE `c`.`s`.`t` SET TAGS ('a' = '', 'b' = 'bad', 'c' = '1');", tags=tags,
             statement_type="table", blanks=["a"], removals=1),
        item("c.s.t", "comment", "COMMENT ON TABLE `c`.`s`.`t` IS 'x';"),
    ]

    outcomes = apply.execute(FakeProvider(statements), items)

    assert [(o.operation, o.tags, o.status, o.removals) for o in outcomes] == [
        ("set", {"a": None}, "applied", 1), ("set", {"b": "bad"}, "failed", 0), ("set", {"c": "1"}, "applied", 0),
        ("comment", {}, "applied", 0),
    ]
    assert statements.executed[1:] == [
        "ALTER TABLE `c`.`s`.`t` SET TAGS ('a' = '');", "ALTER TABLE `c`.`s`.`t` SET TAGS ('b' = 'bad');",
        "ALTER TABLE `c`.`s`.`t` SET TAGS ('c' = '1');", "COMMENT ON TABLE `c`.`s`.`t` IS 'x';",
    ]


def _view_column_change():
    def sql(operation, **kwargs):
        return build_tag_statement("view", "c.s.v", operation, column="x", **kwargs)[0]

    common = {"securable_type": "column", "name": "c.s.v", "column": "x", "statement_type": "view"}
    return [
        item("c.s.v::x", "unset", sql("unset", keys=["k"]), keys=["k"], **common),
        item("c.s.v::x", "set", sql("set", tags={"k": "new"}), tags={"k": "new"}, restore={"k": "old"}, **common),
    ]


def test_a_view_column_value_that_is_refused_gets_its_old_value_back():
    statements = FakeStatements({"SET TAG ON COLUMN `c`.`s`.`v`.`x` `k` = `new`": "[INVALID_PARAMETER_VALUE] refused"})

    outcomes = apply.execute(FakeProvider(statements), _view_column_change())

    assert [(o.operation, o.tags, o.status) for o in outcomes] == [
        ("unset", {}, "applied"), ("set", {"k": "new"}, "failed"), ("set", {"k": "old"}, "applied"),
    ]
    assert statements.executed[-1] == "SET TAG ON COLUMN `c`.`s`.`v`.`x` `k` = `old`;"
    assert "previous value back" in outcomes[2].detail


@pytest.mark.parametrize("unset_error,expected", [
    ("[PERMISSION_DENIED] no APPLY TAG", ["failed", "skipped"]),
    ("[TAG_NOT_FOUND] Tag 'k' not found.", ["noop", "failed"]),
])
def test_a_view_column_value_change_never_restores_what_it_didnt_remove(unset_error, expected):
    statements = FakeStatements({
        "UNSET TAG ON COLUMN": unset_error,
        "SET TAG ON COLUMN `c`.`s`.`v`.`x` `k` = `new`": "[INVALID_PARAMETER_VALUE] refused",
    })

    outcomes = apply.execute(FakeProvider(statements), _view_column_change())

    assert [o.status for o in outcomes] == expected
    assert not any("`old`" in s for s in statements.executed)


def test_an_empty_statement_is_a_noop():
    statements = FakeStatements()

    [outcome] = apply.execute(FakeProvider(statements), [item("c.s.t", "set", "  ")])

    assert outcome.status == "noop" and statements.executed == []


def test_a_slow_statement_is_polled_until_it_finishes(monkeypatch):
    monkeypatch.setattr(apply, "_POLL_SECONDS", 0)
    statements = FakeStatements(states=["PENDING", "RUNNING", "SUCCEEDED"])

    [outcome] = apply.execute(FakeProvider(statements), [item("c.s.t", "set", "ALTER TABLE `c`.`s`.`t` SET TAGS ('a' = '1');")])

    assert outcome.status == "applied"
    assert statements.polled == ["stmt-1", "stmt-1"]


def test_a_statement_that_never_finishes_is_cancelled_and_failed(monkeypatch):
    monkeypatch.setattr(apply, "_MAX_WAIT_SECONDS", 0)
    statements = FakeStatements(states=["RUNNING"])

    [outcome] = apply.execute(FakeProvider(statements), [item("c.s.t", "set", "ALTER TABLE `c`.`s`.`t` SET TAGS ('a' = '1');")])

    assert outcome.status == "failed" and "Timed out" in outcome.detail
    assert statements.cancelled == ["stmt-1"]


def test_a_broken_provider_fails_items_without_raising():
    outcomes = apply.execute(object(), [
        item("c.s.a", "set", "ALTER TABLE `c`.`s`.`a` SET TAGS ('a' = '1');"),
        item("c.s.b", "set", "ALTER TABLE `c`.`s`.`b` SET TAGS ('a' = '1');"),
    ])

    assert [o.status for o in outcomes] == ["failed", "failed"]


def test_parallel_workers_keep_each_relation_in_order():
    statements = FakeStatements({"ALTER TABLE `c`.`s`.`t3` UNSET": "[PERMISSION_DENIED] no"})
    items = []
    for n in range(8):
        name = f"c.s.t{n}"
        items += [
            item(name, "unset", f"ALTER TABLE `c`.`s`.`t{n}` UNSET TAGS ('x');", keys=["x"]),
            item(name, "set", f"ALTER TABLE `c`.`s`.`t{n}` SET TAGS ('y' = '1');", tags={"y": "1"}),
        ]

    outcomes = apply.execute(FakeProvider(statements), items, workers=4)

    assert [o.sql for o in outcomes] == [i.sql for i in items]
    for n in range(8):
        if n != 3:
            assert statements.executed.index(items[2 * n].sql) < statements.executed.index(items[2 * n + 1].sql)
    assert [o.status for o in outcomes if o.fqn_key == "c.s.t3"] == ["failed", "applied"]
    assert sum(o.status == "applied" for o in outcomes) == 15


def test_only_the_first_failures_are_logged_one_by_one(caplog):
    statements = FakeStatements({"ALTER TABLE": "[PERMISSION_DENIED] no"})
    items = [item(f"c.s.t{n}", "set", f"ALTER TABLE `c`.`s`.`t{n}` SET TAGS ('y' = '1');") for n in range(25)]

    with caplog.at_level(logging.WARNING, logger="app.services.metadata.apply"):
        outcomes = apply.execute(FakeProvider(statements), items, workers=3)

    messages = [r.getMessage() for r in caplog.records]
    assert {o.status for o in outcomes} == {"failed"}
    assert len(messages) == apply._MAX_FAILURE_LOGS + 1
    assert "5 more failed statements" in messages[-1]
