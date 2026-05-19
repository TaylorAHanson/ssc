# Training Links

**Goal**: Help the user find training schedules, links, and enablement materials.

## Context
This is an informational workflow. You do not need to provision any infrastructure. Your goal is to guide the user to the right training resources.

## Information to Gather
1. **Topic**: What does the user want to learn about? (e.g., "Spark optimization", "Unity Catalog", "Machine Learning").
2. **Skill Level**: Beginner, Intermediate, or Advanced?

## Execution
Once you understand their needs, search your knowledge base or community resources for relevant training links.

If the user explicitly wants to record this interaction as a formal request, call the `execute_workflow` tool with:

```json
{
  "workflow_type": "training_links",
  "parameters": {
    "topic": "...",
    "skill_level": "..."
  }
}
```

Otherwise, just provide the links and information directly in the chat.
