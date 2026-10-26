"""
GitHub Git Data API client for the Context Catalog's Git sync.

The sync reads and writes whole trees in one commit, which the Contents API
(one commit per file) can't do, so this talks to the Git Data API directly:
refs, trees and commits. It needs no git binary, so it runs inside a Databricks
App. Calls are synchronous; callers run them off the event loop
(``asyncio.to_thread``).
"""
import base64
import io
import logging
import re
import tarfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)

_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitSyncError(Exception):
    """A Git operation failed; the message is safe to show an admin."""


@dataclass(frozen=True)
class RepoRef:
    """Where a repository lives: ``owner/repo`` plus its API and web base URLs."""

    full_name: str
    api_url: str
    web_url: str


def parse_repo(value: str) -> Optional[RepoRef]:
    """Parse ``owner/repo``, an https URL or an ``git@host:owner/repo`` address.

    github.com uses ``api.github.com``; any other host is treated as GitHub
    Enterprise Server, whose API lives under ``https://<host>/api/v3``.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    host = "github.com"
    path = raw
    if raw.startswith("git@"):
        host, _, path = raw[len("git@"):].partition(":")
    elif "://" in raw:
        parsed = urlparse(raw)
        host = parsed.hostname or host
        path = parsed.path
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    full_name = f"{parts[0]}/{parts[1]}"
    if not _OWNER_REPO_RE.match(full_name):
        return None
    if host.lower() in ("github.com", "www.github.com"):
        return RepoRef(full_name, "https://api.github.com", "https://github.com")
    return RepoRef(full_name, f"https://{host}/api/v3", f"https://{host}")


class GitHubRepoClient:
    """Minimal Git Data API surface the catalog sync needs."""

    def __init__(self, repo: RepoRef, token: str, timeout: float = 30.0,
                 transport: Optional[httpx.BaseTransport] = None):
        if not token:
            raise GitSyncError(
                "No GitHub token is configured for the Context Catalog Git sync. Set the "
                "CONTEXT_CATALOG_GIT_TOKEN_SECRET_KEY secret (or the shared GitHub PAT)."
            )
        self.repo = repo
        self._http = httpx.Client(
            base_url=repo.api_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "GitHubRepoClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------ transport

    def _url(self, suffix: str) -> str:
        return f"/repos/{self.repo.full_name}{suffix}"

    def _request(self, method: str, suffix: str, *, ok_missing: bool = False, **kwargs) -> Optional[httpx.Response]:
        try:
            response = self._http.request(method, self._url(suffix), **kwargs)
        except httpx.RequestError as e:
            raise GitSyncError(f"Could not reach GitHub ({self.repo.api_url}): {e}") from e
        if ok_missing and response.status_code == 404:
            return None
        if response.status_code < 400:
            return response
        raise GitSyncError(self._describe_error(response))

    def _describe_error(self, response: httpx.Response) -> str:
        status = response.status_code
        try:
            detail = (response.json() or {}).get("message") or ""
        except ValueError:
            detail = response.text[:200]
        name = self.repo.full_name
        if status == 401:
            return "GitHub rejected the configured token (401): it is missing, expired or revoked."
        if status == 403 and response.headers.get("x-ratelimit-remaining") == "0":
            return "GitHub API rate limit reached; the sync will retry later."
        if status == 403:
            return (f"GitHub refused access to '{name}' (403): {detail}. The token needs Contents "
                    f"read/write on the repo, and SSO authorization if the org enforces it.")
        if status == 404:
            return f"Repository '{name}' was not found, or the configured token can't read it."
        if status == 409 and "empty" in detail.lower():
            return (f"Repository '{name}' has no commits yet. Push an initial commit (a README is "
                    f"enough) to its default branch, then sync again.")
        return f"GitHub API error {status} on '{name}': {detail}"

    # ---------------------------------------------------------------- reads

    def get_branch_head(self, branch: str) -> Optional[Tuple[str, str]]:
        """``(commit_sha, tree_sha)`` at the tip of ``branch``, or ``None`` if it doesn't exist."""
        ref = self._request("GET", f"/git/ref/heads/{quote(branch, safe='/')}", ok_missing=True)
        if ref is None:
            return None
        commit_sha = ref.json()["object"]["sha"]
        return commit_sha, self.get_commit_tree(commit_sha)

    def get_commit_tree(self, commit_sha: str) -> str:
        commit = self._request("GET", f"/git/commits/{commit_sha}")
        return commit.json()["tree"]["sha"]

    def get_default_branch(self) -> str:
        repo = self._request("GET", "")
        return repo.json().get("default_branch") or "main"

    def list_blobs(self, tree_sha: str) -> Dict[str, str]:
        """Every file in the tree as ``path -> blob sha`` (paths from the repo root)."""
        response = self._request("GET", f"/git/trees/{tree_sha}", params={"recursive": "1"})
        body = response.json()
        if body.get("truncated"):
            raise GitSyncError(
                "The repository tree is too large for GitHub to list in one call. Move the catalog "
                "to a smaller repository."
            )
        return {e["path"]: e["sha"] for e in body.get("tree") or [] if e.get("type") == "blob"}

    def get_blob_text(self, sha: str) -> str:
        blob = self._request("GET", f"/git/blobs/{sha}").json()
        if blob.get("encoding") == "base64":
            return base64.b64decode(blob.get("content") or "").decode("utf-8")
        return blob.get("content") or ""

    def read_files(self, commit_sha: str, paths: Dict[str, str], tarball_threshold: int = 25) -> Dict[str, str]:
        """Text of ``paths`` (``path -> blob sha``) at ``commit_sha``.

        Few files are fetched blob by blob; many (a first sync) come from one
        tarball download, which keeps a large import inside the API rate limit.
        """
        if len(paths) <= tarball_threshold:
            return {path: self.get_blob_text(sha) for path, sha in paths.items()}
        wanted = set(paths)
        out: Dict[str, str] = {}
        response = self._request("GET", f"/tarball/{commit_sha}", follow_redirects=True, timeout=120.0)
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                # Members are prefixed with "<owner>-<repo>-<sha>/".
                _, _, rel = member.name.partition("/")
                if rel in wanted:
                    handle = archive.extractfile(member)
                    if handle is not None:
                        out[rel] = handle.read().decode("utf-8")
        missing = wanted - set(out)
        for path in missing:
            out[path] = self.get_blob_text(paths[path])
        return out

    # --------------------------------------------------------------- writes

    def create_branch(self, branch: str, from_sha: str) -> None:
        self._request("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": from_sha})

    def create_tree(self, base_tree: str, entries: List[Dict[str, Any]]) -> str:
        response = self._request("POST", "/git/trees", json={"base_tree": base_tree, "tree": entries})
        return response.json()["sha"]

    def create_commit(self, message: str, tree_sha: str, parent_sha: str,
                      author: Dict[str, str], committer: Dict[str, str]) -> str:
        response = self._request("POST", "/git/commits", json={
            "message": message,
            "tree": tree_sha,
            "parents": [parent_sha],
            "author": author,
            "committer": committer,
        })
        return response.json()["sha"]

    def update_branch(self, branch: str, commit_sha: str) -> bool:
        """Fast-forward ``branch`` to ``commit_sha``. ``False`` if it moved meanwhile."""
        try:
            response = self._http.patch(
                self._url(f"/git/refs/heads/{quote(branch, safe='/')}"),
                json={"sha": commit_sha, "force": False},
            )
        except httpx.RequestError as e:
            raise GitSyncError(f"Could not reach GitHub ({self.repo.api_url}): {e}") from e
        if response.status_code == 422:
            return False
        if response.status_code >= 400:
            raise GitSyncError(self._describe_error(response))
        return True
