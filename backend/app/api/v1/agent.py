"""
Agent API endpoints for conversation handling.
"""
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, AsyncIterator
from app.agents.events import (
    DoneEvent,
    ErrorEvent,
    MessageEvent,
    RouteEvent,
    StatusEvent,
    serialize_sse,
)
from app.agents.prompts import get_agent_prompt, AGENT_TOOLS
from app.agents.runner import AgentRunner
from app.core.config import settings
from app.api.deps import get_current_user
from app.models.user import User
import logging
import json
import re

logger = logging.getLogger(__name__)

router = APIRouter()

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

class ConversationRequest(BaseModel):
    query: str
    conversation_history: Optional[List[ChatMessage]] = None
    context: Optional[Dict[str, Any]] = None

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
    form_prefill_data: Optional[Dict[str, Any]] = None

@router.get("/tools")
async def get_agent_tools(current_user: User = Depends(get_current_user)):
    """Get list of available agent tools, filtered by user permissions."""
    visible_tools = []
    for tool in AGENT_TOOLS:
        allowed = True
        if hasattr(tool, "required_role") and tool.required_role:
            if not current_user.has_role(tool.required_role):
                allowed = False
        
        if allowed:
            visible_tools.append({
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema
            })
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

def _build_runner_and_history(
    request: ConversationRequest,
    current_user: User,
) -> tuple[AgentRunner, List[Dict[str, Any]], str]:
    """Shared setup for both the streaming and non-streaming endpoints.

    Returns ``(runner, history, agent_mode)``. Raises ``HTTPException``
    if the agent feature is disabled.
    """
    if not settings.AGENT_ENABLED:
        raise HTTPException(status_code=503, detail="Agent is currently disabled")

    logger.info(f"Incoming agent request context: {request.context}")
    logger.info(f"Current User: {current_user.email}")
    logger.info(f"Current User Roles: {current_user.roles}")

    # Resolve agent mode from frontend context (normalize a few spellings)
    agent_mode = "self_service"
    if request.context:
        raw_mode = request.context.get("mode") or request.context.get("agent_mode", "self_service")
        mode_map = {
            "self service agent": "self_service",
            "self_service": "self_service",
            "governance": "governance",
            "finops": "finops",
            "ask your data": "ask_your_data",
            "ask_your_data": "ask_your_data",
        }
        agent_mode = mode_map.get(str(raw_mode).lower(), "self_service")
    logger.info(f"Resolved agent mode: {agent_mode}")

    # Filter tools by user permissions and mode. The "ask_your_data" mode
    # is a curated allow-list: read-only data-discovery tools (catalog /
    # schema / table / volume listing, owner lookup, target-workspace
    # listing) plus the slow Genie escape hatch. Provisioning workflows,
    # audit tooling, and entitlement search are intentionally excluded so
    # the LLM doesn't try to route the user out of this tab.
    ASK_YOUR_DATA_TOOLS = {
        "get_catalog_list",
        "get_schema_list",
        "get_table_list",
        "get_volume_list",
        "get_target_workspaces",
        "find_owner",
        "ask_your_data",
    }
    visible_tools = []
    for tool in AGENT_TOOLS:
        if agent_mode == "ask_your_data" and tool.name not in ASK_YOUR_DATA_TOOLS:
            continue

        allowed = True
        if hasattr(tool, "required_role") and tool.required_role:
            if not current_user.has_role(tool.required_role):
                allowed = False

        if allowed:
            visible_tools.append(tool)

    user_identity = {
        "email": current_user.email,
        "roles": ", ".join(current_user.roles),
        "entitlements": ", ".join(current_user.entitlements),
    }

    runner = AgentRunner(
        tools=visible_tools,
        user_identity=user_identity,
        max_iterations=settings.AGENT_MAX_ITERATIONS,
        mode=agent_mode,
    )

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
                history.append(
                    {
                        "role": role,
                        "content": msg.content,
                        "timestamp": msg.timestamp,
                        "type": msg.type,
                    }
                )

    return runner, history, agent_mode


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
    current_user: User = Depends(get_current_user)
):
    """Handle a conversation turn with the agent."""
    try:
        runner, history, _agent_mode = _build_runner_and_history(request, current_user)
        obo_token = _extract_obo_token(req)

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
        form_prefill_data = None
        if json_instructions:
            form_prefill_data = json_instructions.get("values_to_insert", {})
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
            form_prefill_data=form_prefill_data
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
    try:
        runner, history, _agent_mode = _build_runner_and_history(request, current_user)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error preparing agent stream: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while preparing the agent stream.",
        )

    obo_token = _extract_obo_token(req)

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
                        prefill = instructions.get("values_to_insert") or None
                        if form_path:
                            path_parts = form_path.strip("/").split("/")
                            title = " ".join(
                                part.replace("-", " ").title()
                                for part in path_parts
                            )
                            yield serialize_sse(
                                RouteEvent(
                                    path=form_path,
                                    title=title,
                                    prefill=prefill if isinstance(prefill, dict) else None,
                                )
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

@router.get("/health")
async def agent_health():
    """Health check for agent endpoint."""
    return {"status": "healthy", "agent_enabled": settings.AGENT_ENABLED}
