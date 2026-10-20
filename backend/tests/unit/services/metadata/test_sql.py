"""Statement builders for UC tags and comments (ADR-0006 D3)."""
import pytest

from app.services.metadata.sql import build_comment_statement, build_tag_statement


@pytest.mark.parametrize("securable_type,name,keyword,target", [
    ("catalog", "sales", "CATALOG", "`sales`"),
    ("schema", "sales.bronze", "SCHEMA", "`sales`.`bronze`"),
    ("table", "sales.bronze.orders", "TABLE", "`sales`.`bronze`.`orders`"),
    ("view", "sales.bronze.v", "VIEW", "`sales`.`bronze`.`v`"),
    ("metric_view", "sales.bronze.mv", "VIEW", "`sales`.`bronze`.`mv`"),
    ("materialized_view", "sales.bronze.m", "MATERIALIZED VIEW", "`sales`.`bronze`.`m`"),
    ("streaming_table", "sales.bronze.st", "STREAMING TABLE", "`sales`.`bronze`.`st`"),
])
def test_each_securable_type_gets_its_alter_keyword(securable_type, name, keyword, target):
    assert build_tag_statement(securable_type, name, "set", tags={"a": "1", "b": "2"}) == [
        f"ALTER {keyword} {target} SET TAGS ('a' = '1', 'b' = '2');"
    ]
    assert build_tag_statement(securable_type, name, "unset", keys=["a", "b"]) == [
        f"ALTER {keyword} {target} UNSET TAGS ('a', 'b');"
    ]


def test_key_only_tags_are_written_with_an_empty_value():
    assert build_tag_statement("table", "c.s.t", "set", tags={"pii": None}) == [
        "ALTER TABLE `c`.`s`.`t` SET TAGS ('pii' = '');"
    ]


@pytest.mark.parametrize("operation,kwargs", [
    ("set", {"tags": {}}), ("set", {}), ("unset", {"keys": []}), ("unset", {}),
])
def test_nothing_to_do_builds_no_statement(operation, kwargs):
    assert build_tag_statement("table", "c.s.t", operation, **kwargs) == []


@pytest.mark.parametrize("securable_type,keyword", [
    ("table", "TABLE"), ("materialized_view", "MATERIALIZED VIEW"), ("streaming_table", "STREAMING TABLE"),
])
def test_table_like_columns_use_alter_column(securable_type, keyword):
    assert build_tag_statement(securable_type, "c.s.t", "set", tags={"pii": None, "cls": "x"}, column="Email") == [
        f"ALTER {keyword} `c`.`s`.`t` ALTER COLUMN `Email` SET TAGS ('pii' = '', 'cls' = 'x');"
    ]
    assert build_tag_statement(securable_type, "c.s.t", "unset", keys=["pii"], column="Email") == [
        f"ALTER {keyword} `c`.`s`.`t` ALTER COLUMN `Email` UNSET TAGS ('pii');"
    ]


def test_a_column_typed_as_column_is_read_as_a_table_column():
    assert build_tag_statement("column", "c.s.t", "unset", keys=["k"], column="x") == [
        "ALTER TABLE `c`.`s`.`t` ALTER COLUMN `x` UNSET TAGS ('k');"
    ]


@pytest.mark.parametrize("securable_type", ["view", "metric_view"])
def test_view_columns_take_one_statement_per_tag(securable_type):
    assert build_tag_statement(securable_type, "c.s.v", "set", tags={"cls": "x", "pii": None}, column="my col") == [
        "SET TAG ON COLUMN `c`.`s`.`v`.`my col` `cls` = `x`;",
        "SET TAG ON COLUMN `c`.`s`.`v`.`my col` `pii`;",
    ]
    assert build_tag_statement(securable_type, "c.s.v", "unset", keys=["cls", "pii"], column="my col") == [
        "UNSET TAG ON COLUMN `c`.`s`.`v`.`my col` `cls`;",
        "UNSET TAG ON COLUMN `c`.`s`.`v`.`my col` `pii`;",
    ]


def test_hostile_names_keys_and_values_are_quoted_and_escaped():
    [stmt] = build_tag_statement(
        "table", "c.s.t`; DROP TABLE x; --", "set", tags={"k'); DROP": "v' OR '1'='1 \\"},
        column="col`name",
    )
    assert stmt == (
        "ALTER TABLE `c`.`s`.`t``; DROP TABLE x; --` ALTER COLUMN `col``name` "
        "SET TAGS ('k\\'); DROP' = 'v\\' OR \\'1\\'=\\'1 \\\\');"
    )


def test_hostile_view_column_tags_are_quoted_identifiers():
    [stmt] = build_tag_statement("view", "c.s.v", "set", tags={"k`x": "v`; DROP"}, column="c`1")
    assert stmt == "SET TAG ON COLUMN `c`.`s`.`v`.`c``1` `k``x` = `v``; DROP`;"


@pytest.mark.parametrize("securable_type,name,column", [
    ("table", "c", None),
    ("table", "c.s", None),
    ("catalog", "c.s", None),
    ("schema", "c", None),
    ("schema", "c.s", "col"),
    ("catalog", "c", "col"),
    ("table", "c.s.t", "  "),
])
def test_a_name_that_does_not_fit_its_type_is_refused(securable_type, name, column):
    with pytest.raises(ValueError):
        build_tag_statement(securable_type, name, "set", tags={"a": "1"}, column=column)


def test_unknown_type_or_operation_is_refused():
    with pytest.raises(ValueError):
        build_tag_statement("volume", "c.s.v", "set", tags={"a": "1"})
    with pytest.raises(ValueError):
        build_tag_statement("table", "c.s.t", "replace", tags={"a": "1"})


@pytest.mark.parametrize("securable_type,name,expected", [
    ("catalog", "c", "COMMENT ON CATALOG `c` IS 'Sales data';"),
    ("schema", "c.s", "COMMENT ON SCHEMA `c`.`s` IS 'Sales data';"),
    ("table", "c.s.t", "COMMENT ON TABLE `c`.`s`.`t` IS 'Sales data';"),
    ("view", "c.s.t", "COMMENT ON TABLE `c`.`s`.`t` IS 'Sales data';"),
    ("metric_view", "c.s.t", "COMMENT ON TABLE `c`.`s`.`t` IS 'Sales data';"),
    ("materialized_view", "c.s.t", "COMMENT ON TABLE `c`.`s`.`t` IS 'Sales data';"),
    ("streaming_table", "c.s.t", "COMMENT ON TABLE `c`.`s`.`t` IS 'Sales data';"),
])
def test_comment_forms(securable_type, name, expected):
    assert build_comment_statement(securable_type, name, "Sales data") == expected


@pytest.mark.parametrize("comment", [None, ""])
def test_no_comment_clears_it(comment):
    assert build_comment_statement("schema", "c.s", comment) == "COMMENT ON SCHEMA `c`.`s` IS NULL;"
    assert build_comment_statement("view", "c.s.v", comment, column="x") == "COMMENT ON COLUMN `c`.`s`.`v`.`x` IS NULL;"


@pytest.mark.parametrize("securable_type", ["table", "view", "materialized_view", "streaming_table", "column"])
def test_column_comments_use_comment_on_column(securable_type):
    assert build_comment_statement(securable_type, "c.s.t", "It's a \\ path", column="Order ID") == (
        "COMMENT ON COLUMN `c`.`s`.`t`.`Order ID` IS 'It\\'s a \\\\ path';"
    )


def test_comment_names_are_checked_against_the_type():
    with pytest.raises(ValueError):
        build_comment_statement("table", "c.s", "x")
    with pytest.raises(ValueError):
        build_comment_statement("schema", "c.s", "x", column="col")
