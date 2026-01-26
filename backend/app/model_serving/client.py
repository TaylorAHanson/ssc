"""
Databricks Model Serving endpoint client.

Supports two authentication modes:
1. Explicit token: Uses MODEL_SERVING_API_KEY or DATABRICKS_TOKEN
2. OAuth (automatic): Uses Databricks SDK for OAuth in Databricks Apps
"""
from typing import Dict, Any, Optional
import httpx
import logging
import json
from app.core.config import settings
from app.core.exceptions import RetryableError
from app.core.retry import retry_on_retryable

logger = logging.getLogger(__name__)


def _get_oauth_token() -> Optional[str]:
    """
    Get OAuth token using Databricks SDK.
    This works automatically in Databricks Apps where OAuth is configured.
    """
    try:
        from databricks.sdk import WorkspaceClient
        # WorkspaceClient auto-detects auth from environment (OAuth in Apps)
        w = WorkspaceClient()
        # Get the auth headers which contain the OAuth token
        headers = w.config.authenticate()
        auth_header = headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            logger.info("Successfully obtained OAuth token via Databricks SDK")
            return token
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
        
        # Try explicit token first, then fall back to OAuth
        self.api_key = settings.MODEL_SERVING_API_KEY or settings.DATABRICKS_TOKEN
        
        if not self.api_key:
            logger.info("No explicit token provided, attempting OAuth via Databricks SDK...")
            self.api_key = _get_oauth_token()
        
        if not self.api_key:
            raise ValueError(
                "Authentication required: Set MODEL_SERVING_API_KEY, DATABRICKS_TOKEN, "
                "or ensure OAuth is configured (automatic in Databricks Apps)"
            )
        
        # Ensure base_url doesn't end with a slash
        self.base_url = self.base_url.rstrip("/")
        
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            },
            timeout=60.0
        )
    
    @retry_on_retryable(max_attempts=3)
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
            logger.info(f"Invoking endpoint: {endpoint_name}")
            logger.info(f"URL: {self.base_url}{url}")
            logger.info(f"Payload keys: {list(payload.keys())}")
            if "messages" in payload:
                logger.info(f"Messages count: {len(payload['messages'])}")
                # Log first message structure
                if payload["messages"]:
                    logger.info(f"First message keys: {list(payload['messages'][0].keys())}")
            # Log full payload (truncated for large payloads)
            import json
            payload_str = json.dumps(payload, indent=2)
            if len(payload_str) > 1000:
                logger.info(f"Payload (truncated): {payload_str[:1000]}...")
            else:
                logger.info(f"Payload: {payload_str}")
            
            # Log request for debugging
            logger.info(f"=== Databricks Request ===")
            logger.info(f"URL: {url}")
            logger.info(f"Payload: {json.dumps(payload, indent=2, default=str)[:2000]}")
            
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            
            # Log response for debugging
            logger.info(f"=== Databricks Response ===")
            logger.info(f"Status: {response.status_code}")
            logger.info(f"Response structure: {json.dumps(result, indent=2, default=str)[:3000]}")
            
            # Handle different response formats
            # Foundation Model APIs typically return: {"choices": [...]} or {"output": "..."}
            # Custom models return: {"predictions": [...]} or {"outputs": [...]}
            if "choices" in result and len(result["choices"]) > 0:
                # Foundation Model API format
                return result["choices"][0]
            elif "predictions" in result and len(result["predictions"]) > 0:
                # Custom model format
                return result["predictions"][0]
            elif "outputs" in result:
                return result["outputs"]
            elif "output" in result:
                return result["output"]
            elif "candidates" in result:
                # Gemini format - return the full result for processing
                return result
            elif "message" in result:
                # New Gemini format with message wrapper
                return result
            else:
                # Return the full result if we can't parse it
                # This should never be None - result comes from response.json()
                if result is None:
                    logger.error("response.json() returned None - this should not happen")
                    return {}
                logger.warning(f"Unexpected response format from endpoint: {list(result.keys()) if isinstance(result, dict) else type(result)}")
                return result
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
            
            if e.response.status_code >= 500:
                raise RetryableError(f"Model serving error: {error_message}")
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

