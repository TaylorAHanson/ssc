"""``draft_odcs_contract`` scope: who may call it, which tables it describes, which lineage it keeps.

The tool reads metadata as the governance service principal and the resulting
contract is shown to every user, so it is gated to governance admins, refuses
tables outside ``SCAN_CATALOGS`` (or the platform catalogs when that is blank),
and drops out-of-scope lineage neighbours both when drafting and when a stored
contract is read back.
"""
import inspect
import uuid
from unittest.mock import MagicMock, patch

import pytest
import yaml

from app.core.config import settings
from app.db.data_contract import DataContractModel
from app.db.tool_registry import TOOL_ORIGIN_LOCAL, ToolRegistryModel
from app.models.user import User
from app.providers.databricks.uc_metadata import TableMetadata, UcMetadataBatch
from app.services.odcs import scope_contract_lineage_yaml, table_in_scope
from app.services.tool_registry_service import ToolRegistryService
from app.tools.governance.draft_odcs import draft_odcs_contract, fetch_datasets_metadata

TOOL = "draft_odcs_contract"


def _user(*roles):
    return User(id="u@example.com", email="u@example.com", full_name="U", roles=list(roles))


@pytest.fixture
def allowlist(monkeypatch):
    def _set(value):
        monkeypatch.setattr(settings, "SCAN_CATALOGS", value, raising=False)
    _set("")
    return _set


def _row(db):
    return db.query(ToolRegistryModel).filter_by(tool_name=TOOL).one()


# ------------------------------------------------------------------ role gating
def test_tool_declares_governance_admin():
    assert draft_odcs_contract.required_role == "governance_admin"


def test_fresh_seed_restricts_tool_to_governance_admins(db_session):
    ToolRegistryService.sync_local_tools(db_session)
    assert _row(db_session).allowed_roles == ["Governance Admin"]

    def visible(user):
        return {r.tool_name for r in ToolRegistryService.tools_for_surface(db_session, "main_agent", user)}

    assert TOOL not in visible(_user("User"))
    assert TOOL not in visible(_user())
    assert TOOL in visible(_user("Governance Admin"))
    assert TOOL in visible(_user("Platform Admin"))


def test_existing_open_row_is_restricted_on_sync(db_session):
    db_session.add(ToolRegistryModel(
        id=str(uuid.uuid4()), tool_name=TOOL, origin=TOOL_ORIGIN_LOCAL,
        enabled=True, enabled_for_main_agent=True, allowed_roles=[],
    ))
    db_session.commit()

    ToolRegistryService.sync_local_tools(db_session)
    assert _row(db_session).allowed_roles == ["Governance Admin"]

    # An admin clearing it back to "All roles" doesn't survive the next sync.
    ToolRegistryService.update_tool(db_session, _row(db_session).id, allowed_roles=[])
    ToolRegistryService.sync_local_tools(db_session)
    assert _row(db_session).allowed_roles == ["Governance Admin"]


def test_admin_chosen_roles_are_kept(db_session):
    db_session.add(ToolRegistryModel(
        id=str(uuid.uuid4()), tool_name=TOOL, origin=TOOL_ORIGIN_LOCAL,
        enabled=True, enabled_for_main_agent=True, allowed_roles=["Security Admin"],
    ))
    db_session.commit()

    ToolRegistryService.sync_local_tools(db_session)
    assert _row(db_session).allowed_roles == ["Security Admin"]


def test_other_role_gated_tools_keep_all_roles_choice(db_session):
    ToolRegistryService.sync_local_tools(db_session)
    row = db_session.query(ToolRegistryModel).filter_by(tool_name="check_asset_quality").one()
    ToolRegistryService.update_tool(db_session, row.id, allowed_roles=[])

    ToolRegistryService.sync_local_tools(db_session)
    db_session.refresh(row)
    assert row.allowed_roles == []


# ------------------------------------------------------------- catalog scope
def test_scope_with_allowlist(allowlist):
    allowlist(" Main , sales ")
    assert table_in_scope("main.s.t")
    assert table_in_scope("`SALES`.s.t")
    assert not table_in_scope("hr.s.t")
    assert not table_in_scope("main.t")
    assert not table_in_scope(None)


def test_scope_without_allowlist_excludes_platform_catalogs(allowlist):
    assert table_in_scope("hr.s.t")
    for catalog in ("system", "samples", "__databricks_internal"):
        assert not table_in_scope(f"{catalog}.s.t")


@pytest.mark.asyncio
async def test_tool_refuses_tables_outside_allowlist(allowlist):
    allowlist("main")
    with patch("app.core.workspaces.get_governance_uc_provider") as provider, \
            patch("app.tools.governance.draft_odcs.AgentLLMClient") as llm:
        out = await draft_odcs_contract.execute(dataset_ids=["main.s.ok", "hr.payroll.salaries"])

    assert out.startswith("Refused:")
    assert "hr.payroll.salaries" in out and "main.s.ok" not in out.split("Not drafted:")[1]
    assert "main" in out
    provider.assert_not_called()
    llm.assert_not_called()


@pytest.mark.asyncio
async def test_tool_refuses_platform_catalogs_when_allowlist_blank(allowlist):
    with patch("app.core.workspaces.get_governance_uc_provider") as provider:
        out = await draft_odcs_contract.execute(dataset_ids=["system.access.audit"])

    assert out.startswith("Refused:") and "system.access.audit" in out
    provider.assert_not_called()


# ----------------------------------------------------------- lineage filtering
def _provider(lineage):
    provider = MagicMock()
    provider.client.api_client.do.return_value = lineage
    return provider


def _batch(*names):
    return UcMetadataBatch(visible={n: TableMetadata(full_name=n) for n in names})


@pytest.mark.asyncio
async def test_fetch_drops_out_of_scope_lineage_and_tables(allowlist):
    allowlist("main")
    lineage = {
        "upstreams": [
            {"tableInfo": {"name": "main.raw.orders", "catalog_name": "main"}},
            {"tableInfo": {"name": "hr.payroll.salaries", "catalog_name": "hr"}},
            {"tableInfo": {"name": "events", "catalog_name": "main", "schema_name": "raw"}},
            {"tableInfo": {"name": "no_catalog"}},
        ],
        "downstreams": [
            {"tableInfo": {"name": "main.gold.kpis"}},
            {"tableInfo": {"name": "secret.x.y"}},
        ],
    }
    fetch = MagicMock(return_value=_batch("main.s.t"))
    with patch("app.core.workspaces.get_governance_uc_provider", return_value=_provider(lineage)), \
            patch("app.providers.databricks.uc_metadata.fetch_uc_metadata", fetch):
        out = await fetch_datasets_metadata(["main.s.t", "hr.payroll.salaries"])

    # The out-of-scope table is never even asked for.
    assert fetch.call_args.args[1] == ["main.s.t"]
    assert [m["dataset_id"] for m in out] == ["main.s.t"]
    assert out[0]["upstream_tables"] == ["main.raw.events", "main.raw.orders"]
    assert out[0]["downstream_tables"] == ["main.gold.kpis"]


@pytest.mark.asyncio
async def test_fetch_drops_platform_lineage_when_allowlist_blank(allowlist):
    lineage = {
        "upstreams": [
            {"tableInfo": {"name": "hr.payroll.salaries"}},
            {"tableInfo": {"name": "system.access.audit"}},
            {"tableInfo": {"name": "__databricks_internal.x.y"}},
            {"tableInfo": {"name": "samples.nyctaxi.trips"}},
        ],
    }
    with patch("app.core.workspaces.get_governance_uc_provider", return_value=_provider(lineage)), \
            patch("app.providers.databricks.uc_metadata.fetch_uc_metadata", return_value=_batch("main.s.t")):
        out = await fetch_datasets_metadata(["main.s.t"])

    assert out[0]["upstream_tables"] == ["hr.payroll.salaries"]


def _contract(upstream, downstream=None):
    return yaml.dump({
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "schema": [{
            "name": "t",
            "physicalName": "main.s.t",
            "customProperties": [
                {"property": "classification", "value": "PII"},
                {"property": "upstream_tables", "value": upstream},
                {"property": "downstream_tables", "value": downstream or []},
            ],
        }],
    }, sort_keys=False)


def _lineage(text):
    props = yaml.safe_load(text)["schema"][0]["customProperties"]
    return {p["property"]: p["value"] for p in props}


def test_stored_contract_lineage_is_scoped(allowlist):
    allowlist("main")
    out = _lineage(scope_contract_lineage_yaml(_contract(
        ["main.raw.orders", "hr.payroll.salaries"], "secret.x.y, main.gold.kpis",
    )))
    assert out["upstream_tables"] == ["main.raw.orders"]
    assert out["downstream_tables"] == ["main.gold.kpis"]
    assert out["classification"] == "PII"


def test_in_scope_contract_text_is_untouched(allowlist):
    allowlist("main")
    text = "# hand-written note\n" + _contract(["main.raw.orders"])
    assert scope_contract_lineage_yaml(text) == text
    assert scope_contract_lineage_yaml(None) is None
    assert scope_contract_lineage_yaml("not: [valid") == "not: [valid"


@pytest.mark.asyncio
async def test_draft_scrubs_lineage_the_model_kept(allowlist):
    allowlist("main")
    metadata = [{"dataset_id": "main.s.t", "catalog": "main", "schema": "s", "table": "t"}]
    llm = MagicMock()

    async def respond(**_kwargs):
        return {"content": _contract(["main.raw.orders", "hr.payroll.salaries"])}

    llm.return_value.generate_response = respond
    with patch("app.tools.governance.draft_odcs.AgentLLMClient", llm):
        out = await draft_odcs_contract._func(
            dataset_ids=["main.s.t"], pre_fetched_metadata=metadata,
            existing_odcs_yaml=_contract(["secret.x.y"]),
        )

    assert _lineage(out)["upstream_tables"] == ["main.raw.orders"]


def _call(route, db, **kwargs):
    if "current_user" in inspect.signature(route).parameters:
        kwargs["current_user"] = _user("User")
    return route(db=db, **kwargs)


def test_contract_read_endpoints_scope_lineage(db_session, allowlist):
    from app.api.v1.data_contracts import get_contract_history, list_contracts

    allowlist("main")
    db_session.add(DataContractModel(
        id=str(uuid.uuid4()), dataset_id="orders_ds", version=1, is_active=True,
        yaml_content=_contract(["main.raw.orders", "hr.payroll.salaries"]),
    ))
    db_session.commit()

    listed = [c for c in _call(list_contracts, db_session) if c["dataset_id"] == "orders_ds"]
    history = _call(get_contract_history, db_session, dataset_id="orders_ds")
    for contract in (listed[0], history[0]):
        assert _lineage(contract["yaml_content"])["upstream_tables"] == ["main.raw.orders"]
    # The stored row itself is not rewritten.
    assert "hr.payroll.salaries" in db_session.query(DataContractModel).filter_by(
        dataset_id="orders_ds").one().yaml_content
