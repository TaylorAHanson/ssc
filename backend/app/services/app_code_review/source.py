"""Turn whatever the requester pasted into an exact ``owner/repo`` + commit SHA.

Requesters paste repo URLs, ``/tree/<branch>/<dir>`` links, file links, PR
links, commit links, SSH remotes, ``owner/repo`` or a bare repo name. All of
them resolve to one pinned commit, so the report always names exactly which
code was reviewed, even if the branch moves afterwards.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote, urlparse

from app.core.exceptions import PermanentError
from app.services.app_code_review.models import ResolvedSource

_SSH_RE = re.compile(r"^git@(?P<host>[^:]+):(?P<path>.+)$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass
class ParsedRepo:
    owner: Optional[str]
    repo: str
    # Segments after /tree/ or /blob/: a ref (which may itself contain "/")
    # followed by a path. Split apart during resolution, against GitHub.
    ref_path: List[str] = field(default_factory=list)
    is_blob: bool = False
    pr_number: Optional[int] = None
    commit: Optional[str] = None
    ref: Optional[str] = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}" if self.owner else self.repo


def _clean_repo(name: str) -> str:
    name = name[:-4] if name.endswith(".git") else name
    if not _NAME_RE.match(name) or name in (".", ".."):
        raise PermanentError(f"'{name}' isn't a valid GitHub repository name.")
    return name


def _clean_owner(name: str) -> str:
    if not _NAME_RE.match(name) or name in (".", ".."):
        raise PermanentError(f"'{name}' isn't a valid GitHub owner.")
    return name


def parse_repo_input(raw: str, allowed_host: str = "github.com") -> ParsedRepo:
    """Parse a repo reference without calling GitHub. Raises ``PermanentError``."""
    text = (raw or "").strip().rstrip("/")
    if not text:
        raise PermanentError("No repository was given.")

    ssh = _SSH_RE.match(text)
    if ssh:
        if ssh.group("host").lower() != allowed_host:
            raise PermanentError(f"Only {allowed_host} repositories can be reviewed.")
        text = f"https://{allowed_host}/{ssh.group('path')}"

    if "://" not in text and text.lower().startswith(allowed_host + "/"):
        text = "https://" + text

    if "://" in text:
        url = urlparse(text)
        host = (url.hostname or "").lower()
        if host not in (allowed_host, "www." + allowed_host):
            raise PermanentError(f"Only {allowed_host} repositories can be reviewed.")
        parts = [p for p in url.path.split("/") if p]
        if len(parts) < 2:
            raise PermanentError(f"'{raw}' doesn't point at a repository.")
        parsed = ParsedRepo(owner=_clean_owner(parts[0]), repo=_clean_repo(parts[1]))
        rest = parts[2:]
        if rest and rest[0] in ("tree", "blob") and len(rest) > 1:
            parsed.ref_path = rest[1:]
            parsed.is_blob = rest[0] == "blob"
        elif rest and rest[0] == "pull" and len(rest) > 1 and rest[1].isdigit():
            parsed.pr_number = int(rest[1])
        elif rest and rest[0] == "commit" and len(rest) > 1:
            parsed.commit = rest[1]
        elif rest[:2] == ["releases", "tag"] and len(rest) > 2:
            parsed.ref = "/".join(rest[2:])
        return parsed

    # owner/repo, owner/repo@ref, or a bare repo name (resolved against the org).
    ref = None
    if "@" in text:
        text, ref = text.split("@", 1)
    parts = text.split("/")
    if len(parts) == 1:
        return ParsedRepo(owner=None, repo=_clean_repo(parts[0]), ref=ref or None)
    if len(parts) == 2 and parts[0]:
        return ParsedRepo(owner=_clean_owner(parts[0]), repo=_clean_repo(parts[1]), ref=ref or None)
    raise PermanentError(f"Couldn't read '{raw}' as a GitHub repository.")


async def resolve_source(
    github,
    raw: str,
    ref: Optional[str] = None,
    app_path: Optional[str] = None,
    web_base_url: str = "https://github.com",
) -> ResolvedSource:
    """Resolve ``raw`` to a pinned commit via the GitHub provider.

    ``ref`` / ``app_path`` are used only when the input doesn't already pin
    them (a PR, commit or tree link wins, since that's what the requester
    pointed at).
    """
    host = (urlparse(web_base_url).hostname or "github.com").lower()
    parsed = parse_repo_input(raw, allowed_host=host)
    repo_meta = await github.get_repo(parsed.full_name)
    full_name = repo_meta.get("full_name") or parsed.full_name
    subpath = ""

    if parsed.pr_number is not None:
        pr = await github.get_pull_request(full_name, parsed.pr_number)
        head = pr.get("head") or {}
        head_repo = (head.get("repo") or {}).get("full_name")
        if not head.get("sha") or not head_repo:
            raise PermanentError(
                f"PR #{parsed.pr_number} in {full_name} has no readable head commit "
                f"(its source branch or fork may have been deleted)."
            )
        sha, ref_label = head["sha"], f"PR #{parsed.pr_number} ({head.get('ref', 'head')})"
        full_name = head_repo
    elif parsed.commit:
        sha = await github.resolve_commit_sha(full_name, parsed.commit)
        if not sha:
            raise PermanentError(f"Commit '{parsed.commit}' was not found in {full_name}.")
        ref_label = sha[:7]
    elif parsed.ref_path:
        # Branch names can contain "/", so try the shortest prefix first: the
        # first one that names a commit is the ref and the rest is the path.
        sha = None
        for i in range(1, len(parsed.ref_path) + 1):
            candidate = "/".join(parsed.ref_path[:i])
            sha = await github.resolve_commit_sha(full_name, candidate)
            if sha:
                ref_label = candidate
                remainder = parsed.ref_path[i:]
                if parsed.is_blob and remainder:
                    remainder = remainder[:-1]  # a file link reviews its directory
                subpath = "/".join(remainder)
                break
        if not sha:
            raise PermanentError(
                f"'{'/'.join(parsed.ref_path)}' doesn't start with a branch, tag or "
                f"commit in {full_name}."
            )
    else:
        wanted = parsed.ref or ref or repo_meta.get("default_branch") or "main"
        sha = await github.resolve_commit_sha(full_name, wanted)
        if not sha:
            raise PermanentError(f"'{wanted}' is not a branch, tag or commit in {full_name}.")
        ref_label = wanted if not _SHA_RE.match(wanted) else wanted[:7]

    if not subpath and app_path:
        subpath = app_path
    subpath = posixpath.normpath(subpath.strip("/")) if subpath else ""
    if subpath in (".", "") or subpath.startswith(".."):
        subpath = ""

    base = web_base_url.rstrip("/")
    html_url = f"{base}/{full_name}/tree/{sha}" + (f"/{quote(subpath)}" if subpath else "")
    return ResolvedSource(
        full_name=full_name, sha=sha, ref_label=ref_label, subpath=subpath, html_url=html_url
    )
