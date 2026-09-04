"""
Databricks Model Serving endpoint client.

ROLE: Low-Level Transport Layer
RESPONSIBILITY: 
- Handles raw HTTP communication with Databricks Model Serving.
- Manages Authentication (Explicit Token vs OAuth).
- Implements Retry logic and Error Handling for HTTP status codes.
- Agnostic to the payload content (works for LLMs, Classifiers, etc.).

Supports two authentication modes:
1. Explicit token: Uses MODEL_SERVING_API_KEY or DATABRICKS_TOKEN
2. OAuth (automatic): Uses Databricks SDK for OAuth in Databricks Apps
"""
from typing import Dict, Any, Optional
import asyncio
import httpx
import logging
import json
import time
from app.core.config import settings
from app.core.exceptions import RetryableError
from app.core.retry import retry_on_retryable

logger = logging.getLogger(__name__)

# Refresh OAuth-derived tokens at most this often on the shared client. Well
# under Databricks token lifetime; explicit static tokens are re-read too
# (harmless no-op).
_TOKEN_TTL_SECONDS = 1800

# Shared client(s), keyed by event loop. Building a ModelServingClient performs
# a blocking OAuth SDK round-trip and spins up a fresh httpx connection pool, so
# doing it per chat request both stalls the event loop and throws away warm
# connections. Agent code shares one instance instead (auth is refreshed
# in-place). We key by event loop because an httpx.AsyncClient's connection pool
# is bound to the loop it runs on, and this app uses two loops (the API request
# loop and the poller thread's loop) — sharing one client across both would
# raise "future attached to a different loop".
_shared_clients: Dict[int, "ModelServingClient"] = {}


def get_model_serving_client() -> "ModelServingClient":
    """Return the shared :class:`ModelServingClient` for the current event loop.

    Reuses one httpx connection pool + cached auth token across requests on that
    loop rather than reconstructing them per call. The token is refreshed
    in-place on a TTL (see :meth:`ModelServingClient.invoke_endpoint`).
    """
    try:
        key = id(asyncio.get_running_loop())
    except RuntimeError:
        key = 0  # constructed outside a running loop; use a default bucket
    client = _shared_clients.get(key)
    if client is None:
        client = ModelServingClient()
        _shared_clients[key] = client
    return client


def _get_oauth_token() -> Optional[str]:
    """
    Get OAuth token using Databricks SDK.
    This works automatically in Databricks Apps where OAuth is configured.
    """
    try:
        from databricks.sdk import WorkspaceClient
        # WorkspaceClient auto-detects auth from environment (OAuth in Apps)
        # It also automatically picks up DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET
        w = WorkspaceClient()
        # Get the auth headers which contain the OAuth token
        headers = w.config.authenticate()
        auth_header = headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            logger.info("Successfully obtained OAuth token via Databricks SDK")
            return token
        
        # Fallback if authenticate() doesn't return Bearer token (sometimes happens with M2M)
        if hasattr(w.config, 'token') and w.config.token:
            logger.info("Successfully obtained token directly from WorkspaceClient config")
            return w.config.token
            
        logger.warning("OAuth token not found in SDK auth headers")
        return None
    except ImportError:
        logger.warning("databricks-sdk not installed, OAuth not available")
        return None
    except Exception as e:
        logger.warning(f"Failed to get OAuth token via SDK: {e}")
        return None


class ModelServingClient:
    """Client for Databricks Model Serving endpoints."""
    
    def __init__(self):
        self.base_url = settings.DATABRICKS_WORKSPACE_URL or settings.DATABRICKS_HOST
        
        if not self.base_url:
            raise ValueError("DATABRICKS_WORKSPACE_URL or DATABRICKS_HOST must be set in configuration")
        
        # Ensure base_url has https:// protocol
        if not self.base_url.startswith("http://") and not self.base_url.startswith("https://"):
            self.base_url = f"https://{self.base_url}"
            logger.info(f"Added https:// protocol to base_url: {self.base_url}")
        
        # Ensure base_url doesn't end with a slash
        self.base_url = self.base_url.rstrip("/")

        self.api_key: Optional[str] = None
        self._token_acquired_at: float = 0.0

        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Content-Type": "application/json"},
            timeout=120.0  # 2 minutes to accommodate long-running LLM generation
        )

        # Acquire the initial token (and bake it into the client's headers).
        self._refresh_token(force=True)
        if not self.api_key:
            raise ValueError(
                "Authentication required: Set MODEL_SERVING_API_KEY, DATABRICKS_TOKEN, "
                "or ensure OAuth is configured (automatic in Databricks Apps)"
            )

    def _acquire_token(self) -> Optional[str]:
        """Resolve an auth token: explicit env token, then SP M2M, then OAuth.

        Blocking (SDK / network); callers off the request path should run it via
        ``asyncio.to_thread``.
        """
        # Try explicit token first.
        token = settings.MODEL_SERVING_API_KEY or settings.DATABRICKS_TOKEN
        if token:
            return token

        logger.info("No explicit token provided, attempting OAuth via Databricks SDK...")
        # Prefer raw SP client-credentials (WorkspaceClient header caching is brittle).
        if settings.DATABRICKS_CLIENT_ID and settings.DATABRICKS_CLIENT_SECRET:
            try:
                from databricks.sdk.core import Config

                cfg = Config(
                    host=self.base_url,
                    client_id=settings.DATABRICKS_CLIENT_ID,
                    client_secret=settings.DATABRICKS_CLIENT_SECRET,
                    auth_type="oauth-m2m",
                )
                auth_headers = cfg.authenticate()
                auth_val = (auth_headers or {}).get("Authorization", "")
                if auth_val.startswith("Bearer "):
                    logger.info("Fetched fresh OAuth token via Config.authenticate()")
                    return auth_val[7:]
            except Exception as e:
                logger.error(f"Failed to fetch SP token for Model Serving: {e}")

        # Fall back to default OAuth (auto-injected env token in Databricks Apps).
        return _get_oauth_token()

    def _token_is_stale(self) -> bool:
        return (time.monotonic() - self._token_acquired_at) > _TOKEN_TTL_SECONDS

    def _refresh_token(self, force: bool = False) -> None:
        """Re-acquire the token and update the client's Authorization header.

        Kept in-place so the process-wide shared client stays authenticated as
        OAuth tokens rotate, without rebuilding the httpx connection pool.
        """
        if not force and not self._token_is_stale():
            return
        token = self._acquire_token()
        if token:
            self.api_key = token
            self._token_acquired_at = time.monotonic()
            self.client.headers["Authorization"] = f"Bearer {token}"
    
    @retry_on_retryable(max_attempts=5, min_wait=2.0, max_wait=30.0)
    async def invoke_endpoint(
        self,
        endpoint_name: str,
        inputs: Dict[str, Any],
        endpoint_url: Optional[str] = None,
        use_foundation_model_format: bool = False
    ) -> Dict[str, Any]:
        """
        Invoke a model serving endpoint.
        
        For Databricks Model Serving, the endpoint URL structure is:
        - Foundation Model APIs: /serving-endpoints/{endpoint_name}/invocations
        - Custom models: /serving-endpoints/{endpoint_name}/invocations
        
        Args:
            endpoint_name: Name of the endpoint
            inputs: Input data for the model
            endpoint_url: Optional full URL (if not using base_url)
            use_foundation_model_format: If True, send inputs directly (for Foundation Model APIs).
                                        If False, wrap in {"inputs": inputs} (for custom models).
            
        Returns:
            Model prediction/response
        """
        # Refresh the OAuth token if it has aged past the TTL (offloaded so the
        # blocking SDK auth call never stalls the event loop).
        if self._token_is_stale():
            await asyncio.to_thread(self._refresh_token)

        try:
            # Construct the endpoint URL
            if endpoint_url:
                url = endpoint_url
            else:
                # Use the standard Databricks Model Serving endpoint path
                url = f"/serving-endpoints/{endpoint_name}/invocations"
            
            # Databricks Model Serving expects different formats:
            # - Foundation Model APIs: Send inputs directly (no wrapper)
            #   {"messages": [...], "temperature": ..., "max_tokens": ...}
            # - Custom models: Wrap in "inputs" key
            #   {"inputs": {...}}
            if use_foundation_model_format:
                payload = inputs
            else:
                payload = {"inputs": inputs}
            
            # Log request details for debugging
            logger.debug(f"Invoking endpoint: {endpoint_name}")
            logger.debug(f"URL: {self.base_url}{url}")
            logger.debug(f"Payload keys: {list(payload.keys())}")
            if "messages" in payload:
                logger.debug(f"Messages count: {len(payload['messages'])}")
                # Log first message structure
                if payload["messages"]:
                    logger.debug(f"First message keys: {list(payload['messages'][0].keys())}")
            # Log full payload (truncated for large payloads)
            import json
            payload_str = json.dumps(payload, indent=2)
            
            # Log request for debugging
            logger.debug(f"=== Databricks Request ===")
            logger.debug(f"URL: {url}")
            logger.debug(f"Payload: {json.dumps(payload, indent=2, default=str)[:2000]}")
            
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            
            # Log response for debugging
            logger.debug(f"=== Databricks Response ===")
            logger.debug(f"Status: {response.status_code}")
            logger.debug(f"Response structure: {json.dumps(result, indent=2, default=str)[:3000]}")
            
            # Handle different response formats
            return self._format_endpoint_result(result)
        except httpx.HTTPStatusError as e:
            # Log the error response body for debugging
            error_body = None
            error_text = None
            try:
                error_body = e.response.json()
                logger.error(f"Endpoint error response (JSON): {error_body}")
            except:
                error_text = e.response.text
                logger.error(f"Endpoint error response (text): {error_text}")
            
            # Extract error message from response
            error_message = str(e)
            if error_body:
                if isinstance(error_body, dict):
                    # Try different common error message fields
                    error_message = (
                        error_body.get("error", {}).get("message") or
                        error_body.get("error", {}).get("detail") or
                        error_body.get("message") or
                        error_body.get("detail") or
                        str(error_body)
                    )
                else:
                    error_message = str(error_body)
            elif error_text:
                error_message = error_text[:500]  # Limit length

            # If the model does not support the temperature parameter, retry once without it
            if e.response.status_code == 400 and "temperature" in error_message.lower() and (
                "not support" in error_message.lower() or "unsupported" in error_message.lower()
            ):
                had_temp = False
                if isinstance(payload, dict) and "temperature" in payload:
                    payload.pop("temperature", None)
                    had_temp = True
                if isinstance(payload, dict) and isinstance(payload.get("inputs"), dict) and "temperature" in payload["inputs"]:
                    payload["inputs"].pop("temperature", None)
                    had_temp = True
                if isinstance(inputs, dict) and "temperature" in inputs:
                    inputs.pop("temperature", None)
                if had_temp:
                    logger.warning(
                        f"Endpoint '{endpoint_name}' rejected temperature parameter. Retrying without temperature."
                    )
                    try:
                        retry_resp = await self.client.post(url, json=payload)
                        retry_resp.raise_for_status()
                        result = retry_resp.json()
                        return self._format_endpoint_result(result)
                    except Exception as retry_err:
                        logger.error(f"Retry without temperature failed for '{endpoint_name}': {retry_err}")
            
            if e.response.status_code >= 500:
                raise RetryableError(f"Model serving error: {error_message}")
            elif e.response.status_code == 429:
                # Rate limit exceeded - definitely retry
                raise RetryableError(f"Rate limit exceeded (429): {error_message}")
            elif e.response.status_code == 404:
                raise RetryableError(f"Endpoint '{endpoint_name}' not found: {error_message}")
            elif e.response.status_code == 401:
                raise RetryableError(f"Authentication failed: {error_message}")
            elif e.response.status_code == 400:
                # 400 errors are usually format issues - include full detail
                raise RetryableError(f"Bad request (400) - Check request format. Error: {error_message}")
            else:
                raise RetryableError(f"Failed to invoke endpoint: {error_message}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    def _format_endpoint_result(self, result: Any) -> Any:
        """Normalize endpoint response payload into a standard dictionary/object."""
        if isinstance(result, dict):
            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]
            elif "predictions" in result and len(result["predictions"]) > 0:
                return result["predictions"][0]
            elif "outputs" in result:
                return result["outputs"]
            elif "output" in result:
                return result["output"]
            elif "candidates" in result:
                return result
            elif "message" in result:
                return result
            else:
                if result is None:
                    logger.error("response.json() returned None - this should not happen")
                    return {}
                logger.warning(f"Unexpected response format from endpoint: {list(result.keys()) if isinstance(result, dict) else type(result)}")
                return result
        return result
    
    async def health_check(self, endpoint_name: str) -> bool:
        """Check if endpoint is healthy."""
        try:
            response = await self.client.get(f"/serving-endpoints/{endpoint_name}/health")
            return response.status_code == 200
        except:
            return False
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

