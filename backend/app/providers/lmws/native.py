"""Native (in-app) LMWS/FWS-API read client.

An experimental alternative to the job-backed :class:`LmwsProvider` for the
read-only lookup actions (``member_retrieve`` / ``list_retrieve``). Where the
serverless path submits the vendored notebook as a Databricks job — needed when
the LMWS/FWS-API gateway is only reachable from a network-pinned cluster — this
client calls the gateway directly from the app's runtime, removing the job
cold-start/poll latency.

It intentionally mirrors the notebook's HTTP contract (basic auth against the
gateway, ``verify`` off by default for the internal CA, and the body-level
``errorInfos`` check) so a native lookup returns the same result shape as the
job-backed path (see ``LmwsProvider.parse_output`` and the notebook handlers).

The service-account password is read from the app environment
(``settings.LMWS_SERVICE_PASSWORD``), NOT from the Databricks secret scope — the
REST Secrets API doesn't return secret values, so it must be injected as an app
env var (e.g. a databricks.yml app-resource secret bound to the ``lmws`` scope
key ``edhapisvc``). This client is only used by the ``*_native`` tools; the
serverless path stays the default until it's explicitly swapped over.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import httpx

from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError

logger = logging.getLogger(__name__)


class LmwsNativeClient:
    """Direct, in-process LMWS/FWS-API reads (no Databricks job)."""

    def __init__(self) -> None:
        self.username = (settings.LMWS_SERVICE_USERNAME or "").strip()
        self.password = settings.LMWS_SERVICE_PASSWORD or ""
        self.verify_tls = bool(getattr(settings, "LMWS_NATIVE_VERIFY_TLS", False))
        self.timeout = float(getattr(settings, "LMWS_NATIVE_TIMEOUT_SECONDS", 30) or 30)

    def _require_creds(self) -> None:
        if not self.username or not self.password:
            raise PermanentError(
                "Native LMWS lookup is not configured: set LMWS_SERVICE_USERNAME and "
                "LMWS_SERVICE_PASSWORD (inject the service-account password into the app "
                "environment). Until then, use the serverless (job-backed) lookup."
            )

    @staticmethod
    def _require_url(base: str, name: str) -> str:
        if not base:
            raise PermanentError(
                f"LMWS base URL '{name}' is not configured. Set it in Admin -> Settings "
                f"(Group Management) or databricks.yml before using the native lookup."
            )
        return base.rstrip("/")

    @staticmethod
    def _check_body(data: Any, where: str) -> Any:
        """Raise on a body-level LMWS/FWS-API error (HTTP 200 with ``errorInfos``)."""
        if not isinstance(data, dict):
            return data
        errs = data.get("errorInfos") or data.get("errorInfo")
        if errs:
            if isinstance(errs, list):
                msgs = "; ".join(str(e.get("message") or e) for e in errs)
            else:
                msgs = str(errs)
            raise PermanentError(f"LMWS API error from {where}: {msgs}")
        return data

    async def _get(self, base: str, name: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._require_creds()
        url = f"{self._require_url(base, name)}/{path}"
        try:
            async with httpx.AsyncClient(verify=self.verify_tls, timeout=self.timeout) as client:
                resp = await client.get(url, params=params, auth=(self.username, self.password))
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
