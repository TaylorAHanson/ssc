"""Catalog-to-environment mapping and counterparts (ADR-0006 D5)."""
import logging

import pytest

from app.core.config import settings
from app.services.metadata import environments
from app.services.metadata.types import ObjectKey

ROWS = [
    {"key": "dev", "catalog_pattern": "{base}_dev", "catalogs": " Sandbox, scratch ", "approval": "sensitive"},
    {"key": "tst", "catalog_pattern": "{base}_tst", "catalogs": "", "approval": ""},
    {"key": "prd", "catalog_pattern": "{base}_prd", "catalogs": "", "approval": "ALL"},
    {"key": "shared", "catalog_pattern": "", "catalogs": "shared_ref", "approval": "bogus"},
]


@pytest.fixture(autouse=True)
def envs(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_ENVIRONMENTS", [dict(r) for r in ROWS])


def test_rows_parse_into_environment_info():
    envs = {e.key: e for e in environments.environments()}

    assert list(envs) == ["dev", "tst", "prd", "shared"]
    assert envs["dev"].catalogs == ["sandbox", "scratch"]
    assert envs["dev"].catalog_pattern == "{base}_dev"
    assert envs["tst"].approval == "sensitive"
    assert envs["prd"].approval == "all"
    assert envs["shared"].approval == "sensitive"
    assert envs["shared"].catalog_pattern is None
    assert environments.environment_keys() == ["dev", "tst", "prd", "shared"]


def test_rows_without_a_key_and_repeated_keys_are_skipped(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_ENVIRONMENTS", [
        {"key": "", "catalog_pattern": "{base}_x"},
        "not a row",
        {"key": "dev", "catalog_pattern": "{base}_dev"},
        {"key": "DEV", "catalog_pattern": "{base}_other"},
        {"key": "prd", "catalogs": ["Main", "main"]},
    ])

    envs = environments.environments()

    assert [e.key for e in envs] == ["dev", "prd"]
    assert envs[0].catalog_pattern == "{base}_dev"
    assert envs[1].catalogs == ["main"]


@pytest.mark.parametrize("catalog,expected", [
    ("sales_dev", "dev"),
    ("SALES_PRD", "prd"),
    ("sales_tst", "tst"),
    ("sandbox", "dev"),
    ("Scratch", "dev"),
    ("shared_ref", "shared"),
    ("sales", None),
    ("_dev", None),
    ("", None),
    (None, None),
])
def test_environment_of(catalog, expected):
    assert environments.environment_of(catalog) == expected


def test_the_first_matching_row_wins(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_ENVIRONMENTS", [
        {"key": "sandbox", "catalogs": "sales_dev"},
        {"key": "dev", "catalog_pattern": "{base}_dev"},
    ])

    assert environments.environment_of("sales_dev") == "sandbox"
    assert environments.environment_of("hr_dev") == "dev"


def test_settings_are_read_at_call_time(monkeypatch):
    assert environments.environment_of("sales_dev") == "dev"

    monkeypatch.setattr(settings, "METADATA_ENVIRONMENTS", [{"key": "lab", "catalog_pattern": "{base}_dev"}])

    assert environments.environment_of("sales_dev") == "lab"


def test_counterpart_keeps_schema_table_and_column():
    key = ObjectKey.parse("sales_dev.bronze.orders", "Customer ID")

    other = environments.counterpart(key, "prd")

    assert other is not None
    assert other.catalog == "sales_prd"
    assert (other.schema_name, other.object_name, other.column_name) == ("bronze", "orders", "Customer ID")
    assert other.fqn_key == "sales_prd.bronze.orders::customer id"


def test_counterpart_keeps_the_base_spelling_and_works_for_catalogs_and_schemas():
    assert environments.counterpart(ObjectKey.parse("Sales_DEV"), "tst").catalog == "Sales_tst"
    schema = environments.counterpart(ObjectKey.parse("sales_dev.bronze"), "prd")
    assert schema.name == "sales_prd.bronze" and schema.level == "schema"


@pytest.mark.parametrize("catalog,base", [
    ("sales_dev", "sales"), ("Sales_PRD", "Sales"), ("sandbox", None), ("shared_ref", None), ("sales", None),
])
def test_base_of(catalog, base):
    assert environments.base_of(catalog) == base


def test_target_environment_is_matched_case_insensitively():
    assert environments.counterpart(ObjectKey.parse("sales_dev.s.t"), "PRD").catalog == "sales_prd"


@pytest.mark.parametrize("name,target", [
    ("sandbox.s.t", "prd"),        # only in an explicit list
    ("sales.s.t", "prd"),          # no environment
    ("sales_dev.s.t", "shared"),   # target has no pattern
    ("sales_dev.s.t", "nope"),     # unknown target
    ("sales_dev.s.t", "dev"),      # its own environment
])
def test_no_counterpart(name, target):
    assert environments.counterpart(ObjectKey.parse(name), target) is None


def test_an_explicitly_listed_catalog_that_also_matches_the_pattern_has_counterparts(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_ENVIRONMENTS", [
        {"key": "dev", "catalog_pattern": "{base}_dev", "catalogs": "sales_dev"},
        {"key": "prd", "catalog_pattern": "{base}_prd"},
    ])

    assert environments.counterpart(ObjectKey.parse("sales_dev.s.t"), "prd").catalog == "sales_prd"


def test_a_counterpart_claimed_by_an_earlier_row_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_ENVIRONMENTS", [
        {"key": "legacy", "catalogs": "sales_prd"},
        {"key": "dev", "catalog_pattern": "{base}_dev"},
        {"key": "prd", "catalog_pattern": "{base}_prd"},
    ])

    assert environments.counterpart(ObjectKey.parse("sales_dev.s.t"), "prd") is None
    assert environments.counterpart(ObjectKey.parse("hr_dev.s.t"), "prd").catalog == "hr_prd"


def test_a_pattern_may_put_the_base_anywhere(monkeypatch):
    monkeypatch.setattr(settings, "METADATA_ENVIRONMENTS", [
        {"key": "dev", "catalog_pattern": "dev_{base}"},
        {"key": "prd", "catalog_pattern": "{base}"},
    ])

    assert environments.environment_of("dev_sales") == "dev"
    assert environments.counterpart(ObjectKey.parse("dev_sales.s.t"), "prd").catalog == "sales"
    assert environments.counterpart(ObjectKey.parse("sales.s.t"), "dev").catalog == "dev_sales"


def test_a_pattern_without_base_matches_nothing(monkeypatch, caplog):
    environments._pattern_regex.cache_clear()
    monkeypatch.setattr(settings, "METADATA_ENVIRONMENTS", [{"key": "x", "catalog_pattern": "fixed_name"}])

    with caplog.at_level(logging.WARNING, logger="app.services.metadata.environments"):
        assert environments.environment_of("fixed_name") is None
    assert "no {base} placeholder" in caplog.text


def test_counterparts_lists_every_other_environment():
    found = environments.counterparts(ObjectKey.parse("sales_dev.bronze.orders"))

    assert {env: k.name for env, k in found.items()} == {
        "tst": "sales_tst.bronze.orders",
        "prd": "sales_prd.bronze.orders",
    }


def test_counterparts_is_empty_without_a_pattern_match():
    assert environments.counterparts(ObjectKey.parse("sandbox.s.t")) == {}
    assert environments.counterparts(ObjectKey.parse("sales.s.t")) == {}


@pytest.mark.parametrize("env,expected", [
    ("prd", "all"),
    ("PRD", "all"),
    ("dev", "sensitive"),
    ("unknown", "sensitive"),
    (None, "sensitive"),
    ("", "sensitive"),
])
def test_approval_rule(env, expected):
    assert environments.approval_rule(env) == expected
