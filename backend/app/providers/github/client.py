"""
GitHub provider client.
"""
from typing import Dict, Any, Optional, List
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError, PermanentError
from app.core.retry import retry_on_retryable
import httpx
import subprocess
import asyncio


class GitHubProvider(BaseProvider):
    """GitHub provider for repository operations."""
    
    def __init__(self, token: str, org: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.token = token
        self.org = org
        self.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json"
            },
            timeout=30.0
        )
    
    @retry_on_retryable(max_attempts=3)
    async def create_repo(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create repository."""
        try:
            # First, check if self.org is actually the current user
            user_resp = await self.client.get("/user")
            user_resp.raise_for_status()
            current_user = user_resp.json()["login"]
            
            # If org is not set, or org is the same as current user, use /user/repos
            if not self.org or self.org.lower() == current_user.lower():
                url = "/user/repos"
            else:
                url = f"/orgs/{self.org}/repos"
                
            response = await self.client.post(url, json={"name": name, **config})
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                raise PermanentError(f"Repository already exists or invalid: {name}")
            elif e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            else:
                raise PermanentError(f"Failed to create repo: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")
    
    async def check_repo_exists(self, name: str) -> bool:
        """Check if repository exists."""
        try:
            if self.org:
                url = f"/repos/{self.org}/{name}"
            else:
                user_resp = await self.client.get("/user")
                user_resp.raise_for_status()
                login = user_resp.json()["login"]
                url = f"/repos/{login}/{name}"
                
            response = await self.client.get(url)
            return response.status_code == 200
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return False
            raise PermanentError(f"Failed to check repo existence: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    async def list_templates(self) -> List[Dict[str, Any]]:
        """List all repositories marked as templates in the organization."""
        try:
            # First, get the authenticated user's login
            user_resp = await self.client.get("/user")
            user_resp.raise_for_status()
            auth_user = user_resp.json()["login"]
            
            # If org is not set, or matches the auth user, use /user/repos to see private repos
            if not self.org or self.org.lower() == auth_user.lower():
                url = "/user/repos"
                response = await self.client.get(url, params={"type": "all", "per_page": 100})
            else:
                # Try org endpoint first
                url = f"/orgs/{self.org}/repos"
                response = await self.client.get(url, params={"type": "all", "per_page": 100})
                
                # If org endpoint fails with 404, try user endpoint (fallback to public repos)
                if response.status_code == 404:
                    url = f"/users/{self.org}/repos"
                    response = await self.client.get(url, params={"type": "all", "per_page": 100})
            
            response.raise_for_status()
            
            repos = response.json()
            # Filter for repositories where is_template is True
            templates = [
                {
                    "id": repo["id"],
                    "name": repo["name"],
                    "full_name": repo["full_name"],
                    "description": repo.get("description") or "No description",
                    "url": repo["html_url"],
                    "is_template": repo.get("is_template", False),
                    "tags": repo.get("topics", []),
                    "created_at": repo.get("created_at"),
                    "updated_at": repo.get("updated_at"),
                    "owner": repo["owner"]["login"]
                }
                for repo in repos if repo.get("is_template")
            ]
            
            return templates
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            else:
                raise PermanentError(f"Failed to list templates: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    @retry_on_retryable(max_attempts=3)
    async def create_from_template(self, template: str, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create repository from template."""
        try:
            # If template doesn't have an owner prefix and we have an org, use the org
            template_path = template
            if "/" not in template and self.org:
                template_path = f"{self.org}/{template}"
                
            url = f"/repos/{template_path}/generate"
            
            # If org is set and different from current user, we should specify it as the new owner
            user_resp = await self.client.get("/user")
            user_resp.raise_for_status()
            current_user = user_resp.json()["login"]
            
            payload = {"name": name, **config}
            if self.org and self.org != current_user:
                payload["owner"] = self.org
                
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            else:
                error_detail = e.response.json().get("message", str(e))
                raise PermanentError(f"Failed to create from template '{template}': {error_detail}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")
    
    async def run_shell_command(self, command: str, cwd: Optional[str] = None) -> Dict[str, Any]:
        """Execute GitHub CLI command."""
        try:
            process = await asyncio.create_subprocess_exec(
                *command.split(),
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"GITHUB_TOKEN": self.token, **asyncio.get_event_loop().get_environ()}
            )
            stdout, stderr = await process.communicate()
            return {
                "stdout": stdout.decode() if stdout else "",
                "stderr": stderr.decode() if stderr else "",
                "returncode": process.returncode
            }
        except Exception as e:
            raise RetryableError(f"Command execution failed: {str(e)}")
    
    @retry_on_retryable(max_attempts=3)
    async def set_permissions(self, repo: str, user: str, permission: str) -> bool:
        """Set repository permissions."""
        try:
            if self.org:
                repo_path = f"{self.org}/{repo}"
            else:
                # If no org, we need the authenticated user's login
                user_resp = await self.client.get("/user")
                user_resp.raise_for_status()
                login = user_resp.json()["login"]
                repo_path = f"{login}/{repo}"
                
            response = await self.client.put(
                f"/repos/{repo_path}/collaborators/{user}",
                json={"permission": permission}
            )
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            else:
                raise PermanentError(f"Failed to set permissions: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")
    
    async def health_check(self) -> bool:
        """Check if GitHub is accessible."""
        try:
            response = await self.client.get("/user")
            return response.status_code == 200
        except:
            return False
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

