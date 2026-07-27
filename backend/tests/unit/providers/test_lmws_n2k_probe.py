"""Tests for the N2K LMWS probe client (app.providers.lmws.n2k).

The contract that matters here is that a probe NEVER raises: a gateway rejection,
an auth failure, and an unreachable host must all come back as a classified
envelope so several endpoints can be compared in one run.
"""
import httpx
import pytest
from unittest.mock import patch

from app.core.config import settings
from app.providers.lmws.n2k import (
    ADD_ENDPOINTS,
    READ_ENDPOINTS,
    LmwsN2kProbeClient,
    encode_members,
)

REST = "https://gw.example.com/iam/v1/lmws-rest/publicAPIrest"
AUTHN = "https://gw.example.com/iam/v1/lmwsrest-authn"


@pytest.fixture
def client():
    """A probe client with credentials stubbed so nothing touches a secret scope."""
    with patch.object(LmwsN2kProbeClient, "_resolve_password", staticmethod(lambda: "pw")), \
         patch.object(settings, "LMWS_SERVICE_USERNAME", "edhapisvc"), \
         patch.object(settings, "LMWS_REST_URL", REST):
        yield LmwsN2kProbeClient()


def _transport(handler):
    """Patch httpx.AsyncClient.request with a handler returning an httpx.Response."""
    async def _request(self, method, url, params=None, auth=None, **kwargs):
        return handler(method, url, params)
    return patch.object(httpx.AsyncClient, "request", _request)


# ---------------------------------------------------------------------------
# encode_members — the documented [u1,u2] form vs the production CSV form
# ---------------------------------------------------------------------------

def test_encode_members_auto_matches_documented_examples():
    # Docs show a bare username for one member and literal brackets for several.
    assert encode_members(["u1"]) == "u1"
    assert encode_members(["u1", "u2"]) == "[u1,u2]"


def test_encode_members_bracketed_always_brackets():
    assert encode_members(["u1"], "bracketed") == "[u1]"
    assert encode_members(["u1", "u2"], "bracketed") == "[u1,u2]"


def test_encode_members_csv_matches_production_path():
    assert encode_members(["u1", "u2"], "csv") == "u1,u2"


def test_encode_members_repeated_returns_list_for_httpx():
    assert encode_members(["u1", "u2"], "repeated") == ["u1", "u2"]


def test_encode_members_accepts_csv_string_and_strips_spaces():
    # The docs are explicit that the bracketed form carries no spaces.
    assert encode_members("u1, u2 ") == "[u1,u2]"


def test_encode_members_rejects_unknown_style():
    with pytest.raises(ValueError):
        encode_members(["u1"], "nope")


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------

def test_add_endpoints_route_to_expected_bases():
    # The n2k/any endpoints live on the REST base; the legacy one on authn.
    assert ADD_ENDPOINTS["listAnyMembershipAdd"] == "rest"
    assert ADD_ENDPOINTS["n2kListMembershipAdd"] == "rest"
    assert ADD_ENDPOINTS["n2kAdminListMembershipAdd"] == "rest"
    assert ADD_ENDPOINTS["listMembersAdd"] == "authn"
    assert READ_ENDPOINTS["n2kListMetadataGet"] == "rest"


def test_base_url_override_wins(client):
    assert client.base_url_for("listAnyMembershipAdd") == REST
    assert client.base_url_for("listAnyMembershipAdd", "https://dev.example.com/x/") == (
        "https://dev.example.com/x"
    )


@pytest.mark.asyncio
async def test_missing_base_url_is_reported_not_raised(client):
    with patch.object(settings, "LMWS_REST_URL", ""):
        result = await client.probe("listAnyMembershipAdd", {"listName": "l"})
    assert result["outcome"] == "not_configured"
    assert result["sent"] is False
    assert "LMWS_REST_URL" in result["detail"]


# ---------------------------------------------------------------------------
# Outcome classification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_success_extracts_request_ids_from_workflow_infos(client):
    body = {
        "Result": "SUCCESS",
        "workflowInfos": [
            {"infoType": "add", "requestType": "N", "requestId": "435918834",
             "message": "AddUser", "userId": "taylhans"}
        ],
    }
    with _transport(lambda m, u, p: httpx.Response(200, json=body)):
        result = await client.probe_membership_add(
            "n2kListMembershipAdd", "n2k_list", ["taylhans"], dry_run=False
        )
    assert result["ok"] is True
    assert result["outcome"] == "success"
    assert result["request_ids"] == ["435918834"]
    assert "requestStatus" in result["detail"]


@pytest.mark.asyncio
async def test_n2k_rejection_is_classified_as_supervisor_required(client):
    # The observed failure: HTTP 200 with errorInfos in the body.
    body = {
        "errorInfos": [
            {"message": "Requester is not authorized to view or modify N2K list 'x'"}
        ]
    }
    # listMembersAdd is on the authn base, not the REST base the fixture sets.
    with patch.object(settings, "LMWS_AUTHN_URL", AUTHN), \
         _transport(lambda m, u, p: httpx.Response(200, json=body)):
        result = await client.probe_membership_add(
            "listMembersAdd", "x", ["taylhans"], dry_run=False
        )
    assert result["outcome"] == "supervisor_required"
    assert result["ok"] is False
    assert result["http_status"] == 200
    assert "per-list" in result["detail"]


@pytest.mark.asyncio
async def test_acl_rejection_is_distinguished_from_supervisor(client):
    # An ACL failure needs a different fix than a supervisor failure, so the two
    # must not collapse into one outcome.
    body = {
        "errorInfos": [
            {"message": "authorized user 'edhapisvc' is not a member of environment "
                        "specific ACL 'lmws.rest'"}
        ]
    }
    with _transport(lambda m, u, p: httpx.Response(200, json=body)):
        result = await client.probe_membership_add(
            "listAnyMembershipAdd", "x", ["taylhans"], dry_run=False
        )
    assert result["outcome"] == "acl_missing"
    assert "lmws.rest" in result["detail"]


@pytest.mark.asyncio
async def test_result_exception_without_error_infos_is_a_failure(client):
    # A business-logic rejection can surface as Result=EXCEPTION with no errorInfos.
    body = {"Result": "EXCEPTION", "message": "user is not a supervisor of the list"}
    with _transport(lambda m, u, p: httpx.Response(200, json=body)):
        result = await client.probe_membership_add(
            "listAnyMembershipAdd", "x", ["taylhans"], dry_run=False
        )
    assert result["ok"] is False
    assert result["outcome"] == "supervisor_required"


@pytest.mark.asyncio
async def test_wrong_list_type_is_its_own_outcome(client):
    body = {"errorInfos": [{"message": "The requested list is not N2K"}]}
    with _transport(lambda m, u, p: httpx.Response(200, json=body)):
        result = await client.probe_membership_add(
            "n2kListMembershipAdd", "x", ["taylhans"], dry_run=False
        )
    assert result["outcome"] == "wrong_list_type"


@pytest.mark.asyncio
async def test_401_is_classified_as_acl_missing(client):
    with _transport(lambda m, u, p: httpx.Response(401, text="denied")):
        result = await client.probe_read("n2kListMetadataGet", {"listName": "x"})
    assert result["outcome"] == "acl_missing"
    assert result["ok"] is False
    assert "ACL" in result["detail"]


@pytest.mark.asyncio
async def test_unreachable_host_is_reported_not_raised(client):
    def _boom(method, url, params):
        raise httpx.ConnectError("no route to host")

    with _transport(_boom):
        result = await client.probe_read("n2kListMetadataGet", {"listName": "x"})
    assert result["outcome"] == "unreachable"
    assert result["ok"] is False
    # The request was attempted, so it counts as sent.
    assert result["sent"] is True


@pytest.mark.asyncio
async def test_non_json_body_is_captured_as_text(client):
    with _transport(lambda m, u, p: httpx.Response(200, text="<html>nope</html>")):
        result = await client.probe_read("n2kListMetadataGet", {"listName": "x"})
    assert result["ok"] is True
    assert "html" in result["body"]


@pytest.mark.asyncio
async def test_missing_credentials_short_circuits_before_sending(client):
    client.password = ""
    with _transport(lambda m, u, p: pytest.fail("should not have sent a request")):
        result = await client.probe_read("n2kListMetadataGet", {"listName": "x"})
    assert result["outcome"] == "no_credentials"
    assert result["sent"] is False


# ---------------------------------------------------------------------------
# Guardrails: dry run + endpoint allowlists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_membership_add_defaults_to_dry_run(client):
    with _transport(lambda m, u, p: pytest.fail("dry run must not send a request")):
        result = await client.probe_membership_add(
            "listAnyMembershipAdd", "n2k_list", ["taylhans"]
        )
    assert result["outcome"] == "dry_run"
    assert result["sent"] is False
    # The built request is still fully visible for inspection.
    assert result["url"] == f"{REST}/listAnyMembershipAdd"
    # Single member under the default 'auto' style is bare, per the docs.
    assert result["params"]["listMembers"] == "taylhans"
    assert result["params"]["justification"]


@pytest.mark.asyncio
async def test_read_probe_rejects_a_mutating_endpoint(client):
    result = await client.probe_read("listAnyMembershipAdd", {"listName": "x"})
    assert result["outcome"] == "rejected"
    assert result["sent"] is False


@pytest.mark.asyncio
async def test_add_probe_rejects_an_unknown_endpoint(client):
    result = await client.probe_membership_add("dropEverything", "x", ["u"], dry_run=False)
    assert result["outcome"] == "rejected"
    assert result["sent"] is False


# ---------------------------------------------------------------------------
# Configuration health (no network)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_config_reports_ready_without_leaking_the_password(client):
    with patch.object(settings, "LMWS_AUTHN_URL", AUTHN), \
         patch.object(settings, "LMWS_CACHE_URL", AUTHN), \
         patch.object(settings, "LMWS_FWS_URL", AUTHN):
        result = await client.probe_config()
    assert result["ready"] is True
    assert result["password_resolved"] is True
    assert result["problems"] == []
    # The value itself must never appear anywhere in the envelope.
    assert "pw" not in str({k: v for k, v in result.items() if k != "password_length"})


@pytest.mark.asyncio
async def test_probe_config_treats_only_the_rest_url_as_blocking(client):
    # authn/cache/fws are blank in the fixture; none of them gate N2K endpoints.
    result = await client.probe_config()
    assert result["ready"] is True
    assert result["problems"] == []
    assert result["notes"] and "LMWS_AUTHN_URL" in result["notes"][0]


@pytest.mark.asyncio
async def test_probe_config_distinguishes_missing_key_from_unreadable_scope(client):
    client.password = ""

    class _Secret:
        def __init__(self, key):
            self.key = key

    class _FakeWorkspaceClient:
        class secrets:  # noqa: N801 - mirrors the SDK's attribute shape
            @staticmethod
            def list_secrets(scope):
                return [_Secret("ses_key"), _Secret("github_pat")]

    with patch.dict("sys.modules", {"databricks.sdk": type("m", (), {"WorkspaceClient": _FakeWorkspaceClient})}):
        result = await client.probe_config()

    diag = result["scope_diagnosis"]
    assert diag["scope_readable"] is True
    assert diag["key_present"] is False
    assert "no key named" in diag["detail"]
    assert result["ready"] is False


@pytest.mark.asyncio
async def test_probe_config_reports_unreadable_scope_as_a_grant_problem(client):
    client.password = ""

    class _Boom:
        class secrets:  # noqa: N801
            @staticmethod
            def list_secrets(scope):
                raise PermissionError("does not have READ on scope")

    with patch.dict("sys.modules", {"databricks.sdk": type("m", (), {"WorkspaceClient": _Boom})}):
        result = await client.probe_config()

    diag = result["scope_diagnosis"]
    assert diag["scope_readable"] is False
    assert "READ" in diag["detail"]


@pytest.mark.asyncio
async def test_probe_config_refresh_evicts_the_cached_secret_miss():
    """A negatively-cached secret must not survive an explicit refresh.

    ``_read_secret`` caches misses as well as hits, so after granting the app SP
    READ on the scope the process would keep reporting the old failure. Refresh
    is what makes the fix observable without a restart.
    """
    import base64

    from app.core import workspaces

    scope, key = "controltower_secret_scope_tst", "edhapisvc"

    class _Secret:
        value = base64.b64encode(b"now-readable").decode()

    class _NowGranted:
        class secrets:  # noqa: N801 - mirrors the SDK's attribute shape
            @staticmethod
            def get_secret(scope, key):
                return _Secret()

            @staticmethod
            def list_secrets(scope):
                return [_Secret()]

    # Exercise the real _read_secret so the cache logic actually runs; only the
    # SDK call underneath is faked, standing in for the ACL having been granted.
    with patch.object(settings, "LMWS_SECRET_SCOPE", scope), \
         patch.object(settings, "LMWS_PASSWORD_SECRET_KEY", key), \
         patch.object(settings, "LMWS_SERVICE_PASSWORD", ""), \
         patch.object(settings, "LMWS_SERVICE_USERNAME", "edhapisvc"), \
         patch.object(settings, "LMWS_REST_URL", REST), \
         patch.dict(workspaces._secret_cache, {(scope, key): None}, clear=False), \
         patch.dict("sys.modules", {"databricks.sdk": type("m", (), {"WorkspaceClient": _NowGranted})}):

        # The cached miss is served even though the grant is now in place.
        probe = LmwsN2kProbeClient()
        assert probe.password == ""
        stale = await probe.probe_config()
        assert stale["password_resolved"] is False
        assert any("refresh=true" in p for p in stale["problems"])

        # Refresh evicts it and re-reads.
        fresh = await probe.probe_config(refresh=True)

        assert fresh["refreshed"] is True
        assert fresh["password_resolved"] is True
        assert fresh["password_length"] == len("now-readable")
        assert fresh["ready"] is True
        assert fresh["problems"] == []
        # The good value is now cached for every subsequent LMWS call in the
        # process (asserted inside the block: patch.dict restores on exit).
        assert workspaces._secret_cache[(scope, key)] == "now-readable"


@pytest.mark.asyncio
async def test_probe_config_points_at_refresh_when_the_secret_is_missing(client):
    client.password = ""

    class _Boom:
        class secrets:  # noqa: N801
            @staticmethod
            def list_secrets(scope):
                raise PermissionError("no READ")

    with patch.dict("sys.modules", {"databricks.sdk": type("m", (), {"WorkspaceClient": _Boom})}):
        result = await client.probe_config()
    assert any("refresh=true" in p for p in result["problems"])


@pytest.mark.asyncio
async def test_probe_config_flags_a_blank_rest_url_as_blocking(client):
    with patch.object(settings, "LMWS_REST_URL", ""):
        result = await client.probe_config()
    assert "LMWS_REST_URL" in result["missing_base_urls"]
    assert result["ready"] is False
    assert any("LMWS_REST_URL is blank" in p for p in result["problems"])


# ---------------------------------------------------------------------------
# Interpreted reads: metadata + request status
# ---------------------------------------------------------------------------

def _metadata_body(*infos):
    return {
        "Result": "SUCCESS",
        "namespaceMetadata": {"metadata": {"metadataInfos": list(infos)}},
        "errorInfos": [],
    }


@pytest.mark.asyncio
async def test_metadata_flags_a_jqs_list_as_unautomatable(client):
    body = _metadata_body(
        {"key": "justQuestFormName", "label": "Justification Question Form Name",
         "value": "iamqa.bigform.hasexp"},
        {"key": "reassignAllowed", "label": "...", "value": "Yes"},
    )
    with _transport(lambda m, u, p: httpx.Response(200, json=body)):
        result = await client.probe_list_metadata("n2k_list")
    assert result["is_n2k"] is True
    assert result["requires_jqs"] is True
    assert result["jqs_form"] == "iamqa.bigform.hasexp"
    assert "[[answerId]]" in result["detail"]


@pytest.mark.asyncio
async def test_metadata_without_jqs_allows_free_text(client):
    body = _metadata_body({"key": "reassignAllowed", "label": "...", "value": "Yes"})
    with _transport(lambda m, u, p: httpx.Response(200, json=body)):
        result = await client.probe_list_metadata("n2k_list")
    assert result["is_n2k"] is True
    assert result["requires_jqs"] is False
    assert result["jqs_form"] is None
    assert "free-text" in result["detail"]


@pytest.mark.asyncio
async def test_metadata_empty_jqs_value_is_not_required(client):
    body = _metadata_body({"key": "justQuestFormName", "label": "...", "value": ""})
    with _transport(lambda m, u, p: httpx.Response(200, json=body)):
        result = await client.probe_list_metadata("n2k_list")
    assert result["requires_jqs"] is False


@pytest.mark.asyncio
async def test_metadata_error_means_the_list_is_not_n2k(client):
    body = {"errorInfos": [{"message": "The requested list should be N2K List"}]}
    with _transport(lambda m, u, p: httpx.Response(200, json=body)):
        result = await client.probe_list_metadata("ordinary_list")
    assert result["is_n2k"] is False
    assert result["outcome"] == "wrong_list_type"
    assert "listMembersAdd" in result["detail"]


@pytest.mark.asyncio
async def test_request_status_uses_lowercase_params_and_reqkey_precedence(client):
    seen = {}

    def _handler(method, url, params):
        seen.update(params)
        return httpx.Response(200, json={
            "listName": "n2k_list", "requestid": "435918834", "status": "Pending Approval",
            "approvers": "Ann (ann), Bob (bob)",
        })

    with _transport(_handler):
        result = await client.probe_request_status(requestid="435918834", reqkey="K1")
    # Both are sent; the gateway prefers reqkey. Lowercase spellings matter.
    assert seen["requestid"] == "435918834"
    assert seen["reqkey"] == "K1"
    assert result["request_state"]["status"] == "Pending Approval"
    assert "pending approvers" in result["detail"]


@pytest.mark.asyncio
async def test_request_status_requires_an_identifier(client):
    with _transport(lambda m, u, p: pytest.fail("should not send without an id")):
        result = await client.probe_request_status()
    assert result["outcome"] == "rejected"
    assert result["sent"] is False


# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_matrix_reports_which_endpoints_accepted(client):
    def _handler(method, url, params):
        if url.endswith("listAnyMembershipAdd"):
            return httpx.Response(200, json={"Result": "SUCCESS"})
        return httpx.Response(200, json={"errorInfos": [{"message": "not authorized"}]})

    with patch.object(settings, "LMWS_AUTHN_URL", AUTHN), _transport(_handler):
        result = await client.probe_add_matrix("n2k_list", ["taylhans"], dry_run=False)

    assert result["endpoints_succeeded"] == ["listAnyMembershipAdd"]
    assert len(result["results"]) == len(ADD_ENDPOINTS)
    assert "listAnyMembershipAdd" in result["summary"]


@pytest.mark.asyncio
async def test_matrix_honors_endpoint_subset_and_dry_run(client):
    with _transport(lambda m, u, p: pytest.fail("dry run must not send a request")):
        result = await client.probe_add_matrix(
            "n2k_list", ["taylhans"], endpoints=["listAnyMembershipAdd"]
        )
    assert result["endpoints_probed"] == ["listAnyMembershipAdd"]
    assert result["endpoints_succeeded"] == []
    assert "Dry run" in result["summary"]
