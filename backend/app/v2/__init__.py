"""
V2 durable agentic execution engine.

Replaces the V1 custom state-machine engine (``app.state_machines`` +
``poller.tick()``) with LangGraph graphs persisted via a checkpointer keyed on
the request id. Mutating work inside a graph runs through the shared
``app.tools.tool_executor.ToolExecutor`` so the M1 guardrail stack (OPA
pre-flight, idempotency, audit) applies uniformly.

As of the M5 cutover this is the **only** execution engine: the poller
(``app.workers.poller._process_request_state_machine``) advances these graphs
directly and the legacy state-machine engine has been removed.
"""
