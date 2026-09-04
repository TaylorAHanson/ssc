"""
Agent LLM model endpoint client.

ROLE: High-Level Domain Logic
RESPONSIBILITY:
- Acts as the Agent wrapper specifically for LLM interactions.
- Formats prompts and manages conversation context (messages).
- Parses complex LLM responses (OpenAI/Gemini formats) into clean, usable dictionaries.
- Uses `ModelServingClient` internally for the network transport.
"""
from typing import Dict, Any, List, Optional
import logging
import json
from app.model_serving.client import ModelServingClient, get_model_serving_client
from app.core.config import settings

logger = logging.getLogger(__name__)


class AgentLLMClient:
    """Client for agent LLM model serving endpoint."""
    
    def __init__(self, endpoint_name: Optional[str] = None):
        # Share the process-wide transport client (warm connection pool + cached,
        # auto-refreshing auth) instead of building a new one — with a blocking
        # OAuth round-trip — on every AgentRunner / chat request.
        self.client = get_model_serving_client()
        # An explicit ``endpoint_name`` (e.g. from an agent profile's ``model``)
        # routes this turn to a specific serving endpoint, bypassing the gateway.
        # Otherwise: prefer the AI Gateway endpoint when configured so model
        # routing / A-B split, rate + cost limits, and input guardrails live in
        # the gateway (config, not code). Falls back to the direct serving
        # endpoint otherwise.
        if endpoint_name:
            self.endpoint_name = endpoint_name
            self.via_gateway = False
        else:
            self.endpoint_name = (
                settings.AI_GATEWAY_ENDPOINT or settings.MODEL_SERVING_AGENT_LLM_ENDPOINT
            )
            self.via_gateway = bool(settings.AI_GATEWAY_ENDPOINT)

        if not self.endpoint_name:
            raise ValueError(
                "AI_GATEWAY_ENDPOINT or MODEL_SERVING_AGENT_LLM_ENDPOINT must be set"
            )
    
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
        # Strip metadata from messages for LLM compatibility
        stripped_messages = []
        for msg in messages:
            stripped = {"role": msg.get("role"), "content": msg.get("content")}
            if "tool_calls" in msg:
                stripped["tool_calls"] = msg["tool_calls"]
            if "tool_call_id" in msg:
                stripped["tool_call_id"] = msg["tool_call_id"]
            if "name" in msg:
                stripped["name"] = msg["name"]
            stripped_messages.append(stripped)

        inputs = {
            "messages": stripped_messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        if tools:
            inputs["tools"] = tools

        endpoint_url = self._apply_routing(inputs)

        try:
            response = await self.client.invoke_endpoint(
                self.endpoint_name, 
                inputs,
                endpoint_url=endpoint_url,
                use_foundation_model_format=True
            )
        except Exception as e:
            logger.error(f"Error calling invoke_endpoint: {str(e)}", exc_info=True)
            return self._create_error_response("I encountered an error connecting to the model. Please try again.")
        
        if response is None:
            logger.error("Model serving endpoint returned None")
            return self._create_error_response("I didn't receive a response from the model.")

        # Log the raw response we receive from invoke_endpoint
        logger.debug(f"=== Raw Response from invoke_endpoint ===")
        logger.debug(f"Type: {type(response)}")
        if isinstance(response, dict):
            logger.debug(f"Keys: {list(response.keys())}")
        else:
            logger.debug(f"Response value: {response}")

        parsed = self._parse_response(response)
        
        # Log what we're returning
        logger.debug(f"=== Parsed Response ===")
        logger.debug(f"Content type: {type(parsed.get('content'))}")
        logger.debug(f"Tool calls: {parsed.get('tool_calls')}")
        
        return parsed

    def _apply_routing(self, inputs: Dict[str, Any]) -> Optional[str]:
        """Adapt ``inputs`` to the configured model and return a URL override.

        This is the ONE place that knows how to reach the configured model, and
        every caller must go through it. Resolving the endpoint name yourself and
        POSTing to ``/serving-endpoints/{name}/invocations`` looks right and works
        right up until an admin points the platform at a gateway model, at which
        point the name is a *model* name and that path 404s — which is exactly how
        the workflow-test judge, the instructions generator, and the test-case
        generator each broke while the chat agent kept working.
        """
        # Some models (e.g. Gemini models like gemini-3.8-flash, reasoning models like o1/o3/o4)
        # do not support the temperature parameter on Databricks Model Serving / AI Gateway.
        model_name = (self.endpoint_name or "").lower()
        if any(prefix in model_name for prefix in ("gemini", "o1", "o3", "o4")):
            inputs.pop("temperature", None)

        # Reasoning models (e.g. gpt-5-6-luna) reject function tools combined
        # with a non-"none" reasoning_effort on chat/completions. Pass it
        # explicitly only when configured (set "none" for gpt-5-6-luna); blank
        # omits it so non-reasoning models (Claude, Llama) aren't sent an
        # unexpected parameter they'd reject.
        effort = (settings.AGENT_LLM_REASONING_EFFORT or "").strip()
        if effort:
            inputs["reasoning_effort"] = effort

        # Route through the AI Gateway's MLflow chat/completions route when a
        # gateway model is configured (the non-deprecated method): the model is
        # named in the request body, not baked into a /serving-endpoints/ path.
        if self.via_gateway:
            inputs["model"] = self.endpoint_name
            return "/ai-gateway/mlflow/v1/chat/completions"
        return None

    async def complete_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 800,
    ) -> str:
        """One-shot text completion on the configured model, for non-chat callers.

        Unlike :meth:`generate_response` this RAISES on transport failure instead
        of returning a friendly sentence — its callers (LLM judges, generators)
        need to distinguish "the model said something unparseable" from "the model
        was unreachable", and a sentence like "I encountered an error" silently
        becomes a bad verdict otherwise.
        """
        inputs: Dict[str, Any] = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        endpoint_url = self._apply_routing(inputs)
        response = await self.client.invoke_endpoint(
            self.endpoint_name,
            inputs,
            endpoint_url=endpoint_url,
            use_foundation_model_format=True,
        )
        if response is None:
            raise RuntimeError(
                f"Model endpoint '{self.endpoint_name}' returned no response."
            )
        content = self._parse_response(response).get("content")
        return str(content or "")

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

        # Strategy: Strictly follow OpenAI format as enforced by Databricks Model Serving
        
        # 1. Top-level tool calls (OpenAI/Gemini variant)
        if "tool_calls" in response or "function_calls" in response:
            return self._extract_top_level_tools(response)

        # 2. OpenAI "choices" format
        if "choices" in response:
            return self._parse_openai_format(response)
            
        # 3. Direct "message" wrapper (Gemini/Databricks variant seen in logs)
        if "message" in response:
            return self._parse_message_wrapper(response["message"])

        # 4. Direct "candidates" format (Native Gemini variant)
        if "candidates" in response and response["candidates"]:
            cand = response["candidates"][0]
            if isinstance(cand, dict):
                if "content" in cand:
                    cand_content = cand["content"]
                    if isinstance(cand_content, dict):
                        parts = cand_content.get("parts", [])
                        text_parts = [p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]
                        return {"role": "assistant", "content": "".join(text_parts)}
                if "message" in cand:
                    return self._parse_message_wrapper(cand["message"])
            
        # 5. Direct content/output (Fallback)
        if "content" in response:
             return {"role": response.get("role", "assistant"), "content": str(response["content"])}
             
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
            return self._parse_message_wrapper(choice["message"])
        elif "text" in choice:
            return {"role": "assistant", "content": choice["text"]}
        elif "content" in choice:
            return {"role": "assistant", "content": choice["content"]}
        
        return self._create_error_response("Unknown OpenAI choice format")

    def _parse_message_wrapper(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parses a message object which might have string content or list of content parts.
        Structure seen: {"role": "assistant", "content": [{"type": "text", "text": "..."}]}
        """
        content = message.get("content")
        role = message.get("role", "assistant")
        tool_calls = message.get("tool_calls")
        
        parsed = {"role": role, "tool_calls": tool_calls}
        
        # Handle list-of-content-parts format (OpenAI/Gemini standard)
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    text_parts.append(part["text"])
            
            # Use joined text as primary content
            parsed["content"] = "\n".join(text_parts)
        else:
            parsed["content"] = content or ""
            
        return parsed
