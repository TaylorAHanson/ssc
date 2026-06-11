# GitHub Token Permissions

This document is for **GitHub organization admins**. It lists every GitHub REST
API call the application makes, maps each one to the **least-privilege
permission** it requires, and provides a ready-to-send request message you can
forward to whoever owns your GitHub org.

The application talks to GitHub for two features:

- **Reusable assets / templates** — listing template repositories and creating
  new repositories from them for self-service users.
- **GitOps workflows** (e.g. the Tag Management flow) — creating a branch,
  committing files, and opening a pull request in a governed config repo.

All calls go to `https://api.github.com` and are implemented in
[`backend/app/providers/github/client.py`](../backend/app/providers/github/client.py).

---

## Authentication options

The app supports two credential types. **A GitHub App is strongly recommended**
for organization use.

| Mode | Config | Notes |
| --- | --- | --- |
| **GitHub App** (recommended) | `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (or the secret-scope variant), `GITHUB_APP_INSTALLATION_ID` | Per-installation, fine-grained, org-approved. **Can create org-level repositories** (fine-grained PATs cannot — see caveats). Best security + audit story. |
| **Personal Access Token (PAT)** | `GITHUB_TOKEN`, `GITHUB_ORG` | Simpler to set up. Use a **fine-grained** PAT where possible; a classic PAT is the fallback. |

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

---

## Fine-grained PAT / GitHub App permissions (recommended)

Grant **only** these repository permissions, scoped to the specific org and the
specific repositories the app touches (template repos + the GitOps config repo):

| Permission | Access | Why (which calls) |
| --- | --- | --- |
| **Metadata** | Read | Mandatory baseline (auto-selected). Backs `GET /repos/{owner}/{repo}` and the repo-list calls. |
| **Contents** | Read & write | Branch refs (`git/ref`, `git/refs`) and file commits (`contents/{path}`). Also the read half of "create from template". |
| **Pull requests** | Read & write | Open/find/poll PRs (`/pulls`). |
| **Administration** | Read & write | Repo creation (`/user/repos`, `/orgs/{org}/repos`), template generation (`/generate`), and adding collaborators (`/collaborators/{user}`). |
| **Workflows** | Read & write | **Only if** any template or committed file lives under `.github/workflows/`. GitHub blocks committing workflow files without this. |

> `GET /user` needs no specific permission — it works with any fine-grained token.

---

## Classic PAT scopes (fallback)

If you must use a classic PAT instead of fine-grained / GitHub App:

| Scope | Why |
| --- | --- |
| `repo` | Full control of repositories — covers repo read/create, contents, branches, PRs, and collaborators. |
| `workflow` | **Only if** the app commits files under `.github/workflows/`. |

`repo` is broad; prefer the fine-grained permission set above whenever possible.

---

## Caveats admins should know

1. **Fine-grained PATs cannot create org-level repositories.** `POST /orgs/{org}/repos`
   returns `403 Forbidden` with a fine-grained PAT even when all repo permissions
   are granted. For org repo creation use a **GitHub App** (preferred) or a
   **classic PAT**, or have the org pre-create the repos.
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

---

## Request message to send your GitHub org admin

> **Subject: GitHub access for our internal self-service application**
>
> Hi [admin],
>
> Our internal self-service platform needs scoped GitHub access to (a) create
> repositories from our template repos for self-service users and (b) run a
> GitOps flow (create a branch, commit config files, open a PR) against our
> governance config repo.
>
> **Preferred:** a **GitHub App** installed on our org, restricted to the
> template repositories and the config repo, with these **repository
> permissions**:
>
> - Metadata: **Read**
> - Contents: **Read & write**
> - Pull requests: **Read & write**
> - Administration: **Read & write** (needed for repo creation + adding collaborators)
> - Workflows: **Read & write** (only if our templates include `.github/workflows/` files)
>
> If a GitHub App isn't feasible, a **classic PAT** with the `repo` scope (plus
> `workflow` if we commit workflow files) works as a fallback. Please avoid a
> fine-grained PAT for repo creation at the org level — GitHub blocks that with a
> 403.
>
> We'll store the credential in our Databricks secret scope and never in source
> control. Happy to walk through the exact API calls — they're documented on our
> side.
>
> Thanks!
