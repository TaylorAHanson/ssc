"""
Native Databricks/MLflow ``ResponsesAgent`` wrapper for the Atlas agent.

This is the *standard, deployable* contract (the same one Databricks Agent
Framework, Playground, and Model Serving understand). It wraps the in-app
:class:`~app.agents.runner.AgentRunner` so the ReAct loop, governed
``ToolExecutor``, and OBO identity are reused verbatim -- we adopt the native
contract instead of home-rolling a second agent implementation.

Two surfaces:

* :meth:`predict` -- single-shot ``ResponsesAgentResponse`` (used by Model
  Serving / batch eval / the LLM-as-judge harness).
* :meth:`predict_stream` -- yields ``ResponsesAgentStreamEvent``s (text deltas,
  tool-call + tool-output items, reasoning). The in-app SSE endpoint keeps its
  richer event protocol by consuming the runner directly; this stream is what
  external consumers (Playground, gateway) get.

Tracing is handled inside the runner (one MLflow trace per turn), so traces are
identical regardless of which surface drives the loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Generator, List, Optional

from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

from app.agents.events import (
    MessageEvent,
    ReasoningEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.agents.runner import AgentRunner

logger = logging.getLogger(__name__)


def _input_to_history(request: ResponsesAgentRequest) -> tuple[str, List[Dict[str, Any]]]:
    """Split a Responses ``input`` list into (latest user query, prior history).

    Accepts the OpenAI Responses message shape; coerces content parts to text.
    """
    msgs: List[Dict[str, Any]] = []
    for item in request.input or []:
        d = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        role = d.get("role", "user")
        content = d.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        msgs.append({"role": role, "content": content})

    query = ""
    if msgs and msgs[-1].get("role") == "user":
        query = msgs.pop()["content"]
    history = [{"type": "user" if m["role"] == "user" else "agent", **m} for m in msgs]
    return query, history


class AtlasResponsesAgent(ResponsesAgent):
    """ResponsesAgent over the governed in-app agent loop."""

    def __init__(self, tools: Optional[List[Any]] = None,
                 user_identity: Optional[Dict[str, str]] = None,
                 max_iterations: int = 5):
        from app.agents.prompts import AGENT_TOOLS

        self._tools = tools if tools is not None else list(AGENT_TOOLS)
        self._user_identity = user_identity or {}
        self._max_iterations = max_iterations

    def _runner(self, custom_inputs: Optional[Dict[str, Any]]) -> AgentRunner:
        identity = dict(self._user_identity)
        if custom_inputs and isinstance(custom_inputs.get("user_identity"), dict):
            identity.update(custom_inputs["user_identity"])
        return AgentRunner(
            tools=self._tools,
            user_identity=identity,
            max_iterations=self._max_iterations,
            mode="unified",
        )

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        outputs: List[Dict[str, Any]] = []
        custom: Dict[str, Any] = {}
        for ev in self.predict_stream(request):
            d = ev.model_dump()
            if d.get("type") == "response.output_item.done" and d.get("item"):
                outputs.append(d["item"])
            if d.get("custom_outputs"):
                custom.update(d["custom_outputs"])
        return ResponsesAgentResponse(output=outputs, custom_outputs=custom or None)

    def predict_stream(
        self, request: ResponsesAgentRequest
    ) -> Generator[ResponsesAgentStreamEvent, None, None]:
        query, history = _input_to_history(request)
        custom_inputs = getattr(request, "custom_inputs", None)
        obo_token = (custom_inputs or {}).get("obo_token")
        runner = self._runner(custom_inputs)

        # Bridge the runner's async generator into this sync generator.
        loop = asyncio.new_event_loop()
        agen = runner.run_stream(
            query=query, history=history, context=None, obo_token=obo_token
        )
        try:
            while True:
                try:
                    ev = loop.run_until_complete(agen.__anext__())
                except StopAsyncIteration:
                    break
                yield from self._map_event(ev)
        finally:
            try:
                loop.run_until_complete(agen.aclose())
            except Exception:  # noqa: BLE001
                pass
            loop.close()

    def _map_event(self, ev: Any) -> Generator[ResponsesAgentStreamEvent, None, None]:
        if isinstance(ev, ReasoningEvent):
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=self.create_reasoning_item(
                    id=str(uuid.uuid4()), reasoning_text=ev.text
                ),
            )
        elif isinstance(ev, ToolCallEvent):
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=self.create_function_call_item(
                    id=str(uuid.uuid4()),
                    call_id=ev.id,
                    name=ev.name,
                    arguments=json.dumps(ev.arguments or {}, default=str),
                ),
            )
        elif isinstance(ev, ToolResultEvent):
            payload = ev.result if ev.ok else {"error": ev.error or ev.summary}
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=self.create_function_call_output_item(
                    call_id=ev.id, output=json.dumps(payload, default=str)
                ),
            )
        elif isinstance(ev, MessageEvent) and ev.content:
            item_id = str(uuid.uuid4())
            yield ResponsesAgentStreamEvent(
                type="response.output_text.delta", item_id=item_id, delta=ev.content
            )
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=self.create_text_output_item(text=ev.content, id=item_id),
            )
        elif type(ev).__name__ == "DoneEvent":
            tid = getattr(ev, "trace_id", None)
            if tid:
                yield ResponsesAgentStreamEvent(
                    type="response.done", custom_outputs={"trace_id": tid}
                )
