"""Read-only agent-profile loader, OBO-scoped.

An *agent profile* is a folder authored by the Command Center Agent Studio::

    <base>/.agents/<slug>/AGENT.md
    <base>/.agents/<slug>/skills/<skill-slug>.md

``AGENT.md`` is markdown with a leading YAML frontmatter block carrying
``name``/``description``/``model``/``tools``; the body is the system prompt.
This module loads one such profile for a single chat turn so the runtime can
override its prompt, skills, and allowed tools per request. Everything is read
under the caller's OBO token, so Unity Catalog governs what is visible — no
governance is re-implemented here.

A *profile reference* (``profile_ref``) may be either:

  * a filesystem path to the folder or its ``AGENT.md``
    (``/Volumes/cat/sch/vol/.agents/my-agent`` or ``.../AGENT.md``;
    ``/Workspace/Users/me/.agents/my-agent``), or
  * an opaque, URL-safe id (base64 of ``"<store>|<dir_path>"``) — the same
    handle the Agent Studio API emits — which we decode back to a path.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

AGENT_FILE = "AGENT.md"
SKILLS_SUBDIR = "skills"

STORE_WORKSPACE = "workspace"
STORE_VOLUME = "volume"


class ProfileError(Exception):
    """A user-facing profile-loading failure (not found / not readable)."""


@dataclass
class LoadedProfile:
    """A fully resolved profile: prompt body + metadata + inlined skills."""

    store: str
    dir_path: str
    name: str
    description: str = ""
    model: str = ""
    tools: List[str] = field(default_factory=list)
    prompt: str = ""
    skills: List[Tuple[str, str]] = field(default_factory=list)  # (name, body)
    # How the profile body combines with the runtime's structural prompt.
    # "full" (default) layers the profile persona on top of the base runtime
    # instructions (formatting, tool mechanics, workflow/form routing). A value
    # of "none"/"standalone"/"off"/"false" makes the profile body the ENTIRE
    # system prompt — use only for agents that intentionally replace all runtime
    # behavior (e.g. a narrow drawer assistant).
    base: str = "full"

    @property
    def standalone(self) -> bool:
        return self.base.strip().lower() in {"none", "standalone", "off", "false", "no"}

    def system_prompt(self) -> str:
        """Compose the profile body + any skills into one system prompt block."""
        parts = [self.prompt.strip() or f"# {self.name}"]
        if self.skills:
            parts.append("\n\n## Skills\n\nApply the following skills when relevant:")
            for name, body in self.skills:
                parts.append(f"\n\n### Skill: {name}\n\n{body.strip()}")
        return "".join(parts)


def _looks_like_path(ref: str) -> bool:
    return ref.startswith("/") or ref.startswith("dbfs:/")


def _resolve_ref(profile_ref: str) -> Tuple[str, str]:
    """Resolve a reference into ``(store, dir_path)`` (dir that holds AGENT.md)."""
    ref = (profile_ref or "").strip()
    if not ref:
        raise ProfileError("Empty profile reference.")

    if _looks_like_path(ref):
        path = ref.rstrip("/")
    else:
        # Opaque id: base64("<store>|<dir_path>").
        pad = "=" * (-len(ref) % 4)
        try:
            raw = base64.urlsafe_b64decode(ref + pad).decode("utf-8")
            store, dir_path = raw.split("|", 1)
        except Exception as exc:  # noqa: BLE001
            raise ProfileError(f"Invalid profile reference: {profile_ref}") from exc
        if store not in (STORE_WORKSPACE, STORE_VOLUME):
            raise ProfileError(f"Invalid profile store: {store}")
        return store, dir_path.rstrip("/")

    # Path form: strip a trailing AGENT.md to get the folder.
    if path.rsplit("/", 1)[-1] == AGENT_FILE:
        path = path.rsplit("/", 1)[0]
    store = STORE_VOLUME if path.startswith("/Volumes") else STORE_WORKSPACE
    return store, path


def _parse_frontmatter(content: str) -> Dict[str, Any]:
    """Tolerant scan of a leading ``---`` YAML block (no YAML dependency)."""
    meta: Dict[str, Any] = {"body": content, "tools": []}
    text = content.lstrip()
    if not text.startswith("---"):
        return meta
    end = text.find("\n---", 3)
    if end == -1:
        return meta
    block = text[3:end]
    meta["body"] = text[end + 4:].lstrip("\n")

    tools: List[str] = []
    in_tools_list = False
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") and in_tools_list:
            tools.append(stripped[2:].strip().strip('"').strip("'"))
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip().lower()
        val = val.strip()
        in_tools_list = False
        if key == "tools":
            in_tools_list = True
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                if inner:
                    tools.extend(t.strip().strip('"').strip("'") for t in inner.split(",") if t.strip())
            continue
        meta[key] = val.strip('"').strip("'")
    meta["tools"] = [t for t in tools if t]
    return meta


def _basename(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


class ProfileProvider:
    """Loads a single agent profile (AGENT.md + skills) under an OBO token."""

    def _client(self, obo_token: Optional[str]):
        from databricks.sdk import WorkspaceClient

        host = settings.DATABRICKS_HOST
        if obo_token and host:
            return WorkspaceClient(host=host, token=obo_token, auth_type="pat")
        if host:
            return WorkspaceClient(host=host)
        return WorkspaceClient()

    def get_profile(self, obo_token: Optional[str], profile_ref: str) -> LoadedProfile:
        store, dir_path = _resolve_ref(profile_ref)
        client = self._client(obo_token)
        agent_path = f"{dir_path}/{AGENT_FILE}"

        text = (
            self._read_volume(client, agent_path)
            if store == STORE_VOLUME
            else self._read_workspace(client, agent_path)
        )
        if text is None:
            raise ProfileError(f"Profile not found or not readable: {dir_path}")

        meta = _parse_frontmatter(text)
        skills = self._read_skills(client, store, dir_path)
        return LoadedProfile(
            store=store,
            dir_path=dir_path,
            name=meta.get("name") or _basename(dir_path),
            description=meta.get("description") or "",
            model=meta.get("model") or "",
            tools=meta.get("tools") or [],
            prompt=meta.get("body", ""),
            skills=skills,
            base=meta.get("base") or "full",
        )

    # ---- skills -----------------------------------------------------------
    def _read_skills(self, client, store: str, dir_path: str) -> List[Tuple[str, str]]:
        skills_dir = f"{dir_path}/{SKILLS_SUBDIR}"
        out: List[Tuple[str, str]] = []
        if store == STORE_VOLUME:
            try:
                entries = list(client.files.list_directory_contents(skills_dir))
            except Exception:  # noqa: BLE001
                entries = []
            for entry in entries:
                if getattr(entry, "is_directory", False):
                    continue
                path = getattr(entry, "path", "")
                if not path.endswith(".md"):
                    continue
                text = self._read_volume(client, path)
                if text:
                    out.append(self._skill_tuple(path, text))
        else:
            from databricks.sdk.service.workspace import ObjectType

            entries = []
            for cand in _ws_variants(skills_dir):
                try:
                    listed = list(client.workspace.list(cand))
                except Exception:  # noqa: BLE001
                    continue
                if listed:
                    entries = listed
                    break
            for obj in entries:
                if getattr(obj, "object_type", None) == ObjectType.DIRECTORY:
                    continue
                path = getattr(obj, "path", "")
                text = self._read_workspace(client, path)
                if text:
                    out.append(self._skill_tuple(path, text))
        out.sort(key=lambda t: t[0].lower())
        return out

    def _skill_tuple(self, path: str, text: str) -> Tuple[str, str]:
        meta = _parse_frontmatter(text)
        base = _basename(path)
        if base.endswith(".md"):
            base = base[:-3]
        name = meta.get("name") or base
        return (name, meta.get("body", text))

    # ---- low-level IO -----------------------------------------------------
    def _read_volume(self, client, path: str) -> Optional[str]:
        try:
            resp = client.files.download(path)
            raw = resp.contents.read()
            return raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            logger.debug("profile volume download failed for %s: %s", path, exc)
            return None

    def _read_workspace(self, client, path: str) -> Optional[str]:
        from databricks.sdk.service.workspace import ExportFormat

        # Agent Studio writes AGENT.md / SKILL.md as RAW *files* (ObjectType.FILE)
        # via ``workspace.import_``. Reading them back with ExportFormat.RAW is
        # REJECTED by the Workspace API ("Invalid export request: format=RAW,
        # directDownload=false") because the SDK doesn't set directDownload — so
        # the file reads back as None and the profile looks "not found". SOURCE
        # returns the bytes for these objects; we keep RAW as a fallback for any
        # older workspaces. This mirrors the Command Center store's reader.
        for cand in _ws_variants(path):
            for fmt in (ExportFormat.SOURCE, ExportFormat.RAW):
                try:
                    resp = client.workspace.export(path=cand, format=fmt)
                    if resp and getattr(resp, "content", None):
                        return base64.b64decode(resp.content).decode("utf-8", errors="replace")
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "profile workspace export (%s) failed for %s: %s", fmt, cand, exc
                    )
        return None


def _ws_variants(path: str) -> List[str]:
    p = (path or "").rstrip("/")
    variants = [p]
    if p.startswith("/Workspace/"):
        alt = p[len("/Workspace"):]
    elif p.startswith("/"):
        alt = "/Workspace" + p
    else:
        alt = p
    if alt and alt not in variants:
        variants.append(alt)
    return variants


_provider: Optional[ProfileProvider] = None


def get_profile_provider() -> ProfileProvider:
    global _provider
    if _provider is None:
        _provider = ProfileProvider()
    return _provider
