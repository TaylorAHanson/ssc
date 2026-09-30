# Release notes

User-facing release notes, rendered in the app at **Account menu → Release notes**
(`/release-notes`). See "Release notes" in `AGENTS.md` for how to add an entry.

Format rules (the app parses this file, so keep to them):

- One `## <major.minor.patch> — <YYYY-MM-DD>` heading per release, newest first.
  The top entry is shown as the current version and must match `package.json`.
- Group bullets under `### Added`, `### Changed`, `### Fixed`, or a feature-area
  subheading. Plain markdown only.
- Anything above the first `##` heading (this preamble) is not shown in the app.

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
