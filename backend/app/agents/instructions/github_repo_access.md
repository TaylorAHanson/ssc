# GitHub Repo / Team Access

**Goal**: Help the user request access to an existing GitHub repository or org team.

## Context
Access is **granted by the repo/team owner inside GitHub**, not by this app. This
workflow does not grant access and has no app-side approval gates — it returns the
GitHub-native request link (repo "Request access" or team "Request to join") and
records the request. The owner approves in GitHub.

## Information to Gather
1. **Target type**: `repo` or `team`. Default `repo`.
2. **Target**:
    *   For a **repo**: the repository name. *Existence check (recommended)*: use `check_github_repo` to confirm it exists.
    *   For a **team**: the team slug. Use `list_github_teams` to help the user find/confirm the right team.
3. **Permission/role** (optional, advisory only): e.g. `pull`/`push`/`admin` for a repo, or `member`/`maintainer` for a team. This is only a note on the request; GitHub controls the actual grant.
4. **Justification**: Why access is needed.

## Execution
Once the target is confirmed, call the `execute_workflow` tool with:

```json
{
  "workflow_type": "github_repo_access",
  "parameters": {
    "target_type": "repo",
    "target": "...",
    "permission": "...",
    "justification": "..."
  }
}
```

After it returns, present the `request_url` and `instructions` to the user and make
clear that a repo/team **owner approves the request in GitHub**.
