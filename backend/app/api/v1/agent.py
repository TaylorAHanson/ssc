"""
Agent API endpoints for conversation handling.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from app.agents.prompts import get_agent_prompt, AGENT_TOOLS, SYSTEM_CONTEXT
from app.agents.forms_registry import get_form_schema
from app.model_serving.agent_llm import AgentLLMClient
from app.core.config import settings
import logging
import json
import re

logger = logging.getLogger(__name__)

router = APIRouter()


def _map_request_type_to_route(request_type: str, route: str) -> Optional[Dict[str, str]]:
    """Map request type to form route."""
    # If route is already a full path, use it
    if route.startswith("/"):
        # Extract title from route
        route_parts = route.strip("/").split("/")
        if len(route_parts) >= 2:
            form_name = route_parts[1].replace("-", " ").title()
            return {"path": route, "title": form_name}
    
    # Map request types to routes
    route_mapping = {
        "workspace_access": {"path": "/paas/workspace-access", "title": "Get Workspace Access"},
        "catalog_schema_table": {"path": "/paas/request-catalog", "title": "Create Catalog/Schema/Table"},
        "catalog_schema_table_access": {"path": "/paas/request-access", "title": "Request Data Access"},
        "workspace_provision": {"path": "/paas/provision-workspace", "title": "Provision New Workspace"},
        "service_principal": {"path": "/paas/service-principal", "title": "Provision Service Principal"},
        "marketplace_certification": {"path": "/paas/marketplace", "title": "Marketplace Certification"},
        "github_repo_creation": {"path": "/paas/github-repo-creation", "title": "GitHub Repository Creation"},
        "rest_api_access": {"path": "/daas/rest-api", "title": "Request REST API Access"},
        "batch_data_access": {"path": "/daas/batch-data", "title": "Request Batch Data Access"},
    }
    
    return route_mapping.get(request_type)


def _extract_json_instructions(message: str) -> Optional[Dict[str, Any]]:
    """Extract JSON instructions from agent message if present."""
    # Look for JSON code blocks in the message - handle nested braces
    # First try with json language tag
    json_pattern = r'```json\s*(\{(?:[^{}]|(?:\{[^{}]*\}))*\})\s*```'
    matches = re.findall(json_pattern, message, re.DOTALL | re.IGNORECASE)
    
    if not matches:
        # Try without language tag
        json_pattern = r'```\s*(\{(?:[^{}]|(?:\{[^{}]*\}))*\})\s*```'
        matches = re.findall(json_pattern, message, re.DOTALL)
    
    # If simple regex doesn't work, try a more robust approach
    if not matches:
        # Find code block boundaries and extract content
        code_block_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
        matches = re.findall(code_block_pattern, message, re.DOTALL | re.IGNORECASE)
    
    for match in matches:
        try:
            # Try to parse the JSON
            data = json.loads(match.strip())
            if isinstance(data, dict) and data.get("action") == "route_to_form":
                return data
        except json.JSONDecodeError:
            # If parsing fails, try to find the JSON object more carefully
            # Look for the action field to locate the JSON
            if '"action"' in match and '"route_to_form"' in match:
                # Try to extract just the JSON object
                try:
                    # Find the opening brace and try to match closing brace
                    start = match.find('{')
                    if start != -1:
                        # Count braces to find the end
                        brace_count = 0
                        end = start
                        for i, char in enumerate(match[start:], start):
                            if char == '{':
                                brace_count += 1
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
                except:
                    continue
            continue
    
    return None


def _clean_message_remove_json(message: str) -> str:
    """Remove JSON code blocks from message, leaving only the text."""
    # Remove JSON code blocks
    cleaned = re.sub(r'```json\s*\{.*?\}\s*```', '', message, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'```\s*\{.*?\}\s*```', '', cleaned, flags=re.DOTALL)
    # Clean up extra whitespace
    cleaned = re.sub(r'\n\s*\n\s*\n', '\n\n', cleaned)
    return cleaned.strip()


def _infer_route_from_conversation(query: str, conversation_history: Optional[List[Any]]) -> Optional[Dict[str, str]]:
    """Infer form route from conversation content."""
    query_lower = query.lower()
    
    # Check for workspace-related keywords
    if any(word in query_lower for word in ["workspace", "databricks"]):
        if any(word in query_lower for word in ["access", "get access", "need access"]):
            return {"path": "/paas/workspace-access", "title": "Get Workspace Access"}
        elif any(word in query_lower for word in ["create", "new", "provision"]):
            return {"path": "/paas/provision-workspace", "title": "Provision New Workspace"}
    
    # Check for data access keywords
    if any(word in query_lower for word in ["catalog", "schema", "table", "data access"]):
        if any(word in query_lower for word in ["create", "new"]):
            return {"path": "/paas/request-catalog", "title": "Create Catalog/Schema/Table"}
        else:
            return {"path": "/paas/request-access", "title": "Request Data Access"}
    
    # Check for service principal
    if "service principal" in query_lower:
        return {"path": "/paas/service-principal", "title": "Provision Service Principal"}
    
    # Check for GitHub repository
    if any(word in query_lower for word in ["github", "repo", "repository", "git"]):
        if any(word in query_lower for word in ["create", "new", "provision", "set up"]):
            return {"path": "/paas/github-repo-creation", "title": "GitHub Repository Creation"}
    
    # Check for API access
    if any(word in query_lower for word in ["api", "rest", "endpoint"]):
        return {"path": "/daas/rest-api", "title": "Request REST API Access"}
    
    # Check for batch data
    if any(word in query_lower for word in ["batch", "delta sharing"]):
        return {"path": "/daas/batch-data", "title": "Request Batch Data Access"}
    
    return None


class ChatMessage(BaseModel):
    """Chat message model."""
    id: str
    type: str  # 'user' | 'agent'
    content: str
    timestamp: str


class ConversationRequest(BaseModel):
    """Request for agent conversation."""
    query: str
    conversation_history: Optional[List[ChatMessage]] = None
    context: Optional[Dict[str, Any]] = None


class FollowUpQuestion(BaseModel):
    """Follow-up question model."""
    id: str
    question: str
    type: str  # 'text' | 'radio' | 'multi-select'
    options: Optional[List[str]] = None
    required: bool


class AgentResponse(BaseModel):
    """Agent response model."""
    message: str
    follow_up_questions: Optional[List[FollowUpQuestion]] = None
    form_route: Optional[Dict[str, str]] = None
    requires_more_info: bool = True
    form_prefill_data: Optional[Dict[str, Any]] = None


@router.get("/tools")
async def get_agent_tools():
    """Get list of available agent tools."""
    return {
        "tools": AGENT_TOOLS,
        "count": len(AGENT_TOOLS)
    }


@router.get("/prompt")
async def get_agent_prompt_endpoint():
    """Get the agent system prompt and instructions."""
    return {
        "prompt": get_agent_prompt(),
        "context": SYSTEM_CONTEXT
    }


@router.post("/conversation", response_model=AgentResponse)
async def handle_conversation(request: ConversationRequest):
    """
    Handle a conversation turn with the agent.
    """
    if not settings.AGENT_ENABLED:
        raise HTTPException(status_code=503, detail="Agent is currently disabled")
    
    try:
        # Initialize the agent LLM client
        llm_client = AgentLLMClient()
        
        # Build conversation messages
        messages = []
        
        # Determine if we're routing to a specific form (check conversation history or infer)
        form_path = None
        form_schema = None
        
        # Check if form route was mentioned in conversation history
        if request.conversation_history:
            for msg in request.conversation_history:
                # Look for form paths in agent messages
                if msg.type == "agent" and msg.content:
                    # Check for form path patterns
                    path_match = re.search(r'/paas/[-\w]+|/daas/[-\w]+', msg.content)
                    if path_match:
                        form_path = path_match.group(0)
                        break
        
        # If no form path found, try to infer from current query
        if not form_path:
            inferred_route = _infer_route_from_conversation(request.query, request.conversation_history)
            if inferred_route:
                form_path = inferred_route.get("path")
        
        # Get form schema if we have a form path
        if form_path:
            form_schema = get_form_schema(form_path)
            if form_schema:
                logger.info(f"Injecting form schema for {form_path} into prompt")
        
        # Gemini models only support ONE system prompt, so we need to combine everything
        system_prompt = get_agent_prompt(form_schema=form_schema)
        
        # Add context to system prompt if provided
        if request.context:
            context_str = "\n".join([f"{k}: {v}" for k, v in request.context.items()])
            system_prompt = f"{system_prompt}\n\nAdditional context:\n{context_str}"
        
        messages.append({
            "role": "system",
            "content": system_prompt
        })
        
        # Add conversation history
        if request.conversation_history:
            for msg in request.conversation_history:
                role = "user" if msg.type == "user" else "assistant"
                messages.append({
                    "role": role,
                    "content": msg.content
                })
        
        # Add current user query
        messages.append({
            "role": "user",
            "content": request.query
        })
        
        # Format tools for function calling
        def map_type_to_gemini(param_type: str) -> str:
            """Map parameter types to Gemini-compatible types."""
            type_mapping = {
                "float": "number",
                "int": "number",
                "integer": "number",
                "str": "string",
                "string": "string",
                "bool": "boolean",
                "boolean": "boolean",
                "object": "object",
                "array": "array",
                "list": "array",
            }
            return type_mapping.get(param_type.lower(), "string")
        
        tools = None
        if AGENT_TOOLS:
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": {
                            "type": "object",
                            "properties": {
                                param_name: {
                                    "type": map_type_to_gemini(param_info.get("type", "string")),
                                    "description": param_info.get("description", "")
                                }
                                for param_name, param_info in tool.get("parameters", {}).items()
                            },
                            "required": [
                                param_name
                                for param_name, param_info in tool.get("parameters", {}).items()
                                if param_info.get("required", False)
                            ]
                        }
                    }
                }
                for tool in AGENT_TOOLS
            ]
        
        # Call the LLM endpoint
        logger.info(f"Calling agent LLM endpoint: {llm_client.endpoint_name}")
        response = await llm_client.generate_response(
            messages=messages,
            tools=tools,
            temperature=0.7,
            max_tokens=2000
        )
        
        # Extract response content using standardized keys
        agent_message = response.get("content", "")
        tool_calls = response.get("tool_calls", [])
        
        # Clean the message content
        if agent_message:
            # Ensure agent_message is a string
            if not isinstance(agent_message, str):
                agent_message = str(agent_message)
            
            # Remove JSON objects that might be embedded in the text (like signatures)
            agent_message = re.sub(r'\{[^{}]*"signature"[^{}]*\}', '', agent_message, flags=re.IGNORECASE | re.DOTALL)
            agent_message = agent_message.strip()
        
        # Log outcome
        logger.info(f"Agent response length: {len(agent_message) if agent_message else 0}")
        logger.info(f"Tool calls found: {len(tool_calls)}")
        
        # Only use fallback if we truly have no message AND no tool calls
        if not agent_message and not tool_calls:
            logger.warning("No agent message or tool calls found in response")
            agent_message = "I understand. Let me help you with that. (System: No response generated)"
        
        # Extract JSON instructions from message if present
        json_instructions = None
        form_prefill_data = None
        if agent_message:
            json_instructions = _extract_json_instructions(agent_message)
            if json_instructions:
                form_path = json_instructions.get("form_path", "")
                form_prefill_data = json_instructions.get("values_to_insert", {})
                # Clean the message to remove JSON block
                agent_message = _clean_message_remove_json(agent_message)
                # Only add fallback if message is truly empty after cleaning
                if not agent_message.strip():
                    agent_message = "Perfect! I have all the information I need. Ready to proceed to the form."
                logger.info(f"Extracted JSON instructions: form_path={form_path}, prefill_data keys={list(form_prefill_data.keys())}")
        
        # Process tool calls if present
        form_route = None
        follow_up_questions = None
        requires_more_info = True
        
        # If we have JSON instructions, use them for routing
        if json_instructions:
            form_path = json_instructions.get("form_path", "")
            if form_path:
                # Extract title from path
                path_parts = form_path.strip("/").split("/")
                if len(path_parts) >= 2:
                    # Handle paths like /community/links -> "Community Links"
                    title = " ".join([part.replace("-", " ").title() for part in path_parts])
                else:
                    # Fallback for simple paths
                    title = form_path.split("/")[-1].replace("-", " ").title()
                
                form_route = {"path": form_path, "title": title}
                requires_more_info = False
                logger.info(f"Using JSON instructions for routing: {form_route}")
        
        if tool_calls:
            # Process each tool call
            for tool_call in tool_calls:
                function_name = tool_call.get("function", {}).get("name", "")
                function_args = tool_call.get("function", {}).get("arguments", {})
                
                # Parse arguments if it's a string (JSON)
                if isinstance(function_args, str):
                    try:
                        function_args = json.loads(function_args)
                    except:
                        function_args = {}
                
                logger.info(f"Tool call: {function_name} with args: {function_args}")
                
                # Handle different tool functions
                if function_name == "determine_request_type":
                    route = function_args.get("route", "")
                    request_type = function_args.get("request_type", "")
                    if route:
                        form_route = _map_request_type_to_route(request_type, route)
                        logger.info(f"Determined form route: {form_route}")
                
                elif function_name == "generate_follow_up_questions":
                    questions_data = function_args.get("questions", [])
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
                        logger.info(f"Generated {len(follow_up_questions)} follow-up questions")
                
                elif function_name == "validate_answers":
                    is_complete = function_args.get("is_complete", False)
                    if is_complete:
                        requires_more_info = False
                        logger.info("Answers validated - ready for form routing")
        
        # If no tool calls but we have a message, try to infer from message content
        if not tool_calls and agent_message:
            if any(phrase in agent_message.lower() for phrase in ["ready to submit", "form is ready", "let's proceed", "i'll create"]):
                requires_more_info = False
                form_route = _infer_route_from_conversation(request.query, request.conversation_history)
        
        return AgentResponse(
            message=agent_message,
            follow_up_questions=follow_up_questions,
            form_route=form_route,
            requires_more_info=requires_more_info,
            form_prefill_data=form_prefill_data
        )
        
    except ValueError as e:
        logger.error(f"Configuration error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Agent configuration error: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error in agent conversation: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error processing conversation: {str(e)}"
        )


@router.post("/determine-request-type")
async def determine_request_type(query: str):
    """Determine request type from user query."""
    raise HTTPException(status_code=501, detail="Not implemented yet")


@router.post("/generate-questions")
async def generate_questions(request_type: str, current_answers: Optional[Dict[str, Any]] = None):
    """Generate follow-up questions for a request type."""
    raise HTTPException(status_code=501, detail="Not implemented yet")


@router.get("/health")
async def agent_health():
    """Health check for agent endpoint and model serving connectivity."""
    try:
        # Check if agent is enabled
        if not settings.AGENT_ENABLED:
            return {
                "status": "disabled",
                "agent_enabled": False,
                "model_serving": "not_checked"
            }
        
        # Check configuration
        config_status = {
            "endpoint_configured": bool(settings.MODEL_SERVING_AGENT_LLM_ENDPOINT),
            "workspace_url_configured": bool(settings.DATABRICKS_WORKSPACE_URL),
            "api_key_configured": bool(settings.MODEL_SERVING_API_KEY or settings.DATABRICKS_TOKEN)
        }
        
        # Try to initialize the client
        try:
            llm_client = AgentLLMClient()
            client_status = "configured"
        except ValueError as e:
            client_status = f"configuration_error: {str(e)}"
            return {
                "status": "configuration_error",
                "agent_enabled": True,
                "config": config_status,
                "client": client_status,
                "model_serving": "not_checked"
            }
        
        return {
            "status": "healthy",
            "agent_enabled": True,
            "config": config_status,
            "client": client_status,
            "model_serving": "configured",
            "endpoint_name": settings.MODEL_SERVING_AGENT_LLM_ENDPOINT
        }
    except Exception as e:
        logger.error(f"Error in agent health check: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
            "agent_enabled": settings.AGENT_ENABLED
        }
