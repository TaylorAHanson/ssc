"""
Reusable Agent Runner for executing agent loops with tools.
"""
import json
import logging
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Union
from app.model_serving.agent_llm import AgentLLMClient
from app.agents.prompts import get_agent_prompt
from app.core.config import settings

logger = logging.getLogger(__name__)

# Placeholder we substitute when pruning an old tool output to stay within
# the context window. Kept short and recognizable so the LLM understands
# the data was dropped (and avoid double-pruning the same message).
_PRUNED_TOOL_PLACEHOLDER = (
    "[truncated: earlier tool result removed to stay within context window. "
    "Re-run the tool with more specific filters if you need this data again.]"
)


def _truncate_tool_output(content: str, max_chars: int) -> str:
    """Cap a single serialized tool output to ``max_chars`` characters.

    We only ever drop from the tail; the prefix is JSON so it's usually
    parseable up to the cut point. The suffix tells the agent the output
    was truncated and how to recover.
    """
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    head = content[:max_chars]
    return (
        head
        + f"\n\n...[truncated: tool returned {len(content)} characters, kept first {max_chars}. "
        + "Re-run the tool with more specific filters/pagination to see the rest.]"
    )


def _estimate_messages_chars(messages: List[Dict[str, Any]]) -> int:
    """Approximate prompt size by summing string lengths of all message content.

    Includes ``tool_calls`` payloads (assistant turns) since those round-trip
    as JSON in the request body and also count toward the model's prompt.
    """
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        tc = m.get("tool_calls")
        if isinstance(tc, list):
            try:
                total += len(json.dumps(tc, default=str))
            except Exception:
                pass
    return total


def _prune_oldest_tool_outputs(messages: List[Dict[str, Any]], max_chars: int) -> int:
    """Replace oldest ``tool`` message contents with a placeholder until the
    total estimated prompt size is at or below ``max_chars``.

    We mutate ``content`` in place rather than removing the message so that
    the ``tool_call_id`` linkage with the assistant turn that requested it
    remains valid (most providers reject orphan tool_calls).

    Returns the number of tool messages whose content was pruned.
    """
    if max_chars <= 0:
        return 0
    pruned = 0
    for m in messages:
        if _estimate_messages_chars(messages) <= max_chars:
            break
        if m.get("role") != "tool":
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        if content == _PRUNED_TOOL_PLACEHOLDER:
            continue
        if len(content) <= len(_PRUNED_TOOL_PLACEHOLDER):
            continue
        m["content"] = _PRUNED_TOOL_PLACEHOLDER
        pruned += 1
    return pruned

class AgentRunner:
    """
    Executes an agent conversation loop (ReAct pattern).
    Works outside of FastAPI request context for background tasks.
    """
    
    def __init__(
        self, 
        system_prompt: Optional[str] = None,
        tools: Optional[List[Any]] = None,
        user_identity: Optional[Dict[str, str]] = None,
        max_iterations: int = 5,
        mode: str = "self_service"
    ):
        self.llm_client = AgentLLMClient()
        self.tools = tools or []
        self.max_iterations = max_iterations
        self.user_identity = user_identity or {}
        self.mode = mode
        
        # Build standard system prompt if not provided
        if system_prompt is None:
            self.system_prompt = get_agent_prompt(tools_override=self.tools, mode=self.mode)
            if self.user_identity:
                id_str = "\n\nCURRENT USER IDENTITY:\n"
                for k, v in self.user_identity.items():
                    id_str += f"- {k.title()}: {v}\n"
                self.system_prompt += id_str
        else:
            self.system_prompt = system_prompt

    async def run(
        self, 
        query: str, 
        history: Optional[List[Dict[str, str]]] = None,
        context: Optional[Dict[str, Any]] = None,
        obo_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes the agent loop for a single query.
        Returns the final standardized response.
        """
        # Inject context into system prompt
        current_system_prompt = self.system_prompt
        if context:
            ctx_str = "\n\nCURRENT CONTEXT:\n" + "\n".join([f"{k}: {v}" for k, v in context.items()])
            current_system_prompt += ctx_str
        
        messages = [{"role": "system", "content": current_system_prompt}]
        
        if history:
            messages.extend(history)
            
        # Add current query with timestamp and type
        messages.append({
            "role": "user", 
            "content": query,
            "timestamp": datetime.now().isoformat(),
            "type": "user"
        })
        
        # Format tools for LLM
        formatted_tools = self._format_tools_for_llm(self.tools)
        
        iteration = 0
        final_content = ""
        final_tool_calls = []
        
        while iteration < self.max_iterations:
            iteration += 1
            logger.info(f"Agent iteration {iteration}/{self.max_iterations}")

            # Defense-in-depth: even with per-tool truncation, a long
            # multi-iteration conversation can accumulate enough tool output
            # to exceed the model's context window. Prune oldest tool
            # results first (newest are most relevant to the current step).
            max_prompt_chars = getattr(settings, "AGENT_MAX_PROMPT_CHARS", 600000)
            pre_prune_size = _estimate_messages_chars(messages)
            if pre_prune_size > max_prompt_chars:
                pruned = _prune_oldest_tool_outputs(messages, max_prompt_chars)
                if pruned:
                    post = _estimate_messages_chars(messages)
                    logger.warning(
                        f"Agent prompt size {pre_prune_size} chars exceeded budget "
                        f"({max_prompt_chars}); pruned {pruned} older tool output(s); "
                        f"new size {post} chars."
                    )

            response = await self.llm_client.generate_response(
                messages=messages,
                tools=formatted_tools,
                temperature=0.0 # Use 0.0 for more deterministic reporting
            )
            
            agent_message = response.get("content") or ""
            tool_calls = response.get("tool_calls", [])
            
            # Standardize message cleaning
            if agent_message:
                agent_message = self._clean_message(agent_message)
            
            # If no tool calls, we're done
            if not tool_calls:
                final_content = agent_message
                break
                
            # Add assistant message to history with timestamp
            messages.append({
                "role": "assistant",
                "tool_calls": tool_calls,
                "timestamp": datetime.now().isoformat(),
                "type": "agent"
            })
            
            # Execute tools
            tool_outputs = []
            executed_any = False
            
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                fn_args = tc.get("function", {}).get("arguments", {})
                
                if isinstance(fn_args, str):
                    try:
                        fn_args = json.loads(fn_args)
                    except:
                        fn_args = {}
                
                # Note: No special case termination tools exist. The agent loop breaks naturally when it stops returning tool_calls.
                
                matching_tool = next((t for t in self.tools if t.name == fn_name), None)
                if matching_tool:
                    try:
                        logger.info(f"Executing tool: {fn_name}")
                        
                        # Inject conversation history/context for execute_workflow if available
                        if fn_name == "execute_workflow":
                            # Pass all messages EXCEPT the system prompt and tool outputs to prevent DB bloat
                            history_for_tool = []
                            for m in messages:
                                if m.get("role") not in ("system", "tool"):
                                    # Copy message and strip heavy tool_calls payload
                                    m_copy = {k: v for k, v in m.items() if k != "tool_calls"}
                                    history_for_tool.append(m_copy)
                            fn_args["conversation_history"] = history_for_tool
                            
                        # Inject OBO token if provided
                        if obo_token:
                            logger.info(f"AgentRunner: Injecting OBO token into tool {fn_name}")
                            fn_args["_obo_token"] = obo_token
                        
                        # Inject user identity for tools that need it (e.g., execute_workflow)
                        if self.user_identity:
                            fn_args["_user_email"] = self.user_identity.get("email")
                            fn_args["_user_roles"] = self.user_identity.get("roles")
                            fn_args["_user_entitlements"] = self.user_identity.get("entitlements")
                            
                        result = await matching_tool.execute(**fn_args)
                        serialized = json.dumps(result, default=str)
                        max_tool_chars = getattr(settings, "AGENT_MAX_TOOL_OUTPUT_CHARS", 25000)
                        truncated = _truncate_tool_output(serialized, max_tool_chars)
                        if len(truncated) < len(serialized):
                            logger.warning(
                                f"Tool '{fn_name}' returned {len(serialized)} chars; "
                                f"truncated to {len(truncated)} for prompt budget. "
                                f"Consider tightening the tool's filters/pagination."
                            )
                        tool_outputs.append({
                            "tool_call_id": tc.get("id", fn_name),
                            "name": fn_name,
                            "content": truncated,
                        })
                        executed_any = True
                    except Exception as e:
                        logger.error(f"Tool error {fn_name}: {e}")
                        tool_outputs.append({
                            "tool_call_id": tc.get("id", fn_name),
                            "name": fn_name,
                            "content": f"Error: {str(e)}"
                        })
                        executed_any = True
            
            if iteration >= self.max_iterations: 
                logger.warning(f"Hit max iterations ({self.max_iterations}) for query")
                fallback_msg = "\n\n<em>Note: I've reached my maximum processing limit for this request. If I haven't fully answered your question, please try rephrasing or breaking it down.</em>"
                final_content = (agent_message + fallback_msg).strip()
                break
            
            if not executed_any:
                final_content = agent_message
                break
                
            # Add tool outputs to history
            for output in tool_outputs:
                messages.append({
                    "role": "tool",
                    "tool_call_id": output["tool_call_id"],
                    "name": output["name"],
                    "content": output["content"]
                })
        
        return {
            "content": final_content or agent_message,
            "tool_calls": tool_calls if not final_tool_calls else final_tool_calls,
            "messages": messages # Return full history for persistence if needed
        }

    def _format_tools_for_llm(self, tools: List[Any]) -> Optional[List[Dict[str, Any]]]:
        if not tools: return None
        
        formatted = []
        for tool in tools:
            # tool.input_schema is already a valid JSON schema (patched in mcp.py)
            schema = tool.input_schema if isinstance(tool.input_schema, dict) else (
                tool.input_schema.model_json_schema() if hasattr(tool.input_schema, "model_json_schema") 
                else tool.input_schema.schema()
            )
            
            formatted.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": schema.get("properties", {}),
                        "required": schema.get("required", [])
                    }
                }
            })
        return formatted

    def _clean_message(self, message: str) -> str:
        # Remove reasoning signatures
        message = re.sub(r'\{[^{}]*"signature"[^{}]*\}', '', message, flags=re.IGNORECASE | re.DOTALL)
        return message.strip()
