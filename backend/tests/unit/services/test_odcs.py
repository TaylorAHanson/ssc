"""Malformed data contracts must not break the readers that walk every contract."""

import pytest

from app.providers.databricks.handlers.dataset_handler import contract_table_names
from app.services.odcs import contract_schema_objects, contract_servers, field_text


@pytest.mark.parametrize(
    "contract, expected",
    [
        (None, []),
        ("just text", []),
        (["a", "b"], []),
        ({"schema": None}, []),
        ({"schema": "orders"}, []),
        ({"schema": [None, "orders", 3, {"physicalName": "orders"}]}, [{"physicalName": "orders"}]),
        ({"schema": {"orders": {"physicalName": "orders"}, "junk": None}}, [{"physicalName": "orders"}]),
    ],
)
def test_contract_schema_objects_keeps_only_objects(contract, expected):
    assert contract_schema_objects(contract) == expected


def test_contract_servers_accepts_a_name_mapping_and_skips_junk():
    contract = {"servers": {"prod": {"catalog": "sales"}, "broken": None}}
    assert contract_servers(contract) == [{"catalog": "sales"}]
    assert contract_servers({"servers": None}) == []


def test_field_text_only_returns_strings():
    assert field_text("  orders ") == "orders"
    assert field_text(None) == ""
    assert field_text(42) == ""
    assert field_text({"a": 1}) == ""


def test_contract_table_names_skips_junk_entries():
    contract = {
        "servers": [None, {"catalog": "sales", "schema": "core"}],
        "schema": [None, {"physicalName": None}, {"physicalName": "orders"}],
    }
    assert contract_table_names(contract) == ["sales.core.orders"]
