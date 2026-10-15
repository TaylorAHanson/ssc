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
That removes it from the sidebar and the account menu, and removes the help
links that point to it from other pages.

## Roles & Access

**General → Roles & Access** controls who holds which role. People get roles
through **Role Mappings**, which link a group, role or user in Databricks to a
role in {{brand_name}}.

To add one, select **Add Mapping**, enter the **External Identity (SCIM)**
exactly as it's named in Databricks (a group name, role or user email), and
pick the **Internal Role**. Everyone in that group gets the role. Roles decide
which sidebar sections people see and which approvals they can act on.

## Other settings

The remaining pages each sit with the feature they configure. The ones admins
visit most often:

- **Notifications**: how emails are sent, and what a requester is told when a
  request is rejected.
- **Agent**: the assistant's model, limits and guardrails, and where it may
  look things up on the web.
- **Data Catalog**: which Unity Catalog catalogs are governed, and how often
  the catalog in Discover is refreshed.
- **Links & Embedded Apps**: the links on the **Community Links** page, and
  other apps shown inside {{brand_name}} from the sidebar.
- **Calendar**: where events on the **Event Calendar** come from, and how
  often they're synced.
- **Target Workspaces**: the Databricks workspaces that OmniGuard scans and
  Data Certification checks.

For the governance settings, see [Governance tools](governance.md). For
Workflow Studio and Preview Features, see
[Workflows, tools and knowledge](workflows-and-tools.md).
