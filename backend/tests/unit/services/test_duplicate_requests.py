"""Matching rules for the duplicate-request check (services/duplicate_requests.py)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.services import duplicate_requests as dup
from tests.factories.request_factory import RequestFactory

ME = "me@example.com"
OTHER = "other@example.com"
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

STATUSES = [
    "pending", "manager_approval", "data_owner_approval", "training_pending",
    "manual_task_pending", "provisioning", "completed",
]


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "DUPLICATE_CHECK_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "DUPLICATE_CHECK_LOOKBACK_DAYS", 14, raising=False)
    monkeypatch.setattr(settings, "DUPLICATE_CHECK_STATUSES", list(STATUSES), raising=False)


def _req(db, *, type="workspace_provision", status="pending", requester=OTHER, ctx=None, finished_days_ago=None):
    req = RequestFactory.create(
        db, type=type, status=status, requester_email=requester, state_context=ctx or {},
        title=f"Agent Request: {type}",
    )
    if finished_days_ago is not None:
        stamp = (NOW - timedelta(days=finished_days_ago)).replace(tzinfo=None)
        req.created_at = stamp
        req.updated_at = stamp
        db.commit()
    return req


def _find(db, wf, params, *, me=ME, roles=None):
    return dup.find_duplicate_requests(db, wf, params, requester_email=me, roles=roles, now=NOW)


WS = {"business_domain": "supply_chain", "environment": "prod", "workspace_name": "ws-supply_chain-prod",
      "justification": "Onboard supply chain", "cost_center": "CC-1"}


class TestNormalization:
    def test_cosmetic_differences_fold_together(self):
        assert dup._normalize("Supply Chain") == dup._normalize("supply-chain") == dup._normalize("supply_chain")
        assert dup._normalize("https://adb-1.azuredatabricks.net/") == dup._normalize("adb-1.azuredatabricks.net")
        assert dup._normalize("`main.sales`") == "main.sales"

    def test_identity_vs_ignored_fields(self):
        ident = dup.identity_values({**WS, "requested_by_email": ME, "display_name": "Ops", "team_slug": "x"})
        assert set(ident) == {"business_domain", "workspace_name"}


class TestMatching:
    def test_same_resource_by_someone_else_is_anonymized(self, db_session):
        _req(db_session, ctx={**WS, "business_domain": "Supply Chain", "justification": "different words"})
        result = _find(db_session, "workspace_provision", WS)
        assert result["count"] == 1
        match = result["possible_duplicates"][0]
        assert match["requester"] == "another user"
        assert match["submitted_by_you"] is False
        assert "request_id" not in match and "request_link" not in match and "title" not in match
        assert match["matched_fields"]["business_domain"] == ["supply_chain"]

    def test_platform_admin_sees_ids_of_other_peoples_requests(self, db_session):
        req = _req(db_session, ctx=WS)
        result = _find(db_session, "workspace_provision", WS, roles="Requester,Platform Admin")
        match = result["possible_duplicates"][0]
        assert match["request_id"] == req.id
        assert match["request_link"] == f"[{req.id}](/requests/{req.id})"
        assert match["requester"] == OTHER

    def test_own_request_includes_link(self, db_session):
        req = _req(db_session, requester=ME, ctx=WS)
        match = _find(db_session, "workspace_provision", WS)["possible_duplicates"][0]
        assert match["submitted_by_you"] is True
        assert match["request_id"] == req.id
        assert "in progress" in match["recommendation"]

    def test_different_resource_is_not_a_match(self, db_session):
        _req(db_session, ctx={**WS, "business_domain": "finance", "workspace_name": "ws-finance-prod"})
        assert _find(db_session, "workspace_provision", WS)["count"] == 0

    def test_one_conflicting_identity_field_rules_it_out(self, db_session):
        _req(db_session, ctx={**WS, "workspace_name": "ws-supply_chain-dev", "environment": "dev"})
        assert _find(db_session, "workspace_provision", WS)["count"] == 0

    def test_qualifier_conflict_rules_it_out(self, db_session):
        ctx = {"feature": "Serverless Notebooks", "targets": ["https://adb-1.net"], "action": "enable"}
        _req(db_session, type="preview_feature_request", ctx=ctx)
        disable = {**ctx, "action": "disable"}
        assert _find(db_session, "preview_feature_request", disable)["count"] == 0
        assert _find(db_session, "preview_feature_request", ctx)["count"] == 1

    def test_list_values_match_on_overlap(self, db_session):
        _req(db_session, type="preview_feature_request",
             ctx={"feature": "X", "targets": ["https://adb-1.net", "https://adb-2.net"]})
        result = _find(db_session, "preview_feature_request", {"feature": "x", "targets": ["adb-2.net/"]})
        assert result["count"] == 1
        assert result["possible_duplicates"][0]["matched_fields"]["targets"] == ["adb-2.net/"]

    def test_assets_given_as_dicts_or_names_match(self, db_session):
        _req(db_session, type="data_access_request", requester=ME,
             ctx={"assets": [{"asset_name": "main.sales.orders"}], "access_level": "read"})
        result = _find(db_session, "data_access_request",
                       {"assets": ["main.sales.orders", "main.sales.returns"], "access_level": "read"})
        assert result["count"] == 1

    def test_same_workspace_alone_does_not_make_different_tables_duplicates(self, db_session):
        host = "https://adb-1.net"
        _req(db_session, type="data_access_request", requester=ME,
             ctx={"target_host": host, "asset_name": "main.sales.orders", "access_level": "read"})
        result = _find(db_session, "data_access_request",
                       {"target_host": host, "assets": [{"asset_name": "main.hr.people"}], "access_level": "read"})
        assert result["count"] == 0

    def test_asset_key_variants_compare_as_one_field(self, db_session):
        host = "https://adb-1.net"
        _req(db_session, type="data_access_request", requester=ME,
             ctx={"target_host": host, "access_level": "read",
                  "assets": [{"asset_type": "table", "asset_name": "main.sales.orders"}]})
        result = _find(db_session, "data_access_request",
                       {"target_host": host, "asset_name": "main.sales.orders", "access_level": "read"})
        assert result["count"] == 1
        assert result["possible_duplicates"][0]["matched_fields"]["asset"] == ["main.sales.orders"]

    def test_location_alone_matches_when_nothing_more_specific_is_named(self, db_session):
        params = {"target_host": "https://adb-9.net", "environment": "prd", "duration": "permanent"}
        _req(db_session, type="workspace_access", requester=ME, ctx=params)
        assert _find(db_session, "workspace_access", params)["count"] == 1

    def test_different_workflow_type_is_not_a_match(self, db_session):
        _req(db_session, type="workspace_access", ctx={"workspace_name": "ws-supply_chain-prod"})
        assert _find(db_session, "workspace_provision", WS)["count"] == 0

    def test_legacy_alias_counts_as_same_workflow(self, db_session):
        _req(db_session, type="create_workspace", ctx=WS)
        assert _find(db_session, "workspace_provision", WS)["count"] == 1

    def test_no_identifying_parameters_skips_the_check(self, db_session):
        _req(db_session, ctx={"justification": "same reason"})
        result = _find(db_session, "workspace_provision", {"justification": "same reason"})
        assert result["count"] == 0 and result["compared_fields"] == []


class TestAccessWorkflows:
    def test_someone_elses_access_request_is_not_your_duplicate(self, db_session):
        params = {"workspace_name": "ws-finance-prod", "host": "https://adb-9.net", "duration": "permanent"}
        _req(db_session, type="workspace_access", requester=OTHER, ctx=params)
        assert _find(db_session, "workspace_access", params)["count"] == 0
        _req(db_session, type="workspace_access", requester=ME, ctx=params)
        assert _find(db_session, "workspace_access", params)["count"] == 1

    def test_named_beneficiary_compares_across_requesters(self, db_session):
        params = {"repo_name": "demo-app", "github_username": "octo", "permission": "write"}
        _req(db_session, type="github_repo_access", requester=OTHER, ctx=params)
        assert _find(db_session, "github_repo_access", params)["count"] == 1
        assert _find(db_session, "github_repo_access", {**params, "github_username": "someone"})["count"] == 0

    def test_access_check_without_a_caller_finds_nothing(self, db_session):
        params = {"workspace_name": "ws-finance-prod", "duration": "permanent"}
        _req(db_session, type="workspace_access", requester=ME, ctx=params)
        assert _find(db_session, "workspace_access", params, me=None)["count"] == 0


class TestStatusAndLookback:
    def test_completed_within_lookback_counts(self, db_session):
        _req(db_session, status="completed", ctx=WS, finished_days_ago=3)
        result = _find(db_session, "workspace_provision", WS)
        assert result["count"] == 1
        assert "completed recently" in result["possible_duplicates"][0]["recommendation"]

    def test_completed_outside_lookback_is_ignored(self, db_session):
        _req(db_session, status="completed", ctx=WS, finished_days_ago=30)
        assert _find(db_session, "workspace_provision", WS)["count"] == 0

    def test_open_request_counts_at_any_age(self, db_session):
        _req(db_session, status="manager_approval", ctx=WS, finished_days_ago=200)
        assert _find(db_session, "workspace_provision", WS)["count"] == 1

    def test_rejected_is_ignored_by_default_but_configurable(self, db_session, monkeypatch):
        _req(db_session, status="rejected", ctx=WS, finished_days_ago=1)
        assert _find(db_session, "workspace_provision", WS)["count"] == 0
        monkeypatch.setattr(settings, "DUPLICATE_CHECK_STATUSES", "pending, rejected")
        result = _find(db_session, "workspace_provision", WS)
        assert result["count"] == 1
        assert "rejected" in result["possible_duplicates"][0]["recommendation"]

    def test_zero_lookback_counts_only_open_requests(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "DUPLICATE_CHECK_LOOKBACK_DAYS", 0)
        _req(db_session, status="completed", ctx=WS, finished_days_ago=0)
        assert _find(db_session, "workspace_provision", WS)["count"] == 0

    def test_open_matches_are_listed_before_finished_ones(self, db_session):
        _req(db_session, status="pending", ctx=WS, finished_days_ago=5)
        _req(db_session, status="completed", ctx=WS, finished_days_ago=1)
        statuses = [m["status"] for m in _find(db_session, "workspace_provision", WS)["possible_duplicates"]]
        assert statuses == ["pending", "completed"]
