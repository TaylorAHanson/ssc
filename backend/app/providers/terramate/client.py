"""
Terramate API Provider client (ADR-0004).

Interfaces with the `terramate-api-wrapper` service which acts as a request intake
and status oracle, expanding provisioning requests into Steps and PRs on GitHub.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
import httpx

from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
from app.core.retry import retry_on_retryable
from app.providers.base import BaseProvider

logger = logging.getLogger(__name__)


def _format_validation_error(detail: Any) -> str:
    """Format FastAPI/Pydantic validation error lists into clean, readable strings."""
    if isinstance(detail, list):
        messages = []
        for item in detail:
            if isinstance(item, dict):
                loc_parts = [str(x) for x in item.get("loc", []) if x != "body"]
                loc = " -> ".join(loc_parts)
                msg = item.get("msg", "")
                if loc:
                    messages.append(f"'{loc}': {msg}")
                else:
                    messages.append(msg)
            else:
                messages.append(str(item))
        if messages:
            return "; ".join(messages)
    return str(detail)


class TerramateProvider(BaseProvider):
    """
    HTTP client provider for the Terramate Provisioning API (terramate-api-wrapper).

    Per ADR-0004:
    - POST /v1/requests: Submit request (returns 202 with request_id and pending status).
    - GET /v1/requests/{id}: Poll request and Step statuses (done/not-done signal).
    - GET /v1/requests/{id}/steps/{ordinal}: Step detail.
    - POST /v1/requests/{id}/cancel: Cancel in-flight request.
    - GET /v1/health: Liveness check.
    """

    def __init__(
        self,
        api_url: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config)
        self.api_url = (api_url or getattr(settings, "TERRAMATE_API_URL", "http://localhost:8000")).rstrip("/")
        self.timeout = float(timeout_seconds or getattr(settings, "TERRAMATE_HTTP_TIMEOUT_SECONDS", 30))

    def _resolve_token(self) -> Optional[str]:
        # In Databricks Apps, Databricks SDK auto-detects OAuth credentials for the App's Service Principal
        try:
            from databricks.sdk import WorkspaceClient

            w = WorkspaceClient()
            auth_headers = w.config.authenticate()
            auth_header = (auth_headers or {}).get("Authorization", "")
            if auth_header.startswith("Bearer "):
                return auth_header[7:]
            if hasattr(w.config, "token") and w.config.token:
                return w.config.token
        except Exception as e:
            logger.debug(f"Could not auto-resolve Databricks token via SDK: {e}")
        return getattr(settings, "DATABRICKS_TOKEN", None) or None

    def _get_headers(
        self,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        token = self._resolve_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        return headers

    @retry_on_retryable(max_attempts=3)
    async def create_request(
        self,
        request_type: str,
        params: Dict[str, Any],
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """
        Submit a new provisioning request to the Terramate API.

        Args:
            request_type: Resource type (discriminated union, e.g. "workspace", "schema").
            params: Type-specific parameter payload.
            idempotency_key: Stable client-generated UUIDv4 preventing duplicate submissions (required).

        Returns:
            Dict with request_id and status (e.g. {"success": True, "request_id": "...", "status": "pending"}).
        """
        if not idempotency_key:
            raise PermanentError("Idempotency-Key is required for Terramate provisioning requests.")

        url = f"{self.api_url}/v1/requests"
        payload = {
            "type": request_type,
            "params": params,
        }
        headers = self._get_headers(idempotency_key=idempotency_key)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=headers)

                if response.status_code == 503:
                    detail = response.json().get("detail", "Intake is currently disabled")
                    raise PermanentError(f"Terramate intake gate closed: {detail}")

                if response.status_code == 401:
                    detail = response.json().get("detail", "No resolvable caller identity")
                    raise PermanentError(f"Terramate authentication error (401): {detail}")

                if response.status_code == 422:
                    raw_detail = response.json().get("detail", response.text)
                    formatted = _format_validation_error(raw_detail)
                    raise PermanentError(f"Terramate parameter validation failed for type '{request_type}': {formatted}")

                if 400 <= response.status_code < 500:
                    raise PermanentError(f"Terramate API client error ({response.status_code}): {response.text}")

                response.raise_for_status()
                data = response.json()
                logger.info(
                    "terramate_request_created request_id=%s type=%s status=%s",
                    data.get("request_id"),
                    request_type,
                    data.get("status"),
                )
                return {
                    "success": True,
                    "request_id": data.get("request_id"),
                    "status": data.get("status", "pending"),
                }
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"Terramate API server error ({e.response.status_code}): {e}") from e
            raise PermanentError(f"Terramate API HTTP error ({e.response.status_code}): {e}") from e
        except (httpx.RequestError, httpx.TimeoutException) as e:
            raise RetryableError(f"Terramate API connection failed: {e}") from e

    @retry_on_retryable(max_attempts=3)
    async def get_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve full request details, including Step statuses and open PR URLs.

        Args:
            request_id: The UUID of the request.

        Returns:
            Request dict or None if 404.
        """
        url = f"{self.api_url}/v1/requests/{request_id}"
        headers = self._get_headers()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 404:
                    return None
                if response.status_code == 401:
                    detail = response.json().get("detail", "No resolvable caller identity")
                    raise PermanentError(f"Terramate authentication error (401): {detail}")
                if 400 <= response.status_code < 500:
                    raise PermanentError(f"Terramate API client error ({response.status_code}): {response.text}")
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"Terramate API server error ({e.response.status_code}): {e}") from e
            raise PermanentError(f"Terramate API HTTP error ({e.response.status_code}): {e}") from e
        except (httpx.RequestError, httpx.TimeoutException) as e:
            raise RetryableError(f"Terramate API connection failed: {e}") from e

    @retry_on_retryable(max_attempts=3)
    async def get_step(self, request_id: str, ordinal: int = 0) -> Optional[Dict[str, Any]]:
        """
        Retrieve detail for a single Step in the request.

        Args:
            request_id: The UUID of the request.
            ordinal: Zero-indexed step ordinal in the playbook.

        Returns:
            Step detail dict or None if 404.
        """
        url = f"{self.api_url}/v1/requests/{request_id}/steps/{ordinal}"
        headers = self._get_headers()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 404:
                    return None
                if 400 <= response.status_code < 500:
                    raise PermanentError(f"Terramate API client error ({response.status_code}): {response.text}")
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"Terramate API server error ({e.response.status_code}): {e}") from e
            raise PermanentError(f"Terramate API HTTP error ({e.response.status_code}): {e}") from e
        except (httpx.RequestError, httpx.TimeoutException) as e:
            raise RetryableError(f"Terramate API connection failed: {e}") from e

    @retry_on_retryable(max_attempts=3)
    async def cancel_request(self, request_id: str) -> Dict[str, Any]:
        """
        Halt an in-flight request.

        Args:
            request_id: The UUID of the request to cancel.

        Returns:
            Dict containing {"request_id": ..., "status": "cancelled"}.
        """
        url = f"{self.api_url}/v1/requests/{request_id}/cancel"
        headers = self._get_headers()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers)
                if response.status_code == 404:
                    raise PermanentError(f"Request {request_id} not found.")
                if response.status_code == 409:
                    detail = response.json().get("detail", "Request already reached a terminal state")
                    raise PermanentError(f"Request {request_id} cannot be cancelled: {detail}")
                if 400 <= response.status_code < 500:
                    raise PermanentError(f"Terramate API client error ({response.status_code}): {response.text}")
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"Terramate API server error ({e.response.status_code}): {e}") from e
            raise PermanentError(f"Terramate API HTTP error ({e.response.status_code}): {e}") from e
        except (httpx.RequestError, httpx.TimeoutException) as e:
            raise RetryableError(f"Terramate API connection failed: {e}") from e

    async def health_check(self) -> bool:
        """Check liveness connectivity to the Terramate API."""
        try:
            url = f"{self.api_url}/v1/health"
            headers = self._get_headers()
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, headers=headers)
                return response.status_code == 200
        except Exception as e:
            logger.debug(f"TerramateProvider health check failed: {e}")
            return False
