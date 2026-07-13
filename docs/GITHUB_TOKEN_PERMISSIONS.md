# GitHub Token Permissions

This document is for **GitHub organization admins**. It lists every GitHub REST
API call the application makes, maps each one to the **least-privilege
permission** it requires, and provides a ready-to-send request message you can
forward to whoever owns your GitHub org.

The application talks to GitHub for three features:

- **Reusable assets / templates** — listing template repositories and creating
  new repositories from them for self-service users.
- **GitHub team access** — validating org membership, listing teams, creating
  teams, granting repo access, and adding members (GitHub access-request workflow).
- **GitOps workflows** (e.g. the Tag Management flow) — creating a branch,
  committing files, and opening a pull request in a governed config repo.

All calls go to `https://api.github.com` and are implemented in
[`backend/app/providers/github/client.py`](../backend/app/providers/github/client.py).

---

## Authentication

The app authenticates to GitHub with a **Personal Access Token (PAT)** only (GitHub App credentials are not supported).

| Config | Notes |
| --- | --- |
| `GITHUB_TOKEN`, `GITHUB_ORG` | Use a token owned by a dedicated machine/service account. A **classic PAT** with `repo` scope is required for org-level repo creation (fine-grained PATs are blocked from `POST /orgs/{org}/repos` — see caveats). The token is stored in a Databricks secret scope and read at runtime. |

---

## Exact API calls the app makes

| Method & path | Purpose | Code |
| --- | --- | --- |
| `GET /user` | Resolve the authenticated login (owner inference) + health check | `create_repo`, `check_repo_exists`, `list_templates`, `create_from_template`, `set_permissions`, `_resolve_repo_path`, `health_check` |
| `GET /user/repos` | List the user's repos (template discovery) | `list_templates` |
| `GET /orgs/{org}/repos` | List org repos (template discovery) | `list_templates` |
| `GET /users/{org}/repos` | Public-repo fallback when the org list 404s | `list_templates` |
| `GET /repos/{owner}/{repo}` | Check whether a repo already exists | `check_repo_exists` |
| `POST /user/repos` | Create a repo under the user | `create_repo` |
| `POST /orgs/{org}/repos` | Create a repo under the org | `create_repo` |
| `POST /repos/{template}/generate` | Create a repo from a template repo | `create_from_template` |
| `PUT /repos/{owner}/{repo}/collaborators/{user}` | Grant a user access to a repo | `set_permissions` |
| `GET /repos/{owner}/{repo}/git/ref/heads/{branch}` | Read a branch's head SHA | `create_branch` |
| `POST /repos/{owner}/{repo}/git/refs` | Create a branch | `create_branch` |
| `GET /repos/{owner}/{repo}/contents/{path}` | Read a file (to get its blob SHA before update) | `create_or_update_file` |
| `PUT /repos/{owner}/{repo}/contents/{path}` | Create/update a file (commit) | `create_or_update_file` |
| `POST /repos/{owner}/{repo}/pulls` | Open a pull request | `create_pull_request` |
| `GET /repos/{owner}/{repo}/pulls` | Find an existing PR (idempotent retry) | `create_pull_request` |
| `GET /repos/{owner}/{repo}/pulls/{number}` | Poll a PR's state / merge status | `get_pull_request` |
| `GET /users/{username}` | Look up a GitHub user (access-request validation) | `get_user`, `check_github_user` |
| `GET /orgs/{org}/members/{username}` | Check org membership | `is_org_member` |
| `GET /orgs/{org}/teams` | List org teams | `list_teams` |
| `GET /orgs/{org}/teams/{slug}` | Resolve a team by slug | `create_team` (idempotent retry) |
| `POST /orgs/{org}/teams` | Create an org team | `create_team` |
| `PUT /orgs/{org}/teams/{slug}/repos/{org}/{repo}` | Grant a team permission on a repo | `grant_team_repo` |
| `PUT /orgs/{org}/teams/{slug}/memberships/{username}` | Add/update team membership | `add_team_member` |

---

## Fine-grained PAT permissions

Grant **only** these permissions, scoped to the specific org and the
specific repositories the app touches (template repos + the GitOps config repo):

### Repository permissions

| Permission | Access | Why (which calls) |
| --- | --- | --- |
| **Metadata** | Read | Mandatory baseline (auto-selected). Backs `GET /repos/{owner}/{repo}` and the repo-list calls. |
| **Contents** | Read & write | Branch refs (`git/ref`, `git/refs`) and file commits (`contents/{path}`). Also the read half of "create from template". |
| **Pull requests** | Read & write | Open/find/poll PRs (`/pulls`). |
| **Administration** | Read & write | Repo creation (`/user/repos`, `/orgs/{org}/repos`), template generation (`/generate`), and adding collaborators (`/collaborators/{user}`). |
| **Workflows** | Read & write | **Only if** any template or committed file lives under `.github/workflows/`. GitHub blocks committing workflow files without this. |

### Organization permissions

Required for team management and org membership checks:

| Permission | Access | Why (which calls) |
| --- | --- | --- |
| **Members** | Read | `GET /orgs/{org}/members/{username}` |
| **Members** | Read & write | `PUT /orgs/{org}/teams/.../memberships/{username}` (invite/add to team) |
| **Administration** | Read & write | `POST /orgs/{org}/teams`, `GET /orgs/{org}/teams`, team–repo grants |

> `GET /user` and `GET /users/{username}` need no specific permission — they work with any fine-grained token.

---

## Classic PAT scopes (required for org repo creation)

Use a classic PAT when the app must create repositories at the org level:

| Scope | Why |
| --- | --- |
| `repo` | Full control of repositories — covers repo read/create, contents, branches, PRs, and collaborators. |
| `read:org` | List teams and check org membership. |
| `admin:org` | Create teams and manage team membership (narrower than full org admin if your org supports it). |
| `workflow` | **Only if** the app commits files under `.github/workflows/`. |

`repo` is broad; prefer the fine-grained permission set above whenever possible.

---

## Caveats admins should know

1. **Fine-grained PATs cannot create org-level repositories.** `POST /orgs/{org}/repos`
   returns `403 Forbidden` with a fine-grained PAT even when all repo permissions
   are granted. For org repo creation use a **classic PAT** or have the org
   pre-create the repos.
2. **Org approval is required.** A fine-grained PAT scoped to an org's resources
   must be approved by an org owner before it works.
3. **SSO authorization.** If the org enforces SAML SSO, the token must be
   explicitly authorized for the org.
4. **Workflow files.** Committing anything under `.github/workflows/` requires
   the **Workflows** permission (fine-grained) or the `workflow` scope (classic),
   in addition to Contents write.
5. **Scope the token to specific repos.** Limit the token to the template repos
   and the GitOps config repo (`GOVERNANCE_TAGS_REPO`) rather than "all
   repositories" to keep blast radius minimal.
6. **Team invites for non-members.** Adding a user who is not yet an org member
   sends a GitHub org invitation they must accept before team membership is active.

---

## Request message to send your GitHub org admin

> **Subject: GitHub access for our internal self-service application**
>
> Hi [admin],
>
> Our internal self-service platform needs scoped GitHub access to (a) create
> repositories from our template repos for self-service users, (b) manage GitHub
> team membership for access requests, and (c) run a GitOps flow (create a branch,
> commit config files, open a PR) against our governance config repo.
>
> We'd like a **Personal Access Token owned by a dedicated machine/service
> account**, scoped to the template repositories and the config repo.
>
> Because we create repositories at the org level, we need a **classic PAT** with
> the `repo` scope (plus `read:org` / `admin:org` for team flows, and `workflow`
> only if we commit files under `.github/workflows/`). A fine-grained PAT can't
> create org-level repos — GitHub blocks `POST /orgs/{org}/repos` with a 403 — so
> if you'd prefer fine-grained, we'd need the org to pre-create the repos instead.
> For reference, the equivalent fine-grained permissions would be:
>
> - Metadata: **Read**
> - Contents: **Read & write**
> - Pull requests: **Read & write**
> - Administration: **Read & write** (repo + org, for repo creation, teams, collaborators)
> - Members: **Read & write** (org membership + team invites)
> - Workflows: **Read & write** (only if our templates include `.github/workflows/` files)
>
> We'll store the credential in our Databricks secret scope and never in source
> control. Happy to walk through the exact API calls — they're documented on our
> side.
>
> Thanks!
