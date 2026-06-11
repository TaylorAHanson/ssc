"""Unit tests for the MLflow tracing layer.

Tracing must be a transparent no-op when disabled (the default) so the agent
runs unchanged and never breaks on a missing/uninitialized MLflow backend.
"""
import app.agents.tracing as tracing


def test_helpers_are_noop_when_inactive(monkeypatch):
    monkeypatch.setattr(tracing, "_active", False, raising=False)

    # Context-manager span yields None and swallows output setting.
    with tracing.span("llm_call", "LLM", inputs={"n": 1}) as s:
        assert s is None
        tracing.set_span_outputs(s, {"anything": True})  # must not raise

    # Root-span lifecycle returns no handle and tolerates None.
    handle = tracing.start_root_span("agent_turn", inputs={"q": "hi"})
    assert handle is None
    assert tracing.root_trace_id(handle) is None
    tracing.end_root_span(handle, outputs={"done": True})  # must not raise

    # Feedback is a no-op (False) and metadata update is harmless.
    assert tracing.log_feedback("trace-123", value="up") is False
    tracing.update_trace_metadata({"user": "u@corp.com"})
    assert tracing.current_trace_id() is None


def test_tracing_active_reflects_flag(monkeypatch):
    monkeypatch.setattr(tracing, "_active", False, raising=False)
    assert tracing.tracing_active() is False
    monkeypatch.setattr(tracing, "_active", True, raising=False)
    assert tracing.tracing_active() is True
