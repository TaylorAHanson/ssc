"""
Agent LLM model endpoint client.
"""
from typing import Dict, Any, List, Optional
import logging
import json
from app.model_serving.client import ModelServingClient
from app.core.config import settings

logger = logging.getLogger(__name__)


class AgentLLMClient:
    """Client for agent LLM model serving endpoint."""
    
    def __init__(self):
        self.client = ModelServingClient()
        self.endpoint_name = settings.MODEL_SERVING_AGENT_LLM_ENDPOINT
        
        if not self.endpoint_name:
            raise ValueError("MODEL_SERVING_AGENT_LLM_ENDPOINT must be set in configuration")
    
    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> Dict[str, Any]:
        """
        Generate LLM response for agent conversation.
        """
        inputs = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        if tools:
            inputs["tools"] = tools
        
        try:
            response = await self.client.invoke_endpoint(
                self.endpoint_name, 
                inputs,
                use_foundation_model_format=True
            )
        except Exception as e:
            logger.error(f"Error calling invoke_endpoint: {str(e)}", exc_info=True)
            return self._create_error_response("I encountered an error connecting to the model. Please try again.")
        
        if response is None:
            logger.error("Model serving endpoint returned None")
            return self._create_error_response("I didn't receive a response from the model.")

        # Log the raw response we receive from invoke_endpoint
        logger.info(f"=== Raw Response from invoke_endpoint ===")
        logger.info(f"Type: {type(response)}")
        if isinstance(response, dict):
            logger.info(f"Keys: {list(response.keys())}")
            logger.info(f"Full response: {json.dumps(response, indent=2, default=str)[:3000]}")
        else:
            logger.info(f"Response value: {response}")

        parsed = self._parse_response(response)
        
        # Log what we're returning
        logger.info(f"=== Parsed Response ===")
        logger.info(f"Content type: {type(parsed.get('content'))}")
        logger.info(f"Content value: {parsed.get('content')}")
        logger.info(f"Tool calls: {parsed.get('tool_calls')}")
        
        return parsed

    def _create_error_response(self, message: str) -> Dict[str, Any]:
        return {"role": "assistant", "content": message}

    def _log_response_structure(self, response: Any):
        if isinstance(response, dict):
            logger.info(f"Response keys: {list(response.keys())}")
            try:
                # Truncate large logs
                logger.debug(f"Full response: {json.dumps(response, default=str)[:2000]}")
            except:
                pass

    def _parse_response(self, response: Any) -> Dict[str, Any]:
        """
        Normalize response into standard format:
        {
            "role": "assistant",
            "content": "...",
            "tool_calls": [...] (optional)
        }
        """
        if not isinstance(response, dict):
            return self._create_error_response(str(response))

        # Strategy: Try specific parsers in order of likelihood
        
        # 1. Top-level tool calls (OpenAI/Gemini variant)
        if "tool_calls" in response or "function_calls" in response:
            return self._extract_top_level_tools(response)

        # 2. OpenAI "choices" format
        if "choices" in response:
            return self._parse_openai_format(response)
            
        # 3. Gemini "candidates" format
        if "candidates" in response:
            return self._parse_gemini_format(response)
            
        # 4. "message" wrapper (custom/databricks wrapper)
        if "message" in response:
            return self._parse_message_wrapper(response["message"])
            
        # 5. Direct content/output
        if "content" in response:
             # This might still contain complex Gemini content structures
             content = response["content"]
             if isinstance(content, (list, dict)):
                 res = self._parse_gemini_content(content)
                 res["role"] = response.get("role", "assistant")
                 return res
             return {"role": response.get("role", "assistant"), "content": str(content)}
             
        if "output" in response:
             return {"role": "assistant", "content": str(response["output"])}

        # Fallback
        logger.warning(f"Unknown response format: {list(response.keys())}")
        return self._create_error_response("I received an unexpected response format.")

    def _extract_top_level_tools(self, response: Dict[str, Any]) -> Dict[str, Any]:
        tool_calls = response.get("tool_calls", [])
        
        # Handle function_calls -> tool_calls conversion
        if "function_calls" in response:
            for fc in response["function_calls"]:
                tool_calls.append({
                    "id": fc.get("name", "call_id"),
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": fc.get("args", fc.get("arguments", {}))
                    }
                })
        
        return {
            "role": "assistant", 
            "content": response.get("content", ""), 
            "tool_calls": tool_calls
        }

    def _parse_openai_format(self, response: Dict[str, Any]) -> Dict[str, Any]:
        choices = response.get("choices", [])
        if not choices:
            return self._create_error_response("Empty choices in response")
            
        choice = choices[0]
        if "message" in choice:
            message = choice["message"]
            # Handle complex content in OpenAI message wrapper
            if isinstance(message.get("content"), (list, dict)):
                parsed = self._parse_gemini_content(message["content"])
                # Preserve existing tool_calls if they exist in the message but not in parsed
                if "tool_calls" in message and not parsed.get("tool_calls"):
                    parsed["tool_calls"] = message["tool_calls"]
                # Override role if not present
                if "role" not in parsed:
                    parsed["role"] = message.get("role", "assistant")
                return parsed
            return message
        elif "text" in choice:
            return {"role": "assistant", "content": choice["text"]}
        elif "content" in choice:
            content = choice["content"]
            if isinstance(content, (list, dict)):
                return self._parse_gemini_content(content)
            return {"role": "assistant", "content": content}
        
        return self._create_error_response("Unknown OpenAI choice format")

    def _parse_gemini_format(self, response: Dict[str, Any]) -> Dict[str, Any]:
        candidates = response.get("candidates", [])
        if not candidates:
            return self._create_error_response("Empty candidates in response")
            
        candidate = candidates[0]
        
        # Check for top-level functionCalls in candidate
        if "functionCalls" in candidate:
             tool_calls = []
             for fc in candidate["functionCalls"]:
                tool_calls.append({
                    "id": fc.get("name", "call_id"),
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": fc.get("args", {})
                    }
                })
             return {"role": "assistant", "content": "", "tool_calls": tool_calls}

        content = candidate.get("content")
        return self._parse_gemini_content(content)

    def _parse_gemini_content(self, content: Any) -> Dict[str, Any]:
        if isinstance(content, str):
            return {"role": "assistant", "content": content}
            
        if isinstance(content, dict) and "parts" in content:
            # Old Gemini format
            text_parts = []
            tool_calls = []
            for part in content["parts"]:
                if "text" in part:
                    text_parts.append(part["text"])
                if "functionCall" in part:
                    self._extract_gemini_function_call(part["functionCall"], tool_calls)
            return {
                "role": "assistant", 
                "content": "\n".join(text_parts),
                "tool_calls": tool_calls
            }
            
        if isinstance(content, list):
            # New Gemini format (reasoning, summary, etc)
            text_parts = []
            tool_calls = []
            
            for item in content:
                if not isinstance(item, dict): continue
                
                item_type = item.get("type")
                
                # Direct text
                if "text" in item:
                    text_parts.append(item["text"])
                    
                # Direct function call in item
                if "functionCall" in item:
                    self._extract_gemini_function_call(item["functionCall"], tool_calls)
                if "function_call" in item:
                    self._extract_gemini_function_call(item["function_call"], tool_calls)
                
                # Top level tool_calls list
                if "tool_calls" in item and isinstance(item["tool_calls"], list):
                    tool_calls.extend(item["tool_calls"])

                if item_type == "functionCall":
                     # Sometimes the dict itself is the function call wrapper
                     # But usually it has a key 'functionCall' inside, which we handled above
                     pass

                # Reasoning/Summary block
                if item_type == "reasoning":
                    if "summary" in item:
                        for summary_item in item["summary"]:
                            if "text" in summary_item:
                                text_parts.append(summary_item["text"])
                            if "functionCall" in summary_item:
                                self._extract_gemini_function_call(summary_item["functionCall"], tool_calls)
                    
                    # Tool calls might be directly in reasoning block?
                    # Previous debugging suggested this might happen
                    if "tool_calls" in item and isinstance(item["tool_calls"], list):
                        tool_calls.extend(item["tool_calls"])

            return {
                "role": "assistant",
                "content": "\n".join(filter(None, text_parts)),
                "tool_calls": tool_calls
            }
            
        return {"role": "assistant", "content": str(content)}

    def _extract_gemini_function_call(self, fc_data: Dict, tool_calls_list: List):
        # Handle various function call shapes
        name = fc_data.get("name")
        if not name: return
        
        tool_calls_list.append({
            "id": name, # Gemini doesn't always provide ID, use name
            "type": "function",
            "function": {
                "name": name,
                "arguments": fc_data.get("args", fc_data.get("arguments", {}))
            }
        })

    def _parse_message_wrapper(self, message_obj: Any) -> Dict[str, Any]:
        # Recursive call if it looks like a standard response structure inside "message"
        if not isinstance(message_obj, dict):
             return {"role": "assistant", "content": str(message_obj)}
             
        # Check for tool calls at this level
        if "tool_calls" in message_obj or "function_calls" in message_obj:
            return self._extract_top_level_tools(message_obj)
            
        content = message_obj.get("content")
        role = message_obj.get("role", "assistant")
        
        # If content is a list/dict, use the Gemini content parser
        if isinstance(content, (dict, list)):
            parsed = self._parse_gemini_content(content)
            parsed["role"] = role
            return parsed
            
        return {
            "role": role, 
            "content": content or ""
        }
