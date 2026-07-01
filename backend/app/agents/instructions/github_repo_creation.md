# GitHub Repository Creation Instructions

**Goal**: Create a new GitHub repository in the organization.

## Information to Gather
1.  **Project Description**: What is the purpose of this repository? What technologies or languages will it use?
    *   *Action*: Use the description to help suggest relevant templates in step 6.
2.  **Business Domain**: What is the business domain for this project? (e.g., "marketing", "sales", "finance")
    *   *Validation*: Alphanumeric with hyphens only.
3.  **Project Name**: What is the name of the project?
    *   *Validation*: Alphanumeric with hyphens only.
4.  **Admin Group**: The LMWS group/list that will be granted admin rights over this repository.
    *   *Enterprise Policy*: Individual users CANNOT be repo admins. It must be an LMWS group/list.
5.  **Check Availability**: Once you have the domain and project name, **MANDATORY**: Call `check_github_repo` with the name `{{brand_slug}}-{businessdomain}-{projectname}` to ensure it is available.
    *   If it exists already, ask the user for a different project name.
6.  **Visibility**: Public (Internal) or Private?
    *   *Options*: `internal`, `private`.
7.  **Template Discovery**: Call `list_github_templates` AFTER you get the description to find available reusable templates.
    *   Use the **Project Description** to filter or recommend templates.
    *   Present the available templates to the user and ask if they would like to use one.
    *   If they choose a template, use its `name` in the `execute_workflow` call.
8.  **Contributors**: Ask for the **GitHub usernames** of the people who will be contributing to this repository.
    *   *Explain the team setup*: Tell the user that a **GitHub team will be created alongside the repository** (named after the repo by default) and granted **write** access, and that the contributors they name will be added to that team. This is how ongoing access is managed — add/remove people from the team rather than the repo directly.
    *   *Validation (MANDATORY when usernames are given)*: Call `check_github_user` with the list of usernames to confirm each login exists. If any are not found, show which ones and ask the user to correct them before proceeding.
    *   *Set expectations*: Users who are already org members are added immediately; anyone who isn't will receive a GitHub invitation they must accept.
    *   Contributors are optional — if the user has none yet, proceed and note the team can have members added later.

## Naming Convention
You MUST construct the repository name as: `{{brand_slug}}-{businessdomain}-{projectname}`.
Before executing, inform the user: "The repository will be created with the name: **{{brand_slug}}-{businessdomain}-{projectname}**".

## Disambiguation
If you asked multiple questions and the user responds with one or two words and it isn't clear which question they are responding to, ask for clarification.

## Templates
Try to determine what the user is doing. There are currently templates for the following
- `data-engineering` - includes a databricks.yml file, sample notebook, and sample job configuration
- `data-science` - includes a databricks.yml file, sample notebook, and sample job configuration
- `databricks-apps` - includes a sample app, app.yml file, and databricks.yml file
- `genie-room` - includes a method to copy data from a dev genie room to higher environments

## SLA
This workflow is near instant. There is a high likelihood that it completes in less than a minute. Let the user know this. 

## What This Workflow Does
For transparency, tell the user this workflow will, in one go:
1. Create the repository.
2. Create a GitHub **team** (named after the repo by default) and grant it **write** access to the repo.
3. Add the named contributors to that team (org members immediately; others by invitation).

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "github_repo_creation",
  "parameters": {
    "repo_name": "{{brand_slug}}-{businessdomain}-{projectname}",
    "description": "...",
    "admin_group": "...",
    "visibility": "...",
    "template": "...",
    "members": ["contributor1", "contributor2"]
  }
}
```

*Optional overrides*: `team_name` (defaults to the repo name), `team_permission` (defaults to `push`/write), `team_privacy` (defaults to `closed`), and `member_role` (defaults to `member`).
