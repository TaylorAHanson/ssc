"""
GitHub provider for repository operations.

Authentication is **GitHub App only** — every call uses a short-lived
installation access token minted from the App's private key (see
``app_auth.generate_github_app_token_with_expiry``). There is no personal
access token, and the provider never calls ``GET /user`` (installation tokens
are not user identities); the operating owner is always the configured ``org``.
"""
import asyncio
import base64
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.core.exceptions import PermanentError, RetryableError
from app.core.retry import retry_on_retryable
from app.providers.base import BaseProvider
from app.providers.github.app_auth import generate_github_app_token_with_expiry

logger = logging.getLogger(__name__)


class GitHubProvider(BaseProvider):
    """GitHub repository operations authenticated as a GitHub App installation."""

    def __init__(
        self,
        *,
        org: str,
        app_id: str,
        private_key: str,
        installation_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config)
        if not org:
            raise PermanentError("GitHubProvider requires an org (GITHUB_ORG).")
        self.org = org
        self._app_id = app_id
        self._private_key = private_key
        self._installation_id = installation_id or None

        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

        self.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={"Accept": "application/vnd.github+json"},
            timeout=30.0,
        )

    @classmethod
    def from_settings(cls, config: Optional[Dict[str, Any]] = None) -> "GitHubProvider":
        """Build a provider from app settings (org + GitHub App credentials)."""
        from app.core.config import settings

        return cls(
            org=settings.GITHUB_ORG,
            app_id=settings.GITHUB_APP_ID,
            private_key=settings.get_github_app_private_key(),
            installation_id=settings.GITHUB_APP_INSTALLATION_ID or None,
            config=config,
        )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    async def _ensure_token(self) -> str:
        """Mint/refresh the installation token and apply it to the client.

        Cached with a 5-minute refresh buffer. Minting is sync (JWT + REST), so
        it runs in a thread to avoid blocking the event loop.
        """
        if self._token and time.time() < (self._token_expires_at - 300):
            return self._token

        self._token, self._token_expires_at = await asyncio.to_thread(
            generate_github_app_token_with_expiry,
            self._app_id,
            self._private_key,
            self._installation_id,
        )
        self.client.headers["Authorization"] = f"token {self._token}"
        logger.info("Minted GitHub App installation token (org=%s)", self.org)
        return self._token

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Issue an authenticated request, ensuring a fresh installation token."""
        await self._ensure_token()
        return await self.client.request(method, url, **kwargs)

    def _repo_path(self, repo: str) -> str:
        """Resolve a bare repo name against the org, or pass through ``owner/repo``."""
        return repo if "/" in repo else f"{self.org}/{repo}"

    # ------------------------------------------------------------------
    # Repository lifecycle
    # ------------------------------------------------------------------
    @retry_on_retryable(max_attempts=3)
    async def create_repo(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create a repository in the org."""
        try:
            response = await self._request(
                "POST", f"/orgs/{self.org}/repos", json={"name": name, **config}
            )
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
        """Check if a repository exists in the org."""
        try:
            response = await self._request("GET", f"/repos/{self._repo_path(name)}")
            return response.status_code == 200
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return False
            raise PermanentError(f"Failed to check repo existence: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    async def list_templates(self) -> List[Dict[str, Any]]:
        """List all repositories in the org marked as templates."""
        try:
            response = await self._request(
                "GET", f"/orgs/{self.org}/repos", params={"type": "all", "per_page": 100}
            )
            response.raise_for_status()
            repos = response.json()
            return [
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
                    "owner": repo["owner"]["login"],
                }
                for repo in repos
                if repo.get("is_template")
            ]
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            else:
                raise PermanentError(f"Failed to list templates: {str(e)}")
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    @retry_on_retryable(max_attempts=3)
    async def create_from_template(
        self, template: str, name: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a repository from a template repo, owned by the org."""
        try:
            template_path = template if "/" in template else f"{self.org}/{template}"
            payload = {"name": name, "owner": self.org, **config}
            response = await self._request(
                "POST", f"/repos/{template_path}/generate", json=payload
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise RetryableError(f"GitHub server error: {str(e)}")
            else:
                error_detail = e.response.json().get("message", str(e))
                raise PermanentError(
                    f"Failed to create from template '{template}': {error_detail}"
                )
        except httpx.RequestError as e:
            raise RetryableError(f"Request error: {str(e)}")

    @retry_on_retryable(max_attempts=3)
    async def set_permissions(self, repo: str, user: str, permission: str) -> bool:
        """Add/update a collaborator's permission on a repo."""
        try:
            response = await self._request(
                "PUT",
                f"/repos/{self._repo_path(repo)}/collaborators/{user}",
                json={"permission": permission},
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
    # Branches / contents / PRs
    # ------------------------------------------------------------------
    @retry_on_retryable(max_attempts=3)
    async def create_branch(self, repo: str, branch: str, from_branch: str) -> Dict[str, Any]:
        """Create a new branch pointing at the head of ``from_branch``."""
        repo_path = self._repo_path(repo)
        try:
            ref_resp = await self._request(
                "GET", f"/repos/{repo_path}/git/ref/heads/{from_branch}"
            )
            ref_resp.raise_for_status()
            from_sha = ref_resp.json()["object"]["sha"]

            response = await self._request(
                "POST",
                f"/repos/{repo_path}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": from_sha},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                # Branch may already exist from a prior attempt; treat as idempotent.
                existing = await self._request(
                    "GET", f"/repos/{repo_path}/git/ref/heads/{branch}"
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
        self, repo: str, path: str, content: str, branch: str, message: str
    ) -> Dict[str, Any]:
        """Create or update a file at ``path`` on ``branch`` via the Contents API."""
        repo_path = self._repo_path(repo)
        try:
            encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
            payload: Dict[str, Any] = {"message": message, "content": encoded, "branch": branch}

            existing = await self._request(
                "GET", f"/repos/{repo_path}/contents/{path}", params={"ref": branch}
            )
            if existing.status_code == 200:
                payload["sha"] = existing.json().get("sha")

            response = await self._request(
                "PUT", f"/repos/{repo_path}/contents/{path}", json=payload
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
        self, repo: str, title: str, head: str, base: str, body: str = ""
    ) -> Dict[str, Any]:
        """Open a pull request from ``head`` into ``base``."""
        repo_path = self._repo_path(repo)
        try:
            response = await self._request(
                "POST",
                f"/repos/{repo_path}/pulls",
                json={"title": title, "head": head, "base": base, "body": body},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                # A PR for this branch may already exist; return it (idempotent).
                owner = repo_path.split("/")[0]
                existing = await self._request(
                    "GET",
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
        """Fetch a pull request's current state."""
        repo_path = self._repo_path(repo)
        try:
            response = await self._request("GET", f"/repos/{repo_path}/pulls/{number}")
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
        """Reachability/auth check that works for an installation token."""
        try:
            response = await self._request(
                "GET", "/installation/repositories", params={"per_page": 1}
            )
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()
