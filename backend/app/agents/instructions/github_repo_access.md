# GitHub Repo Access

**Goal**: Help the user get access to an existing GitHub repository.

## Context
This workflow grants a user specific permissions (read, write, admin) to a GitHub repository.

## Information to Gather
1. **Repository Name**: The name of the GitHub repository.
    *   *Existence Check (REQUIRED)*: Before calling `execute_workflow`, you MUST use `check_github_repo` to verify the repository actually exists.
2. **GitHub Username**: The user's GitHub username.
3. **Permission Level**: The level of access needed (`pull` (read), `push` (write), or `admin`).
    *   *Default*: `push`
4. **Justification**: Why access is needed.

## Execution
Once all information is confirmed, call the `execute_workflow` tool with:

```json
{
  "workflow_type": "github_repo_access",
  "parameters": {
    "repo_name": "...",
    "github_username": "...",
    "permission": "...",
    "justification": "..."
  }
}
```
