# Governance Operating Guide

**Who this guide is for:** Governance and Platform Admins who run day-to-day governance on this platform. It is written for a governance professional, **not** a software engineer — no coding or infrastructure knowledge is assumed. Where a technical term is unavoidable, it is defined in plain language (see the [Glossary](#glossary) at the end).

**What this guide is:** a set of *operating instructions* — what each governance tool does, where to click, and how to respond to what you see. It is not an architecture document. For how the system is built, see `ARCHITECTURE.md`. For authoring approval workflows, see the [Platform Administration Guide](./PLATFORM_ADMINISTRATION.md).

---

## Contents

1. [How governance works here (the 60-second version)](#1-how-governance-works-here-the-60-second-version)
2. [Roles: who can do what](#2-roles-who-can-do-what)
3. [Where to find everything](#3-where-to-find-everything)
4. [Data Governance](#4-data-governance)
5. [Platform Governance](#5-platform-governance)
6. [Workflow Governance](#6-workflow-governance)
7. [Settings you can change yourself](#7-settings-you-can-change-yourself)
8. [Policy reference](#8-policy-reference)
9. [Runbooks: "How do I…?"](#9-runbooks-how-do-i)
10. [Glossary](#10-glossary)

---

## 1. How governance works here (the 60-second version)

The platform lets employees request data and infrastructure on their own, while keeping everything within guardrails. Governance happens in three moments, and you don't have to babysit any of them:

1. **Before** a request is made — an **AI assistant** coaches the user toward the least-privileged, lowest-cost option and stops obviously disallowed requests.
2. **During** a request — an **approval workflow** routes anything risky to a human (you or a data owner) for explicit sign-off. Low-risk requests can auto-approve.
3. **After** things exist — a background watchdog called the **Enforcement Sentinel** continuously scans the environment and flags anything that drifts out of policy.

Two principles worth internalizing:

- **Least privilege by default.** Access starts narrow and widens only with justification and approval.
- **Nothing destructive happens automatically.** The Sentinel will *certify*, *un-certify*, and *notify* on its own, but it will **never delete or shut something down without a human clicking "Review & Act."** More on this in [Platform Governance](#5-platform-governance).

---

## 2. Roles: who can do what

Roles are assigned per user (Platform Admins manage this — see [Settings](#7-settings-you-can-change-yourself)).

| Role | What they can do |
| :--- | :--- |
| **Platform Admin** | Everything. Full access to all governance tools **and** system settings, target workspaces, and user role assignment. |
| **Governance Admin** | Run governance day-to-day: data certification, the Sentinel, allowlist exceptions, tags, data products, workflows, and the context library. Cannot change system settings. |
| **Security Admin** | Security-focused access controls and audits. |
| **Finance Admin** | Cost, budget, and billing views. |
| **User** | Standard self-service: make requests, browse the catalog, take training. |

Most of this guide applies to **Governance Admin** and **Platform Admin**. Where something is Platform-Admin-only, it's called out.

---

## 3. Where to find everything

Governance tools live in the left sidebar under two groups:

**Watch Tower** — day-to-day governance (Governance Admin + Platform Admin):

| Menu item | What it's for |
| :--- | :--- |
| **Data Certification (ODCS)** | Review and track which datasets are certified. |
| **Data Products (ODPS)** | The catalog of logical data products and their contracts. |
| **Allowlist** | Approved exceptions to policy (things the Sentinel should leave alone). |
| **Sentinel** | Run scans, read violation reports, and take action on findings. |
| **Tag Management** | Manage the governance tags applied to data assets. |

**Control Tower** — configuration and building (mostly Platform Admin):

| Menu item | What it's for |
| :--- | :--- |
| **Context Catalog** | The "house rules" and reference material the AI assistant reads. |
| **Admin** | System settings, branding, target workspaces, users & roles. *(Platform Admin only.)* |
| **Workflow Studio** | Build and edit approval workflows (no code). |
| **Training Studio** | Author in-app training tracks, courses, and media. |
| **Tool Registry** | The catalog of actions workflows and the assistant are allowed to take. |

Approvals themselves appear under **Requests & Approvals → Pending Approvals**.

---

## 4. Data Governance

This is about **certifying data and keeping its metadata trustworthy**. The goal: every production dataset that matters is documented, meets quality bars, and carries the right classification tags — with as little manual typing as possible.

### 4.1 Data Certification — what it is and your role

**Certification** is a formal stamp (`system.certification_status = certified`, applied directly in Unity Catalog) that says a dataset meets enterprise standards for **quality, documentation, access control, and classification**.

The heavy lifting is automated. Here is the flow and where you fit in:

1. **Datasets are grouped into Data Products.** A background job looks for tables that share a `dataset` tag and groups them into one logical product. It also auto-fills missing table/column descriptions in Unity Catalog so documentation gaps close on their own.
2. **A draft data contract is written for you.** The system generates an **ODCS** contract (Open Data Contract Standard — a structured description of the dataset: its fields, owners, quality expectations, and classification). It preserves any manual edits from previous versions.
3. **The Sentinel checks the contract on its next run.** It pulls the latest metadata and the dataset's recent **data-quality history**, and checks everything against the certification checklist:
   - **Contract completeness:** the contract names at least one table or view. Every other check below is evaluated *per table*, so a contract that lists nothing has nothing to check — it fails outright rather than passing by default.
   - **Data quality:** no failed quality rules within the dataset's reliability window.
   - **Metadata completeness:** catalog, schema, and *all* column descriptions exist.
   - **Access control:** role-based access is defined.
   - **Tagging & classification:** required tags present (Owner group, Approver group, Domain, SLO/SLA) and sensitive data (e.g. PII) is classified.
4. **Pass → the dataset is certified automatically.** The certified tag is applied to every table in the contract. **Fail → it is not certified** (and if it *was* certified and later broke a rule, it is automatically un-certified). Either way, the outcome is logged.

**Your role as a Governance Admin:**

- In **production**, this is fully automatic once a dataset is tagged correctly — you mainly **monitor** the Data Certification page and follow up with data owners on datasets stuck failing.
- In **dev/test/stage**, a human review step may be required before certification. That review is where you confirm the dataset is genuinely ready.
- When a dataset won't certify, open it on the **Data Certification** page to see *which* checklist item failed (missing descriptions, a failing quality rule, a missing tag) and route that back to the owning team to fix. You don't fix the data — you make the gap visible and owned.

### 4.2 Data Products (ODPS)

The **Data Products** page is the catalog of those logical products and their contracts. Use it to see what products exist, who owns them, and the state of their contracts. Think of it as the inventory that certification operates on.

### 4.3 Tag Management

Tags are how data gets governed at scale — classification (PII and similar), ownership, domain, and the certification status itself all ride on tags. The **Tag Management** page is where you review and manage the governance tags applied across assets. Correct tags are a *precondition* for certification, so this page and the Data Certification page work hand in hand.

### 4.4 A note on "reliability window"

Each dataset can carry a `reliability_window` tag that says how far back to look when judging data quality. If it's missing, the platform still evaluates quality using a default lookback (and reports the missing tag as its own small deficiency), so you find out about quality problems in the *same* review rather than a later one.

---

## 5. Platform Governance

This is about **keeping the live environment within policy** — compute, jobs, apps, dashboards, warehouses, service principals, and so on. The tool here is the **Enforcement Sentinel**.

### 5.1 What the Sentinel does (and the one safety rule)

The Sentinel is a watchdog. On a schedule (and whenever you run it manually) it scans your workspaces, compares every resource it finds against the policies, and produces **one consolidated report** plus **one governance email**.

**The safety rule — read this once and relax:** the Sentinel **never destroys anything on its own.**

- **Safe, reversible outcomes run automatically:** certify, un-certify, and *warn* (notify the owner).
- **Destructive intent is downgraded to a warning.** If a policy says a resource *should* be killed, the Sentinel does **not** kill it — it records the true intent, notifies, and waits. The only way something gets deleted, paused, or shut down is a human opening the finding and clicking **Review & Act**.

So a scan is safe to run any time. The worst it does unattended is send emails and apply/remove a certification tag.

### 5.2 Running a scan and reading the report

Go to **Watch Tower → Sentinel**.

- **Run a scan.** Use the run button. By default it scans **all configured target workspaces**. Under Advanced options you can scope a manual run to a single workspace (pick from the dropdown) or the home workspace.
- **Run history table.** Each row is one scan, showing when it ran, which workspace(s) it covered, how many findings, and an **Issues** column (see §5.5).
- **Open a run** to see every violation. Key columns:
  - **Workspace** — which workspace the finding came from (a single scan covers many).
  - **Policy** and **Severity** — what rule was broken and how serious.
  - **Owner** — who is likely responsible (a person's email or a service principal).
  - **Action** — what the policy *intended* vs. what was actually done.

### 5.3 Responding to a violation

For each finding you generally do one of three things:

1. **Leave it** — if it's expected and will resolve itself, or you'll chase the owner offline.
2. **Allowlist it** — if it's a legitimate, approved exception (see §5.4).
3. **Review & Act** — if the resource genuinely needs to be paused, archived, or deleted. This is the only path that performs a destructive action, and it runs against the correct workspace automatically.

The **daily digest** email summarizes everything for the governance group so nobody has to watch the UI. High-severity findings get full detail; medium/low are aggregated so a big environment (hundreds of findings) stays readable.

### 5.4 The Allowlist — approved exceptions

The **Allowlist** is how you tell the Sentinel "this is fine, leave it alone." Without an entry, a policy-violating resource keeps getting flagged (and stays eligible for a human kill).

To add an exception (**Watch Tower → Allowlist**), you provide:

- **Resource** — the resource ID/path and its type (app, notebook, dashboard, job, etc.).
- **Workspace** — pick from the dropdown of configured workspaces (exceptions are workspace-specific, so the same resource ID in two workspaces is handled independently).
- **Justification** — *why* this exception exists. Required, and it's your audit trail.
- **Expiry** *(optional but recommended)* — the date the exception auto-revokes, so exceptions don't live forever by accident.

Entries have a **status** (pending / approved / rejected). Only an **approved** exception actually suppresses a finding.

### 5.5 When a workspace scan fails ("0 findings — not confirmed clean")

A scan that returns **zero findings is only good news if the scan actually succeeded.** If the Sentinel couldn't authenticate to or reach a workspace, "0" means "couldn't look," not "all clean." The report makes this explicit:

- The **Issues** column flags runs where a workspace failed.
- Opening the run shows a **"Scan issues detected"** panel that names, per workspace: whether the network was reachable, which credentials were used, and the **precise reason** — including the exact authentication error (e.g. `invalid_client — Client authentication failed`).

This is a **configuration** problem, not a data problem, and it's almost always one of these:

| What you see | What it usually means | Who fixes it |
| :--- | :--- | :--- |
| `invalid_client` / authentication error | The workspace's service-principal credentials are wrong, expired, or belong to a different account. | Platform Admin (update the secret / service principal). |
| "not entitled" / authorization error | The credentials are valid but the service principal isn't allowed on that workspace. | Platform Admin (grant the SP access to the workspace). |
| Network / timeout | The app can't reach that workspace at all (networking). | Platform Admin / infrastructure. |

As a Governance Admin, your job is to **notice the Issues flag and escalate** — don't treat a failed workspace as compliant. The fix lives in **Admin → Settings → Target Workspaces** and in Databricks itself; see the runbook in §9.

### 5.6 Target workspaces (Platform Admin)

The Sentinel scans whatever is listed in **Admin → Settings → Target Workspaces**. Each entry names a workspace and the service principal it uses. If the list is **empty**, the Sentinel scans only the workspace the app itself runs in, using the app's own identity. Because Unity Catalog data is shared across a metastore (not per-workspace), **data certification runs once** against a single designated workspace, configurable there too.

---

## 6. Workflow Governance

This is about the **approval workflows** that requests flow through — the deterministic rules that decide what auto-approves and what needs a human.

### 6.1 Approving requests

Day-to-day, this is just **Requests & Approvals → Pending Approvals**. When a request needs sign-off, it appears here with the user's justification and the specifics of what they're asking for. You approve or reject with a reason. Low-risk requests never reach you — they auto-approve by rule — so what lands in your queue is the stuff that genuinely needs judgment (cross-environment access, production provisioning, broad privileges).

Every request has a **timeline** and a **workflow view** showing exactly which gates it passed and who approved what — your audit trail, reconstructed from immutable records.

### 6.2 Building and changing workflows (no code)

Workflows are **configuration, not code** — you can author and change them without a deployment, either in the visual **Workflow Studio** (*Control Tower → Workflow Studio*) or by asking the assistant to build one with you. A workflow is just an ordered list of **gates** (approvals) and **steps** (governed actions); the order *is* the rule.

The full playbook — the safe authoring loop, validation/dry-run, publishing, versioning/rollback, and promoting from dev to production — lives in the **[Platform Administration Guide](./PLATFORM_ADMINISTRATION.md)**. Start there before editing production workflows.

### 6.3 The Context Library (house rules)

The **Context Catalog** (*Control Tower → Context Catalog*) is where you keep the "house rules" and reference material the AI assistant reads when coaching users. Keeping this current is a governance lever: better context means the assistant steers people to the right, compliant choices before a request ever reaches your approval queue.

---

## 7. Settings you can change yourself

The platform is designed so you rarely need an engineer. **Admin → Settings** (Platform Admin) groups the changeable settings; edits take effect immediately, no redeploy. The most governance-relevant groups:

| Group | What you can change | Why it matters |
| :--- | :--- | :--- |
| **Notifications & Governance** | Who receives Sentinel alerts and the daily digest (comma-separated emails); the digest **hour** and **timezone**; the app URL used in email "Review" buttons. | This is how you route governance signal to the right people at the right time. |
| **Target Workspaces** | Which workspaces the Sentinel scans, their service-principal key names, and which workspace runs data certification. | Controls the scope of *all* platform governance. |
| **System Banner** | A site-wide banner (info/warning/alert/success) with your own message. | Announce maintenance, freezes, or policy changes to everyone. |
| **Branding & Appearance** | Names, logo, colors. | Cosmetic. |
| **Agent** | Assistant limits and whether tool-policy enforcement is on. | Tunes how strict the pre-request coaching layer is. |
| **Catalogs & Content** | The landing-page quick actions, community links, and embedded apps. | Curate what users see and do. |

Some settings are **read-only** in the UI (environment, database, email provider, cron schedules, Git settings). Those are set at deploy time and shown for visibility only — changing them is an engineering task.

> **Important:** For target workspaces, the UI holds the *names* of credential keys, never the secret values. Creating the secret, storing the credential, and granting access all happen in Databricks and cannot be self-served here. That separation is intentional (secrets never live in the app).

---

## 8. Policy reference

Policies are the rules the Sentinel and the assistant enforce. This section is a **reference** — you don't edit these here (changing a rule's logic is an engineering change to a policy file), but you should know what's enforced and how seriously.

### Severity scale

- **Critical** — Enforced via platform configuration/automation; exceptions require senior security approval and a time-bound exception record.
- **High** — Enforced via policy/automation where possible; exceptions require documented risk acceptance.
- **Medium** — Recommended default; deviations allowed with team-level approval and compensating controls.
- **Low** — Hygiene/optimization; adopt as capacity allows.

### What "Enforcement Point" means

Each policy is enforced somewhere. In plain terms:

- **Platform Config** — baked into how the platform is set up; users can't opt out.
- **Automation** — a scheduled job handles it.
- **Workflow** — enforced by an approval workflow at request time.
- **OPA Sentinel** — checked by the Enforcement Sentinel on every scan.
- **Agent/Process** — guided by the AI assistant or an operating procedure.
- **Architecture** — a structural guarantee of how things are laid out.

### Policy map

| Category | Policy Rule | Severity | Enforcement Point | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Identity & Access** | Enterprise SSO & MFA | Critical | Platform Config | All human access flows through enterprise SSO; local users disabled. |
| | SCIM/AIM Provisioning | High | Automation | Users/groups provisioned centrally. |
| | Separate Admin Accounts | High | Agent/Process | Admins use separate identities for day-to-day work. |
| | Group-based Access | High | OPA Sentinel | Data access granted to groups, not individuals. |
| | PAT Restrictions | Critical | Platform Config | Personal access tokens allowed only in non-prod (≤ 30 days); disabled in enterprise prod. |
| | Secret Management | Critical | OPA Sentinel | Credentials must be stored in approved secret scopes/managers. |
| **Workspaces & Environments** | Automated Creation | Critical | Automation | Created via account-level automation; manual UI creation disabled. |
| | Workspace Tiering | High | Workflow | Tagged as dev/test/prod and enterprise/domain/ad-hoc. |
| | Enterprise Isolation | High | Agent/Process | Enterprise workspaces host only shared platform services. |
| | Domain Workspaces | High | Architecture | Default home for production data pipelines bound to specific catalogs. |
| | Ad-Hoc/Sandbox Lifecycles | Medium | Automation | Auto-expire after 30–90 days of inactivity; small compute policies. |
| | Network Controls | Critical | Platform Config | Secure network baseline (private connectivity); public access disabled for prod. |
| **Compute, Jobs & Automation** | Cluster Policies | Critical | Platform Config | All compute created via cluster/compute policies. "No policy" disabled. |
| | Interactive Clusters in Prod | High | OPA Sentinel | Shared interactive clusters disallowed in prod. |
| | Prod Job Ownership | High | OPA Sentinel | Owned by service principals; use version-controlled code. |
| | Auto-stopping Compute | High | Platform Config | Max idle timeouts enforced. |
| **Service Principals & Tokens** | SP Ownership | High | Workflow | Clear business owner in central registry. |
| | SP Scope | Critical | OPA Sentinel | Least privilege; broad "*" grants prohibited in prod. |
| | SP Lifecycle | High | Automation | Disabled/deleted after 90 days of inactivity. |
| | Human-owned Tokens | Critical | Agent/Process | Never used for production workloads. |
| **Data & AI Governance** | Unity Catalog Centralization | Critical | Architecture | Unity Catalog is the authoritative control plane. |
| | Catalog Segmentation | High | OPA Sentinel | Segmented by environment and domain; cross-environment access prohibited. |
| | Governed Tags / ABAC | Critical | OPA Sentinel | Sensitive data classified and restricted via attribute-based access control. |
| | DBFS / Local Storage | High | OPA Sentinel | Prod data must not be stored in DBFS/local volumes. |
| | Data Sharing | Critical | OPA Sentinel | Uses Delta Sharing or clean rooms; direct raw bucket access blocked. |
| **Dashboards, SQL & BI** | Prod SQL Warehouses | High | Platform Config | Must use compute policies (max size, timeouts, tagging). |
| | Embedded Credentials | Critical | OPA Sentinel | Dashboards with embedded credentials cannot be shared with ALL_USERS. |
| | External BI Tools | High | Architecture | Must use service principals/managed identities. |
| **Apps & Genie Spaces** | Apps in Enterprise Prod | High | OPA Sentinel | Must be on the platform allowlist. |
| | Apps in Domain Prod | High | Agent/Process | Require CI/CD deployment and review. |
| | App Idle Cleanup | Medium | Automation | Stopped after 30 days inactivity; archived after 60–90 days. |
| | Genie Spaces Prod Data | High | Architecture | Linked to domain workspaces, owned by groups. |
| | Conversational Data Export | Critical | Platform Config | Direct export of sensitive data blocked. |
| **Data Certification** | Contract Completeness | High | OPA Sentinel | The contract must declare at least one table or view; an empty contract cannot certify. |
| | Data Quality | High | OPA Sentinel | No failed quality rules within the reliability window. |
| | Metadata Completeness | High | OPA Sentinel | Catalog, schema, and all column descriptions must exist. |
| | Access Control | High | OPA Sentinel | Role-based access always required; attribute-based access defined where needed. |
| | Tagging & Classification | High | OPA Sentinel | Mandatory tags (Owner group, Approver group, Domain, SLO/SLA) and data classification (e.g. PII) applied. |

> **Changing a policy** is an engineering change (the rules live in policy files the system reads on each run). If a rule needs to change, file that with the engineering team and test it in a lower environment first. A future release aims to make policy editing self-service in the UI.

#### Temporarily disabled Sentinel rules

These rules are commented out in the policy files rather than deleted, so the
Sentinel does **not** currently report them. They each flag most of the estate
today, which buried the findings that need attention. Re-enable them as the
underlying rollouts land.

| Rule | Policy file | Why it's off |
| :--- | :--- | :--- |
| Compute created via a cluster/compute policy | `compute.rego` | Fires on nearly every cluster until compute policies are rolled out. |
| `cost-center` tag on jobs/clusters/warehouses/apps/Genie spaces | `resource_tags.rego` | Fires once per untagged resource across the whole estate. |
| `owner` tag on jobs/clusters/warehouses/apps/Genie spaces | `resource_tags.rego` | Same as above. |

---

## 9. Runbooks: "How do I…?"

**…certify a dataset that won't certify?**
Open **Watch Tower → Data Certification**, find the dataset, and read the failed checklist item(s): missing descriptions, a failing data-quality rule, or a missing required tag. Route the specific gap to the owning team. Once fixed and tagged, the next Sentinel run certifies it automatically (in prod). In dev/test/stage, complete the human review step.

**…figure out why a dataset's contract is empty?**
An empty contract (subtitle "Multiple datasets", a `schema: []` block, "No datasets were provided") means contract generation couldn't see the tables. Metadata is read as the **governance service principal**, and tables it can't see are left out of the contract. Search the backend log for `no metadata available` — the drafting step reports how many tables it omitted and names them. The fix is a Unity Catalog grant, not an app change:

```sql
GRANT BROWSE ON CATALOG <catalog> TO `<service-principal>`;
```

`BROWSE` is all that's required. It is a metadata-only privilege — it lets the scanner see tables, columns, and tags, but **not read any data** — and it does not require `USE CATALOG` or `USE SCHEMA`. If you are ever asked for `SELECT` here, that's a bug: the platform reads `information_schema`, never the tables themselves. Such a contract also fails the **Contract completeness** check rather than certifying, so it shows as uncertified until the grant is in place.

**…stop the Sentinel from flagging something that's actually fine?**
Add an **approved Allowlist** entry (**Watch Tower → Allowlist**) with the resource, its workspace, a justification, and ideally an expiry. Only approved entries suppress findings.

**…actually delete/pause a resource the Sentinel flagged?**
Open the run in **Watch Tower → Sentinel**, find the violation, and use **Review & Act**. This is the only path that performs a destructive action, and it targets the right workspace automatically.

**…change who gets the governance emails, or when?**
**Admin → Settings → Notifications & Governance**: edit the recipient list (comma-separated), the digest hour, and the timezone. Takes effect immediately.

**…figure out why a workspace shows "0 findings — not confirmed clean"?**
Open the run and read the **Scan issues** panel. It states the network reachability, the credentials used, and the exact reason (e.g. `invalid_client`). Then, as Platform Admin: confirm the target workspace's service principal in **Admin → Settings → Target Workspaces**, and in Databricks verify the service principal exists in that workspace, its OAuth secret is current, and it's authorized there. Re-run the scan to confirm the panel clears. (If you're comfortable, you can test the exact credentials in a Databricks notebook before re-running — ask engineering for the snippet.)

**…add a new workspace to be governed?** *(Platform Admin)*
In Databricks: create/choose the service principal, store its credential in the shared secret scope, and grant it access to the workspace. Then in **Admin → Settings → Target Workspaces**: add a row with the workspace name, host URL, environment, and the *key names* of its credentials. Run a manual Sentinel scan scoped to just that workspace to confirm it authenticates before relying on it.

**…announce a change freeze or maintenance to everyone?**
**Admin → Settings → System Banner**: turn it on, pick a style, and write the message.

**…change what needs approval?**
Edit the relevant workflow in **Control Tower → Workflow Studio** (follow the [Platform Administration Guide](./PLATFORM_ADMINISTRATION.md)). Test in a lower environment before publishing to prod.

---

## 10. Glossary

- **Unity Catalog** — Databricks' central system for governing data (catalogs, schemas, tables, tags, permissions). Its data is shared across a *metastore*, not tied to one workspace.
- **Metastore** — the account-wide home for Unity Catalog data. This is why data certification runs once per metastore, not once per workspace.
- **Catalog / schema / table** — the folder hierarchy for data: a catalog contains schemas, a schema contains tables.
- **Workspace** — a single Databricks environment where people and jobs run. There can be many.
- **Service principal (SP)** — a non-human "robot" identity the platform uses to act in a workspace. Each target workspace needs one that's authorized there.
- **Secret scope** — a secure vault in Databricks where credentials are stored. The app references the *names* of secrets, never their values.
- **ODCS (Open Data Contract Standard)** — a structured, standardized description of a dataset (fields, owners, quality expectations, classification). What certification checks against.
- **ODPS / Data Product** — a group of related tables (sharing a `dataset` tag) treated as one governed product.
- **OPA / Rego** — the policy engine (OPA) and the language its rules are written in (Rego). You don't edit these directly; the Sentinel and the assistant consult them.
- **Enforcement Sentinel** — the background watchdog that scans workspaces and reports policy violations. Never destroys anything without a human action.
- **Allowlist** — the list of approved exceptions that tells the Sentinel to leave specific resources alone.
- **Reliability window** — how far back to look when judging a dataset's data quality.
- **Gate / step (in a workflow)** — a gate is an approval; a step is a governed action. Their order defines the rule.
- **PAT (Personal Access Token)** — a personal credential for a human; restricted to non-prod and short lifetimes.
