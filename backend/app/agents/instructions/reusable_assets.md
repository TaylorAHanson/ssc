# Reusable Assets

**Goal**: Help the user find reusable assets, code templates, and architecture patterns.

## Context
This is an informational workflow. You do not need to provision any infrastructure. Your goal is to guide the user to the right templates.

## Information to Gather
1. **Use Case**: What is the user trying to build? (e.g., "a streaming pipeline", "a dbt project", "a new ML model").

## Execution
Once you understand their use case, search your knowledge base or community resources for relevant templates.

If the user explicitly wants to record this interaction as a formal request, call the `execute_workflow` tool with:

```json
{
  "workflow_type": "reusable_assets",
  "parameters": {
    "use_case": "..."
  }
}
```

Otherwise, just provide the links and information directly in the chat.
