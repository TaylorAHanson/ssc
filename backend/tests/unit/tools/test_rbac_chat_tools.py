"""The Access Rules chat tools: find_access and preview_access_change (ADR 0005 D9)."""
from unittest.mock import MagicMock

import pytest

import app.db.session as db_session_module
from app.models.rbac import AccessMatch, FindAccessResult, SecurableSuggestion
from app.services.rbac import ledger
from app.services.rbac import requests as rbac_requests
from app.tools.self_service.find_access import find_access
from app.tools.self_service.preview_access_change import preview_access_change

ITEM = {"action": "grant", "environment": "dev", "securable_type": "schema", "catalog": "sales_dev",
        "schema_name": "orders", "principal_name": "g_new", "privileges": ["SELECT"]}


@pytest.fixture()
def session(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(db_session_module, "get_lakebase_session", lambda: db)
    return db


def test_both_tools_are_read_only_and_behind_the_rbac_flag():
    for t in (find_access, preview_access_change):
        assert t.feature_flag == "rbac"
        assert t.side_effect_class == "read" and not t.is_mutating


@pytest.mark.asyncio
async def test_find_access_wraps_the_ledger(monkeypatch, session):
    seen = {}

    def fake_find(db, **kwargs):
        seen.update(kwargs)
        return FindAccessResult(matches=[AccessMatch(
            rule_id="r-1", sheet="UC_Securables_RBAC_Dev", plane="uc", environment="dev", securable_type="catalog",
            catalog="sales_dev", principal_type="group", principal_name="g_new", privileges=["USE_CATALOG"],
            via="parent")])

    monkeypatch.setattr(ledger, "find_access", fake_find)
    out = await find_access.execute(catalog="sales_dev", schema_name="orders", environment="dev")

    assert out["count"] == 1 and out["matches"][0]["via"] == "parent"
    assert "next_step" not in out
    assert (seen["catalog"], seen["schema_name"], seen["environment"], seen["limit"]) == (
        "sales_dev", "orders", "dev", 25)
    session.close.assert_called_once()


def _match(rule_id, principal, privileges=("SELECT",), via="direct"):
    return AccessMatch(
        rule_id=rule_id, sheet="UC_Securables_RBAC_Dev", plane="uc", environment="dev", securable_type="schema",
        catalog="sales_dev", schema_name="orders", principal_type="group", principal_name=principal,
        privileges=list(privileges), via=via)


@pytest.mark.asyncio
async def test_find_access_flags_the_rules_held_by_the_users_groups(monkeypatch, session):
    seen = {}

    def fake_find(db, **kwargs):
        seen.update(kwargs)
        return FindAccessResult(matches=[
            _match("r-1", "g_other"), _match("r-2", "G_Analysts", via="parent"), _match("r-3", "g_third")])

    monkeypatch.setattr(ledger, "find_access", fake_find)
    out = await find_access.execute(
        catalog="sales_dev", schema_name="orders",
        _user_email="Me@Example.com", _user_entitlements="me@example.com,g_analysts,users")

    assert [(m["principal_name"], m["held_by_you"]) for m in out["matches"]] == [
        ("G_Analysts", True), ("g_other", False), ("g_third", False)]  # yours first
    assert seen["limit"] == 100 and seen["principals_in"] is None
    assert "NOT that you're denied" in out["note"]


@pytest.mark.asyncio
async def test_find_access_cuts_to_the_limit_after_putting_the_users_rules_first(monkeypatch, session):
    monkeypatch.setattr(ledger, "find_access", lambda db, **kw: FindAccessResult(
        matches=[_match(f"r-{i}", f"g_{i}") for i in range(5)] + [_match("r-mine", "g_mine")]))
    out = await find_access.execute(
        catalog="sales_dev", schema_name="orders", limit=2,
        _user_email="me@example.com", _user_entitlements="me@example.com,g_mine")
    assert [m["principal_name"] for m in out["matches"]] == ["g_mine", "g_0"]
    assert out["truncated"] is True and out["count"] == 2


@pytest.mark.asyncio
async def test_find_access_mine_lists_what_the_users_groups_hold(monkeypatch, session):
    seen = {}

    def fake_find(db, **kwargs):
        seen.update(kwargs)
        return FindAccessResult(matches=[_match("r-1", "g_analysts")])

    monkeypatch.setattr(ledger, "find_access", fake_find)
    out = await find_access.execute(mine=True, _user_email="me@example.com",
                                    _user_entitlements="me@example.com,g_analysts,g_readers")
    assert seen["principals_in"] == ["g_analysts", "g_readers"]
    assert out["matches"][0]["held_by_you"] is True


@pytest.mark.asyncio
async def test_find_access_says_so_when_the_users_groups_are_unknown(monkeypatch, session):
    monkeypatch.setattr(ledger, "find_access", lambda db, **kw: FindAccessResult(matches=[_match("r-1", "g_a")]))
    out = await find_access.execute(catalog="sales_dev", schema_name="orders")
    assert out["matches"][0]["held_by_you"] is None
    assert "groups weren't available" in out["note"]

    # Only the user's own email is not a group.
    out = await find_access.execute(mine=True, _user_email="me@example.com", _user_entitlements="me@example.com")
    assert out["count"] == 0 and "groups weren't available" in out["note"]


@pytest.mark.asyncio
async def test_find_access_steers_an_empty_lookup_to_the_suggestions(monkeypatch, session):
    suggested = FindAccessResult(matches=[], suggestions=[SecurableSuggestion(
        securable_type="schema", catalog="enterprise_prd", schema_name="platinum_insights", environments=["prd"])])
    monkeypatch.setattr(ledger, "find_access", lambda db, **kw: suggested)
    out = await find_access.execute(catalog="platinum_insights", environment="prd")
    assert out["suggestions"][0]["catalog"] == "enterprise_prd"
    assert "look it up now" in out["next_step"]

    monkeypatch.setattr(ledger, "find_access", lambda db, **kw: FindAccessResult(matches=[]))
    out = await find_access.execute(catalog="nowhere")
    assert "No access rule mentions this name" in out["next_step"]
    out = await find_access.execute(principal_name="g_nobody")
    assert "next_step" not in out


@pytest.mark.asyncio
async def test_find_access_reports_a_ledger_error(monkeypatch, session):
    def fake_find(db, **kwargs):
        raise ledger.LedgerError("Say which securable or which group to look up.")

    monkeypatch.setattr(ledger, "find_access", fake_find)
    out = await find_access.execute()
    assert out == {"ok": False, "error": "Say which securable or which group to look up."}
    session.close.assert_called_once()


@pytest.mark.asyncio
async def test_preview_hands_the_validated_change_to_the_service(monkeypatch, session):
    seen = {}

    def fake_preview(db, change, *, requester):
        seen["change"], seen["requester"] = change, requester
        return {"group_ref": None, "proposals": [], "next_step": "..."}

    monkeypatch.setattr(rbac_requests, "preview_access_change", fake_preview)
    out = await preview_access_change.execute(items=[ITEM], justification="Finance dashboard work",
                                              _user_email="ana@example.com")

    assert out["next_step"] == "..."
    assert seen["requester"] == "ana@example.com"
    assert seen["change"].items[0].principal_name == "g_new" and seen["change"].justification == "Finance dashboard work"
    session.close.assert_called_once()


@pytest.mark.asyncio
async def test_preview_rejects_invalid_items_without_touching_the_ledger(monkeypatch, session):
    called = []
    monkeypatch.setattr(rbac_requests, "preview_access_change", lambda *a, **k: called.append(1))
    out = await preview_access_change.execute(items=[{"securable_type": "schema"}])
    assert "error" in out and called == []
    out = await preview_access_change.execute(items=[])
    assert "error" in out and called == []
