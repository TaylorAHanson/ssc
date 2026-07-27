"""Native (in-app) LMWS/FWS-API client.

The direct, in-process implementation of the full LMWS/FWS-API surface — reads,
membership writes, and group/SPAC lifecycle actions. Where the job-backed
:class:`LmwsProvider` submits the vendored notebook as a Databricks job (needed
when the gateway is only reachable from a network-pinned cluster), this client
calls the gateway directly from the app's runtime, removing the job
cold-start/poll latency.

It mirrors the vendored notebook's HTTP contract one-for-one (basic auth against
the gateway, ``verify`` off by default for the internal CA, the body-level
``errorInfos`` check, and the same paths/params/payloads) so each action returns
the same result shape as the job-backed path (see ``LmwsProvider.parse_output``
and the notebook handlers).

The service-account password is resolved at runtime from the SAME Databricks
secret scope the vendored notebook uses (``LMWS_SECRET_SCOPE`` / key
``LMWS_PASSWORD_SECRET_KEY``) via the app's own service principal — the exact
pattern already used for the GitHub PAT and SES creds
(``app.core.workspaces._read_secret``). So there's no plaintext injection and no
new secret; the app SP just needs READ on that scope (which it already has).
``settings.LMWS_SERVICE_PASSWORD`` is an optional override (e.g. local dev where
the scope isn't reachable) and wins when set.

This is the active LMWS path when ``settings.LMWS_NATIVE`` is on (the default);
the ``LmwsProvider`` job harness remains as a runtime-toggleable fallback.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Union

import httpx

from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
from app.providers.lmws.client import _csv

logger = logging.getLogger(__name__)


def fws_error_messages(body: Any) -> List[str]:
    """Pull failure messages out of an FWS-API response body.

    The FWS-API shares no error keys with LMWS: it answers HTTP 200 carrying
    ``errorDetails`` (with a nested ``errors`` array of ``{field, message}``)
    plus ``responseStatusCode`` / ``responseMessage``. Anything keying off
    ``errorInfos`` therefore reads an FWS failure as a success.

    Only a real signal counts as a failure — a message, or a ``responseStatusCode``
    of 400+. An ``errorDetails`` that is absent, null, or empty means no error, so
    a success response is never misreported as one.
    """
    if not isinstance(body, dict):
        return []

    messages: List[str] = []
    details = body.get("errorDetails")
    if isinstance(details, dict):
        errors = details.get("errors")
        if isinstance(errors, dict):
            errors = [errors]
        if isinstance(errors, list):
            for e in errors:
                if not isinstance(e, dict):
                    if e:
                        messages.append(str(e))
                    continue
                msg = str(e.get("message") or "").strip()
                field = str(e.get("field") or "").strip()
                if msg:
                    messages.append(f"{field}: {msg}" if field else msg)
        top = str(details.get("message") or "").strip()
        if top:
            messages.append(top)

    code = body.get("responseStatusCode")
    if isinstance(code, int) and code >= 400:
        response_message = str(body.get("responseMessage") or "").strip()
        messages.append(
            f"responseStatusCode={code}: {response_message}"
            if response_message
            else f"responseStatusCode={code} with no responseMessage."
        )
    return messages


class LmwsNativeClient:
    """Direct, in-process LMWS/FWS-API calls (no Databricks job)."""

    def __init__(self) -> None:
        self.username = (settings.LMWS_SERVICE_USERNAME or "").strip()
        self.password = self._resolve_password()
        self.verify_tls = bool(getattr(settings, "LMWS_NATIVE_VERIFY_TLS", False))
        self.timeout = float(getattr(settings, "LMWS_NATIVE_TIMEOUT_SECONDS", 30) or 30)

    # ------------------------------------------------------------------
    # Credentials & HTTP plumbing (mirrors the notebook)
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_password() -> str:
        """Password from the same secret scope the notebook uses; env override wins.

        Mirrors the app's GitHub-PAT / SES pattern: read the value at runtime from
        ``LMWS_SECRET_SCOPE`` / ``LMWS_PASSWORD_SECRET_KEY`` with the app's own SP.
        ``LMWS_SERVICE_PASSWORD`` is an optional escape hatch (local dev) and wins.
        """
        override = settings.LMWS_SERVICE_PASSWORD or ""
        if override:
            return override
        from app.core.workspaces import _read_secret

        return _read_secret(
            settings.LMWS_SECRET_SCOPE, settings.LMWS_PASSWORD_SECRET_KEY
        ) or ""

    def _require_creds(self) -> None:
        if not self.username or not self.password:
            raise PermanentError(
                "Native LMWS is not configured: the service-account password could not be "
                f"resolved from secret scope '{settings.LMWS_SECRET_SCOPE}' "
                f"(key '{settings.LMWS_PASSWORD_SECRET_KEY}'). Verify the key exists and the "
                "app's service principal has READ on that scope (or set LMWS_SERVICE_PASSWORD "
                "for local dev). Until then, fall back to the serverless (job-backed) path."
            )

    def _requester(self, requester: Optional[str] = None, owner: Optional[str] = None) -> str:
        """FWS-API requester: explicit, else the list owner, else the service account."""
        return (requester or "").strip() or (owner or "").strip() or self.username

    @staticmethod
    def _require_url(base: str, name: str) -> str:
        if not base:
            raise PermanentError(
                f"LMWS base URL '{name}' is not configured. Set it in Admin -> Settings "
                f"(Group Management) or databricks.yml before using native LMWS."
            )
        return base.rstrip("/")

    @staticmethod
    def _check_body(data: Any, where: str) -> Any:
        """Raise on a body-level error returned with an HTTP 200.

        Two different services answer here and they do not share error keys. LMWS
        uses ``errorInfos``; the FWS-API endpoints (``createSPGroup``,
        ``processSpacPolicy``, ``getSpacPolicy``, ``requestConfirmation``) instead
        return ``errorDetails`` with a nested ``errors`` array, alongside
        ``responseStatusCode``. Checking only ``errorInfos`` silently accepts every
        FWS failure as a success.
        """
        if not isinstance(data, dict):
            return data
        errs = data.get("errorInfos") or data.get("errorInfo")
        if errs:
            if isinstance(errs, list):
                msgs = "; ".join(str(e.get("message") or e) for e in errs)
            else:
                msgs = str(errs)
            raise PermanentError(f"LMWS API error from {where}: {msgs}")

        fws = fws_error_messages(data)
        if fws:
            raise PermanentError(f"FWS-API error from {where}: {'; '.join(fws)}")
        return data

    async def _request(
        self,
        method: str,
        base: str,
        name: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._require_creds()
        url = f"{self._require_url(base, name)}/{path}"
        try:
            async with httpx.AsyncClient(verify=self.verify_tls, timeout=self.timeout) as client:
                resp = await client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    auth=(self.username, self.password),
                )
                resp.raise_for_status()
                return self._check_body(resp.json(), f"{name}/{path}")
        except httpx.HTTPStatusError as e:
            body = ""
            try:
                body = e.response.text
            except Exception:  # noqa: BLE001 - best-effort detail
                pass
            raise PermanentError(f"LMWS {name}/{path} failed ({e.response.status_code}): {body}")
        except httpx.RequestError as e:
            # Reachability/timeout — retryable so a transient blip doesn't hard-fail.
            raise RetryableError(f"LMWS {name}/{path} unreachable: {e}")

    async def _get(self, base: str, name: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("GET", base, name, path, params=params)

    async def _post(self, base: str, name: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", base, name, path, json_body=payload)

    # ------------------------------------------------------------------
    # Core reads
    # ------------------------------------------------------------------

    async def member_retrieve(self, member: str) -> Dict[str, Any]:
        """All group memberships for a user (mirrors the notebook memberRetrieve)."""
        resp = await self._get(
            settings.LMWS_CACHE_URL, "cache_url", "memberRetrieve", {"member": member}
        )
        return {"Result": "SUCCESS", "memberships": resp.get("memberships", resp), "raw": resp}

    async def list_retrieve(self, list_name: str) -> Dict[str, Any]:
        """Members, owner, supervisors of a list (mirrors the notebook listRetrieve)."""
        resp = await self._get(
            settings.LMWS_AUTHN_URL, "authn_url", "listRetrieve", {"listName": list_name}
        )
        return {
            "Result": "SUCCESS",
            "listOwner": resp.get("listOwner"),
            "listSupervisors": resp.get("listSupervisors", []),
            "listMembers": resp.get("listMembers", []),
            "raw": resp,
        }

    # ------------------------------------------------------------------
    # Membership writes
    # ------------------------------------------------------------------

    async def add_members(
        self,
        list_name: str,
        members: Union[str, List[str]],
        justification: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add members to a list (mirrors the notebook listMembersAdd)."""
        resp = await self._get(settings.LMWS_AUTHN_URL, "authn_url", "listMembersAdd", {
            "listName": list_name,
            "listMembers": _csv(members),
            "justification": justification or settings.LMWS_DEFAULT_JUSTIFICATION,
        })
        return {"Result": "SUCCESS", "workflowInfos": resp.get("workflowInfos", []), "raw": resp}

    async def remove_members(
        self,
        list_name: str,
        members: Union[str, List[str]],
        justification: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Remove members from a list (mirrors the notebook listMembersRemove)."""
        resp = await self._get(settings.LMWS_AUTHN_URL, "authn_url", "listMembersRemove", {
            "listName": list_name,
            "listMembers": _csv(members),
            "justification": justification or settings.LMWS_DEFAULT_JUSTIFICATION,
        })
        return {"Result": "SUCCESS", "removed": _csv(members).split(",") if members else [], "raw": resp}

    async def update_members(
        self,
        list_name: str,
        members: Union[str, List[str]],
        justification: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set list membership to exactly ``members`` (mirrors listMembersUpdate)."""
        resp = await self._get(settings.LMWS_AUTHN_URL, "authn_url", "listMembersUpdate", {
            "listName": list_name,
            "listMembers": _csv(members),
            "justification": justification or settings.LMWS_DEFAULT_JUSTIFICATION,
        })
        return {"Result": "SUCCESS", "members": _csv(members).split(",") if members else [], "raw": resp}

    # ------------------------------------------------------------------
    # Group / SPAC lifecycle
    # ------------------------------------------------------------------

    async def list_create_new(
        self,
        list_name: str,
        owner: str,
        *,
        description: Optional[str] = None,
        supervisors: Union[str, List[str], None] = None,
        qc_list_types: Union[str, List[str], None] = None,
    ) -> Dict[str, Any]:
        """Create a new list (mirrors the notebook listCreateNew)."""
        types = [t for t in _csv(qc_list_types).split(",") if t] or ["qgroup", "email"]
        qc_json = json.dumps(
            {"qcListTypeInfos": {"qcListTypeInfo": [{"qcListType": t} for t in types]}}
        )
        resp = await self._get(settings.LMWS_REST_URL, "rest_url", "listCreateNew", {
            "listName": list_name,
            "description": description or "",
            "listOwner": owner,
            "listSupervisors": _csv(supervisors),
            "qcListTypeInfos": qc_json,
        })
        return {"Result": "SUCCESS", "listName": list_name, "raw": resp}

    async def create_sp_group(
        self,
        list_name: str,
        owner: str,
        *,
        description: Optional[str] = None,
        supervisors: Union[str, List[str], None] = None,
        clone_source: Optional[str] = None,
        requester: Optional[str] = None,
        cci_classification: str = "1",
    ) -> Dict[str, Any]:
        """Create a security (SP) group (mirrors the notebook createSPGroup)."""
        req = self._requester(requester, owner)
        resp = await self._post(settings.LMWS_FWS_URL, "fws_url", "createSPGroup", {
            "actor": self.username,
            "listName": list_name,
            "requester": req,
            "systemEndpoint": "Azure",
            "cloneListName": clone_source or settings.LMWS_DEFAULT_CLONE_SOURCE,
            "description": description or "",
            "owner": owner,
            "supervisors": _csv(supervisors),
            "type": "SECURITY",
            "CCIClassification": cci_classification or "1",
            "notificationCallBack": f"{req}@qualcomm.com",
            "accessRequested": "on-prem-windowsbased",
        })
        return {
            "Result": "SUCCESS",
            "listName": list_name,
            "requestId": resp.get("requestId", resp.get("requestid")),
            "raw": resp,
        }

    async def process_spac_policy(
        self,
        list_name: str,
        spac_policies: Union[str, List[str]],
        *,
        request_type: str = "ADD",
        requester: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add/remove SPAC policies on a list (mirrors the notebook processSpacPolicy)."""
        req_type = (request_type or "ADD").strip().upper() or "ADD"
        policies = [
            {"policyType": "SPAC", "policyName": p}
            for p in _csv(spac_policies).split(",") if p
        ]
        key = "addPolicies" if req_type == "ADD" else "removePolicies"
        resp = await self._post(settings.LMWS_FWS_URL, "fws_url", "processSpacPolicy", {
            "actor": self.username,
            "requester": self._requester(requester),
            "systemEndpoint": "Azure",
            "listName": list_name,
            "requestType": req_type,
            key: policies,
        })
        return {"Result": "SUCCESS", "listName": list_name, "requestType": req_type, "raw": resp}

    async def get_spac_policy(
        self,
        list_name: str,
        *,
        requester: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read the SPAC policies on a list (mirrors the notebook getSpacPolicy)."""
        resp = await self._post(settings.LMWS_FWS_URL, "fws_url", "getSpacPolicy", {
            "actor": self.username,
            "requester": self._requester(requester),
            "systemEndpoint": "Azure",
            "listName": list_name,
        })
        return {"Result": "SUCCESS", "listName": list_name, "policies": resp.get("policies", resp), "raw": resp}

    async def request_confirmation(
        self,
        request_id: str,
        *,
        requester: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Confirm the status of a prior request (mirrors the notebook requestConfirmation)."""
        resp = await self._post(settings.LMWS_FWS_URL, "fws_url", "requestConfirmation", {
            "actor": self.username,
            "requester": self._requester(requester),
            "systemEndpoint": "Azure",
            "requestid": request_id,
        })
        return {"Result": "SUCCESS", "requestId": request_id, "status": resp.get("status", resp), "raw": resp}
