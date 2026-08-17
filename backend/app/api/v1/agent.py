"""
Agent API endpoints for conversation handling.
"""
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field as PydanticField
from typing import List, Optional, Dict, Any, AsyncIterator
from app.agents.events import (
    DoneEvent,
    ErrorEvent,
    MessageEvent,
    RouteEvent,
    StatusEvent,
    serialize_sse,
)
from app.agents.prompts import (
    get_agent_prompt,
    get_profile_base_scaffold,
    AGENT_TOOLS,
    get_onboarding_suggestions_messages,
    default_onboarding_suggestions,
)
from app.agents.runner import AgentRunner
from app.core.config import settings
from app.core.feature_flags import is_feature_enabled
from app.model_serving.agent_llm import AgentLLMClient
from app.api import deps
from app.api.deps import get_current_user
from app.models.user import User
from app.services.tool_registry_service import (
    DEFAULT_AUTHORING_TOOL_NAMES,
    WORKFLOW_ONLY_TOOL_NAMES,
    ToolRegistryService,
)
from app.services.user_context import (
    derive_persona,
    get_user_context,
    render_user_context_block,
    warm_user_context,
)
from app.services import chat_session_service as chat_sessions
from sqlalchemy.orm import Session
import logging
import json
import re

logger = logging.getLogger(__name__)

router = APIRouter()


def _legacy_visible_tools(current_user: User, surface: str) -> List[Any]:
    """Static fallback used only if the dynamic registry can't be consulted.

    Mirrors the pre-registry behavior: filter ``AGENT_TOOLS`` by ``required_role``
    and, in the workflow-authoring surface, the authoring whitelist. The registry
    is the source of truth; this exists purely so a transient DB error never leaves
    the agent with no tools.
    """
    is_authoring = surface == "workflow"
    out: List[Any] = []
    for tool in AGENT_TOOLS:
        role = getattr(tool, "required_role", None)
        if role and not current_user.has_role(role):
            continue
        name = getattr(tool, "name", "")
        if is_authoring:
            if name not in DEFAULT_AUTHORING_TOOL_NAMES:
                continue
        elif name in WORKFLOW_ONLY_TOOL_NAMES:
            # Keep workflow build/preview/publish tools out of the EDH surface.
            continue
        out.append(tool)
    return out


def _resolve_visible_tools(db: Session, current_user: User, surface: str) -> List[Any]:
    """Tools the given agent surface should expose for ``current_user``.

    Consults the dynamic Tool Registry (data-driven per-surface + per-role gating,
    including MCP-discovered tools). Falls back to the static gating only on error.
    """
    try:
        return ToolRegistryService.resolve_tools_for_surface(db, surface, current_user)
    except Exception as e:  # noqa: BLE001 - never break the agent on a registry hiccup
        logger.warning(
            "Tool registry resolution failed (%s); falling back to static gating", e
        )
        return _legacy_visible_tools(current_user, surface)

def _extract_json_instructions(message: str) -> Optional[Dict[str, Any]]:
    """Extract JSON instructions from agent message if present."""
    # Look for JSON code blocks in the message - handle nested braces
    json_pattern = r'```json\s*(\{(?:[^{}]|(?:\{[^{}]*\}))*\})\s*```'
    matches = re.findall(json_pattern, message, re.DOTALL | re.IGNORECASE)
    
    if not matches:
        json_pattern = r'```\s*(\{(?:[^{}]|(?:\{[^{}]*\}))*\})\s*```'
        matches = re.findall(json_pattern, message, re.DOTALL)
    
    if not matches:
        code_block_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
        matches = re.findall(code_block_pattern, message, re.DOTALL | re.IGNORECASE)
    
    for match in matches:
        try:
            data = json.loads(match.strip())
            if isinstance(data, dict) and data.get("action") == "route_to_form":
                return data
        except json.JSONDecodeError:
            if '"action"' in match and '"route_to_form"' in match:
                try:
                    start = match.find('{')
                    if start != -1:
                        brace_count = 0
                        end = start
                        for i, char in enumerate(match[start:], start):
                            if char == '{': brace_count += 1
                            elif char == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    end = i + 1
                                    break
                        if end > start:
                            json_str = match[start:end]
                            data = json.loads(json_str)
                            if isinstance(data, dict) and data.get("action") == "route_to_form":
                                return data
                except: continue
    return None

def _clean_message_remove_json(message: str) -> str:
    """Remove JSON code blocks from message, leaving only the text."""
    cleaned = re.sub(r'```json\s*\{.*?\}\s*```', '', message, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'```\s*\{.*?\}\s*```', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'\n\s*\n\s*\n', '\n\n', cleaned)
    return cleaned.strip()

class ChatMessage(BaseModel):
    id: str
    type: str  # 'user' | 'agent' | 'tool'
    content: str
    timestamp: str
    # Optional metadata used when the UI replays a tool result back to
    # the runner (after a pending-poll completes). The runner injects
    # these into the synthetic ``tool`` message so the LLM can see the
    # original assistant tool-call linkage.
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    # Optional ``tool_calls`` block carried on a ``type='agent'`` (i.e.
    # assistant) message so the chat completion request preserves the
    # ``user → assistant(tool_calls) → tool → ...`` linkage required by
    # the model serving endpoint. The UI synthesizes this entry in
    # ``ChatView.buildHistory`` immediately before each replayed tool
    # result. Without it, the endpoint rejects the request with
    # ``messages with role 'tool' must be a response to a preceding
    # message with 'tool_calls'``.
    tool_calls: Optional[List[Dict[str, Any]]] = None

class ConversationRequest(BaseModel):
    query: str
    conversation_history: Optional[List[ChatMessage]] = None
    context: Optional[Dict[str, Any]] = None
    # Optional reference to an agent profile (authored in the Command Center
    # Agent Studio) stored as ``AGENT.md`` on a UC Volume / Workspace folder.
    # When present, the profile's prompt, skills, and tool allowlist drive this
    # turn. May be a filesystem path or the Studio's opaque profile id. Can also
    # be supplied via ``context.profile_ref``.
    profile_ref: Optional[str] = None
    # An UNSAVED draft profile for the Agent Studio "Try it" loop. Same shape as
    # a saved profile ({name, prompt, base, tools, skills, model}); applied with
    # identical governance (tool intersection + model allowlist). Takes
    # precedence over ``profile_ref`` when both are present.
    inline_profile: Optional[Dict[str, Any]] = None
    # The server-side transcript this turn belongs to. When set and
    # ``conversation_history`` is omitted, history is loaded from the stored
    # session instead of being replayed by the client. ``conversation_history``
    # still wins when both are present, so the frontend can adopt server-side
    # sessions without the two sides having to deploy together.
    session_id: Optional[str] = None

class FollowUpQuestion(BaseModel):
    id: str
    question: str
    type: str  # 'text' | 'radio' | 'multi-select'
    options: Optional[List[str]] = None
    required: bool

class AgentResponse(BaseModel):
    message: str
    follow_up_questions: Optional[List[FollowUpQuestion]] = None
    form_route: Optional[Dict[str, str]] = None
    requires_more_info: bool = True

@router.get("/tools")
async def get_agent_tools(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(get_current_user),
):
    """Get list of agent tools for the unified (EDH) chat, gated by the registry."""
    tools = _resolve_visible_tools(db, current_user, "edh")
    visible_tools = [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]
    return {"tools": visible_tools, "count": len(visible_tools)}

@router.get("/prompt")
async def get_agent_prompt_endpoint(current_user: User = Depends(get_current_user)):
    """Get the agent system prompt and instructions."""
    return {
        "prompt": get_agent_prompt(),
        "context": {
            "user_email": current_user.email,
            "user_roles": [r.name for r in current_user.roles]
        }
    }


@router.post("/user-context/warm", status_code=202)
async def warm_user_context_endpoint(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(get_current_user),
):
    """Pre-build the caller's context so their first message doesn't wait for it.

    Called on app boot and when a chat surface mounts. The response is only a
    freshness signal — the *point* is the background rebuild it schedules, which
    is why this returns 202 immediately instead of waiting for the (slow)
    identity-provider lookup. Failures are reported, never raised: warming is an
    optimization and must not break page load.
    """
    try:
        return await warm_user_context(db, current_user)
    except Exception as e:  # noqa: BLE001 - warming must never break the client
        logger.warning("Could not warm user context for %s: %s", current_user.email, e)
        return {"enabled": True, "state": "error", "stale": True, "refreshed_at": None}


@router.get("/user-context")
async def get_user_context_endpoint(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(get_current_user),
):
    """The caller's assembled context plus freshness metadata.

    Scoped to the caller by construction — there is no way to ask for someone
    else's profile. Useful for debugging what the agent was told, and for showing
    a user what the assistant knows about them.
    """
    if not is_feature_enabled("user_context"):
        raise HTTPException(status_code=404, detail="User context is not enabled")
    payload = await get_user_context(db, current_user)
    return {**payload, "prompt_block": render_user_context_block(payload)}


# ---------------------------------------------------------------------------
# Chat sessions (server-side transcripts)
#
# Every handler resolves a session through ``chat_sessions``, which filters on
# the owner's email as part of the lookup — a session is never addressable by id
# alone, so there is no path that returns someone else's conversation.
# ---------------------------------------------------------------------------
class ChatSessionUpsert(BaseModel):
    # The client's DisplayMessage[] array, stored verbatim.
    messages: List[Dict[str, Any]] = PydanticField(default_factory=list)
    surface: Optional[str] = None
    title: Optional[str] = None


@router.get("/sessions")
async def list_chat_sessions(
    surface: Optional[str] = None,
    limit: int = 20,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(get_current_user),
):
    """The caller's transcripts, newest first. Metadata only, no message bodies."""
    sessions = chat_sessions.list_sessions(db, current_user.email, surface=surface, limit=limit)
    return {"sessions": [chat_sessions.to_summary(s) for s in sessions], "count": len(sessions)}


@router.get("/sessions/{session_id}")
async def get_chat_session(
    session_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(get_current_user),
):
    """One of the caller's transcripts, with its messages."""
    session = chat_sessions.get_session(db, current_user.email, session_id)
    if session is None:
        # 404 for both "no such session" and "not yours" — distinguishing them
        # would confirm another user's session exists.
        raise HTTPException(status_code=404, detail="Chat session not found")
    return chat_sessions.to_detail(session)


@router.put("/sessions/{session_id}")
async def put_chat_session(
    session_id: str,
    payload: ChatSessionUpsert,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(get_current_user),
):
    """Create or replace one of the caller's transcripts."""
    session = chat_sessions.upsert_session(
        db,
        current_user.email,
        session_id,
        messages=payload.messages,
        surface=payload.surface,
        title=payload.title,
    )
    return chat_sessions.to_summary(session)


@router.delete("/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete one of the caller's transcripts."""
    if not chat_sessions.delete_session(db, current_user.email, session_id):
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"deleted": 1}


@router.delete("/sessions")
async def delete_chat_sessions(
    surface: Optional[str] = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(get_current_user),
):
    """Clear the caller's transcripts, optionally just one surface's."""
    return {"deleted": chat_sessions.delete_sessions(db, current_user.email, surface=surface)}

# Process-wide profile metrics (cheap, in-memory). Surfaced via the structured
# log line below and readable by tests / a future scrape endpoint. Keys:
#   applied        — a profile drove a turn
#   load_error     — profile could not be loaded (bad ref / no access / missing)
#   tool_fallback  — profile listed tools but none matched this surface
#   model_rejected — profile pinned a model not in the allowlist
from collections import Counter as _Counter

_PROFILE_METRICS: "_Counter[str]" = _Counter()
_PROFILE_LOAD_MS_TOTAL = {"sum": 0.0, "n": 0}


def _profile_metric(name: str, inc: int = 1) -> None:
    _PROFILE_METRICS[name] += inc


def get_profile_metrics() -> Dict[str, Any]:
    """Snapshot of profile counters + mean load latency (for tests / scraping)."""
    n = _PROFILE_LOAD_MS_TOTAL["n"]
    return {
        **dict(_PROFILE_METRICS),
        "load_ms_avg": (_PROFILE_LOAD_MS_TOTAL["sum"] / n) if n else 0.0,
        "load_count": n,
    }


def _profile_unavailable_result(
    profile_ref: str, reason: str
) -> tuple[str, List[Any], Optional[str]]:
    """Fail-safe result for a profile that was requested but couldn't be loaded.

    A profile was *explicitly* selected, so the worst possible outcome is to
    silently fall back to the full Self-Service surface + default persona — the
    narrow agent then masquerades as the whole hub (every tool, every capability)
    which is both confusing and a governance hole. Instead we grant NO tools and
    a minimal prompt that tells the user the selected agent could not be loaded,
    so the failure is visible and actionable rather than masked.
    """
    scaffold = get_profile_base_scaffold(tools_override=[])
    notice = (
        "\n\n## SELECTED AGENT PROFILE UNAVAILABLE\n"
        "The agent profile the user selected could not be loaded, so NO "
        "specialized persona or tools are active for this turn. Do not pretend "
        "to be the Self-Service Hub or any other agent, and do not claim access "
        "to tools you were not given (you have none). Briefly tell the user that "
        "their selected agent profile could not be loaded and to retry or contact "
        "an administrator, then answer only from general knowledge if you can.\n"
        f"(diagnostic — load failure: {reason})"
    )
    return f"{scaffold}{notice}", [], None


def _apply_agent_profile(
    profile_ref: str,
    obo_token: Optional[str],
    visible_tools: List[Any],
    user_identity: Dict[str, str],
    agent_mode: str = "unified",
) -> tuple[Optional[str], List[Any], Optional[str]]:
    """Load an agent profile and derive (system_prompt, tools, model_endpoint).

    A failure to load the profile (bad ref, no access, missing file) never
    breaks chat, but it also must NOT silently fall back to the full surface +
    default Self-Service persona — that turns the explicitly-selected narrow
    agent into a masquerade of the whole hub. Instead we fail safe via
    ``_profile_unavailable_result`` (no tools + a visible "profile unavailable"
    notice) so the failure is surfaced rather than masked.

    Tools: the profile's allowlist is *intersected* with ``visible_tools`` — it
    can only ever narrow what the admin-governed surface already permits, never
    widen it. If the allowlist matches *nothing* (e.g. the profile is authored
    against the Command Center's AI Gateway MCP tool ids, which differ from this
    runtime's tool registry) the agent gets NO tools and we log loudly — we do
    NOT fall back to the full surface, since handing a narrow agent every tool
    makes it behave (and describe itself) like the full Self-Service Hub.

    Prompt: by default the profile persona is *layered on top of* the runtime's
    structural prompt (formatting, tool mechanics, workflow/form routing) so a
    profile doesn't silently drop those contracts. A profile can opt into full
    replacement with ``base: none`` in its frontmatter.
    """
    import time as _time

    from app.providers.profiles import ProfileError, get_profile_provider

    _t0 = _time.perf_counter()
    try:
        profile = get_profile_provider().get_profile(obo_token, profile_ref)
    except ProfileError as exc:
        _profile_metric("load_error")
        logger.warning("Agent profile '%s' could not be loaded: %s", profile_ref, exc)
        return _profile_unavailable_result(profile_ref, str(exc))
    except Exception as exc:  # noqa: BLE001 - never break chat on profile load
        _profile_metric("load_error")
        logger.warning("Unexpected error loading agent profile '%s': %s", profile_ref, exc)
        return _profile_unavailable_result(profile_ref, str(exc))
    finally:
        _PROFILE_LOAD_MS_TOTAL["sum"] += (_time.perf_counter() - _t0) * 1000.0
        _PROFILE_LOAD_MS_TOTAL["n"] += 1

    return _compose_profile(profile, visible_tools, user_identity, agent_mode)


def _compose_profile(
    profile: Any,
    visible_tools: List[Any],
    user_identity: Dict[str, str],
    agent_mode: str = "unified",
) -> tuple[Optional[str], List[Any], Optional[str]]:
    """Turn a loaded/inline profile into (system_prompt, tools, model_endpoint).

    Shared by the saved-profile (``_apply_agent_profile``) and unsaved-draft
    (``_apply_inline_profile`` / Try-it) paths so both enforce the same tool
    intersection, prompt layering, and model allowlist rules.
    """
    # ---- tools: narrow to the profile's allowlist (intersection only) --------
    # Profiles store canonical, server-qualified tool ids ("<server>/<tool>"),
    # but this runtime's tool registry keys on the bare tool name. Match on the
    # full id when present, else on the suffix after the last "/", so a profile
    # authored as "sql/run_sql" still binds to this surface's "run_sql".
    allow = {t.strip() for t in (profile.tools or []) if t.strip()}
    if not allow:
        # An empty allowlist means the profile grants NO tools — NOT the full
        # surface. A new/blank draft therefore can't masquerade as the
        # Self-Service agent by inheriting all 50+ tools (which is what made an
        # unconfigured agent describe itself like Self-Service).
        tools = []
    else:
        def _tool_allowed(t: Any) -> bool:
            # Match a runtime tool against the profile's allowlist. Profiles store
            # canonical ids; a bare name ("run_sql") or a server-qualified id
            # ("<server_label>/<tool>") authored against the AI Gateway MCP
            # catalog. Runtime MCP tools carry a ``server_label`` (the registered
            # source name); local tools don't.
            #  - bare allow id  -> matches a tool with that name (any server)
            #  - "L/t" allow id -> if the tool HAS a label, require an EXACT
            #    "label/name" match (so it can't bind a same-named tool on a
            #    different server); if the tool has NO label (local tool), fall
            #    back to a suffix match so "sql/run_sql" still binds local "run_sql".
            n = getattr(t, "name", None)
            if n is None:
                return False
            label = getattr(t, "server_label", None)
            for a in allow:
                if a == n:
                    return True
                if "/" in a:
                    srv, suffix = a.rsplit("/", 1)
                    if label is not None:
                        if a == f"{label}/{n}":
                            return True
                    elif suffix == n:
                        return True
            return False

        matched = [t for t in visible_tools if _tool_allowed(t)]
        matched_names = {getattr(t, "name", None) for t in matched}
        missing = {a for a in allow if a.rsplit("/", 1)[-1] not in matched_names}
        # A profile can only ever NARROW the admin-governed surface — never widen
        # it. So we grant exactly the intersection. When NOTHING matches we grant
        # NO tools (not the full surface): handing a narrow agent all 50+ tools is
        # the opposite of narrowing and makes it describe itself as the full
        # Self-Service Hub. An empty match usually means the profile's tool ids
        # (authored against the Agent Studio's AI Gateway MCP catalog) aren't
        # exposed on this runtime — surface that loudly so it gets fixed at the
        # source rather than silently masking it with the whole toolset.
        tools = matched
        if not matched:
            _profile_metric("tool_no_match")
            logger.warning(
                "Agent profile '%s' lists tools but NONE match this surface (%s). "
                "Granting NO tools (a profile can only narrow, never widen). The "
                "tool ids likely differ between the Agent Studio (AI Gateway MCP) "
                "and this runtime's registry — align the names so the agent can "
                "bind its intended tools.",
                profile.name, ", ".join(sorted(allow)),
            )
        elif missing:
            logger.info(
                "Agent profile '%s' references tools not available on this surface: %s",
                profile.name, ", ".join(sorted(missing)),
            )

    # ---- prompt: layer the profile on a MINIMAL structural scaffold ----------
    # The profile body is the agent's identity. We layer it on a small runtime
    # output/tool contract (markdown + OBO + tool list) — NOT the Self-Service
    # persona. The Self-Service prompt is one profile among many, not a global
    # baseline, so a custom profile never inherits the Self-Service identity. A
    # profile can drop even the scaffold with ``base: none`` (standalone).
    profile_block = profile.system_prompt()
    if profile.standalone:
        system_prompt = profile_block
    else:
        base_prompt = get_profile_base_scaffold(tools_override=tools)
        system_prompt = (
            f"{base_prompt}\n\n"
            "## ACTIVE AGENT PROFILE (authoritative persona & task instructions)\n"
            f"You are running as the **{profile.name}** profile. The instructions "
            "below define your persona, specialization, and task behavior. They are "
            "your primary identity — follow them fully. The only rules that override "
            "them are the runtime output/tool contracts above (markdown formatting "
            "and tool-use/OBO mechanics).\n\n"
            f"{profile_block}"
        )

    if user_identity:
        system_prompt += "\n\nCURRENT USER IDENTITY:\n" + "".join(
            f"- {k.title()}: {v}\n" for k, v in user_identity.items()
        )

    # ---- model: honor the profile's pinned endpoint only if allowlisted ------
    # Routing to an arbitrary serving endpoint bypasses the AI Gateway's
    # guardrails / rate + cost limits, so a profile's model must be explicitly
    # allowlisted (AGENT_PROFILE_MODEL_ALLOWLIST). Otherwise we ignore it and let
    # the default gateway routing apply.
    model_endpoint: Optional[str] = None
    if profile.model:
        allow = settings.agent_profile_model_allowlist
        if "*" in allow or profile.model in allow:
            model_endpoint = profile.model
        else:
            _profile_metric("model_rejected")
            logger.warning(
                "Agent profile '%s' pins model '%s' which is not in "
                "AGENT_PROFILE_MODEL_ALLOWLIST — ignoring and using default routing.",
                profile.name, profile.model,
            )

    _profile_metric("applied")
    logger.info(
        "Applied agent profile '%s' (tools=%d, skills=%d, model=%s, base=%s)",
        profile.name, len(tools), len(profile.skills),
        model_endpoint or "default", "standalone" if profile.standalone else "layered",
    )
    return system_prompt, tools, model_endpoint


def _apply_inline_profile(
    spec: Dict[str, Any],
    visible_tools: List[Any],
    user_identity: Dict[str, str],
    agent_mode: str = "unified",
) -> tuple[Optional[str], List[Any], Optional[str]]:
    """Apply an UNSAVED draft profile (Agent Studio "Try it").

    Same governance as a saved profile — tools intersect the user's surface and
    the model is allowlist-gated — but the spec is supplied inline rather than
    loaded from UC, so an author can test a draft before persisting it.
    """
    from app.providers.profiles.client import LoadedProfile

    skills = [
        (s.get("name") or "Skill", s.get("content") or "")
        for s in (spec.get("skills") or [])
        if isinstance(s, dict)
    ]
    profile = LoadedProfile(
        store="inline",
        dir_path="(draft)",
        name=(spec.get("name") or "Draft").strip(),
        prompt=spec.get("prompt") or "",
        tools=[t for t in (spec.get("tools") or []) if isinstance(t, str)],
        skills=skills,
        model=(spec.get("model") or "").strip(),
        base=(spec.get("base") or "full").strip(),
    )
    _profile_metric("inline_applied")
    return _compose_profile(profile, visible_tools, user_identity, agent_mode)


async def _resolve_user_context_block(db: Session, current_user: User) -> Optional[str]:
    """Render the cached user-context block, or nothing at all.

    Deliberately swallows every failure: this is an enrichment, and a broken
    identity provider or an empty profile must degrade to the old behavior
    (an agent that asks more questions) rather than break the conversation.
    """
    if not is_feature_enabled("user_context"):
        return None
    try:
        payload = await get_user_context(db, current_user)
        return render_user_context_block(payload)
    except Exception as e:  # noqa: BLE001 - never fail a turn over prompt enrichment
        logger.warning("Could not build user context for %s: %s", current_user.email, e)
        return None


async def _build_runner_and_history(
    request: ConversationRequest,
    current_user: User,
    db: Session,
    obo_token: Optional[str] = None,
) -> tuple[AgentRunner, List[Dict[str, Any]], str]:
    """Shared setup for both the streaming and non-streaming endpoints.

    Returns ``(runner, history, agent_mode)``. Raises ``HTTPException``
    if the agent feature is disabled.
    """
    if not settings.AGENT_ENABLED:
        raise HTTPException(status_code=503, detail="Agent is currently disabled")

    # Summarize rather than dump: ``editor_draft`` carries a whole workflow draft
    # (instructions markdown + graph), which would flood the log on every turn.
    _ctx = request.context or {}
    _loggable_ctx = {
        k: (f"<{len(v)} keys>" if isinstance(v, dict) else v)
        for k, v in _ctx.items()
        if k != "inline_profile"
    }
    logger.info(f"Incoming agent request context: {_loggable_ctx}")
    logger.info(f"Current User: {current_user.email}")
    logger.info(f"Current User Roles: {current_user.roles}")

    # Two chat surfaces, gated dynamically by the Tool Registry. The unified
    # self-service ("EDH") chat and the workflow-authoring studio each expose a
    # distinct, admin-controlled toolset so e.g. ``ask_your_data`` never appears
    # while authoring and ``preview_workflow_spec`` never appears in EDH. Authoring
    # mode is requested by the in-page assistant via ``context.mode: "authoring"``.
    requested_mode = ((request.context or {}).get("mode") or "").strip().lower()
    is_authoring = requested_mode == "authoring"
    agent_mode = "authoring" if is_authoring else "unified"
    surface = "workflow" if is_authoring else "edh"

    # The authoring studio sends the workflow the admin has open — including edits
    # they typed but have not saved — so the agent edits what is on screen instead
    # of the (stale) database copy and can't silently revert their wording. Popped
    # from the context dict because the runner dumps remaining context entries into
    # the prompt verbatim as "key: value" lines, which would render this as an
    # unreadable dict repr on top of the formatted block we build here.
    surface_context_block: Optional[str] = None
    if is_authoring and isinstance(request.context, dict):
        editor_draft = request.context.pop("editor_draft", None)
        if editor_draft:
            from app.agents.prompts import render_editor_draft_block

            surface_context_block = render_editor_draft_block(editor_draft) or None

    visible_tools = _resolve_visible_tools(db, current_user, surface)

    user_identity = {
        "email": current_user.email,
        # The display name was resolved by auth but never reached the agent, so
        # it had to ask the user their own name.
        "name": current_user.full_name,
        "roles": ", ".join(current_user.roles),
        "entitlements": ", ".join(current_user.entitlements),
    }

    # Everything the agent already knows about this user (roles, open requests,
    # approvals waiting on them, group memberships), so it stops asking. Served
    # from cache and refreshed in the background, so this never blocks the turn.
    user_context_block = await _resolve_user_context_block(db, current_user)

    # An agent profile (authored in the Command Center Agent Studio) can drive
    # this turn: it supplies the system prompt + skills, narrows the tool
    # allowlist, and optionally routes to a specific model. Tools are always
    # intersected with the admin-governed surface toolset, so a profile can only
    # ever *narrow* what the user could already use — never widen it.
    profile_ref = request.profile_ref or ((request.context or {}).get("profile_ref"))
    inline_profile = request.inline_profile or ((request.context or {}).get("inline_profile"))
    profile_system_prompt: Optional[str] = None
    model_endpoint: Optional[str] = None
    if inline_profile:
        # Unsaved draft ("Try it") takes precedence over a saved reference.
        profile_system_prompt, visible_tools, model_endpoint = _apply_inline_profile(
            inline_profile, visible_tools, user_identity, agent_mode
        )
    elif profile_ref:
        profile_system_prompt, visible_tools, model_endpoint = _apply_agent_profile(
            profile_ref, obo_token, visible_tools, user_identity, agent_mode
        )

    runner = AgentRunner(
        system_prompt=profile_system_prompt,
        tools=visible_tools,
        user_identity=user_identity,
        # A design turn chains far more tool calls than a runtime turn, so the
        # studio gets its own budget — on the shared cap it ran out mid-design.
        max_iterations=(
            settings.AGENT_AUTHORING_MAX_ITERATIONS
            if is_authoring
            else settings.AGENT_MAX_ITERATIONS
        ),
        mode=agent_mode,
        model_endpoint=model_endpoint,
        user_context_block=user_context_block,
        surface_context_block=surface_context_block,
    )

    history = _resolve_history(request, current_user, db)

    return runner, history, agent_mode


def _resolve_history(
    request: ConversationRequest,
    current_user: User,
    db: Session,
) -> List[Dict[str, Any]]:
    """Build the model's message history for this turn.

    The client-replayed ``conversation_history`` takes precedence: it is still the
    source of truth while the frontend adopts server-side sessions, so the two
    sides can ship independently. A request carrying only ``session_id`` falls
    back to the stored transcript.
    """
    if not request.conversation_history and request.session_id:
        try:
            session = chat_sessions.get_session(db, current_user.email, request.session_id)
            if session is None:
                # Unknown id, or someone else's — either way this user has no
                # such history. Start clean rather than leaking its existence.
                return []
            return chat_sessions.transcript_to_history(session.messages or [])
        except Exception as e:  # noqa: BLE001 - a bad transcript must not kill the turn
            logger.warning("Could not load session %s history: %s", request.session_id, e)
            return []

    history: List[Dict[str, Any]] = []
    if request.conversation_history:
        for msg in request.conversation_history:
            if msg.type == "tool":
                # Synthetic tool-result replay (e.g. after a Genie poll
                # completes). Preserve linkage to the originating
                # assistant tool_call so the LLM accepts the message.
                history.append(
                    {
                        "role": "tool",
                        "content": msg.content,
                        "tool_call_id": msg.tool_call_id or msg.id,
                        "name": msg.name or "tool",
                    }
                )
            else:
                role = "user" if msg.type == "user" else "assistant"
                entry: Dict[str, Any] = {
                    "role": role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "type": msg.type,
                }
                # When the UI replays an assistant tool-call announcement,
                # carry the ``tool_calls`` block through verbatim so the
                # subsequent ``role='tool'`` message has its required
                # linkage (model serving endpoints reject orphan tool
                # messages with ``HTTP 400 BAD_REQUEST``).
                if msg.type == "agent" and msg.tool_calls:
                    entry["tool_calls"] = msg.tool_calls
                history.append(entry)

    return history


def _extract_obo_token(req: Request) -> Optional[str]:
    """Pull the forwarded user OBO token off the request, if present."""
    if hasattr(req, "state") and hasattr(req.state, "token"):
        token = req.state.token
        if token:
            logger.info(
                f"Agent Endpoint: Found OBO token in request state (len={len(token)})"
            )
            return token
        logger.info("Agent Endpoint: No OBO token in request state")
    return None


@router.post("/conversation", response_model=AgentResponse)
async def handle_conversation(
    request: ConversationRequest, 
    req: Request,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(get_current_user)
):
    """Handle a conversation turn with the agent."""
    try:
        obo_token = _extract_obo_token(req)
        runner, history, _agent_mode = await _build_runner_and_history(request, current_user, db, obo_token)

        # Run agent
        result = await runner.run(
            query=request.query,
            history=history,
            context=request.context,
            obo_token=obo_token
        )
        
        agent_message = result.get("content") or ""
        tool_calls = result.get("tool_calls", [])
        
        # Post-processing: Extract JSON instructions
        json_instructions = _extract_json_instructions(agent_message)
        if json_instructions:
            agent_message = _clean_message_remove_json(agent_message)
            if not agent_message.strip():
                agent_message = "Perfect! I have all the information I need. Ready to proceed to the form."

        # Routing and Follow-ups
        form_route = None
        follow_up_questions = None
        requires_more_info = True
        
        if json_instructions:
            form_path = json_instructions.get("form_path", "")
            if form_path:
                path_parts = form_path.strip("/").split("/")
                title = " ".join([part.replace("-", " ").title() for part in path_parts])
                form_route = {"path": form_path, "title": title}
                requires_more_info = False
        
        return AgentResponse(
            message=agent_message,
            follow_up_questions=follow_up_questions,
            form_route=form_route,
            requires_more_info=requires_more_info,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in agent conversation: {str(e)}", exc_info=True)
        # Don't expose usage internal errors to the client
        raise HTTPException(status_code=500, detail="An internal error occurred while processing your request.")


@router.post("/conversation/stream")
async def stream_conversation(
    request: ConversationRequest,
    req: Request,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream a conversation turn as Server-Sent Events.

    The response media type is ``text/event-stream``; the body is a
    sequence of frames defined by :mod:`app.agents.events`. The browser
    consumes this via fetch + ``ReadableStream`` (see
    ``src/lib/agentStream.ts``) rather than the built-in ``EventSource``,
    since we need POST + custom auth headers.

    Errors during setup raise plain HTTP responses; errors *during*
    streaming are surfaced as ``error`` events so the UI can render
    them in-line without dropping the response.
    """
    obo_token = _extract_obo_token(req)

    try:
        runner, history, _agent_mode = await _build_runner_and_history(request, current_user, db, obo_token)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error preparing agent stream: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while preparing the agent stream.",
        )

    async def event_source() -> AsyncIterator[bytes]:
        try:
            async for event in runner.run_stream(
                query=request.query,
                history=history,
                context=request.context,
                obo_token=obo_token,
            ):
                # The Self Service agent occasionally embeds a JSON
                # ``route_to_form`` instruction inside its final
                # ``MessageEvent``. We do the same post-processing the
                # non-streaming endpoint does (extract + clean) and
                # emit a structured ``RouteEvent`` so the UI can render
                # the "Continue to form" CTA without parsing markdown.
                if isinstance(event, MessageEvent) and event.content:
                    instructions = _extract_json_instructions(event.content)
                    if instructions:
                        cleaned = _clean_message_remove_json(event.content)
                        if not cleaned.strip():
                            cleaned = (
                                "Perfect! I have all the information I need. "
                                "Ready to proceed to the form."
                            )
                        form_path = instructions.get("form_path", "")
                        if form_path:
                            path_parts = form_path.strip("/").split("/")
                            title = " ".join(
                                part.replace("-", " ").title()
                                for part in path_parts
                            )
                            yield serialize_sse(
                                RouteEvent(path=form_path, title=title)
                            ).encode("utf-8")
                        # Replace the original message with the cleaned
                        # one so the user never sees the raw JSON block.
                        event = MessageEvent(content=cleaned)
                yield serialize_sse(event).encode("utf-8")
        except Exception as e:
            logger.error(f"Agent stream failed mid-flight: {e}", exc_info=True)
            yield serialize_sse(
                ErrorEvent(message="Agent stream failed.", fatal=True)
            ).encode("utf-8")
            yield serialize_sse(DoneEvent()).encode("utf-8")

    headers = {
        # Disable buffering on intermediaries (nginx etc.) so events
        # arrive promptly rather than being held until the response
        # body is complete.
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(event_source(), media_type="text/event-stream", headers=headers)

# ---------------------------------------------------------------------------
# Onboarding suggestions (pre-prompting)
# ---------------------------------------------------------------------------

_SUGGESTIONS_LIMIT = 4


class SuggestionItem(BaseModel):
    label: str
    prompt: str


class SuggestionsRequest(BaseModel):
    # Lightweight, client-supplied personalization hints (e.g. the user's most
    # recent chat topics pulled from localStorage). Never trusted for auth.
    recent_topics: Optional[List[str]] = None


class SuggestionsResponse(BaseModel):
    suggestions: List[SuggestionItem]
    # True when generated by the LLM; False when the deterministic fallback ran.
    generated: bool


def _derive_persona(roles: Optional[List[str]]) -> str:
    """Map application roles to a single persona, mirroring the frontend's
    ``derivePersona`` priority order.

    The priority list lives in ``user_context`` because the agent's user-context
    block reports the same persona; keeping one copy stops the two from drifting.
    """
    return derive_persona(roles)


def _parse_suggestions(content: Optional[str], limit: int) -> List[Dict[str, str]]:
    """Extract a JSON array of ``{label, prompt}`` from the LLM response.

    Defensive: tolerates markdown fences and leading/trailing prose, and
    returns ``[]`` for anything unparseable so the caller can fall back.
    """
    if not content:
        return []
    text = content.strip()
    # Pull out the first JSON array we can find.
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    out: List[Dict[str, str]] = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt", "")).strip()
        if not prompt or prompt.lower() in seen:
            continue
        label = str(item.get("label", "")).strip() or "Suggestion"
        seen.add(prompt.lower())
        out.append({"label": label, "prompt": prompt})
    return out[:limit]


@router.post("/suggestions", response_model=SuggestionsResponse)
async def get_onboarding_suggestions(
    request: SuggestionsRequest,
    current_user: User = Depends(get_current_user),
) -> SuggestionsResponse:
    """Return a short set of personalized starting prompts for the home page.

    One cheap, tool-less LLM call per invocation (the frontend caches the
    result per session). Falls back to deterministic, role-based prompts if the
    model is unavailable or returns anything we can't parse.
    """
    if not is_feature_enabled("onboarding_suggestions"):
        raise HTTPException(status_code=404, detail="Onboarding suggestions are not enabled")

    persona = _derive_persona(current_user.roles)

    try:
        messages = get_onboarding_suggestions_messages(
            persona=persona,
            roles=current_user.roles,
            recent_topics=request.recent_topics,
            limit=_SUGGESTIONS_LIMIT,
        )
        client = AgentLLMClient()
        result = await client.generate_response(
            messages=messages,
            temperature=0.4,
            max_tokens=500,
        )
        parsed = _parse_suggestions(result.get("content", ""), _SUGGESTIONS_LIMIT)
        if parsed:
            return SuggestionsResponse(
                suggestions=[SuggestionItem(**s) for s in parsed],
                generated=True,
            )
        logger.info("Onboarding suggestions: no parseable LLM output, using fallback")
    except Exception as e:  # noqa: BLE001 - never block login on suggestions
        logger.warning(f"Onboarding suggestions LLM call failed, using fallback: {e}")

    fallback = default_onboarding_suggestions(persona, limit=_SUGGESTIONS_LIMIT)
    return SuggestionsResponse(
        suggestions=[SuggestionItem(**s) for s in fallback],
        generated=False,
    )


class FeedbackRequest(BaseModel):
    # trace_id returned on the terminal SSE ``done`` event for the turn.
    trace_id: str
    # Thumbs up/down or a numeric rating; free-form value the judge correlates.
    value: Any
    comment: Optional[str] = None


@router.post("/feedback")
async def submit_agent_feedback(
    request: FeedbackRequest,
    current_user: User = Depends(get_current_user),
):
    """Attach user feedback to an agent turn's MLflow trace (best practice).

    Feedback keyed by ``trace_id`` powers the quality dashboard and the
    scheduled LLM-as-judge comparison (human vs. judge agreement).
    """
    from app.agents.tracing import log_feedback, tracing_active

    if not tracing_active():
        # Tracing off: accept but no-op so the UI doesn't error.
        logger.info(
            "Feedback received but tracing disabled (trace=%s value=%s)",
            request.trace_id, request.value,
        )
        return {"recorded": False, "reason": "tracing_disabled"}

    recorded = log_feedback(
        trace_id=request.trace_id,
        value=request.value,
        comment=request.comment,
        user=current_user.email,
    )
    return {"recorded": recorded}


@router.get("/health")
async def agent_health():
    """Health check for agent endpoint."""
    return {"status": "healthy", "agent_enabled": settings.AGENT_ENABLED}
