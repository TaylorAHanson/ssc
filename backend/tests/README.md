# Backend Test Harness

This directory contains the test suite for the backend. After the V2 cutover the
legacy `python-statemachine` engine is gone; workflows are now LangGraph graphs
exercised by the V2 eval/sandbox harness (see below).

## Structure

```
backend/tests/
├── conftest.py             # Global fixtures (in-memory DB session) + model registration
├── factories/              # Factories for easy data seeding (RequestFactory)
├── integration/            # Real integration tests (hit external APIs)
└── unit/                   # Fast, mocked tests (default)
    ├── agents/             # AgentRunner stream protocol + MLflow tracing no-op
    ├── providers/          # Provider contracts incl. pluggable IdentityGroupProvider
    ├── services/           # WorkflowService (DB-backed "workflows as data")
    ├── tools/              # Tool contracts + the governed ToolExecutor choke point
    └── v2/                 # Drives the V2 graph harness (all graphs green)
```

## Running Tests

### Unit Tests (Mocked)
By default, tests run in **Mock Mode**. All external provider calls are mocked.
```bash
pytest
```

### Integration Tests (Real)
To run tests against real external systems (requires proper .env configuration):
```bash
pytest --mode=real
```
*Note: This will actually create resources in the target environment.*

## Writing Tests

### V2 Workflows (LangGraph)
Workflows are validated by the pre-publish eval harness, which compiles every
registered graph, runs it hermetically (fake providers, no-op facts), proves each
gate interrupts for HITL and resumes to `completed`, and asserts every mutation
routed through the shared `ToolExecutor`. Run it directly:

```bash
python -m app.workflows.harness
```

`tests/unit/workflows/test_graph_harness.py` runs this as a subprocess so the suite fails
if any graph regresses.

### Providers
Provider tests should exist in `unit/` (checking request construction) and, where
applicable, `integration/` (checking real API behavior).

```python
# unit/providers/test_identity_provider.py
def test_factory_defaults_to_noop(monkeypatch):
    monkeypatch.setattr(settings, "IDENTITY_PROVIDER", "noop", raising=False)
    assert isinstance(get_identity_provider(), NoopIdentityProvider)
```
