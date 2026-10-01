# Release notes

User-facing release notes, rendered in the app at **Account menu → Release notes**
(`/release-notes`). See "Release notes" in `AGENTS.md` for how to add an entry.

Format rules (the app parses this file, so keep to them):

- One `## <major.minor.patch> — <YYYY-MM-DD>` heading per release, newest first.
  The top entry is shown as the current version and must match `package.json`.
- Group bullets under `### Added`, `### Changed`, `### Fixed`, or a feature-area
  subheading. Plain markdown only.
- Anything above the first `##` heading (this preamble) is not shown in the app.

## 1.2.0 — 2026-10-01

### Tag Management

- You can now tag any table or view in the governed catalogs, not just tables that belong to a dataset. A single search box lists every governed dataset when you click it and finds tables and views as you type. Tick as many results as you like: a ticked dataset adds all of its tables, a ticked table adds just that one, and both can be edited and reviewed together in one change.
- Column tags are now supported. Open a table's **Columns** to see each column's tags and edit them, for example to mark a column as restricted. Column changes go through the same policy, typo and risk checks as table tags. They can be applied when changes are applied directly; when changes go through pull requests, column tags are shown read-only.
- The search box also finds objects by their tags: type `main.sales.*` for a whole schema, `classification=restricted` for objects with that value, or `!data_owner` for objects missing a tag. Press Enter to add every match.
- Filter the list by name or tag, then set or remove one tag on everything shown in a single step. Tables with many columns offer the same filter and bulk edit for their columns. Each row shows its tags at a glance, with new, changed and removed tags highlighted, and you can show only what you've changed.
- Tags governed by a Unity Catalog tag policy are now checked before anything is applied. If a policy limits a tag to certain values, the editor suggests those values and flags any other value as you type, and the review blocks the change instead of letting it fail partway through.
- Tag Management is now split into two tabs: **Edit Tags** for making changes and **Change History** for reviewing past changes, which now shows who submitted each one. Your edits are kept while you switch between them.
- The key field now suggests Unity Catalog governed tags as you type, showing how many values each one allows.
- If a column mask or row filter depends on a tag you're changing, the review now names that access policy and raises the risk score, because the change can affect who can see the data.
- Tables whose edit would add them to, move them between, or take them out of a dataset are now clearly marked.
- Tags on materialized views and streaming tables, and on their columns, are now applied with the statements those objects require.
- Lowering or removing a classification or PII tag now raises the risk score, since access and masking rules often depend on those tags. Tags meant for tables, such as `dataset` or `data_owner`, can no longer be set on columns by mistake.

### Fixed

- Changes to certified tables are now counted in the risk score as intended.
- When changes go through pull requests, the review now shows the exact SQL that will be committed.
- Table and column names are now validated and quoted before any SQL is run, so names with special characters can't produce unsafe statements.
- Trying to change the `system.certification_status` tag, which is set automatically by certification, now explains why it isn't allowed instead of producing an empty change. Other `system.` tags can now be viewed and edited like any other tag.

## 1.1.0 — 2026-09-30

### Added

- A new **Features & Navigation** page in **Admin → Settings** lists every capability in one place, grouped like the sidebar. Each capability has a single switch, and the sidebar pages it owns can be shown or hidden underneath it. Any new capability appears automatically under **Other**.
- The **Admin Dashboard** has been redesigned around what needs action. It now shows charts of requests over time, by status, by type, and median time to complete, plus a **Needs attention** panel that lists stuck requests, the latest failures, and the oldest requests awaiting approval.
- Every number and chart on the Admin Dashboard is clickable. Selecting a card, chart bar, status slice, or requester filters the request list below, and each active filter appears as a chip you can clear. Click any request to open its full details.
- A time range selector (7, 30, or 90 days, or all time) applies to the whole dashboard. Open requests are always included so older work that is still waiting doesn't disappear. The range and filters are kept in the page address, so you can share a filtered view.

### Changed

- The Admin Dashboard now hides automated runs, such as scheduled policy scans and scheduled reports, by default so people's requests aren't buried. A switch shows them again, and the dashboard always says how many are hidden.
- The dashboard's stuck-request count now works for requests waiting on an approval, and all times are shown in your local time zone. The "Active Workspaces" and estimated "Labor Saved" figures have been removed because they didn't reflect real activity.
- **Admin** is now the first link in the **Control Tower** section of the sidebar.
- **Admin → Settings** is now organized into the same sections as the sidebar (General, Discover & Analyze, Requests & Approvals, Learn & Share, Watch Tower, Control Tower, Platform), with sub-headings inside longer pages. The system banner now lives under **Appearance**, and each schedule sits with the feature it runs.
- The Event Calendar's feed URL can now be changed under **Admin → Settings → Calendar**, next to its sync schedule, without a redeploy.
- Settings for a capability that is switched off are collapsed under **Inactive settings** with a link to turn it on. A page whose capability is off is marked **Off** in the settings menu.
- Turning a capability off now also hides its pages from the sidebar, so what people see always matches what is enabled.
- The optional grid of quick-action cards on the home page has been removed, along with the switch between it and the chat. The home page now always opens straight into the chat.
- The Algolia option for documentation search has been removed. Documentation lookups now always use the built-in search.
- The Genie link and default Genie space settings are no longer editable in Settings.

## 1.0.0 — 2026-09-30

The first stable release. It brings a conversational agent, governed requests and
approvals, data discovery, governance tooling, and no-code building blocks together
in one place.

### Agent and requests

- Ask the agent in plain language to find data, answer questions about your data, or request access and resources. Before it changes anything, it shows you its plan and asks you to confirm.
- Answers about your data appear directly in the chat, with tables and charts rendered inline, and your conversations are saved so you can pick up where you left off.
- Every request is tracked under **My Requests**, with its live status, step-by-step progress, and a full history of what was done on your behalf.
- Long-running work continues in the background and resumes safely after an interruption, so a request is never applied twice.

### Discover and analyze

- Browse business domains, search the catalog, discover metric views, and explore data lineage from the **Discover** page.
- Every domain, subdomain, and asset on **Discover** has its own link. Use **Copy link** to share exactly what you're looking at, and anyone who opens it lands on the same view.
- **Reports** lets you manage recurring reports that the agent generates automatically.

### Approvals

- Higher-impact changes, such as granting access or creating infrastructure, are routed to the right approvers before they run.
- Approvers work from **Pending Approvals**, where each approval card includes a summary of the work completed so far, and can set up out-of-office delegations so nothing stalls while they are away.

### Governance

- Every action the agent takes is checked against policy and recorded in an audit trail, and the agent never has more access to data than the person asking.
- **OmniGuard** scans workspaces for policy violations and reports findings you can act on, while the **Allowlist** records approved exceptions it should leave alone.
- **Data Certification (ODCS)** tracks which datasets meet your standards for quality, documentation, access control, and classification.
- **Data Products (ODPS)** catalogs your logical data products and their contracts, and **Tag Management** keeps governance tags on data assets consistent.
- The **Context Catalog** holds the house rules and reference material the agent reads, so it steers people toward compliant choices before a request reaches an approver.

### Build and customize

- **Workflow Studio** lets administrators design request workflows visually, choosing the instructions, allowed actions, and approval rules without writing code.
- The **Tool Registry** shows every action that workflows and the agent are allowed to take, and who each one is available to.
- **Training Studio** is where administrators author learning tracks and courses, upload media, and see how the content is being used.

### Learn and share

- **Training** offers structured learning paths, and the **Event Calendar** lists upcoming sessions.
- **Templates & Assets** is a shared library of design patterns, templates, and reusable components, and **Community Links** collects the resources and tools your organization recommends.

### Administration

- Administrators can change the app's name, logo, colors, and enabled features, and adjust runtime settings live from **Admin → Settings** without a redeploy.
- Anyone can send feedback or clear their saved data from the account menu, and this **Release notes** page shows what has changed in each version.
