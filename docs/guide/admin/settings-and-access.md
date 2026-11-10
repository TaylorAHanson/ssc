# Settings, features and access

Super Admins and Application Admins configure {{brand_name}} under
**Admin → Settings**, without a
redeploy. This page explains how the settings are organized and covers the
ones you're most likely to change.

## How Settings works

The settings menu on the left is organized like the sidebar: **General**,
**Discover & Request**, **Requests & Approvals**, **Learn & Share**, **Watch
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

## Role Mappings

**General → Role Mappings** controls who holds which role. A mapping links a
group, role or user in Databricks to a role in {{brand_name}}. Someone with
several roles gets everything each of them allows.

| Role | What it's for |
|---|---|
| **Super Admin** | Everything. The only role that changes role mappings, and the only one that can act on any approval as a break-glass override. |
| **Application Admin** | Runs the app: settings, the Admin pages, the Tool Registry, Workflow Studio, Application Admin approvals and Edit & Restart. Doesn't hold governance or security permissions. This role used to be called Platform Admin. |
| **Governance Admin** | The governance tools in Watch Tower, Access Rules administration, Governance Admin approvals and the assistant's contract and data product drafting tools. |
| **Security Admin** | Security Admin approvals. |
| **Ops Admin** | Operations: sees every request and delegation, watches OmniGuard runs and clears stuck ones, and uses the workspace diagnostics tools, including the URL reachability check. |
| **User** | Self-service: asking the assistant, filing and tracking your own requests. |

The assistant no longer has its own cost, audit-log or privilege-audit tools.
Cost and usage analysis, audit-log investigations and Unity Catalog-wide access
audits are better done in Databricks Genie Code, which runs as you in your
workspace; ask the assistant and it gives you a prompt to start from. See
[Work that happens in Databricks](../using-the-assistant.md#work-that-happens-in-databricks).

Below the mappings, **Roles** lists each role with the groups and people mapped
to it.

To add a mapping, select **Add Mapping**, enter the **External Identity
(SCIM)** exactly as it's named in Databricks (a group name, role or user
email), and pick the **Internal Role**. Everyone in that group gets the role.
Only Super Admins see **Add Mapping**, **Edit** and **Delete**; Application
Admins see the page read-only.

When the Platform Admin role was renamed, every mapping to it became an
Application Admin mapping, and the same group or person was also mapped to
Super Admin so nobody lost access. Delete the Super Admin mappings you don't
want to keep.

## Role Permissions

**General → Role Permissions** shows what each role can do: every permission,
grouped by area, with a check under each role that holds it. The Super Admin
column is always fully checked. A permission marked with a number also goes to
the roles listed in a setting, named in the footnote with its current value:
for example the Metadata Manager editor roles, or the Access Rules viewer
roles. The page is read-only; change who holds a role on
[Role Mappings](#role-mappings).

## Approvals

**Requests & Approvals → Approvals** holds settings that apply to every
workflow. **Block approving your own request** is on by default: nobody can
approve a request they submitted, Super Admins included, so another
approver must. Rejecting your own request, which withdraws it, is still
allowed.

Some approvals go to a Databricks group rather than a role, such as the
approver groups for [access rules](access-rules.md). Anyone in that group sees
the approval and can act on it; there's no role mapping to add. The approval
card names the group, or says Application Admins approve because none is set.

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
  Discover's domains and subdomains are the ones defined in Databricks
  Discover (**Manage Domains**), read at each catalog refresh. The identity
  the catalog sync runs as needs the **MANAGE DISCOVER** permission there;
  without it, Discover keeps the last domains it read, and shows everything
  under **Other** if it has never read any.
- **Legacy Dashboards** (under **Discover & Request**): map dashboards people
  used before, for example in Tableau, to the metric views that replace them.
  People then find them in Discover under **Legacy Dashboards** and when they
  search for the old name. Select **Add mapping**, or **Import** to paste a
  list from a spreadsheet. The page is marked **Off** while the data catalog
  is switched off, because nobody would see the mappings.
- **Links & Embedded Apps**: the links on the **Community Links** page, and
  other apps shown inside {{brand_name}} from the sidebar. An embedded app
  with no **Sidebar group** goes under **Data & AI**; apps saved under the old
  **Build & Customize** or **Discover & Analyze** groups move to **Data & AI**
  and **Discover & Request**. **Genie One link** is the address of Genie One
  for your Databricks account, such as the one you open from the account
  console. While it's blank, **Genie One** is hidden from the sidebar and the
  assistant links each workspace's own Genie One instead. **Support contact**
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
