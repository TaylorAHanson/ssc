"""
V2 durable agentic execution engine.

Replaces the V1 custom state-machine engine (``app.state_machines`` +
``poller.tick()``) with LangGraph graphs persisted via a checkpointer keyed on
the request id. Mutating work inside a graph runs through the shared
``app.tools.tool_executor.ToolExecutor`` so the M1 guardrail stack (OPA
pre-flight, idempotency, audit) applies uniformly.

This package is additive and feature-flagged (``V2_ENGINE_ENABLED``); it is NOT
wired into the poller until the M5 cutover.
"""
