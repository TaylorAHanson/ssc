"""Agent Skills provider — OBO-scoped CRUD over SKILL.md folders.

A *skill* is a folder containing a ``SKILL.md`` file (YAML frontmatter with
``name`` + ``description``, followed by markdown instructions the agent loads).
Skills live in two On-Behalf-Of (OBO) scoped places, mirroring the product
vision of "author once, scope by domain":

  1. **Personal** — the user's Databricks *Workspace* folder, by default
     ``/Workspace/Users/<email>/.skills/<slug>/SKILL.md``. Read/written via the
     Workspace API with ``RAW`` format so content round-trips faithfully.
  2. **Shared** — any ``.skills`` directory inside a *UC Volume* the user can
     read/write, discovered by walking catalogs → schemas → volumes with the
     user's own token. Read/written via the Files API. Because every call uses
     the caller's OBO token, a user only ever sees and edits what Unity Catalog
     already lets them — governance is enforced by UC, not re-implemented here.

All methods take an ``obo_token``; the WorkspaceClient is always built with the
caller's token (``auth_type="pat"``) so file access is genuinely on-behalf-of
the user. Failures are surfaced as :class:`SkillsError` (caught at the API
layer) and discovery is best-effort + bounded (see ``SKILLS_SCAN_*`` settings).
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"

STORE_WORKSPACE = "workspace"
STORE_VOLUME = "volume"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class SkillsError(Exception):
    """A user-facing skills failure (permission denied, not found, etc.)."""


@dataclass
class SkillRef:
    """A discovered skill (folder + parsed metadata). ``id`` is an opaque,
    URL-safe handle the frontend/agent round-trips to address the skill."""

    store: str
    dir_path: str  # folder that contains SKILL.md
    name: str
    description: str = ""
    location_label: str = ""
    writable: bool = True
    # Only populated when a single skill is fetched (list views omit the body).
    content: Optional[str] = None

    @property
    def md_path(self) -> str:
        return f"{self.dir_path.rstrip('/')}/{SKILL_FILE}"

    @property
    def id(self) -> str:
        return encode_skill_id(self.store, self.dir_path)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "store": self.store,
            "dir_path": self.dir_path,
            "name": self.name,
            "description": self.description,
            "location_label": self.location_label,
            "writable": self.writable,
        }
        if self.content is not None:
            d["content"] = self.content
        return d


@dataclass
class SkillLocation:
    """A place a new skill can be created."""

    store: str
    # For workspace: the personal .skills dir. For volume: the .skills dir.
    base_path: str
    label: str
    is_personal: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "store": self.store,
            "base_path": self.base_path,
            "label": self.label,
            "is_personal": self.is_personal,
        }


def encode_skill_id(store: str, dir_path: str) -> str:
    raw = f"{store}|{dir_path}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_skill_id(skill_id: str) -> Tuple[str, str]:
    pad = "=" * (-len(skill_id) % 4)
    try:
        raw = base64.urlsafe_b64decode(skill_id + pad).decode("utf-8")
        store, dir_path = raw.split("|", 1)
    except Exception as exc:  # noqa: BLE001
        raise SkillsError(f"Invalid skill id: {skill_id}") from exc
    if store not in (STORE_WORKSPACE, STORE_VOLUME):
        raise SkillsError(f"Invalid skill store: {store}")
    return store, dir_path


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug or "skill"


def parse_frontmatter(content: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract ``name`` + ``description`` from leading ``---`` YAML frontmatter.

    Uses a tolerant line scan rather than a YAML parser so a malformed block
    never blocks listing a skill.
    """
    name: Optional[str] = None
    desc: Optional[str] = None
    text = content.lstrip()
    if not text.startswith("---"):
        return name, desc
    end = text.find("\n---", 3)
    if end == -1:
        return name, desc
    block = text[3:end]
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip().lower()
        val = val.strip().strip('"').strip("'")
        if key == "name" and not name:
            name = val
        elif key == "description" and not desc:
            desc = val
    return name, desc


def build_skill_markdown(name: str, description: str, body: str = "") -> str:
    """Compose a SKILL.md from metadata + body (used when the body lacks its
    own frontmatter)."""
    body = (body or "").strip()
    if body.lstrip().startswith("---"):
        # Caller already supplied a full document with frontmatter.
        return body if body.endswith("\n") else body + "\n"
    safe_desc = description.replace("\n", " ").strip()
    lines = [
        "---",
        f"name: {name.strip()}",
        f"description: {safe_desc}",
        "---",
        "",
    ]
    if body:
        lines.append(body)
    else:
        lines.extend(
            [
                f"# {name.strip()}",
                "",
                "## When to use",
                "",
                "Describe the situations where the agent should apply this skill.",
                "",
                "## Instructions",
                "",
                "Step-by-step guidance for the agent.",
            ]
        )
    return "\n".join(lines) + "\n"


def _is_missing_workspace_scope(exc: Exception) -> bool:
    """True if a Workspace-API call failed because the OBO token lacks the scope.

    In Databricks Apps the user's forwarded token is *downscoped* to the app's
    declared ``user_api_scopes``. If the Workspace scope isn't declared, writes
    under ``/Workspace/Users/...`` fail with "does not have required scopes:
    workspace" — a config problem, not a per-user permission problem.
    """
    msg = str(exc).lower()
    return "required scopes" in msg and "workspace" in msg


def _workspace_write_error(dir_path: str, exc: Exception) -> str:
    """User-facing message for a failed personal (Workspace) skill write."""
    if _is_missing_workspace_scope(exc):
        return (
            "Can't save personal skills: this app's on-behalf-of token isn't "
            "authorized for the Workspace API. Add the 'workspace.workspace' scope "
            "to the app's user authorization scopes (databricks.yml "
            "`user_api_scopes`, or the app's Authorization tab), restart the app, "
            "and re-consent on next sign-in. Until then you can save a shared skill "
            "to a UC Volume instead (those use the already-authorized Files API). "
            f"(Underlying error: {exc})"
        )
    return (
        f"Could not create the skill folder '{dir_path}': {exc}. Skills are saved on "
        "your behalf, so your own Workspace home must exist and be writable by you. "
        "If this app can't act on your behalf, set SKILLS_PERSONAL_WORKSPACE_DIR to a "
        "folder you can write to."
    )


class SkillsProvider:
    """OBO CRUD for skills across the Workspace tree and UC Volumes."""

    def __init__(self) -> None:
        self._dir_name = settings.SKILLS_DIR_NAME or ".skills"

    # ---- client -----------------------------------------------------------
    def _client(self, obo_token: Optional[str]):
        from databricks.sdk import WorkspaceClient

        host = settings.DATABRICKS_HOST
        if obo_token and host:
            return WorkspaceClient(host=host, token=obo_token, auth_type="pat")
        # Local/dev fallback (default auth chain). Skills file ops will simply
        # fail gracefully if no workspace is reachable.
        if host:
            return WorkspaceClient(host=host)
        return WorkspaceClient()

    def personal_dir(self, user_email: str) -> str:
        configured = (settings.SKILLS_PERSONAL_WORKSPACE_DIR or "").strip()
        if configured:
            return configured.rstrip("/")
        return f"/Workspace/Users/{user_email}/{self._dir_name}"

    def _effective_personal_dir(self, client, user_email: str) -> str:
        """Personal ``.skills`` dir under the home of whoever the token resolves to.

        A Workspace home folder only exists for (and is only writable by) the
        principal the token authenticates as. In true OBO that's the user
        (== ``user_email``); in a non-OBO/dev fallback the client authenticates
        as the app/developer principal, so deriving the path from ``user_email``
        would point at a home that doesn't exist / can't be written. Resolve the
        real identity from the workspace when possible. An explicit
        ``SKILLS_PERSONAL_WORKSPACE_DIR`` override always wins.
        """
        configured = (settings.SKILLS_PERSONAL_WORKSPACE_DIR or "").strip()
        if configured:
            return configured.rstrip("/")
        email = user_email
        try:
            uname = getattr(client.current_user.me(), "user_name", None)
            if uname:
                email = uname
        except Exception as exc:  # noqa: BLE001 - fall back to the provided email
            logger.debug("current_user.me() failed; using '%s': %s", user_email, exc)
        return f"/Workspace/Users/{email}/{self._dir_name}"

    # ---- listing ----------------------------------------------------------
    def list_skills(
        self,
        obo_token: Optional[str],
        user_email: str,
        include_shared: bool = True,
    ) -> List[SkillRef]:
        skills: List[SkillRef] = []
        client = self._client(obo_token)
        skills.extend(self._list_personal(client, user_email))
        if include_shared:
            skills.extend(self._discover_shared(client))
        # Stable order: personal first, then by label/name.
        skills.sort(key=lambda s: (s.store != STORE_WORKSPACE, s.location_label, s.name.lower()))
        return skills

    def _list_personal(self, client, user_email: str) -> List[SkillRef]:
        base = self._effective_personal_dir(client, user_email)
        out: List[SkillRef] = []
        try:
            from databricks.sdk.service.workspace import ObjectType

            for obj in client.workspace.list(base):
                if obj.object_type != ObjectType.DIRECTORY:
                    continue
                text = self._read_workspace_text(client, f"{obj.path}/{SKILL_FILE}")
                if text is None:
                    continue
                name, desc = parse_frontmatter(text)
                out.append(
                    SkillRef(
                        store=STORE_WORKSPACE,
                        dir_path=obj.path,
                        name=name or _basename(obj.path),
                        description=desc or "",
                        location_label="Personal",
                        writable=True,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - missing dir / no workspace is fine
            logger.debug("Personal skills listing skipped for %s: %s", base, exc)
        return out

    def _discover_shared(self, client) -> List[SkillRef]:
        out: List[SkillRef] = []
        only = [c.strip() for c in (settings.SKILLS_SCAN_CATALOGS or "").split(",") if c.strip()]
        try:
            catalogs = list(client.catalogs.list())
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skills scan: catalogs.list failed: %s", exc)
            return out
        cat_count = 0
        for cat in catalogs:
            cat_name = getattr(cat, "name", None)
            if not cat_name:
                continue
            if only and cat_name not in only:
                continue
            if cat_count >= settings.SKILLS_SCAN_MAX_CATALOGS:
                break
            cat_count += 1
            try:
                schemas = list(client.schemas.list(catalog_name=cat_name))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skills scan: schemas.list(%s) failed: %s", cat_name, exc)
                continue
            sch_count = 0
            for sch in schemas:
                sch_name = getattr(sch, "name", None)
                if not sch_name or sch_name == "information_schema":
                    continue
                if sch_count >= settings.SKILLS_SCAN_MAX_SCHEMAS:
                    break
                sch_count += 1
                try:
                    volumes = list(client.volumes.list(catalog_name=cat_name, schema_name=sch_name))
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "Skills scan: volumes.list(%s.%s) failed: %s", cat_name, sch_name, exc
                    )
                    continue
                vol_count = 0
                for vol in volumes:
                    vol_name = getattr(vol, "name", None)
                    if not vol_name:
                        continue
                    if vol_count >= settings.SKILLS_SCAN_MAX_VOLUMES:
                        break
                    vol_count += 1
                    skills_dir = f"/Volumes/{cat_name}/{sch_name}/{vol_name}/{self._dir_name}"
                    out.extend(self._list_volume_skills_dir(client, skills_dir))
        return out

    def _list_volume_skills_dir(self, client, skills_dir: str) -> List[SkillRef]:
        out: List[SkillRef] = []
        try:
            entries = list(client.files.list_directory_contents(skills_dir))
        except Exception:  # noqa: BLE001 - .skills dir absent is the common case
            return out
        label = _volume_label(skills_dir)
        for entry in entries:
            if not getattr(entry, "is_directory", False):
                continue
            dir_path = getattr(entry, "path", None)
            if not dir_path:
                continue
            text = self._read_volume_text(client, f"{dir_path.rstrip('/')}/{SKILL_FILE}")
            if text is None:
                # No readable SKILL.md -> not a skill folder.
                continue
            name, desc = parse_frontmatter(text)
            out.append(
                SkillRef(
                    store=STORE_VOLUME,
                    dir_path=dir_path.rstrip("/"),
                    name=name or _basename(dir_path),
                    description=desc or "",
                    location_label=label,
                    writable=True,
                )
            )
        return out

    # ---- read -------------------------------------------------------------
    def get_skill(self, obo_token: Optional[str], skill_id: str) -> SkillRef:
        store, dir_path = decode_skill_id(skill_id)
        client = self._client(obo_token)
        md_path = f"{dir_path.rstrip('/')}/{SKILL_FILE}"
        if store == STORE_WORKSPACE:
            content = self._read_workspace_text(client, md_path)
            label = "Personal"
        else:
            content = self._read_volume_text(client, md_path)
            label = _volume_label(dir_path)
        if content is None:
            raise SkillsError("Skill not found or not readable.")
        name, desc = parse_frontmatter(content)
        return SkillRef(
            store=store,
            dir_path=dir_path.rstrip("/"),
            name=name or _basename(dir_path),
            description=desc or "",
            location_label=label,
            content=content,
        )

    # ---- write ------------------------------------------------------------
    def save_skill(
        self,
        obo_token: Optional[str],
        user_email: str,
        name: str,
        content: str,
        store: str = STORE_WORKSPACE,
        base_path: Optional[str] = None,
        skill_id: Optional[str] = None,
        description: str = "",
    ) -> SkillRef:
        """Create or update a skill.

        When ``skill_id`` is given the existing folder is overwritten in place.
        Otherwise a new folder ``<base>/<slug>`` is created (``base`` defaults to
        the personal workspace ``.skills`` dir for ``store == workspace``).
        """
        if not name or not name.strip():
            raise SkillsError("Skill name is required.")

        # Allow callers to pass either a bare body or a full SKILL.md.
        document = build_skill_markdown(name, description, content)
        data = document.encode("utf-8")
        if len(data) > settings.SKILLS_MAX_BYTES:
            raise SkillsError(
                f"Skill exceeds max size ({settings.SKILLS_MAX_BYTES // 1024} KB)."
            )

        client = self._client(obo_token)

        if skill_id:
            store, dir_path = decode_skill_id(skill_id)
        else:
            slug = slugify(name)
            if store == STORE_WORKSPACE:
                base = (base_path or self._effective_personal_dir(client, user_email)).rstrip("/")
            else:
                if not base_path:
                    raise SkillsError("A target .skills volume path is required.")
                base = base_path.rstrip("/")
            dir_path = f"{base}/{slug}"

        md_path = f"{dir_path}/{SKILL_FILE}"
        if store == STORE_WORKSPACE:
            self._write_workspace(client, dir_path, md_path, data)
            label = "Personal"
        else:
            self._write_volume(client, md_path, data)
            label = _volume_label(dir_path)

        parsed_name, parsed_desc = parse_frontmatter(document)
        return SkillRef(
            store=store,
            dir_path=dir_path,
            name=parsed_name or name,
            description=parsed_desc or description,
            location_label=label,
            content=document,
        )

    def delete_skill(self, obo_token: Optional[str], skill_id: str) -> None:
        store, dir_path = decode_skill_id(skill_id)
        client = self._client(obo_token)
        if store == STORE_WORKSPACE:
            try:
                client.workspace.delete(dir_path, recursive=True)
            except Exception as exc:  # noqa: BLE001
                raise SkillsError(f"Could not delete skill: {exc}") from exc
        else:
            md_path = f"{dir_path.rstrip('/')}/{SKILL_FILE}"
            try:
                client.files.delete(md_path)
            except Exception as exc:  # noqa: BLE001
                raise SkillsError(f"Could not delete skill: {exc}") from exc
            # Best-effort: remove the now-empty folder.
            try:
                client.files.delete_directory(dir_path)
            except Exception:  # noqa: BLE001
                pass

    # ---- locations --------------------------------------------------------
    def list_locations(
        self,
        obo_token: Optional[str],
        user_email: str,
        include_shared: bool = True,
    ) -> List[SkillLocation]:
        client = self._client(obo_token)
        locations: List[SkillLocation] = [
            SkillLocation(
                store=STORE_WORKSPACE,
                base_path=self._effective_personal_dir(client, user_email),
                label="Personal workspace",
                is_personal=True,
            )
        ]
        if include_shared:
            seen = set()
            for ref in self._discover_shared(client):
                base = ref.dir_path.rsplit("/", 1)[0]  # the .skills dir
                if base in seen:
                    continue
                seen.add(base)
                locations.append(
                    SkillLocation(
                        store=STORE_VOLUME,
                        base_path=base,
                        label=_volume_label(base),
                    )
                )
        return locations

    # ---- low-level workspace IO ------------------------------------------
    def _read_workspace_text(self, client, md_path: str) -> Optional[str]:
        try:
            from databricks.sdk.service.workspace import ExportFormat

            resp = client.workspace.export(path=md_path, format=ExportFormat.RAW)
            if not resp or not getattr(resp, "content", None):
                return None
            return base64.b64decode(resp.content).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            logger.debug("workspace export failed for %s: %s", md_path, exc)
            return None

    def _write_workspace(self, client, dir_path: str, md_path: str, data: bytes) -> None:
        from databricks.sdk.service.workspace import ImportFormat

        # The Workspace import API does NOT create parent directories — the skill
        # folder (and any missing ancestors, e.g. the user's `.skills` dir or even
        # their home) must exist first. ``mkdirs`` is recursive, so if it fails the
        # import can't succeed either; surface the *real* reason here instead of
        # letting import raise the misleading "parent folder does not exist".
        try:
            client.workspace.mkdirs(dir_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skills: mkdirs(%s) failed: %s", dir_path, exc)
            raise SkillsError(_workspace_write_error(dir_path, exc)) from exc
        try:
            client.workspace.import_(
                path=md_path,
                format=ImportFormat.RAW,
                content=base64.b64encode(data).decode("ascii"),
                overwrite=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise SkillsError(_workspace_write_error(dir_path, exc)) from exc

    # ---- low-level volume IO ---------------------------------------------
    def _read_volume_text(self, client, md_path: str) -> Optional[str]:
        try:
            resp = client.files.download(md_path)
            raw = resp.contents.read()
            if isinstance(raw, str):
                return raw
            return raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            logger.debug("volume download failed for %s: %s", md_path, exc)
            return None

    def _write_volume(self, client, md_path: str, data: bytes) -> None:
        try:
            client.files.upload(file_path=md_path, contents=BytesIO(data), overwrite=True)
        except Exception as exc:  # noqa: BLE001
            raise SkillsError(f"Could not save skill to volume: {exc}") from exc


def _basename(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def _volume_label(volume_path: str) -> str:
    """Human label for a /Volumes/<cat>/<schema>/<vol>/... path."""
    parts = [p for p in volume_path.split("/") if p]
    # parts: ['Volumes', cat, schema, vol, '.skills', ...]
    if len(parts) >= 4 and parts[0] == "Volumes":
        return f"{parts[1]}.{parts[2]}.{parts[3]}"
    return volume_path


_provider: Optional[SkillsProvider] = None


def get_skills_provider() -> SkillsProvider:
    global _provider
    if _provider is None:
        _provider = SkillsProvider()
    return _provider
