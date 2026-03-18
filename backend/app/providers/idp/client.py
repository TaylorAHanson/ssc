"""
IDP provider client.
"""
from typing import Dict, Any, Optional
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError, PermanentError
from app.core.retry import retry_on_retryable
import httpx


class IDPProvider(BaseProvider):
    """Identity Provider (IDP) provider for user and access management."""
    
    def __init__(self, base_url: str, api_key: str, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.base_url = base_url
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0
        )
    
    @retry_on_retryable(max_attempts=3)
    async def create_user(self, email: str, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Create user in IDP."""
        try:
            response = await self.client.post(
                "/api/v1/users",
                json={"email": email, **attributes}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                raise PermanentError(f"User already exists: {email}")
            elif e.response.status_code >= 500:
                raise RetryableError(f"IDP server error: {str(e)}")
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
    async def add_to_group(self, user_id: str, group_id: str) -> bool:
        """Add user to group."""
        try:
            response = await self.client.post(
                f"/api/v1/groups/{group_id}/members",
                json={"user_id": user_id}
            )
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"IDP server error: {str(e)}")
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

