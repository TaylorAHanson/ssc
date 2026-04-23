"""
IDP provider client.
"""
from typing import Dict, Any, Optional
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError, PermanentError
from app.core.retry import retry_on_retryable
import httpx


class EntraIdProvider(BaseProvider):
    """Entra ID provider for user and access management via Microsoft Graph API."""
    
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = "https://graph.microsoft.com/v1.0"
        self._access_token = None
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30.0
        )
        
    async def _get_token(self) -> str:
        """Get or refresh the OAuth2 access token for Microsoft Graph."""
        # In a real implementation, this would cache the token and handle expiration.
        # For now, we simulate getting a token or just return a dummy.
        if self._access_token:
            return self._access_token
            
        # Simulated token fetch
        # token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        # ... fetch token ...
        self._access_token = "simulated_entra_id_token"
        self.client.headers.update({"Authorization": f"Bearer {self._access_token}"})
        return self._access_token
    
    @retry_on_retryable(max_attempts=3)
    async def create_user(self, email: str, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Create user in Entra ID."""
        await self._get_token()
        try:
            response = await self.client.post(
                "/users",
                json={
                    "accountEnabled": True,
                    "displayName": attributes.get("displayName", email.split("@")[0]),
                    "mailNickname": email.split("@")[0],
                    "userPrincipalName": email,
                    "passwordProfile": {
                        "forceChangePasswordNextSignIn": True,
                        "password": "TemporaryPassword123!"
                    }
                }
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                raise PermanentError(f"User already exists: {email}")
            elif e.response.status_code >= 500:
                raise RetryableError(f"Entra ID server error: {str(e)}")
            else:
                raise PermanentError(f"Failed to create user: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")
    
    @retry_on_retryable(max_attempts=3)
    async def create_service_principal(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create service principal."""
        try:
            response = await self.client.post(
                "/api/v1/service-principals",
                json={"name": name, **config}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                raise PermanentError(f"Service principal already exists: {name}")
            elif e.response.status_code >= 500:
                raise RetryableError(f"IDP server error: {str(e)}")
            else:
                raise PermanentError(f"Failed to create service principal: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")
    
    @retry_on_retryable(max_attempts=3)
    async def create_api_key(self, principal_id: str, name: str) -> Dict[str, Any]:
        """Create API key for service principal."""
        try:
            response = await self.client.post(
                f"/api/v1/service-principals/{principal_id}/api-keys",
                json={"name": name}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"IDP server error: {str(e)}")
            else:
                raise PermanentError(f"Failed to create API key: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")
    
    @retry_on_retryable(max_attempts=3)
    async def get_user_manager(self, user_email: str) -> Optional[str]:
        """Get the manager's email for a given user from Entra ID."""
        await self._get_token()
        try:
            # First, get the user's object ID
            user_response = await self.client.get(
                f"/users/{user_email}"
            )
            user_response.raise_for_status()
            user_id = user_response.json().get("id")
            
            if not user_id:
                return None
                
            # Then, get the user's manager
            manager_response = await self.client.get(
                f"/users/{user_id}/manager"
            )
            manager_response.raise_for_status()
            manager_data = manager_response.json()
            
            return manager_data.get("mail") or manager_data.get("userPrincipalName")
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # User or manager not found
                return None
            elif e.response.status_code >= 500:
                raise RetryableError(f"Entra ID server error: {str(e)}")
            else:
                raise PermanentError(f"Failed to get user manager: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")
    
    @retry_on_retryable(max_attempts=3)
    async def add_to_group(self, user_id: str, group_id: str) -> bool:
        """Add user to Entra ID group."""
        await self._get_token()
        try:
            response = await self.client.post(
                f"/groups/{group_id}/members/$ref",
                json={"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"}
            )
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"Entra ID server error: {str(e)}")
            else:
                raise PermanentError(f"Failed to add user to group: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    @retry_on_retryable(max_attempts=3)
    async def grant_permission(self, principal_id: str, resource: str, permissions: list) -> bool:
        """Grant permission to principal."""
        try:
            response = await self.client.post(
                "/api/v1/permissions",
                json={
                    "principal_id": principal_id,
                    "resource": resource,
                    "permissions": permissions
                }
            )
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"IDP server error: {str(e)}")
            else:
                raise PermanentError(f"Failed to grant permission: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")
    
    async def health_check(self) -> bool:
        """Check if IDP is accessible."""
        try:
            response = await self.client.get("/health")
            return response.status_code == 200
        except:
            return False

    @retry_on_retryable(max_attempts=3)
    async def search_users(self, query: str) -> Dict[str, Any]:
        """
        Search for users in the IDP.
        Currently mocked for development.
        """
        # Mock data
        mock_users = [
            {"id": "usr_101", "email": "alice@example.com", "name": "Alice Smith", "department": "Engineering"},
            {"id": "usr_102", "email": "bob@example.com", "name": "Bob Jones", "department": "Data Science"},
            {"id": "usr_103", "email": "charlie@example.com", "name": "Charlie Brown", "department": "Finance"}
        ]
        
        # Simple mock filtering
        results = [u for u in mock_users if query.lower() in u["email"].lower() or query.lower() in u["name"].lower()]
        
        return {
            "query": query,
            "results": results,
            "count": len(results)
        }

    @retry_on_retryable(max_attempts=3)
    async def search_groups(self, query: str) -> Dict[str, Any]:
        """
        Search for groups in the IDP.
        Currently mocked for development.
        """
        # Mock data
        mock_groups = [
            {"id": "grp_123", "name": "data-engineering-prod", "description": "Data Engineering Production Access"},
            {"id": "grp_124", "name": "data-science-dev", "description": "Data Science Development"},
            {"id": "grp_125", "name": "finance-analysts", "description": "Finance Analytics Team"}
        ]
        
        # Simple mock filtering
        results = [g for g in mock_groups if query.lower() in g["name"].lower()]
        
        return {
            "query": query,
            "results": results,
            "count": len(results)
        }
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

