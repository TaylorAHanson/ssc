"""Tests for the TagPlan and diff planning engine."""

from app.workflows.tag_plan import (
    ObjectState,
    build_tag_plan,
)


def test_plan_with_no_changes():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={"dataset": "orders_ds", "env": "prod"},
        )
    }
    desired = [{"table": "main.sales.orders", "desired_tags": {"dataset": "orders_ds", "env": "prod"}}]

    plan = build_tag_plan(desired, live_state)
    assert not plan.actionable
    assert plan.statement_count == 0
    assert len(plan.diffs) == 1
    diff = plan.diffs["main.sales.orders"]
    assert diff.changed_keys == []
    assert diff.removed_keys == []
    assert diff.overwritten_keys == []


def test_plan_with_set_and_unset():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={"dataset": "orders_ds", "old_key": "old_val", "env": "dev"},
        )
    }
    desired = [
        {
            "table": "main.sales.orders",
            "desired_tags": {"dataset": "orders_ds", "env": "prod", "new_key": "new_val"},
        }
    ]

    plan = build_tag_plan(desired, live_state)
    assert plan.actionable
    assert plan.statement_count == 2
    diff = plan.diffs["main.sales.orders"]
    assert "old_key" in diff.removed_keys
    assert "new_key" in diff.changed_keys
    assert "env" in diff.overwritten_keys

    # Check generated statements
    stmts = plan.statements
    assert any("SET TAGS" in s and "'new_key' = 'new_val'" in s for s in stmts)
    assert any("UNSET TAGS ('old_key')" in s for s in stmts)


def test_plan_with_view_object_type():
    live_state = {
        "main.sales.orders_v": ObjectState(
            display="main.sales.orders_v",
            object_type="VIEW",
            exists=True,
            tags={},
        )
    }
    desired = [{"table": "main.sales.orders_v", "desired_tags": {"tier": "gold"}}]

    plan = build_tag_plan(desired, live_state)
    assert plan.actionable
    assert len(plan.statements) == 1
    assert "ALTER VIEW `main`.`sales`.`orders_v` SET TAGS" in plan.statements[0]


def test_plan_with_missing_object():
    live_state = {
        "main.sales.missing": ObjectState(
            display="main.sales.missing",
            object_type="TABLE",
            exists=False,
            tags={},
        )
    }
    desired = [{"table": "main.sales.missing", "desired_tags": {"tier": "gold"}}]

    plan = build_tag_plan(desired, live_state)
    assert plan.missing_objects == ["main.sales.missing"]
    assert plan.statement_count == 1


def test_plan_preserves_dataset_tag_when_omitted():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={"dataset": "orders_ds", "old_key": "old_val"},
        )
    }
    # Desired tags omit 'dataset', but provide a new tag
    desired = [{"table": "main.sales.orders", "desired_tags": {"owner": "analytics"}}]

    plan = build_tag_plan(desired, live_state)
    assert plan.actionable
    diff = plan.diffs["main.sales.orders"]
    # 'dataset' should be preserved in diff.after and NOT in removed_keys
    assert diff.after.get("dataset") == "orders_ds"
    assert "dataset" not in diff.removed_keys
    assert "old_key" in diff.removed_keys

    # Generated statements must UNSET 'old_key' but NEVER 'dataset'
    stmts = plan.statements
    assert any("UNSET TAGS ('old_key')" in s for s in stmts)
    assert not any("UNSET TAGS" in s and "'dataset'" in s for s in stmts)



# --- Columns, quoting and name safety ------------------------------------------

import pytest
from unittest.mock import MagicMock

from app.workflows.tag_plan import (
    fetch_live_state,
    quote_identifier,
    target_key,
    validate_target,
)


def test_column_on_a_table_uses_alter_column():
    live_state = {
        "main.sales.orders": ObjectState(display="main.sales.orders", object_type="TABLE", exists=True),
        "main.sales.orders::email": ObjectState(
            display="main.sales.orders.email", object_type="TABLE", exists=True, column="email",
            tags={"pii": "true", "old": "x"},
        ),
    }
    desired = [{"table": "main.sales.orders", "column": "email", "desired_tags": {"pii": "true", "classification": "restricted"}}]

    plan = build_tag_plan(desired, live_state)
    assert plan.has_column_changes
    assert plan.statements == [
        "ALTER TABLE `main`.`sales`.`orders` ALTER COLUMN `email` SET TAGS ('classification' = 'restricted');",
        "ALTER TABLE `main`.`sales`.`orders` ALTER COLUMN `email` UNSET TAGS ('old');",
    ]
    diff = plan.diffs["main.sales.orders::email"]
    assert diff.label == "main.sales.orders.email"
    assert diff.to_dict()["column"] == "email"


def test_view_column_unsets_before_overwriting_one_tag_at_a_time():
    live_state = {
        "main.sales.orders_v": ObjectState(display="main.sales.orders_v", object_type="VIEW", exists=True),
        "main.sales.orders_v::email": ObjectState(
            display="main.sales.orders_v.email", object_type="VIEW", exists=True, column="email",
            tags={"classification": "internal", "stale": ""},
        ),
    }
    desired = [{"table": "main.sales.orders_v", "column": "email", "desired_tags": {"classification": "restricted", "pii": ""}}]

    plan = build_tag_plan(desired, live_state)
    target = "`main`.`sales`.`orders_v`.`email`"
    assert plan.statements == [
        f"UNSET TAG ON COLUMN {target} `stale`;",
        f"UNSET TAG ON COLUMN {target} `classification`;",
        f"SET TAG ON COLUMN {target} `classification` = `restricted`;",
        f"SET TAG ON COLUMN {target} `pii`;",
    ]


def test_column_dataset_tag_is_not_preserved_like_a_tables():
    live_state = {
        "main.sales.orders::id": ObjectState(
            display="main.sales.orders.id", exists=True, column="id", tags={"dataset": "orders"},
        ),
    }
    plan = build_tag_plan([{"table": "main.sales.orders", "column": "id", "desired_tags": {}}], live_state)
    assert plan.diffs["main.sales.orders::id"].removed_keys == ["dataset"]


def test_column_inherits_certification_from_its_table():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders", exists=True, all_tags={"system.certification_status": "certified"},
        ),
        "main.sales.orders::email": ObjectState(display="main.sales.orders.email", exists=True, column="email"),
    }
    plan = build_tag_plan([{"table": "main.sales.orders", "column": "email", "desired_tags": {"pii": "true"}}], live_state)
    assert plan.diffs["main.sales.orders::email"].certified is True


def test_missing_column_is_reported_by_label():
    plan = build_tag_plan([{"table": "main.sales.orders", "column": "nope", "desired_tags": {"pii": "true"}}], {})
    assert plan.missing_objects == ["main.sales.orders.nope"]


@pytest.mark.parametrize("name", [
    "main.sales.orders; DROP TABLE x",
    "main.sales.`orders`",
    "main.sales.o'rders",
    "main.sales.orders.extra",
    "main..orders",
    "",
    "main;",
])
def test_unsafe_or_malformed_names_are_rejected(name):
    with pytest.raises(ValueError):
        validate_target(name)


def test_catalogs_and_schemas_are_valid_targets_but_have_no_columns():
    validate_target("main")
    validate_target("main.sales")
    with pytest.raises(ValueError):
        validate_target("main.sales", "email")


def test_column_names_may_have_spaces_but_not_backticks():
    validate_target("main.sales.orders", "Customer Email")
    with pytest.raises(ValueError):
        validate_target("main.sales.orders", "bad`col")
    with pytest.raises(ValueError):
        validate_target("main.sales.orders", "  ")


def test_identifiers_are_backtick_quoted_and_escaped():
    assert quote_identifier("a`b") == "`a``b`"
    assert target_key("Main.Sales.Orders", "EMail") == "main.sales.orders::email"


def test_fetch_live_state_reads_column_existence_and_tags():
    def execute(statement, **kwargs):
        if "information_schema.tables" in statement:
            rows = [["sales", "orders", "VIEW"]]
        elif "information_schema.columns" in statement:
            rows = [["sales", "orders", "Email"]]
        elif "information_schema.column_tags" in statement:
            rows = [["sales", "orders", "Email", "pii", "true"], ["sales", "orders", "Email", "system.x", "y"]]
        else:
            rows = []
        return MagicMock(result=MagicMock(data_array=rows))

    provider = MagicMock()
    provider.client.statement_execution.execute_statement.side_effect = execute

    state = fetch_live_state(provider, [], [("main.sales.orders", "email")])
    col = state["main.sales.orders::email"]
    assert col.exists is True
    assert col.object_type == "VIEW"
    assert col.tags == {"pii": "true", "system.x": "y"}  # only certification is reserved
    assert "system.x" in col.all_tags
    assert state["main.sales.orders"].exists is True


@pytest.mark.parametrize("table_type,keyword", [
    ("MATERIALIZED_VIEW", "MATERIALIZED VIEW"),
    ("STREAMING_TABLE", "STREAMING TABLE"),
    ("METRIC_VIEW", "VIEW"),
    ("MANAGED", "TABLE"),
    ("EXTERNAL", "TABLE"),
])
def test_relation_types_get_their_own_alter_statement(table_type, keyword):
    from app.workflows.tag_plan import relation_type
    obj = relation_type(table_type)
    live_state = {
        "main.s.t": ObjectState(display="main.s.t", object_type=obj, exists=True),
        "main.s.t::c": ObjectState(display="main.s.t.c", object_type=obj, exists=True, column="c"),
    }
    plan = build_tag_plan(
        [{"table": "main.s.t", "desired_tags": {"k": "v"}},
         {"table": "main.s.t", "column": "c", "desired_tags": {"k": "v"}}],
        live_state,
    )
    if keyword == "VIEW":
        assert plan.statements == [
            "ALTER VIEW `main`.`s`.`t` SET TAGS ('k' = 'v');",
            "SET TAG ON COLUMN `main`.`s`.`t`.`c` `k` = `v`;",
        ]
    else:
        assert plan.statements == [
            f"ALTER {keyword} `main`.`s`.`t` SET TAGS ('k' = 'v');",
            f"ALTER {keyword} `main`.`s`.`t` ALTER COLUMN `c` SET TAGS ('k' = 'v');",
        ]


def test_live_state_keeps_materialized_view_type():
    def execute(statement, **kwargs):
        rows = [["s", "mv", "MATERIALIZED_VIEW"]] if "information_schema.tables" in statement else []
        return MagicMock(result=MagicMock(data_array=rows))

    provider = MagicMock()
    provider.client.statement_execution.execute_statement.side_effect = execute
    assert fetch_live_state(provider, ["main.s.mv"])["main.s.mv"].object_type == "MATERIALIZED_VIEW"


def test_only_the_certification_tag_is_kept_out_of_edits():
    live_state = {
        "main.s.t": ObjectState(
            display="main.s.t", exists=True,
            tags={"system.deprecated": "true"},
            all_tags={"system.deprecated": "true", "system.certification_status": "certified"},
        )
    }
    plan = build_tag_plan(
        [{"table": "main.s.t", "desired_tags": {"system.owner_note": "x", "system.certification_status": "certified"}}],
        live_state,
    )
    diff = plan.diffs["main.s.t"]
    assert diff.after == {"system.owner_note": "x"}
    assert diff.removed_keys == ["system.deprecated"]
    assert diff.certified is True


# --- Catalogs, schemas and descriptions ----------------------------------------

from app.workflows.tag_plan import CATALOG, SCHEMA, sql_string_literal


def test_string_literals_escape_backslashes_and_quotes():
    # Databricks concatenates adjacent literals, so a doubled quote would drop the apostrophe.
    assert sql_string_literal("It's") == "'It\\'s'"
    assert sql_string_literal("a\\") == "'a\\\\'"
    assert sql_string_literal("a\\'b") == "'a\\\\\\'b'"


def test_tag_values_with_quotes_are_escaped_in_statements():
    plan = build_tag_plan([{"table": "main.s.t", "desired_tags": {"owner": "O'Brien"}}],
                          {"main.s.t": ObjectState(display="main.s.t", exists=True)})
    assert plan.statements == ["ALTER TABLE `main`.`s`.`t` SET TAGS ('owner' = 'O\\'Brien');"]


def test_catalog_and_schema_tags_use_their_own_alter_statements():
    live_state = {
        "main": ObjectState(display="main", object_type=CATALOG, exists=True, tags={"old": "x"}),
        "main.sales": ObjectState(display="main.sales", object_type=SCHEMA, exists=True),
    }
    plan = build_tag_plan(
        [{"table": "main", "desired_tags": {"domain": "core"}},
         {"table": "main.sales", "desired_tags": {"domain": "sales"}}],
        live_state,
    )
    assert plan.has_container_changes
    assert plan.statements == [
        "ALTER CATALOG `main` SET TAGS ('domain' = 'core');",
        "ALTER CATALOG `main` UNSET TAGS ('old');",
        "ALTER SCHEMA `main`.`sales` SET TAGS ('domain' = 'sales');",
    ]


def test_missing_catalog_or_schema_keeps_its_type():
    plan = build_tag_plan([{"table": "nope.s", "desired_tags": {"k": "v"}}], {})
    assert plan.missing_objects == ["nope.s"]
    assert plan.statements == ["ALTER SCHEMA `nope`.`s` SET TAGS ('k' = 'v');"]


def test_description_changes_on_catalogs_and_schemas():
    live_state = {
        "main": ObjectState(display="main", object_type=CATALOG, exists=True, comment="Old"),
        "main.sales": ObjectState(display="main.sales", object_type=SCHEMA, exists=True, comment="Keep"),
        "main.hr": ObjectState(display="main.hr", object_type=SCHEMA, exists=True, comment="Gone soon"),
    }
    plan = build_tag_plan(
        [{"table": "main", "desired_tags": {}, "desired_comment": "Finance team's data"},
         {"table": "main.sales", "desired_tags": {}, "desired_comment": "Keep"},
         {"table": "main.hr", "desired_tags": {}, "desired_comment": ""}],
        live_state,
    )
    assert plan.statements == [
        "COMMENT ON CATALOG `main` IS 'Finance team\\'s data';",
        "COMMENT ON SCHEMA `main`.`hr` IS NULL;",
    ]
    assert plan.object_count == 2
    d = plan.diffs["main"].to_dict()
    assert (d["comment_before"], d["comment_after"], d["comment_changed"]) == ("Old", "Finance team's data", True)
    assert not plan.diffs["main.sales"].has_changes


def test_description_is_left_alone_when_not_requested():
    live_state = {"main": ObjectState(display="main", object_type=CATALOG, exists=True, comment="Old")}
    plan = build_tag_plan([{"table": "main", "desired_tags": {"k": "v"}}], live_state)
    assert plan.diffs["main"].comment_after is None
    assert not any(s.startswith("COMMENT") for s in plan.statements)


def test_fetch_live_state_reads_catalogs_and_schemas_from_system_information_schema():
    def execute(statement, **kwargs):
        if "information_schema.catalogs" in statement:
            rows = [["main", "Main catalog"]]
        elif "information_schema.catalog_tags" in statement:
            rows = [["main", "domain", "core"], ["main", "system.certification_status", "certified"]]
        elif "information_schema.schemata" in statement:
            rows = [["main", "sales", None]]
        elif "information_schema.schema_tags" in statement:
            rows = [["main", "sales", "pii", None]]
        else:
            rows = []
        return MagicMock(result=MagicMock(data_array=rows))

    provider = MagicMock()
    provider.client.statement_execution.execute_statement.side_effect = execute
    state = fetch_live_state(provider, ["main", "main.sales", "main.gone"])
    assert (state["main"].object_type, state["main"].exists, state["main"].comment) == (CATALOG, True, "Main catalog")
    assert state["main"].tags == {"domain": "core"}
    assert state["main"].all_tags["system.certification_status"] == "certified"
    assert (state["main.sales"].object_type, state["main.sales"].comment, state["main.sales"].tags) == (SCHEMA, "", {"pii": ""})
    assert state["main.gone"].exists is False
    statements = [c.kwargs["statement"] for c in provider.client.statement_execution.execute_statement.call_args_list]
    assert all("system.information_schema" in s for s in statements)
