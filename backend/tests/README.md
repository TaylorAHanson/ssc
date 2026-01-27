# Backend Test Harness

This directory contains the test suite for the ATLAS backend, designed to support independent testing of Providers and State Machines.

## Structure

```
backend/tests/
├── conftest.py             # Global fixtures (DB session, Settings, etc.)
├── factories/              # FactoryBoy factories for easy data seeding
├── harness/                # Core test infrastructure (Mocking, DB tools)
├── integration/            # Real integration tests (hit external APIs)
└── unit/                   # Fast, mocked tests (default)
```

## Running Tests

### Unit Tests (Mocked)
By default, tests run in **Mock Mode**. All external provider calls are intercepted and mocked.
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

### State Machines
Use the `StateMachineTestHarness` to test logic without needing the full API stack.

```python
def test_onboarding_flow(db_session):
    harness = StateMachineTestHarness(db_session)
    request = harness.create_request("project_onboarding", project_name="Test")
    
    # Tick the state machine
    harness.tick(request.id)
    
    # Assert state change
    harness.assert_state(request.id, "manager_approval")
```

### Providers
Provider tests should exist in both `unit/` (checking request construction) and `integration/` (checking real API behavior).

```python
# unit/providers/test_terraform.py
def test_plan_command_construction(mock_subprocess):
    provider = TerraformProvider()
    provider.plan(...)
    assert mock_subprocess.called_with(["terraform", "plan", ...])
```
