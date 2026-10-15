"""Malformed data contracts must not break the readers that walk every contract."""

import uuid
from types import SimpleNamespace

import pytest

from app.db.data_contract import DataContractModel
from app.providers.databricks.handlers.dataset_handler import contract_table_names
from app.services.odcs import contract_schema_objects, contract_servers, field_text
from app.services.tag_change.datasets import extract_contract_info, list_datasets

GOOD = """
servers:
  - catalog: sales
    schema: core
schema:
  - physicalName: orders
  - physicalName: other.ref.customers
"""

# ``- `` with nothing after it loads as None: the crash behind the tag dataset list.
EMPTY_ITEM = """
servers:
  - catalog: sales
    schema: core
schema:
  -
  - physicalName: orders
"""


def _contract(yaml_content, dataset_id="sales.core.orders"):
    return SimpleNamespace(dataset_id=dataset_id, yaml_content=yaml_content)


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


def test_extract_contract_info_reads_a_good_contract():
    assert extract_contract_info(_contract(GOOD)) == (
        "sales",
        "core",
        ["sales.core.orders", "other.ref.customers"],
    )


def test_extract_contract_info_skips_an_empty_schema_item():
    assert extract_contract_info(_contract(EMPTY_ITEM)) == ("sales", "core", ["sales.core.orders"])


@pytest.mark.parametrize(
    "yaml_content",
    [
        "servers: null\nschema: null\n",
        "servers:\n  -\nschema:\n  - physicalName: 7\n",
        "- just\n- a list\n",
        "plain text\n",
    ],
)
def test_extract_contract_info_tolerates_malformed_contracts(yaml_content):
    assert extract_contract_info(_contract(yaml_content)) == (None, None, [])


def test_list_datasets_survives_a_malformed_contract(db_session):
    for dataset_id, yaml_content in (("sales.core.orders", EMPTY_ITEM), ("hr.people.staff", GOOD)):
        db_session.add(
            DataContractModel(
                id=str(uuid.uuid4()), dataset_id=dataset_id, yaml_content=yaml_content, is_active=True
            )
        )
    db_session.flush()

    datasets = {d.dataset_id: (d.catalog, d.schema_name) for d in list_datasets(db_session)}

    assert datasets["sales.core.orders"] == ("sales", "core")
    assert datasets["hr.people.staff"] == ("sales", "core")


def test_contract_table_names_skips_junk_entries():
    contract = {
        "servers": [None, {"catalog": "sales", "schema": "core"}],
        "schema": [None, {"physicalName": None}, {"physicalName": "orders"}],
    }
    assert contract_table_names(contract) == ["sales.core.orders"]
