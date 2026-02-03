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
                
                # Special case for termination tools (like route_to_form) 
                # though these usually return content, not tool calls in our Gemini setup
                if fn_name in ["determine_request_type", "generate_follow_up_questions", "validate_answers"]:
                   final_content = agent_message
                   final_tool_calls = tool_calls
                   iteration = self.max_iterations + 1
                   break

                matching_tool = next((t for t in self.tools if t.name == fn_name), None)
                if matching_tool:
                    try:
                        logger.info(f"Executing tool: {fn_name}")
                        
                        # Inject conversation history/context for execute_workflow if available
                        if fn_name == "execute_workflow":
                            # Pass all messages EXCEPT the system prompt to the tool
                            fn_args["conversation_history"] = [m for m in messages if m.get("role") != "system"]
                            
                        # Inject OBO token if provided
                        if obo_token:
                            fn_args["_obo_token"] = obo_token
                        
                        # Inject user identity for tools that need it (e.g., execute_workflow)
                        if self.user_identity:
                            fn_args["_user_email"] = self.user_identity.get("email")
                            
                        result = await matching_tool.execute(**fn_args)
                        tool_outputs.append({
                            "tool_call_id": tc.get("id", fn_name),
                            "name": fn_name,
                            "content": json.dumps(result, default=str)
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
            
            if iteration > self.max_iterations: break
            
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
        
        def map_type(t: str) -> str:
            mapping = {"float": "number", "int": "number", "str": "string", "bool": "boolean", "object": "object", "array": "array"}
            return mapping.get(t.lower(), "string")

        formatted = []
        for tool in tools:
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
                        "properties": {
                            p: {"type": map_type(info.get("type", "string")), "description": info.get("description", "")}
                            for p, info in schema.get("properties", {}).items()
                        },
                        "required": schema.get("required", [])
                    }
                }
            })
        return formatted

    def _clean_message(self, message: str) -> str:
        # Remove reasoning signatures
        message = re.sub(r'\{[^{}]*"signature"[^{}]*\}', '', message, flags=re.IGNORECASE | re.DOTALL)
        return message.strip()
