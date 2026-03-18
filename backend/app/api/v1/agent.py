"""
Agent API endpoints for conversation handling.
"""
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from app.agents.prompts import get_agent_prompt, AGENT_TOOLS
from app.agents.runner import AgentRunner
from app.core.config import settings
from app.api.deps import get_current_user
from app.db.user import UserModel
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
    type: str  # 'user' | 'agent'
    content: str
    timestamp: str

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
async def get_agent_tools(current_user: UserModel = Depends(get_current_user)):
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
async def get_agent_prompt_endpoint(current_user: UserModel = Depends(get_current_user)):
    """Get the agent system prompt and instructions."""
    return {
        "prompt": get_agent_prompt(),
        "context": {
            "user_email": current_user.email,
            "user_roles": [r.name for r in current_user.roles]
        }
    }

@router.post("/conversation", response_model=AgentResponse)
async def handle_conversation(
    request: ConversationRequest, 
    req: Request,
    current_user: UserModel = Depends(get_current_user)
):
    """Handle a conversation turn with the agent."""
    if not settings.AGENT_ENABLED:
        raise HTTPException(status_code=503, detail="Agent is currently disabled")
    
    try:
        # User Refresh removed to preserve Dev Persona overrides
        # The dependency injection provides the correct user state
        
        logger.info(f"Incoming agent request context: {request.context}")
        
        # DEBUG: Log user roles to debug visibility issues
        logger.info(f"Current User: {current_user.email}")
        logger.info(f"Current User Roles: {[r.name for r in current_user.roles]}")

        # Extract mode from context first to filter tools
        agent_mode = "self_service"
        if request.context:
            raw_mode = request.context.get("mode") or request.context.get("agent_mode", "self_service")
            # Normalize mode strings from frontend (e.g., 'Self Service Agent' -> 'self_service')
            mode_map = {
                "self service agent": "self_service",
                "self_service": "self_service",
                "governance": "governance",
                "finops": "finops"
            }
            agent_mode = mode_map.get(str(raw_mode).lower(), "self_service")
        
        logger.info(f"Resolved agent mode: {agent_mode} (from raw: {request.context.get('mode') or request.context.get('agent_mode') if request.context else 'None'})")

        # Filter tools by user permissions AND mode
        visible_tools = []
        for tool in AGENT_TOOLS:
            # Mode-based filtering (Removed restriction on execute_workflow to allow Governance/FinOps workflows)
            # if tool.name == "execute_workflow" and agent_mode != "self_service":
            #     continue

            # Role-based filtering
            allowed = True
            if hasattr(tool, "required_role") and tool.required_role:
                if not current_user.has_role(tool.required_role):
                    allowed = False
            
            if allowed:
                visible_tools.append(tool)

        # Build user identity for the runner
        user_identity = {
            "email": current_user.email,
            "roles": ", ".join([r.name for r in current_user.roles])
        }
        
        # Initialize Runner
        runner = AgentRunner(
            tools=visible_tools,
            user_identity=user_identity,
            max_iterations=settings.AGENT_MAX_ITERATIONS,
            mode=agent_mode
        )
        
        # Format history for runner - preserve all metadata including timestamps
        history = []
        if request.conversation_history:
            for msg in request.conversation_history:
                # Standard roles for LLM
                role = "user" if msg.type == "user" else "assistant"
                history.append({
                    "role": role, 
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "type": msg.type
                })
        
        # Get OBO token if available
        obo_token = None
        if hasattr(req, "state") and hasattr(req.state, "token"):
            obo_token = req.state.token
            if obo_token:
                logger.info(f"Agent Endpoint: Found OBO token in request state (len={len(obo_token)})")
            else:
                logger.info("Agent Endpoint: No OBO token in request state")
            
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

        if tool_calls:
            for tool_call in tool_calls:
                fn_name = tool_call.get("function", {}).get("name", "")
                fn_args = tool_call.get("function", {}).get("arguments", {})
                if isinstance(fn_args, str):
                    try: fn_args = json.loads(fn_args)
                    except: fn_args = {}
                
                if fn_name == "generate_follow_up_questions":
                    questions_data = fn_args.get("questions", [])
                    if questions_data:
                        follow_up_questions = [
                            FollowUpQuestion(
                                id=q.get("id", f"q_{i}"),
                                question=q.get("question", ""),
                                type=q.get("type", "text"),
                                options=q.get("options"),
                                required=q.get("required", True)
                            )
                            for i, q in enumerate(questions_data)
                        ]
                elif fn_name == "validate_answers":
                    if fn_args.get("is_complete", False):
                        requires_more_info = False
        
        return AgentResponse(
            message=agent_message,
            follow_up_questions=follow_up_questions,
            form_route=form_route,
            requires_more_info=requires_more_info,
            form_prefill_data=form_prefill_data
        )
        
    except Exception as e:
        logger.error(f"Error in agent conversation: {str(e)}", exc_info=True)
        # Don't expose usage internal errors to the client
        raise HTTPException(status_code=500, detail="An internal error occurred while processing your request.")

@router.get("/health")
async def agent_health():
    """Health check for agent endpoint."""
    return {"status": "healthy", "agent_enabled": settings.AGENT_ENABLED}
