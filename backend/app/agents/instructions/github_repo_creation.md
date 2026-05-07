# GitHub Repository Creation Instructions

**Goal**: Create a new GitHub repository in the organization.

## Information to Gather
1.  **Project Description**: What is the purpose of this repository? What technologies or languages will it use?
    *   *Action*: Use the description to help suggest relevant templates in step 6.
2.  **Business Domain**: What is the business domain for this project? (e.g., "marketing", "sales", "finance")
    *   *Validation*: Alphanumeric with hyphens only.
3.  **Project Name**: What is the name of the project?
    *   *Validation*: Alphanumeric with hyphens only.
4.  **Check Availability**: Once you have the domain and project name, **MANDATORY**: Call `check_github_repo` with the name `atlas-{businessdomain}-{projectname}` to ensure it is available.
    *   If it exists already, ask the user for a different project name.
5.  **Visibility**: Public (Internal) or Private?
    *   *Options*: `internal`, `private`.
6.  **Template Discovery**: Call `list_github_templates` AFTER you get the description to find available reusable templates.
    *   Use the **Project Description** to filter or recommend templates.
    *   Present the available templates to the user and ask if they would like to use one.
    *   If they choose a template, use its `name` in the `execute_workflow` call.

## Naming Convention
You MUST construct the repository name as: `atlas-{businessdomain}-{projectname}`.
Before executing, inform the user: "The repository will be created with the name: **atlas-{businessdomain}-{projectname}**".

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

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "github_repo_creation",
  "parameters": {
    "repo_name": "atlas-{businessdomain}-{projectname}",
    "description": "...",
    "visibility": "...",
    "template": "..."
  }
}
```
