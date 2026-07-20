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

The service-account password is resolved at runtime from the SAME Databricks
secret scope the vendored notebook uses (``LMWS_SECRET_SCOPE`` / key
``LMWS_PASSWORD_SECRET_KEY``) via the app's own service principal — the exact
pattern already used for the GitHub PAT and SES creds
(``app.core.workspaces._read_secret``). So there's no plaintext injection and no
new secret; the app SP just needs READ on that scope (which it already has).
``settings.LMWS_SERVICE_PASSWORD`` is an optional override (e.g. local dev where
the scope isn't reachable) and wins when set. This client is only used by the
``*_native`` tools; the serverless path stays the default until it's explicitly
swapped over.
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
        self.password = self._resolve_password()
        self.verify_tls = bool(getattr(settings, "LMWS_NATIVE_VERIFY_TLS", False))
        self.timeout = float(getattr(settings, "LMWS_NATIVE_TIMEOUT_SECONDS", 30) or 30)

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
                "Native LMWS lookup is not configured: the service-account password could "
                f"not be resolved from secret scope '{settings.LMWS_SECRET_SCOPE}' "
                f"(key '{settings.LMWS_PASSWORD_SECRET_KEY}'). Verify the key exists and the "
                "app's service principal has READ on that scope (or set LMWS_SERVICE_PASSWORD "
                "for local dev). Until then, use the serverless (job-backed) lookup."
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
