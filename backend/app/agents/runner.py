"""
Reusable Agent Runner for executing agent loops with tools.

Two entry points:

* :py:meth:`AgentRunner.run_stream` is an async generator that yields
  :mod:`app.agents.events` events as the ReAct loop progresses. The HTTP
  layer wraps these in SSE frames so the browser can render live
  progress (status line, tool-call pills, optional reasoning, final
  message).
* :py:meth:`AgentRunner.run` is a thin shim that drains the streaming
  generator into the legacy dict shape (``{content, tool_calls,
  messages}``). Background callers and the existing non-streaming
  endpoint keep working unchanged.

When a tool returns a value matching the *pending-poll envelope*
(``{"pending_poll": {...}}``), the runner emits a :class:`PendingPollEvent`
and stops the iteration loop early. The UI is responsible for draining
the poll, then re-invoking the runner with a synthetic ``tool`` message
carrying the resolved result so the LLM can summarize the answer.
"""
import json
import logging
import re
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

from app.agents.events import (
    AgentEvent,
    DoneEvent,
    ErrorEvent,
    MessageEvent,
    PendingPollEvent,
    ReasoningEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.agents import tracing
from app.agents.prompts import get_agent_prompt
from app.core.config import settings
from app.model_serving.agent_llm import AgentLLMClient
from app.tools.tool_executor import ToolContext, executor

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


def _summarize_args(fn_args: Dict[str, Any], max_chars: int = 120) -> Optional[str]:
    """Best-effort short string of tool arguments for display under a pill.

    We prefer obvious "natural language" keys (``query``, ``question``,
    ``prompt``) since those make the most useful tool-call labels. Falls
    back to a truncated JSON dump when no friendly key is present.
    """
    if not fn_args:
        return None
    for key in ("question", "query", "prompt", "filter_string"):
        v = fn_args.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:max_chars]
    try:
        rendered = json.dumps(fn_args, default=str)
    except Exception:
        return None
    if len(rendered) > max_chars:
        return rendered[: max_chars - 1] + "\u2026"
    return rendered


def _extract_pending_poll(result: Any) -> Optional[Dict[str, Any]]:
    """If a tool result is a pending-poll envelope, return its payload.

    The envelope shape is ``{"pending_poll": {kind, ids, ...}}``. Anything
    else (or an envelope missing ``kind``) is treated as a normal result.
    """
    if not isinstance(result, dict):
        return None
    pp = result.get("pending_poll")
    if not isinstance(pp, dict):
        return None
    if not pp.get("kind"):
        return None
    return pp


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
        mode: str = "self_service",
        model_endpoint: Optional[str] = None,
        user_context_block: Optional[str] = None,
        surface_context_block: Optional[str] = None,
        dry_run: bool = False,
    ):
        self.llm_client = AgentLLMClient(endpoint_name=model_endpoint)
        self.tools = tools or []
        self.max_iterations = max_iterations
        self.user_identity = user_identity or {}
        self.mode = mode
        # Sandbox for workflow tests: forwarded to every ToolContext so the
        # executor simulates mutating calls instead of running them. Set only by
        # the test runner — a normal turn must never be silently faked.
        self.dry_run = dry_run

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

        # Appended outside the branch above on purpose: an agent profile supplies
        # its own system prompt, and it needs to know who it is talking to just
        # as much as the default prompt does.
        if user_context_block:
            self.system_prompt += user_context_block

        # What the user currently has open in the host page (today: the workflow
        # draft in the authoring studio, including their unsaved edits). Appended
        # last so it is the freshest thing the model reads.
        if surface_context_block:
            self.system_prompt += surface_context_block

    def _find_tool(self, name: str):
        return next((t for t in self.tools if t.name == name), None)

    async def run_stream(
        self,
        query: str,
        history: Optional[List[Dict[str, str]]] = None,
        context: Optional[Dict[str, Any]] = None,
        obo_token: Optional[str] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Execute the agent loop, yielding SSE events as work progresses.

        The caller is expected to forward each event to the client (the
        FastAPI streaming endpoint serializes them as ``text/event-stream``
        frames). The final event is always a :class:`DoneEvent` (or an
        :class:`ErrorEvent` with ``fatal=True``) so the UI knows when to
        close the reader.
        """
        # Inject context into system prompt
        current_system_prompt = self.system_prompt
        if context:
            ctx_str = "\n\nCURRENT CONTEXT:\n" + "\n".join(
                [f"{k}: {v}" for k, v in context.items()]
            )
            current_system_prompt += ctx_str

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": current_system_prompt}
        ]
        if history:
            messages.extend(history)

        # Add current query with timestamp and type
        messages.append(
            {
                "role": "user",
                "content": query,
                "timestamp": datetime.now().isoformat(),
                "type": "user",
            }
        )

        formatted_tools = self._format_tools_for_llm(self.tools)

        iteration = 0
        final_content = ""
        agent_message = ""
        last_tool_calls: List[Dict[str, Any]] = []

        # One MLflow trace per turn; child LLM/tool spans group under it. No-op
        # when tracing is disabled. trace_id is surfaced on the terminal events.
        turn_span = tracing.start_root_span(
            "agent_turn",
            inputs={"query": query, "tool_count": len(self.tools), "mode": self.mode},
        )
        trace_id = tracing.root_trace_id(turn_span)
        if self.user_identity:
            tracing.update_trace_metadata(
                {"user": self.user_identity.get("email", ""), "mode": self.mode}
            )

        try:
            while iteration < self.max_iterations:
                iteration += 1
                logger.info(f"Agent iteration {iteration}/{self.max_iterations}")

                # Defense-in-depth context window pruning. See _prune_oldest_tool_outputs.
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

                # Surface a status line per iteration so the UI's
                # progress indicator updates as the agent reasons.
                yield StatusEvent(
                    label="Thinking..." if iteration == 1 else "Working on it..."
                )

                with tracing.span(
                    f"llm_call_{iteration}", "LLM",
                    inputs={"message_count": len(messages)},
                ) as llm_span:
                    response = await self.llm_client.generate_response(
                        messages=messages,
                        tools=formatted_tools,
                        temperature=0.0,  # 0.0 for deterministic reporting
                        # The client's 2000-token default is far too small for a
                        # tool call that carries a payload: authoring a workflow
                        # emits a whole graph_spec plus the runtime playbook in
                        # one `save_workflow_draft` call. Truncated arguments are
                        # invalid JSON, which used to arrive at the tool as {}.
                        max_tokens=settings.AGENT_MAX_RESPONSE_TOKENS,
                    )
                    tracing.set_span_outputs(
                        llm_span,
                        {
                            "content": (response or {}).get("content"),
                            "tool_calls": [
                                tc.get("function", {}).get("name")
                                for tc in (response or {}).get("tool_calls", []) or []
                            ],
                        },
                    )

                agent_message = response.get("content") or ""
                tool_calls = response.get("tool_calls", []) or []
                last_tool_calls = tool_calls

                if agent_message:
                    agent_message = self._clean_message(agent_message)

                # Some endpoints surface a separate reasoning block. If we
                # got non-empty reasoning text, hand it to the UI for the
                # collapsible "Thinking" disclosure.
                reasoning_text = self._extract_reasoning(response)
                if reasoning_text:
                    yield ReasoningEvent(text=reasoning_text)

                # Terminal: model decided no more tool calls. Emit the
                # final message and exit.
                if not tool_calls:
                    final_content = agent_message
                    break

                # Record the assistant's tool-calling turn so subsequent
                # iterations have the linkage between assistant + tool
                # messages required by most LLM tool schemas.
                messages.append(
                    {
                        "role": "assistant",
                        "tool_calls": tool_calls,
                        "timestamp": datetime.now().isoformat(),
                        "type": "agent",
                    }
                )

                tool_outputs: List[Dict[str, Any]] = []
                executed_any = False
                pending_poll_event: Optional[PendingPollEvent] = None

                for tc in tool_calls:
                    fn_name = tc.get("function", {}).get("name", "")
                    fn_args = tc.get("function", {}).get("arguments", {})
                    tool_call_id = tc.get("id", fn_name)
                    if isinstance(fn_args, str):
                        raw_args = fn_args
                        try:
                            fn_args = json.loads(raw_args)
                        except Exception as e:
                            # Do NOT fall back to {}. Empty arguments reach the
                            # tool as "Field required" errors that name the wrong
                            # problem: the model didn't forget the fields, its
                            # arguments were cut off (or malformed) in transit.
                            # Tell it that, so it can retry smaller instead of
                            # resending the same oversized call.
                            err_msg = (
                                f"Your arguments for '{fn_name}' were not valid JSON "
                                f"({e}). They were {len(raw_args)} characters and look "
                                "cut off, so nothing was called. Retry with the SAME "
                                "intent but a smaller payload: send the essential "
                                "fields now and add long prose (e.g. "
                                "instructions_markdown) in a follow-up call."
                            )
                            logger.warning(
                                "Unparseable tool arguments for '%s' (%d chars): %s",
                                fn_name, len(raw_args), e,
                            )
                            yield ToolResultEvent(
                                id=tool_call_id, name=fn_name, ok=False, error=err_msg
                            )
                            tool_outputs.append(
                                {
                                    "tool_call_id": tool_call_id,
                                    "name": fn_name,
                                    "content": err_msg,
                                }
                            )
                            executed_any = True
                            continue
                    if not isinstance(fn_args, dict):
                        fn_args = {}

                    matching_tool = self._find_tool(fn_name)
                    if not matching_tool:
                        # Unknown tool name from the LLM. Surface as a
                        # tool_result error so the user sees what went
                        # wrong and the loop can self-correct.
                        err_msg = f"Tool '{fn_name}' is not registered for this mode."
                        logger.warning(err_msg)
                        yield ToolResultEvent(
                            id=tool_call_id, name=fn_name, ok=False, error=err_msg
                        )
                        tool_outputs.append(
                            {
                                "tool_call_id": tool_call_id,
                                "name": fn_name,
                                "content": err_msg,
                            }
                        )
                        executed_any = True
                        continue

                    # Announce the call so the UI can show a running pill.
                    yield ToolCallEvent(
                        id=tool_call_id,
                        name=fn_name,
                        friendly_label=matching_tool.friendly_label,
                        args_summary=_summarize_args(fn_args),
                        arguments=fn_args,
                    )

                    try:
                        logger.info(f"Executing tool: {fn_name}")

                        # Route through the shared ToolExecutor. Identity
                        # injection (_obo_token / _user_*) is centralized there
                        # so the /mcp path gets the same treatment, and mutating
                        # tools get OPA pre-flight + audit. conversation_history
                        # is passed as injected context, not a model-supplied arg.
                        injected_args: Dict[str, Any] = {}
                        if fn_name == "execute_workflow":
                            history_for_tool = []
                            for m in messages:
                                if m.get("role") not in ("system", "tool"):
                                    m_copy = {k: v for k, v in m.items() if k != "tool_calls"}
                                    history_for_tool.append(m_copy)
                            injected_args["conversation_history"] = history_for_tool

                        tool_ctx = ToolContext(
                            tool_call_id=tool_call_id,
                            obo_token=obo_token,
                            user_identity=self.user_identity,
                            injected_args=injected_args,
                            dry_run=self.dry_run,
                        )
                        with tracing.span(
                            f"tool:{fn_name}", "TOOL",
                            inputs=fn_args,
                            attributes={
                                "side_effect_class": getattr(
                                    matching_tool, "side_effect_class", "read"
                                ),
                                "is_mutating": getattr(
                                    matching_tool, "is_mutating", False
                                ),
                            },
                        ) as tool_span:
                            result = await executor.run(matching_tool, tool_ctx, **fn_args)
                            tracing.set_span_outputs(tool_span, result)

                        # Async hand-off: the tool returned a poll envelope
                        # rather than a final result. We surface it to the
                        # UI, stop processing further tool calls in this
                        # iteration, and break out of the ReAct loop. The
                        # UI re-invokes us once the poll resolves.
                        pending_poll = _extract_pending_poll(result)
                        if pending_poll is not None:
                            pending_poll_event = PendingPollEvent(
                                kind=str(pending_poll.get("kind")),
                                ids={
                                    k: v
                                    for k, v in pending_poll.items()
                                    if k not in ("kind", "friendly_label")
                                },
                                friendly_label=str(
                                    pending_poll.get(
                                        "friendly_label", matching_tool.friendly_label
                                    )
                                ),
                                tool_call_id=tool_call_id,
                                tool_name=fn_name,
                            )
                            # Stop processing remaining tool calls so the
                            # UI sees a single poll for the turn.
                            break

                        serialized = json.dumps(result, default=str)
                        max_tool_chars = getattr(
                            settings, "AGENT_MAX_TOOL_OUTPUT_CHARS", 25000
                        )
                        truncated = _truncate_tool_output(serialized, max_tool_chars)
                        if len(truncated) < len(serialized):
                            logger.warning(
                                f"Tool '{fn_name}' returned {len(serialized)} chars; "
                                f"truncated to {len(truncated)} for prompt budget. "
                                f"Consider tightening the tool's filters/pagination."
                            )
                        tool_outputs.append(
                            {
                                "tool_call_id": tool_call_id,
                                "name": fn_name,
                                "content": truncated,
                            }
                        )
                        # Many tools surface validation / auth issues as a
                        # ``{"error": "..."}`` payload rather than raising.
                        # Treat that as a failed pill so the UI doesn't show
                        # the friendly "X completed" label on what was
                        # actually a failure (and the user doesn't think
                        # something succeeded that didn't).
                        tool_error_msg: Optional[str] = None
                        if isinstance(result, dict):
                            err_val = result.get("error")
                            if isinstance(err_val, str) and err_val.strip():
                                tool_error_msg = err_val
                        if tool_error_msg:
                            yield ToolResultEvent(
                                id=tool_call_id,
                                name=fn_name,
                                ok=False,
                                summary=tool_error_msg[:200],
                                result=result,
                            )
                        else:
                            yield ToolResultEvent(
                                id=tool_call_id,
                                name=fn_name,
                                ok=True,
                                summary=matching_tool.friendly_completion_label,
                                result=result,
                            )
                        executed_any = True
                    except Exception as e:
                        logger.error(f"Tool error {fn_name}: {e}", exc_info=True)
                        err_text = f"Error: {e}"
                        tool_outputs.append(
                            {
                                "tool_call_id": tool_call_id,
                                "name": fn_name,
                                "content": err_text,
                            }
                        )
                        yield ToolResultEvent(
                            id=tool_call_id,
                            name=fn_name,
                            ok=False,
                            error=str(e),
                        )
                        executed_any = True

                # Pending poll handed off control to the UI. Emit the event and
                # exit. The *polled* tool gets its tool message later (the poll
                # continuation adds it once it resolves), but any OTHER tool calls
                # in this same assistant turn already ran — we MUST persist their
                # outputs now. Otherwise the assistant turn carries tool_calls with
                # no matching tool messages (orphan tool_calls), which providers
                # reject on resume, and those results would be silently lost.
                if pending_poll_event is not None:
                    for output in tool_outputs:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": output["tool_call_id"],
                                "name": output["name"],
                                "content": output["content"],
                            }
                        )
                    yield pending_poll_event
                    final_content = agent_message
                    tracing.end_root_span(turn_span, outputs={"pending_poll": True})
                    turn_span = None
                    yield DoneEvent(messages=messages, trace_id=trace_id)
                    return

                if iteration >= self.max_iterations:
                    logger.warning(
                        f"Hit max iterations ({self.max_iterations}) for query"
                    )
                    fallback_msg = (
                        "\n\n*Note: I've reached my maximum processing limit "
                        "for this request. If I haven't fully answered your "
                        "question, please try rephrasing or breaking it down.*"
                    )
                    final_content = (agent_message + fallback_msg).strip()
                    break

                if not executed_any:
                    final_content = agent_message
                    break

                # Append tool outputs so the next iteration can use them.
                for output in tool_outputs:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": output["tool_call_id"],
                            "name": output["name"],
                            "content": output["content"],
                        }
                    )

            # Normal exit: emit the final message + done.
            final_text = final_content or agent_message
            tracing.end_root_span(turn_span, outputs={"final_message": final_text})
            turn_span = None
            if final_text:
                yield MessageEvent(content=final_text)
            yield DoneEvent(messages=messages, trace_id=trace_id)
        except Exception as e:
            logger.error(f"AgentRunner.run_stream failed: {e}", exc_info=True)
            tracing.end_root_span(turn_span)
            turn_span = None
            yield ErrorEvent(message=str(e), fatal=True)

    async def run(
        self,
        query: str,
        history: Optional[List[Dict[str, str]]] = None,
        context: Optional[Dict[str, Any]] = None,
        obo_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes the agent loop for a single query.

        Backward-compatible shim over :py:meth:`run_stream`. Drains all
        events into the legacy ``{content, tool_calls, messages}`` shape
        used by the non-streaming endpoint and any background callers.

        Pending-poll events have no analogue in this dict shape, so we
        surface them via a ``pending_poll`` key for callers that opt
        into the streaming mental model.
        """
        final_content = ""
        tool_calls: List[Dict[str, Any]] = []
        messages: List[Dict[str, Any]] = []
        pending_poll: Optional[Dict[str, Any]] = None
        last_tool_call_event: Optional[ToolCallEvent] = None
        trace_id: Optional[str] = None

        async for ev in self.run_stream(
            query=query, history=history, context=context, obo_token=obo_token
        ):
            if isinstance(ev, MessageEvent):
                final_content = ev.content
            elif isinstance(ev, ToolCallEvent):
                last_tool_call_event = ev
                tool_calls.append(
                    {
                        "id": ev.id,
                        "type": "function",
                        # Carry the real arguments: a caller that only learns
                        # *which* tool ran can't tell a correct call from a
                        # plausible-looking wrong one (the workflow test judge
                        # needs exactly that distinction).
                        "function": {"name": ev.name, "arguments": ev.arguments or {}},
                    }
                )
            elif isinstance(ev, PendingPollEvent):
                pending_poll = ev.model_dump(mode="json")
            elif isinstance(ev, DoneEvent):
                if ev.messages is not None:
                    messages = ev.messages
                trace_id = ev.trace_id
            elif isinstance(ev, ErrorEvent) and ev.fatal:
                final_content = (
                    final_content
                    or "I encountered an error processing your request. Please try again."
                )

        return {
            "content": final_content,
            "tool_calls": tool_calls,
            "messages": messages,
            "pending_poll": pending_poll,
            "trace_id": trace_id,
        }

    def _format_tools_for_llm(self, tools: List[Any]) -> Optional[List[Dict[str, Any]]]:
        if not tools:
            return None

        formatted = []
        for tool in tools:
            schema = (
                tool.input_schema
                if isinstance(tool.input_schema, dict)
                else (
                    tool.input_schema.model_json_schema()
                    if hasattr(tool.input_schema, "model_json_schema")
                    else tool.input_schema.schema()
                )
            )
            formatted.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": {
                            "type": "object",
                            "properties": schema.get("properties", {}),
                            "required": schema.get("required", []),
                        },
                    },
                }
            )
        return formatted

    def _clean_message(self, message: str) -> str:
        # Remove reasoning signatures
        message = re.sub(
            r'\{[^{}]*"signature"[^{}]*\}', "", message, flags=re.IGNORECASE | re.DOTALL
        )
        return message.strip()

    def _extract_reasoning(self, response: Dict[str, Any]) -> Optional[str]:
        """Pull a reasoning/thinking block out of an LLM response, if any.

        Different endpoints surface this differently:
        - Anthropic-style: ``thinking`` field at top level or in message.
        - OpenAI-style "reasoning": ``reasoning_content`` or ``reasoning``.
        - Embedded in content as a ``[reasoning]...[/reasoning]`` block.

        We sniff for the common shapes and return ``None`` when none
        match - the UI then simply doesn't render the disclosure.
        """
        if not isinstance(response, dict):
            return None
        for key in ("reasoning", "thinking", "reasoning_content"):
            v = response.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, list):
                # List of content parts (Anthropic-style ``thinking``).
                texts = [
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in v
                ]
                joined = "\n".join(t for t in texts if t).strip()
                if joined:
                    return joined
        return None
