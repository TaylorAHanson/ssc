"""
GitHub provider client.
"""
from typing import Dict, Any, Optional, List
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError, PermanentError
from app.core.retry import retry_on_retryable
import base64
import httpx
import subprocess
import asyncio
import re


def _slugify_team(name: str) -> str:
    """Approximate GitHub's team-slug derivation (lowercase, non-alnum -> hyphen).

    Used only as a best-effort fallback to re-fetch a team when creation returns
    422 (already exists); GitHub itself is the source of truth for the real slug.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    return slug.strip("-")


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

    # ------------------------------------------------------------------
    # Org team management (requires an org token with admin:org / org
    # Members: Read & write). Teams are org-scoped, so ``self.org`` is
    # required for every call below.
    # ------------------------------------------------------------------
    def _require_org(self, op: str) -> str:
        if not self.org:
            raise PermanentError(f"{op} requires an organization (GITHUB_ORG is not set).")
        return self.org

    @retry_on_retryable(max_attempts=3)
    async def create_team(self, name: str, description: Optional[str] = None,
                          privacy: str = "closed") -> Dict[str, Any]:
        """Create an org team (idempotent: returns the existing team on 422).

        Returns the team object (notably ``slug``, used by the other team calls).
        """
        org = self._require_org("create_team")
        try:
            payload: Dict[str, Any] = {"name": name, "privacy": privacy}
            if description:
                payload["description"] = description
            response = await self.client.post(f"/orgs/{org}/teams", json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            # 422 => a team with this name already exists. Treat as success and
            # fetch it so the flow is idempotent on re-run.
            if e.response.status_code == 422:
                slug = _slugify_team(name)
                existing = await self.client.get(f"/orgs/{org}/teams/{slug}")
                if existing.status_code == 200:
                    return existing.json()
                raise PermanentError(f"Team '{name}' exists but could not be resolved: {existing.text}")
            if e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            raise PermanentError(f"Failed to create team '{name}': {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    @retry_on_retryable(max_attempts=3)
    async def grant_team_repo(self, team_slug: str, repo: str,
                              permission: str = "push") -> bool:
        """Give a team a permission level on a repo.

        ``permission`` is one of pull|triage|push|maintain|admin.
        """
        org = self._require_org("grant_team_repo")
        try:
            response = await self.client.put(
                f"/orgs/{org}/teams/{team_slug}/repos/{org}/{repo}",
                json={"permission": permission},
            )
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            raise PermanentError(f"Failed to grant team '{team_slug}' access to '{repo}': {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    @retry_on_retryable(max_attempts=3)
    async def add_team_member(self, team_slug: str, username: str,
                              role: str = "member") -> Dict[str, Any]:
        """Add/update a single user's membership in a team (``role``: member|maintainer).

        For org members this is immediate; for non-members GitHub sends an org
        invitation the user must accept.
        """
        org = self._require_org("add_team_member")
        try:
            response = await self.client.put(
                f"/orgs/{org}/teams/{team_slug}/memberships/{username}",
                json={"role": role},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            raise PermanentError(f"Failed to add '{username}' to team '{team_slug}': {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    async def add_team_members(self, team_slug: str, members: List[str],
                               role: str = "member") -> List[Dict[str, Any]]:
        """Add several users to a team; returns a per-member result list."""
        results: List[Dict[str, Any]] = []
        for username in members:
            try:
                res = await self.add_team_member(team_slug, username, role)
                results.append({"username": username, "state": res.get("state", "active"), "ok": True})
            except PermanentError as e:
                results.append({"username": username, "ok": False, "error": str(e)})
        return results

    async def list_teams(self) -> List[Dict[str, Any]]:
        """List org teams (name, slug, description, permission)."""
        org = self._require_org("list_teams")
        try:
            response = await self.client.get(f"/orgs/{org}/teams", params={"per_page": 100})
            response.raise_for_status()
            teams = response.json()
            return [
                {
                    "name": t.get("name"),
                    "slug": t.get("slug"),
                    "description": t.get("description"),
                    "privacy": t.get("privacy"),
                }
                for t in teams
            ]
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            raise PermanentError(f"Failed to list teams for org '{org}': {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    async def _resolve_repo_path(self, repo: str) -> str:
        """Resolve a repo reference to a full ``owner/repo`` path.

        Accepts either a bare repo name (resolved against ``self.org`` or the
        authenticated user) or an already-qualified ``owner/repo`` string.
        """
        if "/" in repo:
            return repo
        if self.org:
            return f"{self.org}/{repo}"
        user_resp = await self.client.get("/user")
        user_resp.raise_for_status()
        return f"{user_resp.json()['login']}/{repo}"

    @retry_on_retryable(max_attempts=3)
    async def create_branch(self, repo: str, branch: str, from_branch: str) -> Dict[str, Any]:
        """Create a new branch in ``repo`` pointing at the head of ``from_branch``."""
        try:
            repo_path = await self._resolve_repo_path(repo)

            ref_resp = await self.client.get(f"/repos/{repo_path}/git/ref/heads/{from_branch}")
            ref_resp.raise_for_status()
            from_sha = ref_resp.json()["object"]["sha"]

            response = await self.client.post(
                f"/repos/{repo_path}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": from_sha},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                # The branch may already exist from a prior (partially failed)
                # attempt. Treat creation as idempotent and return it if present.
                existing = await self.client.get(
                    f"/repos/{repo_path}/git/ref/heads/{branch}"
                )
                if existing.status_code == 200:
                    return existing.json()
                raise PermanentError(f"Invalid branch: {branch}")
            elif e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            else:
                raise PermanentError(f"Failed to create branch: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    @retry_on_retryable(max_attempts=3)
    async def create_or_update_file(
        self,
        repo: str,
        path: str,
        content: str,
        branch: str,
        message: str,
    ) -> Dict[str, Any]:
        """Create or update a file at ``path`` on ``branch`` via the Contents API."""
        try:
            repo_path = await self._resolve_repo_path(repo)
            encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")

            payload: Dict[str, Any] = {
                "message": message,
                "content": encoded,
                "branch": branch,
            }

            # If the file already exists on this branch we must pass its blob sha.
            existing = await self.client.get(
                f"/repos/{repo_path}/contents/{path}", params={"ref": branch}
            )
            if existing.status_code == 200:
                payload["sha"] = existing.json().get("sha")

            response = await self.client.put(
                f"/repos/{repo_path}/contents/{path}", json=payload
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            else:
                error_detail = e.response.json().get("message", str(e))
                raise PermanentError(f"Failed to write file '{path}': {error_detail}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    @retry_on_retryable(max_attempts=3)
    async def create_pull_request(
        self,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
    ) -> Dict[str, Any]:
        """Open a pull request from ``head`` into ``base``."""
        try:
            repo_path = await self._resolve_repo_path(repo)
            response = await self.client.post(
                f"/repos/{repo_path}/pulls",
                json={"title": title, "head": head, "base": base, "body": body},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                # A PR for this branch may already exist from a prior attempt;
                # return it so the flow is idempotent on retry.
                owner = repo_path.split("/")[0]
                existing = await self.client.get(
                    f"/repos/{repo_path}/pulls",
                    params={"head": f"{owner}:{head}", "state": "all"},
                )
                if existing.status_code == 200 and existing.json():
                    return existing.json()[0]
                error_detail = e.response.json().get("message", str(e))
                raise PermanentError(f"Failed to create pull request: {error_detail}")
            elif e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            else:
                error_detail = e.response.json().get("message", str(e))
                raise PermanentError(f"Failed to create pull request: {error_detail}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    async def get_pull_request(self, repo: str, number: int) -> Dict[str, Any]:
        """Fetch a pull request's current state (``state``, ``merged``, etc.)."""
        try:
            repo_path = await self._resolve_repo_path(repo)
            response = await self.client.get(f"/repos/{repo_path}/pulls/{number}")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            else:
                raise PermanentError(f"Failed to get pull request #{number}: {str(e)}")
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

