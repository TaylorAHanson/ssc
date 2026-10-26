"""
Git integration for the Context Catalog.

Each environment syncs its catalog with one branch of a GitHub repository
(Admin -> Settings -> Context Catalog maps environments to branches). The
repository holds one folder per domain, nested the way the domain tree is::

    <base path>/
      gtm-operations/
        _domain.yml           # name, description, owners, reviewers, categories
        playbook.md           # YAML front matter + markdown body
        ssa/
          _domain.yml
          onboarding.md

Folder names are domain slugs and document file names follow their titles.
Uploaded files (docx, pptx, pdf) are committed as their extracted markdown.

Two directions:

* **Push.** A save in the app records a pending change; the poller then commits
  the catalog's new state to the environment's branch in one commit authored
  by the editor. A save never waits on Git, so an outage only delays the
  commit; the error and the queued changes show in the admin UI.
* **Pull.** "Sync from Git" and a scheduled poll apply what changed on the
  branch (merged pull requests, promotions) to the database.

Each sync is a three-way comparison against what the environment last agreed
with its branch on (``ContextCatalogGitStateModel``): a file that changed only
in the app is committed, one that changed only in Git is imported, and one that
changed in both is a conflict, which the app's save wins (the overwritten Git
version stays in the branch history and the conflict is listed in the status).
An environment whose branch row has push turned off (stage reads prod's branch)
never commits: it mirrors the branch, reverting any local edits.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import yaml
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.context_catalog import ContextChunkModel, ContextDocumentModel, ContextDomainModel
from app.db.context_catalog_git import ContextCatalogGitStateModel
from app.providers.context_catalog.git_repo import GitHubRepoClient, GitSyncError, RepoRef, parse_repo
from app.services.context_catalog_service import ContextCatalogService

logger = logging.getLogger(__name__)

DOMAIN_FILE = "_domain.yml"
DOC_SUFFIX = ".md"
MAX_PENDING = 200
MAX_CONFLICTS = 20

# Front-matter keys with a column behind them. Anything else in a file's front
# matter is kept in the sync state and written back, so it survives the trip.
_DOC_KEYS = ("id", "title", "status", "doc_type", "tags", "source_url", "source_filename", "owner", "created_by")
_DOMAIN_KEYS = ("name", "description", "domain_type", "primary_owner", "secondary_owner", "reviewers", "categories")

_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$\n?", re.S | re.M)
_STEM_RE = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class GitConfig:
    enabled: bool
    repo: Optional[RepoRef]
    environment: str
    branch: Optional[str]
    push_enabled: bool
    base_path: str
    problem: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.enabled and self.repo is not None and bool(self.branch) and not self.problem

    def full_path(self, rel: str) -> str:
        return f"{self.base_path}/{rel}" if self.base_path else rel

    @property
    def branch_url(self) -> Optional[str]:
        if not self.repo or not self.branch:
            return None
        url = f"{self.repo.web_url}/{self.repo.full_name}/tree/{self.branch}"
        return f"{url}/{self.base_path}" if self.base_path else url

    def commit_url(self, sha: Optional[str]) -> Optional[str]:
        if not self.repo or not sha:
            return None
        return f"{self.repo.web_url}/{self.repo.full_name}/commit/{sha}"


def current_environment() -> str:
    """The deployment's environment key (``ENVIRONMENT``, set per bundle target)."""
    return (settings.ENVIRONMENT or "").strip().lower()


def resolve_branch(environment: str, rows: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The branch-map row for ``environment`` (matched on its key or an alias)."""
    env = (environment or "").strip().lower()
    if not env:
        return None
    for row in rows or []:
        names = [str(row.get("environment") or "")]
        names += str(row.get("aliases") or "").split(",")
        if env in {n.strip().lower() for n in names if n.strip()}:
            branch = str(row.get("branch") or "").strip()
            if not branch:
                return None
            return {"environment": str(row.get("environment")).strip(), "branch": branch,
                    "push": bool(row.get("push"))}
    return None


def normalize_base_path(value: Optional[str]) -> str:
    parts = [p for p in (value or "").replace("\\", "/").split("/") if p and p != "."]
    return "/".join(parts)


def resolve_config() -> GitConfig:
    env = current_environment()
    enabled = bool(getattr(settings, "CONTEXT_CATALOG_GIT_ENABLED", False))
    repo = parse_repo(getattr(settings, "CONTEXT_CATALOG_GIT_REPO", "") or "")
    row = resolve_branch(env, getattr(settings, "CONTEXT_CATALOG_GIT_BRANCHES", []) or [])
    cfg = GitConfig(
        enabled=enabled,
        repo=repo,
        environment=env,
        branch=row["branch"] if row else None,
        push_enabled=bool(row and row["push"]),
        base_path=normalize_base_path(getattr(settings, "CONTEXT_CATALOG_GIT_BASE_PATH", "")),
    )
    if not enabled:
        cfg.problem = "Git sync is turned off (Admin → Settings → Context Catalog)."
    elif repo is None:
        cfg.problem = "No repository is configured, or it isn't in owner/repo or URL form."
    elif row is None:
        cfg.problem = f"No branch is mapped to the '{env or 'unset'}' environment in the branch map."
    return cfg


_token_cache: Dict[str, str] = {}


def resolve_token() -> str:
    """The GitHub token: a dedicated catalog token if configured, else the shared GitHub PAT.

    ``CONTEXT_CATALOG_GIT_TOKEN`` (local ``.env``) wins; otherwise
    ``CONTEXT_CATALOG_GIT_TOKEN_SECRET_KEY`` is read from the control-tower
    secret scope (``GIT_TOKEN_SECRET_SCOPE``).
    """
    if settings.CONTEXT_CATALOG_GIT_TOKEN:
        return settings.CONTEXT_CATALOG_GIT_TOKEN
    scope = settings.GIT_TOKEN_SECRET_SCOPE
    key = settings.CONTEXT_CATALOG_GIT_TOKEN_SECRET_KEY
    if scope and key and key != settings.GIT_TOKEN_SECRET_KEY:
        cache_key = f"{scope}/{key}"
        if cache_key not in _token_cache:
            try:
                from databricks.sdk import WorkspaceClient

                secret = WorkspaceClient().secrets.get_secret(scope=scope, key=key)
                _token_cache[cache_key] = base64.b64decode(secret.value or "").decode("utf-8").strip()
            except Exception as e:  # noqa: BLE001 - fall back to the shared PAT
                logger.warning("Context Catalog Git: could not read secret %s: %s", cache_key, e)
                _token_cache[cache_key] = ""
        if _token_cache[cache_key]:
            return _token_cache[cache_key]
    return settings.GITHUB_TOKEN or settings.get_git_token()


def direct_editing_enabled() -> bool:
    return bool(getattr(settings, "CONTEXT_CATALOG_DIRECT_EDITING_ENABLED", True))


def direct_editing_message() -> str:
    cfg = resolve_config()
    if cfg.repo and cfg.branch:
        return (
            "Direct editing of the Context Catalog is turned off in this environment. Make the change "
            f"in Git on the '{cfg.branch}' branch ({cfg.branch_url}); it is synced here automatically."
        )
    return ("Direct editing of the Context Catalog is turned off in this environment "
            "(Admin → Settings → Context Catalog).")


# ---------------------------------------------------------------------------
# Serialization: DB <-> files
# ---------------------------------------------------------------------------

def blob_sha(content: str) -> str:
    """Git's blob id for ``content``, so local and remote files compare without a download."""
    data = content.encode("utf-8")
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def _segment(slug: str) -> str:
    seg = (slug or "").replace("/", "-").replace("\\", "-").strip().lstrip(".")
    return seg or "domain"


def _file_stem(title: str) -> str:
    return _STEM_RE.sub("-", (title or "").strip().lower()).strip("-")[:80].strip("-") or "document"


def _normalize_body(body: Optional[str]) -> str:
    return (body or "").replace("\r\n", "\n").strip("\n")


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        value = [value]
    out: List[str] = []
    for item in value:
        text = _opt_str(item)
        if text and text not in out:
            out.append(text)
    return out


def _dump_yaml(data: Dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000)


def _has_column(model: Any, name: str) -> bool:
    return name in model.__table__.columns


def _extras(store: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = store.get(key)
    return dict(value) if isinstance(value, dict) else {}


def dump_domain(domain: ContextDomainModel, extras: Optional[Dict[str, Any]] = None) -> str:
    data: Dict[str, Any] = {"name": domain.name, "domain_type": domain.domain_type or "community"}
    for key in ("description", "primary_owner", "secondary_owner"):
        value = _opt_str(getattr(domain, key))
        if value:
            data[key] = value
    for key in ("reviewers", "categories"):
        values = _str_list(getattr(domain, key))
        if values:
            data[key] = values
    for key, value in (extras or {}).items():
        data.setdefault(key, value)
    return _dump_yaml(data)


def dump_document(doc: ContextDocumentModel, extras: Optional[Dict[str, Any]] = None) -> str:
    meta: Dict[str, Any] = {
        "id": doc.id,
        "title": doc.title,
        "status": doc.status or "published",
        "doc_type": doc.doc_type or "markdown",
    }
    tags = _str_list(doc.tags)
    if tags:
        meta["tags"] = tags
    for key in ("source_url", "source_filename", "owner", "created_by"):
        value = _opt_str(getattr(doc, key, None))
        if value:
            meta[key] = value
    for key, value in (extras or {}).items():
        meta.setdefault(key, value)
    body = _normalize_body(doc.body_markdown)
    text = f"---\n{_dump_yaml(meta)}---\n"
    return f"{text}\n{body}\n" if body else text


def parse_document(text: str, filename: str = "") -> Tuple[Dict[str, Any], str]:
    """Split a document file into ``(front matter, body)``. Raises ``ValueError`` on bad YAML."""
    text = (text or "").replace("\r\n", "\n")
    meta: Dict[str, Any] = {}
    body = text
    match = _FRONT_MATTER_RE.match(text)
    if match:
        try:
            loaded = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"invalid front matter: {e}") from e
        if not isinstance(loaded, dict):
            raise ValueError("front matter must be a YAML mapping")
        meta = loaded
        body = text[match.end():]
    body = _normalize_body(body)
    if not _opt_str(meta.get("title")):
        heading = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), "")
        stem = filename.rsplit("/", 1)[-1]
        stem = stem[: -len(DOC_SUFFIX)] if stem.lower().endswith(DOC_SUFFIX) else stem
        meta["title"] = heading or stem.replace("-", " ").replace("_", " ").strip() or "Untitled"
    return meta, body


def parse_domain(text: str) -> Dict[str, Any]:
    try:
        loaded = yaml.safe_load(text or "") or {}
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML: {e}") from e
    if not isinstance(loaded, dict):
        raise ValueError(f"{DOMAIN_FILE} must be a YAML mapping")
    return loaded


@dataclass
class Rendering:
    """The catalog as files: ``path -> content`` and ``path -> 'doc:<id>' | 'domain:<id>'``."""

    files: Dict[str, str] = field(default_factory=dict)
    index: Dict[str, str] = field(default_factory=dict)

    @property
    def shas(self) -> Dict[str, str]:
        return {p: blob_sha(c) for p, c in self.files.items()}


def domain_folders(domains: List[ContextDomainModel]) -> Dict[str, str]:
    """``domain id -> folder path`` mirroring the domain tree (cycles are cut at the repeat)."""
    by_id = {d.id: d for d in domains}
    out: Dict[str, str] = {}

    def resolve(domain: ContextDomainModel, seen: Set[str]) -> str:
        if domain.id in out:
            return out[domain.id]
        parent = by_id.get(domain.parent_id) if domain.parent_id else None
        if parent is None or parent.id in seen:
            path = _segment(domain.slug)
        else:
            path = f"{resolve(parent, seen | {domain.id})}/{_segment(domain.slug)}"
        out[domain.id] = path
        return path

    for d in domains:
        resolve(d, {d.id})
    return out


def document_filenames(docs: List[ContextDocumentModel]) -> Dict[str, str]:
    """``doc id -> file name``. Oldest document keeps the plain title; clashes get an id suffix."""
    used: Set[str] = set()
    out: Dict[str, str] = {}
    for doc in sorted(docs, key=lambda d: (d.created_at or datetime.min, d.id)):
        stem = _file_stem(doc.title)
        name = f"{stem}{DOC_SUFFIX}"
        if name in used:
            name = f"{stem}-{doc.id[:8]}{DOC_SUFFIX}"
        used.add(name)
        out[doc.id] = name
    return out


def render_catalog(db: Session, extras: Optional[Dict[str, Any]] = None) -> Rendering:
    extras = extras or {}
    domains = db.query(ContextDomainModel).all()
    folders = domain_folders(domains)
    docs_by_domain: Dict[str, List[ContextDocumentModel]] = {}
    for doc in db.query(ContextDocumentModel).all():
        docs_by_domain.setdefault(doc.domain_id, []).append(doc)
    out = Rendering()
    for domain in domains:
        folder = folders[domain.id]
        path = f"{folder}/{DOMAIN_FILE}"
        out.files[path] = dump_domain(domain, _extras(extras, f"domain:{domain.id}"))
        out.index[path] = f"domain:{domain.id}"
        docs = docs_by_domain.get(domain.id, [])
        names = document_filenames(docs)
        for doc in docs:
            doc_path = f"{folder}/{names[doc.id]}"
            out.files[doc_path] = dump_document(doc, _extras(extras, f"doc:{doc.id}"))
            out.index[doc_path] = f"doc:{doc.id}"
    return out


def is_catalog_path(rel: str) -> bool:
    parts = rel.split("/")
    if len(parts) < 2 or any(not p or p.startswith(".") for p in parts):
        return False
    return parts[-1] == DOMAIN_FILE or parts[-1].lower().endswith(DOC_SUFFIX)


def catalog_files(blobs: Dict[str, str], base_path: str) -> Dict[str, str]:
    """Catalog files of a repo tree, keyed by path relative to ``base_path``."""
    prefix = f"{base_path}/" if base_path else ""
    out: Dict[str, str] = {}
    for path, sha in blobs.items():
        if prefix and not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        if is_catalog_path(rel):
            out[rel] = sha
    return out


# ---------------------------------------------------------------------------
# Applying files to the database
# ---------------------------------------------------------------------------

@dataclass
class ApplyReport:
    domains_created: List[str] = field(default_factory=list)
    domains_updated: List[str] = field(default_factory=list)
    domains_deleted: List[str] = field(default_factory=list)
    documents_created: List[str] = field(default_factory=list)
    documents_updated: List[str] = field(default_factory=list)
    documents_deleted: List[str] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any((self.domains_created, self.domains_updated, self.domains_deleted,
                    self.documents_created, self.documents_updated, self.documents_deleted))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "domains": {"created": self.domains_created, "updated": self.domains_updated,
                        "deleted": self.domains_deleted},
            "documents": {"created": self.documents_created, "updated": self.documents_updated,
                          "deleted": self.documents_deleted},
            "errors": self.errors,
        }


def _folder_of(path: str) -> str:
    return path.rsplit("/", 1)[0]


def _ancestor_folders(paths: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    for path in paths:
        parts = path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            out.add("/".join(parts[:i]))
    return out


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title() or slug


class _Applier:
    """Applies Git files to the database inside the caller's transaction (no commit)."""

    def __init__(self, db: Session, extras: Dict[str, Any], actor: str):
        self.db = db
        self.extras = extras
        self.actor = actor
        self.report = ApplyReport()
        self.folders: Dict[str, ContextDomainModel] = {}
        self.seen_docs: Set[str] = set()
        self.index: Dict[str, str] = {}
        self.storage_paths: List[str] = []

    # --- domains

    def _is_ancestor(self, candidate_id: str, domain_id: str) -> bool:
        """True if ``domain_id`` is ``candidate_id`` or one of its ancestors' descendants."""
        current = candidate_id
        seen: Set[str] = set()
        while current and current not in seen:
            if current == domain_id:
                return True
            seen.add(current)
            node = self.db.get(ContextDomainModel, current)
            current = node.parent_id if node else None
        return False

    def ensure_domain(self, folder: str, meta: Optional[Dict[str, Any]] = None,
                      path: Optional[str] = None) -> ContextDomainModel:
        if folder in self.folders and meta is None:
            return self.folders[folder]
        parent_folder = folder.rsplit("/", 1)[0] if "/" in folder else None
        parent = self.ensure_domain(parent_folder) if parent_folder else None
        slug = folder.rsplit("/", 1)[-1]
        domain = self.db.query(ContextDomainModel).filter(ContextDomainModel.slug == slug).first()
        created = domain is None
        if created:
            domain = ContextDomainModel(
                id=str(uuid.uuid4()), slug=slug, name=_title_from_slug(slug), domain_type="community",
                reviewers=[], categories=[], created_by=self.actor,
            )
            self.db.add(domain)
            self.report.domains_created.append(slug)
        changed = False
        if meta is not None:
            values = {
                "name": _opt_str(meta.get("name")) or domain.name or _title_from_slug(slug),
                "description": _opt_str(meta.get("description")),
                "domain_type": _opt_str(meta.get("domain_type")) or "community",
                "primary_owner": _opt_str(meta.get("primary_owner")),
                "secondary_owner": _opt_str(meta.get("secondary_owner")),
                "reviewers": _str_list(meta.get("reviewers")),
                "categories": _str_list(meta.get("categories")),
            }
            for key, value in values.items():
                if getattr(domain, key) != value:
                    setattr(domain, key, value)
                    changed = True
            self.db.flush()
            extra = {k: v for k, v in meta.items() if k not in _DOMAIN_KEYS}
            self._set_extras(f"domain:{domain.id}", extra)
        new_parent = parent.id if parent else None
        if domain.parent_id != new_parent:
            self.db.flush()
            if new_parent and self._is_ancestor(new_parent, domain.id):
                self.report.errors.append({"path": folder, "error": "moving this domain would create a cycle"})
            else:
                domain.parent_id = new_parent
                changed = True
        if changed and not created and slug not in self.report.domains_updated:
            self.report.domains_updated.append(slug)
        self.db.flush()
        self.folders[folder] = domain
        if path:
            self.index[path] = f"domain:{domain.id}"
        return domain

    def _set_extras(self, key: str, extra: Dict[str, Any]) -> None:
        if extra:
            self.extras[key] = extra
        else:
            self.extras.pop(key, None)

    # --- documents

    def _rekey(self, doc: ContextDocumentModel, new_id: str) -> ContextDocumentModel:
        """Give ``doc`` the id from Git so every environment shares one id per document.

        Skipped when another table points at the document, since its id can't
        change under them.
        """
        from sqlalchemy import func, select

        from app.db.base import Base

        if self.db.get(ContextDocumentModel, new_id) is not None:
            return doc
        doc_table = ContextDocumentModel.__table__
        for table in Base.metadata.sorted_tables:
            if table.name == ContextChunkModel.__tablename__:
                continue
            for fk in table.foreign_keys:
                if fk.column.table is doc_table:
                    count = self.db.execute(
                        select(func.count()).select_from(table).where(fk.parent == doc.id)
                    ).scalar()
                    if count:
                        return doc
        values = {attr.key: getattr(doc, attr.key) for attr in ContextDocumentModel.__mapper__.column_attrs}
        values["id"] = new_id
        self.db.query(ContextChunkModel).filter(ContextChunkModel.document_id == doc.id).delete(
            synchronize_session=False
        )
        self.db.delete(doc)
        self.db.flush()
        replacement = ContextDocumentModel(**values)
        self.db.add(replacement)
        self.db.flush()
        return replacement

    def upsert_document(self, path: str, text: str) -> None:
        meta, body = parse_document(text, path)
        domain = self.ensure_domain(_folder_of(path))
        title = _opt_str(meta.get("title")) or "Untitled"
        doc_id = _opt_str(meta.get("id"))
        if doc_id and (not _ID_RE.match(doc_id) or doc_id in self.seen_docs):
            if doc_id in self.seen_docs:
                self.report.errors.append({"path": path, "error": f"id {doc_id} is used by another file; "
                                                                  "imported as a new document"})
            doc_id = None
        doc = self.db.get(ContextDocumentModel, doc_id) if doc_id else None
        if doc is None:
            doc = (
                self.db.query(ContextDocumentModel)
                .filter(ContextDocumentModel.domain_id == domain.id, ContextDocumentModel.title == title)
                .filter(~ContextDocumentModel.id.in_(self.seen_docs or {""}))
                .first()
            )
            if doc is not None and doc_id and doc.id != doc_id:
                doc = self._rekey(doc, doc_id)
        created = doc is None
        if created:
            doc = ContextDocumentModel(
                id=doc_id or str(uuid.uuid4()), domain_id=domain.id, title=title,
                created_by=_opt_str(meta.get("created_by")),
            )
            self.db.add(doc)
        status = _opt_str(meta.get("status")) or "published"
        values: Dict[str, Any] = {
            "domain_id": domain.id,
            "title": title,
            "body_markdown": body,
            "status": status if status in ("draft", "published") else "published",
            "doc_type": _opt_str(meta.get("doc_type")) or "markdown",
            "tags": _str_list(meta.get("tags")),
            "source_url": _opt_str(meta.get("source_url")),
            "source_filename": _opt_str(meta.get("source_filename")),
        }
        if _has_column(ContextDocumentModel, "owner"):
            from app.services.context_catalog_staleness import normalize_owner

            values["owner"] = normalize_owner(_opt_str(meta.get("owner")))
        changed_keys = set()
        for key, value in values.items():
            current = getattr(doc, key)
            if key == "body_markdown":
                current = _normalize_body(current)
            if created or current != value:
                setattr(doc, key, value)
                changed_keys.add(key)
        changed = created or bool(changed_keys)
        content_changed = created or bool(changed_keys & {"title", "body_markdown", "source_url", "doc_type"})
        if content_changed:
            if _has_column(ContextDocumentModel, "content_updated_at"):
                doc.content_updated_at = datetime.utcnow()
            if _has_column(ContextDocumentModel, "stale_notified_at"):
                doc.stale_notified_at = None
        self.db.flush()
        if changed:
            ContextCatalogService._rebuild_chunks(self.db, doc)
            (self.report.documents_created if created else self.report.documents_updated).append(path)
        known = set(_DOC_KEYS)
        if not _has_column(ContextDocumentModel, "owner"):
            known.discard("owner")
        self._set_extras(f"doc:{doc.id}", {k: v for k, v in meta.items() if k not in known})
        self.seen_docs.add(doc.id)
        self.index[path] = f"doc:{doc.id}"

    def delete_document(self, doc_id: str, label: str) -> None:
        if doc_id in self.seen_docs:
            return
        doc = self.db.get(ContextDocumentModel, doc_id)
        if doc is None:
            return
        if doc.storage_path:
            self.storage_paths.append(doc.storage_path)
        self.db.query(ContextChunkModel).filter(ContextChunkModel.document_id == doc_id).delete(
            synchronize_session=False
        )
        self.db.delete(doc)
        self.db.flush()
        self.extras.pop(f"doc:{doc_id}", None)
        self.report.documents_deleted.append(label)

    def delete_domain(self, domain_id: str, present_folders: Set[str]) -> None:
        domain = self.db.get(ContextDomainModel, domain_id)
        if domain is None or domain in self.folders.values():
            return
        if any(f.rsplit("/", 1)[-1] == domain.slug for f in present_folders):
            return
        has_docs = self.db.query(ContextDocumentModel).filter(ContextDocumentModel.domain_id == domain_id).count()
        has_children = self.db.query(ContextDomainModel).filter(ContextDomainModel.parent_id == domain_id).count()
        if has_docs or has_children:
            self.report.errors.append({
                "path": domain.slug,
                "error": "removed in Git but kept: it still has documents or sub-domains here",
            })
            return
        self.db.delete(domain)
        self.db.flush()
        self.extras.pop(f"domain:{domain_id}", None)
        self.report.domains_deleted.append(domain.slug)

    def _domain_depth(self, domain_id: str) -> int:
        depth, current, seen = 0, self.db.get(ContextDomainModel, domain_id), set()
        while current is not None and current.parent_id and current.id not in seen:
            seen.add(current.id)
            depth += 1
            current = self.db.get(ContextDomainModel, current.parent_id)
        return depth

    def apply(self, upserts: Dict[str, str], deletions: Iterable[str], present: Set[str],
              full: bool = False) -> None:
        """Upsert ``upserts`` (path -> text), then delete ``deletions`` ('doc:<id>' / 'domain:<id>').

        ``full`` mirrors the files exactly: anything in the database that no
        file defines is deleted as well.
        """
        domain_metas: Dict[str, Optional[Dict[str, Any]]] = {}
        folders: Set[str] = set()
        for path, text in upserts.items():
            folder = _folder_of(path)
            folders.add(folder)
            if path.endswith(f"/{DOMAIN_FILE}"):
                try:
                    domain_metas[folder] = parse_domain(text)
                except ValueError as e:
                    self.report.errors.append({"path": path, "error": str(e)})
        for folder in sorted(folders, key=lambda f: (f.count("/"), f)):
            meta = domain_metas.get(folder)
            self.ensure_domain(folder, meta, path=f"{folder}/{DOMAIN_FILE}" if meta is not None else None)
        for path in sorted(p for p in upserts if not p.endswith(f"/{DOMAIN_FILE}")):
            try:
                self.upsert_document(path, upserts[path])
            except ValueError as e:
                self.report.errors.append({"path": path, "error": str(e)})

        deletions = set(deletions)
        if full:
            deletions |= {f"doc:{i}" for (i,) in self.db.query(ContextDocumentModel.id).all()}
            deletions |= {f"domain:{i}" for (i,) in self.db.query(ContextDomainModel.id).all()}
        for key in sorted(k for k in deletions if k.startswith("doc:")):
            doc_id = key[len("doc:"):]
            doc = self.db.get(ContextDocumentModel, doc_id)
            self.delete_document(doc_id, doc.title if doc else doc_id)
        present_folders = _ancestor_folders(present)
        domain_ids = [k[len("domain:"):] for k in deletions if k.startswith("domain:")]
        for domain_id in sorted(domain_ids, key=self._domain_depth, reverse=True):
            self.delete_domain(domain_id, present_folders)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

@dataclass
class SyncPlan:
    push: Set[str] = field(default_factory=set)
    pull: Set[str] = field(default_factory=set)
    conflicts: Set[str] = field(default_factory=set)
    full_mirror: bool = False


def plan_sync(local: Dict[str, str], remote: Dict[str, str], synced_tree: Dict[str, str],
              synced_render: Dict[str, str], *, push_enabled: bool, bootstrap: bool) -> SyncPlan:
    """Decide which files to commit and which to import.

    ``local`` / ``remote`` are ``path -> sha`` for the app's rendering and the
    branch; ``synced_*`` are the same at the last sync.
    """
    if not push_enabled:
        local_drift = {p for p in set(local) | set(synced_render) if local.get(p) != synced_render.get(p)}
        if bootstrap or local_drift:
            return SyncPlan(pull=set(remote), full_mirror=True)
        changed = {p for p in set(remote) | set(synced_tree) if remote.get(p) != synced_tree.get(p)}
        # Deletions always apply: a mirrored file may live under a name the app wouldn't pick.
        return SyncPlan(pull={p for p in changed if p not in remote or local.get(p) != remote.get(p)})
    if bootstrap:
        # First sync: adopt what the branch already has; app-only files follow on the next pass.
        return SyncPlan(pull={p for p in remote if local.get(p) != remote[p]})
    paths = set(local) | set(remote) | set(synced_tree) | set(synced_render)
    changed_here = {p for p in paths if local.get(p) != synced_render.get(p)}
    changed_there = {p for p in paths if remote.get(p) != synced_tree.get(p)}
    conflicts = {p for p in changed_here & changed_there if local.get(p) != remote.get(p)}
    push = {p for p in changed_here if local.get(p) != remote.get(p)}
    pull = {p for p in changed_there - conflicts if local.get(p) != remote.get(p)}
    return SyncPlan(push=push, pull=pull, conflicts=conflicts)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.utcnow()


def _load_state(db: Session, cfg: GitConfig) -> ContextCatalogGitStateModel:
    state = db.get(ContextCatalogGitStateModel, cfg.environment)
    if state is None:
        state = ContextCatalogGitStateModel(environment=cfg.environment)
        db.add(state)
    target = (cfg.repo.full_name if cfg.repo else None, cfg.branch, cfg.base_path)
    if (state.repo, state.branch, state.base_path) != target:
        # Pointed at a different repo, branch or folder: start over with a first sync.
        state.repo, state.branch, state.base_path = target
        state.last_synced_sha = None
        state.synced_tree = None
        state.synced_render = None
        state.synced_index = None
    return state


def record_change(db: Session, user: Any, action: str, subject: str) -> None:
    """Queue a save for the next commit. Never raises: Git must not block a save."""
    try:
        cfg = resolve_config()
        if not cfg.active or not cfg.push_enabled:
            return
        state = _load_state(db, cfg)
        pending = list(state.pending_changes or [])
        pending.append({
            "id": uuid.uuid4().hex,
            "action": action,
            "subject": subject,
            "email": getattr(user, "email", None),
            "name": getattr(user, "full_name", None),
            "at": _utcnow().isoformat(),
        })
        state.pending_changes = pending[-MAX_PENDING:]
        state.pending_since = state.pending_since or _utcnow()
        db.commit()
    except Exception as e:  # noqa: BLE001 - the save already succeeded
        logger.warning("Context Catalog Git: could not queue change for commit: %s", e)
        db.rollback()


def has_pending_changes(db: Session) -> bool:
    cfg = resolve_config()
    if not cfg.active or not cfg.push_enabled:
        return False
    state = db.get(ContextCatalogGitStateModel, cfg.environment)
    return bool(state and state.pending_changes)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def _commit_identity(pending: List[Dict[str, Any]]) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    bot = {"name": settings.GIT_USERNAME, "email": settings.GIT_EMAIL}
    people: List[Dict[str, str]] = []
    for change in reversed(pending):
        email = _opt_str(change.get("email"))
        if not email or any(p["email"] == email for p in people):
            continue
        people.append({"name": _opt_str(change.get("name")) or email.split("@")[0], "email": email})
    if not people:
        return bot, []
    return people[0], people[1:]


def build_commit_message(cfg: GitConfig, pending: List[Dict[str, Any]], push: Set[str],
                         local: Dict[str, str], remote: Dict[str, str],
                         coauthors: List[Dict[str, str]], normalized: bool = False) -> str:
    env = cfg.environment or "app"
    if len(pending) == 1:
        headline = f"Context Catalog ({env}): {pending[0]['action'].lower()} {pending[0]['subject']}"
    elif pending:
        headline = f"Context Catalog ({env}): {len(pending)} changes"
    elif normalized:
        headline = f"Context Catalog ({env}): normalize {len(push)} file(s) imported from Git"
    else:
        headline = f"Context Catalog ({env}): sync {len(push)} file(s) from the app"
    lines = [headline[:120], ""]
    if pending:
        lines.append(f"Saved in the {env} environment:")
        for change in pending[:50]:
            who = change.get("email") or "unknown"
            lines.append(f"- {change['action']} {change['subject']} ({who})")
        if len(pending) > 50:
            lines.append(f"- ...and {len(pending) - 50} more")
        lines.append("")
    lines.append("Files:")
    for path in sorted(push)[:100]:
        mark = "D" if path not in local else ("A" if path not in remote else "M")
        lines.append(f"  {mark} {cfg.full_path(path)}")
    if len(push) > 100:
        lines.append(f"  ...and {len(push) - 100} more")
    if coauthors:
        lines.append("")
        lines.extend(f"Co-authored-by: {p['name']} <{p['email']}>" for p in coauthors)
    return "\n".join(lines) + "\n"


def _pass(db: Session, cfg: GitConfig, client: GitHubRepoClient, state: ContextCatalogGitStateModel,
          *, bootstrap: bool, actor: str) -> Dict[str, Any]:
    """One push+pull pass. Retries when the branch moves under the commit."""
    for _attempt in range(3):
        head = client.get_branch_head(cfg.branch)
        if head is None:
            if not cfg.push_enabled:
                raise GitSyncError(f"Branch '{cfg.branch}' does not exist in {cfg.repo.full_name}.")
            default = client.get_default_branch()
            base_head = client.get_branch_head(default)
            if base_head is None:
                raise GitSyncError(f"Neither '{cfg.branch}' nor the default branch '{default}' exists.")
            client.create_branch(cfg.branch, base_head[0])
            logger.info("Context Catalog Git: created branch %s from %s", cfg.branch, default)
            head = base_head
        head_sha, head_tree = head
        remote = catalog_files(client.list_blobs(head_tree), cfg.base_path)
        extras = dict(state.front_matter_extras or {})
        rendering = render_catalog(db, extras)
        local = rendering.shas
        synced_tree = dict(state.synced_tree or {})
        synced_render = dict(state.synced_render or {})
        synced_index = dict(state.synced_index or {})
        plan = plan_sync(local, remote, synced_tree, synced_render,
                         push_enabled=cfg.push_enabled, bootstrap=bootstrap)

        pending = list(state.pending_changes or [])
        final_sha, final_tree = head_sha, dict(remote)
        pushed: Optional[str] = None
        if plan.push:
            entries = []
            for path in sorted(plan.push):
                entry = {"path": cfg.full_path(path), "mode": "100644", "type": "blob"}
                if path in rendering.files:
                    entry["content"] = rendering.files[path]
                else:
                    entry["sha"] = None
                entries.append(entry)
            author, coauthors = _commit_identity(pending)
            message = build_commit_message(cfg, pending, plan.push, local, remote, coauthors,
                                           normalized=not pending)
            committer = {"name": settings.GIT_USERNAME, "email": settings.GIT_EMAIL}
            tree_sha = client.create_tree(head_tree, entries)
            commit_sha = client.create_commit(message, tree_sha, head_sha, author, committer)
            if not client.update_branch(cfg.branch, commit_sha):
                logger.info("Context Catalog Git: %s moved during commit; retrying", cfg.branch)
                continue
            pushed = final_sha = commit_sha
            for path in plan.push:
                if path in local:
                    final_tree[path] = local[path]
                else:
                    final_tree.pop(path, None)

        upserts_sha = {p: remote[p] for p in plan.pull if p in remote}
        texts = client.read_files(head_sha, {cfg.full_path(p): s for p, s in upserts_sha.items()}) if upserts_sha else {}
        upserts = {p: texts[cfg.full_path(p)] for p in upserts_sha}
        deletions: Set[str] = set()
        for path in plan.pull:
            if path not in remote:
                key = synced_index.get(path) or rendering.index.get(path)
                if key:
                    deletions.add(key)
        if not remote and (plan.full_mirror or deletions) and rendering.files:
            raise GitSyncError(
                f"Branch '{cfg.branch}' has no catalog files under '{cfg.base_path or '/'}'. Refusing to "
                f"delete this environment's catalog; check the base folder setting."
            )
        applier = _Applier(db, extras, actor)
        applier.apply(upserts, deletions, set(final_tree), full=plan.full_mirror)
        # A file that failed to import stays out of the agreed state, so it is
        # retried (and reported) every sync and never deleted by a later push.
        failed = {e["path"] for e in applier.report.errors} & set(remote)

        # Record what this environment and the branch now agree on.
        if cfg.push_enabled and bootstrap:
            # Pass two of the first sync then commits app-only files and
            # rewrites imported files the app would format differently.
            new_render = dict(remote)
        elif cfg.push_enabled:
            new_render = dict(synced_render)
            for path in {p for p in set(local) | set(synced_render) if local.get(p) != synced_render.get(p)}:
                if path in local:
                    new_render[path] = local[path]
                else:
                    new_render.pop(path, None)
            # Imported files count as the app's rendering, so a file Git wrote
            # differently from the app's own format is normalized next pass.
            for path in plan.pull:
                if path in remote:
                    new_render[path] = remote[path]
                else:
                    new_render.pop(path, None)
        else:
            new_render = render_catalog(db, extras).shas
        for path in failed:
            final_tree.pop(path, None)
            if cfg.push_enabled and path in local:
                new_render[path] = local[path]
            elif cfg.push_enabled:
                new_render.pop(path, None)
        new_index = {p: k for p, k in synced_index.items() if p in final_tree}
        for path in plan.push:
            if path in rendering.index and path in final_tree:
                new_index[path] = rendering.index[path]
        new_index.update(applier.index)

        now = _utcnow()
        state.synced_tree = final_tree
        state.synced_render = new_render
        state.synced_index = new_index
        state.front_matter_extras = extras
        state.last_synced_sha = final_sha
        if pushed:
            state.last_commit_sha = pushed
            state.last_commit_at = now
        if applier.report.changed:
            state.last_pull_at = now
        if plan.conflicts:
            entries = [{"path": cfg.full_path(p), "at": now.isoformat(),
                        "resolution": "kept the app's version; the Git version is in the branch history"}
                       for p in sorted(plan.conflicts)]
            state.conflicts = (list(state.conflicts or []) + entries)[-MAX_CONFLICTS:]
        if pending and not bootstrap:
            db.refresh(state, ["pending_changes"])
            done = {c.get("id") for c in pending}
            remaining = [c for c in (state.pending_changes or []) if c.get("id") not in done]
            state.pending_changes = remaining
            if not remaining:
                state.pending_since = None
        db.commit()
        for path in applier.storage_paths:
            try:
                from app.providers.context_catalog import ContextCatalogStorage

                ContextCatalogStorage().delete_original(path)
            except Exception as e:  # noqa: BLE001 - cleanup is best-effort
                logger.warning("Context Catalog Git: could not delete original %s: %s", path, e)
        return {
            "commit": pushed,
            "head": final_sha,
            "pushed": sorted(cfg.full_path(p) for p in plan.push) if pushed else [],
            "pulled": applier.report.as_dict(),
            "conflicts": sorted(cfg.full_path(p) for p in plan.conflicts),
        }
    raise GitSyncError(f"Branch '{cfg.branch}' kept moving while committing; the sync will retry.")


def _merge_reports(first: Dict[str, Any], second: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(second)
    merged["commit"] = second.get("commit") or first.get("commit")
    merged["pushed"] = first.get("pushed", []) + second.get("pushed", [])
    merged["conflicts"] = first.get("conflicts", []) + second.get("conflicts", [])
    pulled: Dict[str, Any] = {"errors": first["pulled"]["errors"] + second["pulled"]["errors"]}
    for kind in ("domains", "documents"):
        pulled[kind] = {op: first["pulled"][kind][op] + second["pulled"][kind][op]
                        for op in ("created", "updated", "deleted")}
    merged["pulled"] = pulled
    return merged


def sync_catalog(db: Session, cfg: GitConfig, client: GitHubRepoClient, *, trigger: str,
                 actor: Optional[str] = None) -> Dict[str, Any]:
    """Run one sync and record its outcome on the environment's state row."""
    state = _load_state(db, cfg)
    db.commit()
    who = f"git:{cfg.branch}"
    try:
        bootstrap = state.last_synced_sha is None
        result = _pass(db, cfg, client, state, bootstrap=bootstrap, actor=who)
        pulled = result["pulled"]
        imported = any(pulled[k][op] for k in ("domains", "documents") for op in ("created", "updated"))
        if cfg.push_enabled and (bootstrap or imported):
            # Commit what the import changed in the files (new ids, the app's
            # formatting, file names following titles) in the same sync.
            result = _merge_reports(result, _pass(db, cfg, client, state, bootstrap=False, actor=who))
    except Exception as e:  # noqa: BLE001 - recorded on the state row and shown in the UI
        db.rollback()
        message = str(e) if isinstance(e, GitSyncError) else f"Unexpected error: {e}"
        if not isinstance(e, GitSyncError):
            logger.exception("Context Catalog Git sync failed")
        else:
            logger.warning("Context Catalog Git sync failed (%s): %s", trigger, message)
        state = _load_state(db, cfg)
        state.last_error = message
        state.last_error_at = _utcnow()
        state.last_sync_trigger = trigger
        db.commit()
        return {"ok": False, "error": message}
    state.last_sync_at = _utcnow()
    state.last_sync_trigger = trigger
    state.last_error = None
    state.last_error_at = None
    state.last_report = {**result, "trigger": trigger, "actor": actor}
    db.commit()
    logger.info("Context Catalog Git sync (%s) on %s: commit=%s conflicts=%d", trigger, cfg.branch,
                result.get("commit"), len(result.get("conflicts") or []))
    return {"ok": True, **result}


_sync_lock = threading.Lock()


def sync_once(trigger: str = "manual", actor: Optional[str] = None) -> Dict[str, Any]:
    """Sync this environment with its branch. Blocking: run it with ``asyncio.to_thread``."""
    cfg = resolve_config()
    if not cfg.active:
        return {"ok": False, "skipped": True, "error": cfg.problem}
    if not _sync_lock.acquire(blocking=False):
        return {"ok": False, "busy": True, "error": "A sync is already running."}
    from app.db.session import get_lakebase_session

    db = get_lakebase_session()
    try:
        with GitHubRepoClient(cfg.repo, resolve_token()) as client:
            return sync_catalog(db, cfg, client, trigger=trigger, actor=actor)
    except GitSyncError as e:
        state = _load_state(db, cfg)
        state.last_error = str(e)
        state.last_error_at = _utcnow()
        db.commit()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()
        _sync_lock.release()


def sync_running() -> bool:
    return _sync_lock.locked()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def get_status(db: Session) -> Dict[str, Any]:
    cfg = resolve_config()
    state = db.get(ContextCatalogGitStateModel, cfg.environment) if cfg.environment else None
    if state is not None and (state.repo, state.branch, state.base_path) != (
        cfg.repo.full_name if cfg.repo else None, cfg.branch, cfg.base_path
    ):
        state = None
    pending = list(state.pending_changes or []) if state else []
    return {
        "direct_editing_enabled": direct_editing_enabled(),
        "direct_editing_message": None if direct_editing_enabled() else direct_editing_message(),
        "git": {
            "enabled": cfg.enabled,
            "active": cfg.active,
            "problem": cfg.problem,
            "environment": cfg.environment,
            "repo": cfg.repo.full_name if cfg.repo else None,
            "repo_url": f"{cfg.repo.web_url}/{cfg.repo.full_name}" if cfg.repo else None,
            "branch": cfg.branch,
            "branch_url": cfg.branch_url,
            "push_enabled": cfg.push_enabled,
            "base_path": cfg.base_path,
            "poll_cron": getattr(settings, "CONTEXT_CATALOG_GIT_POLL_CRON", "") or "",
            "syncing": sync_running(),
            "last_synced_sha": state.last_synced_sha if state else None,
            "last_commit_sha": state.last_commit_sha if state else None,
            "last_commit_url": cfg.commit_url(state.last_commit_sha) if state else None,
            "last_commit_at": _iso(state.last_commit_at) if state else None,
            "last_pull_at": _iso(state.last_pull_at) if state else None,
            "last_sync_at": _iso(state.last_sync_at) if state else None,
            "last_sync_trigger": state.last_sync_trigger if state else None,
            "last_error": state.last_error if state else None,
            "last_error_at": _iso(state.last_error_at) if state else None,
            "last_report": state.last_report if state else None,
            "conflicts": list(state.conflicts or []) if state else [],
            "pending_count": len(pending),
            "pending_since": _iso(state.pending_since) if state else None,
            "pending": pending[-10:],
        },
    }


# ---------------------------------------------------------------------------
# Poller hook
# ---------------------------------------------------------------------------

_next_poll_time: Optional[datetime] = None
_boot_done = False
_retry_after: Optional[datetime] = None
_failures = 0


def _pending_in_db() -> bool:
    from app.db.session import get_lakebase_session

    db = get_lakebase_session()
    try:
        return has_pending_changes(db)
    finally:
        db.close()


async def git_sync_task() -> None:
    """Poller hook: push queued saves, poll the branch on its cron, and sync once at boot."""
    global _next_poll_time, _boot_done, _retry_after, _failures
    if not getattr(settings, "CONTEXT_CATALOG_GIT_ENABLED", False) or _sync_lock.locked():
        return
    now = datetime.now(timezone.utc)
    trigger: Optional[str] = None
    if not _boot_done:
        _boot_done = True
        trigger = "boot"
    cron_expr = (getattr(settings, "CONTEXT_CATALOG_GIT_POLL_CRON", "") or "").strip()
    if cron_expr:
        from croniter import CroniterBadCronError, croniter

        try:
            if _next_poll_time is None:
                _next_poll_time = croniter(cron_expr, now).get_next(datetime)
            elif now >= _next_poll_time:
                _next_poll_time = croniter(cron_expr, now).get_next(datetime)
                trigger = trigger or "schedule"
        except (CroniterBadCronError, ValueError):
            logger.error("Invalid CONTEXT_CATALOG_GIT_POLL_CRON expression: %s", cron_expr)
    if trigger is None and (_retry_after is None or now >= _retry_after):
        if await asyncio.to_thread(_pending_in_db):
            trigger = "save"
    if trigger is None:
        return
    result = await asyncio.to_thread(sync_once, trigger)
    if result.get("ok"):
        _failures, _retry_after = 0, None
    elif not result.get("skipped") and not result.get("busy"):
        _failures += 1
        _retry_after = now + timedelta(seconds=min(300, 15 * 2 ** (_failures - 1)))
