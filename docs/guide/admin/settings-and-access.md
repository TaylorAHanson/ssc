# Settings, features and access

Platform admins configure {{brand_name}} under **Admin → Settings**, without a
redeploy. This page explains how the settings are organized and covers the
ones you're most likely to change.

## How Settings works

The settings menu on the left is organized like the sidebar: **General**,
**Discover & Analyze**, **Requests & Approvals**, **Learn & Share**, **Watch
Tower**, **Control Tower** and **Platform**. Each page starts with a short
description of what it controls.

- Change as many values as you like, then select **Save**. The button shows
  how many changes you're saving. **Discard** throws them away.
- Saved changes are live straight away. Some take effect the next time people
  load a page.
- Settings for a capability that's switched off are folded away under
  **Inactive settings**, with a link to turn it on. The page itself is marked
  **Off** in the settings menu.
- **Platform → Infrastructure** shows settings that are managed by your
  deployment configuration. They're there for reference, and only change on a
  redeploy.

## Appearance

**General → Appearance** controls how the app presents itself:

- **Brand name**, **Short name** and **Logo URL**. The brand name is used
  throughout the app, including this guide.
- The colors: primary, secondary, sidebar, and the colors used for
  information, alerts, warnings and success.
- The **System banner**: a message shown across the top of every page. Turn on
  **Show banner**, pick a **Banner style** and write the **Banner message**.
  Use it for planned maintenance or important announcements.

## Features & Navigation

**General → Features & Navigation** lists every capability in one place,
grouped like the sidebar. Each capability has one main switch. Underneath it,
you can show or hide the sidebar pages it owns.

- Turning a capability off disables it completely: its pages, its background
  jobs and the assistant's tools for it. It also disappears from the sidebar.
- Capabilities marked **Sidebar only** just show or hide a page. Hiding one
  removes it from the menu, but the page still works if someone has a link to
  it.
- New capabilities appear automatically under **Other**.

For example, to hide this guide, switch off **Guide** under **Learn & Share**.
That removes it from the account menu and removes the help links that point to
it from other pages.

## Roles & Access

**General → Roles & Access** controls who holds which role. People get roles
through **Role Mappings**, which link a group, role or user in Databricks to a
role in {{brand_name}}.

To add one, select **Add Mapping**, enter the **External Identity (SCIM)**
exactly as it's named in Databricks (a group name, role or user email), and
pick the **Internal Role**. Everyone in that group gets the role. Roles decide
which sidebar sections people see and which approvals they can act on.

## Approvals

**Requests & Approvals → Approvals** holds settings that apply to every
workflow. **Block approving your own request** is on by default: nobody can
approve a request they submitted, Platform Admins included, so another
approver must. Rejecting your own request, which withdraws it, is still
allowed.

Some approvals go to a Databricks group rather than a role, such as the
approver groups for [access rules](access-rules.md). Anyone in that group sees
the approval and can act on it; there's no role mapping to add. The approval
card names the group, or says Platform Admins approve because none is set.

## Other settings

The remaining pages each sit with the feature they configure. The ones admins
visit most often:

- **Notifications**: how emails are sent, and what a requester is told when a
  request is rejected.
- **Agent**: the assistant's model, limits and guardrails, and where it may
  look things up on the web.
- **Data Catalog**: which Unity Catalog catalogs are governed, how often the
  catalog in Discover is refreshed, the most rows a **Data preview** shows
  (**Data preview rows**), and how many assets each person can pin
  (**Pinned assets per user**). **Hide from Discover tag keys** keeps assets
  out of Discover and the assistant's catalog search: add a key such as
  `hide_from_discover`, then put a tag with that key (any value) on a
  catalog, schema, table, view, metric view or column in Unity Catalog, or on
  a dashboard, Genie space, app or job in the workspace. Leave the list empty
  to hide nothing. Changing the list applies straight away; a tag you add or
  remove in Unity Catalog applies after the next catalog refresh, and on
  dashboards, Genie spaces and apps within about ten minutes. Hiding isn't
  access control: people with access can still open the asset in Databricks.
- **Legacy Dashboards** (under **Discover & Analyze**): map dashboards people
  used before, for example in Tableau, to the metric views that replace them.
  People then find them in Discover under **Legacy Dashboards** and when they
  search for the old name. Select **Add mapping**, or **Import** to paste a
  list from a spreadsheet. The page is marked **Off** while the data catalog
  is switched off, because nobody would see the mappings.
- **Links & Embedded Apps**: the links on the **Community Links** page, and
  other apps shown inside {{brand_name}} from the sidebar. **Support contact**
  holds the name, email and hours or channel of whoever handles day-to-day
  help. It shows on the **Team & Support** tab of every dataset in Discover,
  and the assistant gives it when someone asks who to contact. It is blank
  until you fill it in, and nothing is shown while it is.
- **Calendar**: where events on the **Event Calendar** come from, and how
  often they're synced.
- **Training** (under **Learn & Share**): the Databricks Academy catalog that
  Training Studio links courses to (its address, how often it syncs, and which
  course languages to keep), and the upload for a CSV of training completions
  from your learning system. Uploaded completions update people's progress on
  the **Training** page and satisfy any workflow step that requires a course.
- **Target Workspaces**: the Databricks workspaces that OmniGuard scans and
  Data Certification checks.
- **Infrastructure → Logging**: **Log page views and browser errors** writes
  each page a person opens, and any error their browser hits, to
  {{brand_name}}'s own log, with their email and app roles. It's on by
  default. Nothing is sent outside the app, and no page content, chat text or
  query results are included. Turn it off to stop the browser reporting
  anything. Every line in the log, not only these, now names the person's app
  roles, and a bug report with diagnostics attached shows a request ID for
  each failed call, which you can search the log for.

For the governance settings, see [Governance tools](governance.md). For
Workflow Studio and Preview Features, see
[Workflows, tools and knowledge](workflows-and-tools.md).
