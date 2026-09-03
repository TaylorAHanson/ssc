"""
Terramate API Provider client.

Interfaces with the `terramate-api-wrapper` service which orchestrates GitOps
pull requests against the Terramate/Terraform infrastructure repository.
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


class TerramateProvider(BaseProvider):
    """
    HTTP client provider for the Terramate Provisioning API (terramate-api-wrapper).
    """

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config)
        self.api_url = (api_url or getattr(settings, "TERRAMATE_API_URL", "http://localhost:8000")).rstrip("/")
        self.api_token = api_token or getattr(settings, "TERRAMATE_API_TOKEN", "")
        self.timeout = float(timeout_seconds or getattr(settings, "TERRAMATE_HTTP_TIMEOUT_SECONDS", 30))

    def _get_headers(
        self,
        idempotency_key: Optional[str] = None,
        requester: Optional[str] = None,
    ) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        if requester:
            headers["X-Requester"] = str(requester)
        return headers

    @retry_on_retryable(max_attempts=3)
    async def create_request(
        self,
        request_type: str,
        params: Dict[str, Any],
        idempotency_key: str,
        requester: str,
    ) -> Dict[str, Any]:
        """
        Submit a new provisioning request to the Terramate API.

        Args:
            request_type: Resource type (e.g. "workspace", "schema").
            params: Type-specific parameter payload.
            idempotency_key: Client-generated UUIDv4 preventing duplicate submissions.
            requester: User or service principal identity initiating the request.

        Returns:
            Dict with request_id and status (e.g. {"success": True, "request_id": "...", "status": "pending"}).
        """
        url = f"{self.api_url}/v1/requests"
        payload = {
            "type": request_type,
            "params": params,
        }
        headers = self._get_headers(idempotency_key=idempotency_key, requester=requester)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=headers)

                if response.status_code == 503:
                    detail = response.json().get("detail", "Intake is currently disabled")
                    raise PermanentError(f"Terramate intake gate closed: {detail}")

                if response.status_code == 422:
                    detail = response.json().get("detail", response.text)
                    raise PermanentError(f"Terramate parameter validation failed for type '{request_type}': {detail}")

                if response.status_code >= 400 and response.status_code < 500:
                    raise PermanentError(f"Terramate API error ({response.status_code}): {response.text}")

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
                    "status": data.get("status"),
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
        Retrieve request details, step progress, and outputs from Terramate API.

        Args:
            request_id: The UUID of the request.

        Returns:
            RequestDetailResponse dictionary or None if 404.
        """
        url = f"{self.api_url}/v1/requests/{request_id}"
        headers = self._get_headers()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 404:
                    return None
                if response.status_code >= 400 and response.status_code < 500:
                    raise PermanentError(f"Terramate API error ({response.status_code}): {response.text}")
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"Terramate API server error ({e.response.status_code}): {e}") from e
            raise PermanentError(f"Terramate API HTTP error ({e.response.status_code}): {e}") from e
        except (httpx.RequestError, httpx.TimeoutException) as e:
            raise RetryableError(f"Terramate API connection failed: {e}") from e

    @retry_on_retryable(max_attempts=3)
    async def get_step_plan(self, request_id: str, ordinal: int = 0) -> Dict[str, Any]:
        """
        Retrieve Terraform plan output for a specific step.

        Args:
            request_id: The UUID of the request.
            ordinal: Zero-indexed step ordinal in the playbook.

        Returns:
            Dict with `available: bool`, `plan: Optional[str]`, `status: str`.
        """
        url = f"{self.api_url}/v1/requests/{request_id}/steps/{ordinal}/plan"
        headers = self._get_headers()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 409:
                    # 409 Conflict means plan is not yet available in the orchestrator
                    return {
                        "available": False,
                        "ordinal": ordinal,
                        "status": "pending_plan",
                        "message": "Terraform plan is not yet available.",
                    }
                if response.status_code == 404:
                    raise PermanentError(f"Step {ordinal} for request {request_id} not found.")
                if response.status_code >= 400 and response.status_code < 500:
                    raise PermanentError(f"Terramate API error ({response.status_code}): {response.text}")
                response.raise_for_status()
                data = response.json()
                return {
                    "available": True,
                    "ordinal": data.get("ordinal", ordinal),
                    "key": data.get("key"),
                    "status": data.get("status"),
                    "plan": data.get("plan"),
                }
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
        """
        url = f"{self.api_url}/v1/requests/{request_id}/cancel"
        headers = self._get_headers()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers)
                if response.status_code == 404:
                    raise PermanentError(f"Request {request_id} not found.")
                if response.status_code == 409:
                    # Already reached terminal state
                    detail = response.json().get("detail", "Request already in terminal state")
                    return {"request_id": request_id, "status": "already_terminal", "message": detail}
                if response.status_code >= 400 and response.status_code < 500:
                    raise PermanentError(f"Terramate API error ({response.status_code}): {response.text}")
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"Terramate API server error ({e.response.status_code}): {e}") from e
            raise PermanentError(f"Terramate API HTTP error ({e.response.status_code}): {e}") from e
        except (httpx.RequestError, httpx.TimeoutException) as e:
            raise RetryableError(f"Terramate API connection failed: {e}") from e

    @retry_on_retryable(max_attempts=2)
    async def get_intake_gate(self) -> Dict[str, Any]:
        """Check the global intake gate status."""
        url = f"{self.api_url}/v1/admin/intake-gate"
        headers = self._get_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()

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
