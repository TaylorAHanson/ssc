
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
import logging
import uuid

from app.core.config import settings
from app.core.logging_formatter import (
    current_user_email, current_endpoint, current_request_id,
    current_method, current_client_ip, current_user_agent, current_correlation_id
)

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to extract user identity from headers (Databricks Apps)
    and provide mock defaults for local development.
    """
    async def dispatch(self, request: Request, call_next):
        # Generate a request ID
        req_id = str(uuid.uuid4())
        
        # Set context variables for logging
        endpoint_token = current_endpoint.set(request.url.path)
        req_id_token = current_request_id.set(req_id)
        method_token = current_method.set(request.method)
        
        # Extract correlation ID if present (useful for microservices/Databricks)
        corr_id = request.headers.get("x-correlation-id") or request.headers.get("x-request-id", "N/A")
        corr_id_token = current_correlation_id.set(corr_id)
        
        client_ip = request.client.host if request.client else None
        # Databricks often load balances so we check X-Forwarded-For
        x_forwarded_for = request.headers.get("X-Forwarded-For")
        if x_forwarded_for:
            client_ip = x_forwarded_for.split(",")[0].strip()
        ip_token = current_client_ip.set(client_ip)
        
        agent_token = current_user_agent.set(request.headers.get("User-Agent", "Unknown"))
        
        # Skip middleware for MCP SSE routes to avoid BaseHTTPMiddleware buffering issues
        if request.url.path.startswith("/mcp"):
             try:
                 return await call_next(request)
             finally:
                 current_endpoint.reset(endpoint_token)
                 current_request_id.reset(req_id_token)
                 current_method.reset(method_token)
                 current_client_ip.reset(ip_token)
                 current_user_agent.reset(agent_token)
                 current_correlation_id.reset(corr_id_token)

        # Extract headers (case-insensitive usually, but using request.headers.get is safe)
        # Databricks Apps headers:
        # X-Forwarded-Email: IdP email
        # X-Forwarded-Preferred-Username: IdP display/username
        # X-Forwarded-User: IdP user identifier
        
        email = request.headers.get("X-Forwarded-Email", settings.MOCK_USER_EMAIL)
        username = request.headers.get("X-Forwarded-Preferred-Username", settings.MOCK_USER_NAME)
        user_id = request.headers.get("X-Forwarded-User", settings.MOCK_USER_ID)
        
        # Set user context for logging
        user_email_token = current_user_email.set(email)

        
        
        # Extract OBO Token (Databricks Apps)
        # Check both X-Forwarded-Access-Token and Authorization header
        obo_token = request.headers.get("X-Forwarded-Access-Token")
        
        # DEBUG: Precise check for standard OBO header
        if obo_token:
            logger.debug(f"AuthMiddleware: OBO Token found in standard header. Length: {len(obo_token)}")
        else:
            logger.debug("AuthMiddleware: OBO Token HEADER MISSING in standard location (X-Forwarded-Access-Token). If this is local dev, this is expected.")
        
        # Fallback to mock token for local dev if configured and no header present
        if not obo_token and settings.MOCK_USER_TOKEN:
             obo_token = settings.MOCK_USER_TOKEN
             # Only log this in debug mode to avoid leaking secrets in logs
             logger.debug("AuthMiddleware: Using MOCK_USER_TOKEN")
        
        # Store in request state
        request.state.user = {
            "email": email,
            "username": username,
            "id": user_id
        }
        request.state.token = obo_token
        
        # DEBUG: Decode token to verify scopes (if it looks like a JWT)
        if obo_token and obo_token.startswith("eyJ"):
             try:
                 import base64
                 import json
                 # Simple decode of middle part (payload)
                 parts = obo_token.split('.')
                 if len(parts) > 1:
                     payload_str = parts[1]
                     # Add padding if needed
                     payload_str += '=' * (-len(payload_str) % 4)
                     payload_data = json.loads(base64.b64decode(payload_str))
                     scopes = payload_data.get("scp") or payload_data.get("scope")
                     logger.info(f"DEBUG AUTH: OBO Token Scopes: {scopes}")
             except Exception as e:
                 logger.error(f"DEBUG AUTH: Failed to decode token scopes: {e}")

        # Log only if different from default (to avoid noise) or on every request for debugging
        if email != settings.MOCK_USER_EMAIL:
             logger.debug(f"AuthMiddleware: User context set for {email}")
        
        import time
        start_time = time.time()
        
        try:
            response = await call_next(request)
            
            # Calculate execution time
            process_time = time.time() - start_time
            content_length = response.headers.get("content-length", "unknown")
            
            # Log the request details including status code and duration
            logger.info(
                f"HTTP_REQUEST: status_code={response.status_code} "
                f"duration_ms={round(process_time * 1000, 2)} "
                f"bytes={content_length}"
            )
            
            return response
        except Exception as e:
            # Calculate execution time for failures
            process_time = time.time() - start_time
            
            logger.error(
                f"HTTP_REQUEST_FAILED: "
                f"duration_ms={round(process_time * 1000, 2)} "
                f"error='{str(e)}'",
                exc_info=True
            )
            raise
        finally:
            current_endpoint.reset(endpoint_token)
            current_request_id.reset(req_id_token)
            current_user_email.reset(user_email_token)
            current_method.reset(method_token)
            current_client_ip.reset(ip_token)
            current_user_agent.reset(agent_token)
            current_correlation_id.reset(corr_id_token)
