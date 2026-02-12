# Architecture Analysis: Policy as Code with OPA/Rego

## 1. Executive Summary

This document analyzes the feasibility and benefits of introducing **Open Policy Agent (OPA)** and **Rego** to the Self-Service Center architecture. 

The current architecture relies on a mix of **Natural Language Instructions** (for Agents) and **Hardcoded Python Logic** (in State Machines) to enforce business rules. Transitioning to **Policy as Code** would decouple decision-making from workflow execution, enabling easier updates, better auditability, and shared logic between Agents and State Machines.

## 2. Current State Analysis

### 2.1. Policy Distribution
Currently, "Policy" (who can do what, when, and how) is scattered across three layers:

1.  **Agent Layer (`instructions/*.md`)**:
    *   **Format**: Natural Language.
    *   **Enforcement**: Probabilistic (LLM interprets instructions).
    *   **Example**: "A new workspace is only valid for new business domain onboarding."
    *   **Risk**: LLM might hallucinate or be "jailbroken" to bypass soft rules.

2.  **State Machine Layer (`state_machines/*.py`)**:
    *   **Format**: Python Code (`if/else`).
    *   **Enforcement**: Deterministic but hardcoded.
    *   **Example**: `if not missing: ...` (Training verification).
    *   **Risk**: Changing a rule (e.g., adding a new required training course) requires a code deployment.

3.  **Database/Config**:
    *   **Format**: DB Tables / Config Variables.
    *   **Enforcement**: Structural.
    *   **Example**: `APPROVAL_NODES` dict defines who approves.

### 2.2. Limitations
*   **Duplication**: If an Agent needs to know *exactly* what the training requirements are to inform the user, it relies on the prompt. The State Machine has the *real* check in code. These can drift.
*   **Opacity**: It is hard to answer "What are all the rules for Workspace Provisioning?" without reading both the Markdown prompts and the Python code.
*   **Rigidity**: Changing a policy requires redeploying the application code.

## 3. Proposed Architecture: Policy as Code

We propose integrating **OPA** to centralize decision-making.

### 3.1. The "Policy Decision Point" Pattern
Instead of hardcoding logic, State Machines and Agents query OPA.

**New Flow**:
1.  **State Machine Tick**:
    *   Instead of `if user.has_completed_training(): transition()`, the State Machine asks OPA:
    *   `POST /v1/data/selfservice/provisioning/allow { user: "...", training_history: [...] }`
    *   OPA returns: `true` or `false`.
2.  **Agent Tool Use**:
    *   Agent calls `check_policy(action="create_workspace", params={...})`.
    *   Tool queries OPA.
    *   Agent gets definitive "Yes/No" + "Reason" to show the user.

### 3.2. Integration Strategy (Databricks Apps)
Since this is a **Databricks App** (likely a single container/environment without full Docker control):

*   **Constraint**: Cannot easily run a sidecar container.
*   **Solution**: **Embedded OPA Server** (Subprocess) or **WASM**.
    *   **Recommendation**: Download the `opa` binary at runtime (or checking into the repo if size permits) and run it as a background subprocess in `run.py`.
    *   The Python app communicates with OPA via `http://localhost:8181`.

### 3.3. Directory Structure
```
backend/
  app/
    policies/          # New Directory
      main.rego        # Entrypoint
      workspace.rego   # Workspace specific policies
      training.rego    # Training logic
    state_machines/
      base.py          # Updated to call OPA
```

## 4. Example: Migrating Training Logic

**Current Python (`state_machines/base.py`)**:
```python
# Hardcoded logic
required_courses = ctx.get("required_trainings", [])
completed_courses = provider.get_user_training_status(user_email)
missing = [c for c in required_courses if c not in completed_courses]
if not missing:
    transition()
```

**Proposed Rego (`policies/training.rego`)**:
```rego
package selfservice.training

default allow = false

allow {
    # All required courses must be in completed_courses
    every course in input.required_courses {
        course in input.completed_courses
    }
}

missing_courses[course] {
    course := input.required_courses[_]
    not startswith(course, "optional_")
    not course_in_completed(course)
}

course_in_completed(course) {
    input.completed_courses[_] == course
}
```

## 5. Benefits & Risks

### Benefits
*   **Decoupling**: Business logic changes (e.g., "Add 'Security 101' to required training") can be made in `.rego` files without touching Python orchestration code.
*   **Shared Truth**: The Agent can query the *exact same policy* that the State Machine uses to enforce rules.
*   **Testability**: OPA has a built-in test framework (`opa test`). We can unit test policies independently of the DB or API.
*   **Visualization**: Policies can be visualized and audited more easily than Python code.

### Risks
*   **Deployment Complexity**: Need to manage the OPA binary and process lifecycle within the Databricks App environment.
*   **Performance**: Slight overhead of HTTP calls to localhost (negligible for this use case).
*   **Learning Curve**: Team needs to learn Rego (declarative vs imperative).

## 6. Migration Plan

1.  **Phase 1: Foundation**
    *   Add `opa` binary download to startup script.
    *   Add `OPAService` class to `backend/app/core/opa.py` to manage the subprocess.
    *   Add simple `health.rego`.

2.  **Phase 2: Hybrid Adoption**
    *   Create `check_policy` tool for Agents.
    *   Migrate *one* complex check (e.g., Training Verification) to Rego.
    *   Keep existing Python logic as fallback during testing.

3.  **Phase 3: Full Cutover**
    *   Remove Python logic.
    *   Expand Rego to cover Approval Assignments (Routing) and Validator checks.

## 7. Recommendation

**Go for it, starting with Phase 1 & 2.**
The "Training Verification" and "Workspace Justification" logic are prime candidates. The overhead is low, and the clarity gained for the "Agent-Driven" model is high.
