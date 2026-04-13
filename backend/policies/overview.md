# Enterprise Databricks Platform Policy Map

## Vision
This opinionated policy set assumes 100+ workspaces and broad self‑service. Controls are designed as guardrails: they constrain how users build on Databricks, not whether they can build at all. They build on Databricks Security Best Practices, well‑architected guidance, and Unity Catalog governance patterns.

## Governance Architecture
As detailed in [GOVERNANCE.md](../docs/GOVERNANCE.md), our platform enforces policies across three layers:
1. **The AI Agent (Proactive Guidance & Chokepoint):** Intercepts intent, downgrades risk, and performs dry-runs against OPA to guide users before infrastructure changes.
2. **State Machine Conditions (Proactive Enforcement):** Evaluates deterministic guardrails, triggers human-in-the-loop approvals for high-risk operations, and enforces strict tagging.
3. **Reactive Enforcers (Continuous Audit):** Background Sentinels continuously evaluate resources against Open Policy Agent (OPA) `.rego` policies to flag, pause, or kill unauthorized assets.

## Severity Scale
- **Critical** – Must be enforced via platform configuration / automation; exceptions require senior security approval and time‑bound exception records.
- **High** – Should be enforced via policies & automation where possible; exceptions require documented risk acceptance.
- **Medium** – Recommended default; deviations allowed with team‑level approval and compensating controls.
- **Low** – Optimization / hygiene; adopt as capacity allows.

---

## Policy Categories

### 1. Identity & Access (`identity_and_access.rego`)
- **Enterprise SSO & MFA (Critical):** All human access flows through enterprise SSO; local users disabled.
- **SCIM/AIM Provisioning (High):** Users/groups provisioned centrally.
- **Separate Admin Accounts (High):** Admins use separate identities for day-to-day work.
- **Group-based Access (High):** Data access granted to groups, not individuals.
- **PAT Restrictions (Critical):** PATs allowed only in non-prod (≤ 30 days). Disabled in enterprise prod.
- **Secret Management (Critical):** Credentials must be stored in approved secret scopes/managers.

### 2. Workspaces & Environments (`workspaces_and_environments.rego`)
- **Automated Creation (Critical):** Created via account-level automation; manual UI creation disabled.
- **Workspace Tiering (High):** Tagged as dev/test/prod and enterprise/domain/ad-hoc.
- **Enterprise Isolation (High):** Enterprise workspaces host only shared platform services.
- **Domain Workspaces (High):** Default home for production data pipelines bound to specific catalogs.
- **Ad-Hoc/Sandbox Lifecycles (Medium/High):** Auto-expire after 30-90 days of inactivity. Small compute policies.
- **Network Controls (Critical):** Secure network baseline (private connectivity). Public access disabled for prod.

### 3. Compute, Jobs & Automation (`compute_and_jobs.rego`)
- **Cluster Policies (Critical):** All compute created via cluster/compute policies. "No policy" disabled.
- **Interactive Clusters in Prod (High):** Shared interactive clusters disallowed in prod.
- **Prod Job Ownership (High):** Owned by service principals, use version-controlled code.
- **Auto-stopping Compute (High):** Max idle timeouts enforced.

### 4. Service Principals & Tokens (`service_principals.rego`)
- **SP Ownership (High):** Clear business owner in central registry.
- **SP Scope (Critical):** Least privilege; broad "*" grants prohibited in prod.
- **SP Lifecycle (High):** Disabled/deleted after 90 days of inactivity.
- **Human-owned Tokens (Critical):** Never used for production workloads.

### 5. Data & AI Governance (`data_and_ai_governance.rego`)
- **Unity Catalog Centralization (Critical):** UC is the authoritative control plane.
- **Catalog Segmentation (High):** Segmented by environment and domain. Cross-environment access prohibited.
- **Governed Tags / ABAC (Critical):** Sensitive data classified and restricted via ABAC.
- **DBFS / Local Storage (High):** Prod data must not be stored in DBFS/local volumes.
- **Data Sharing (Critical):** Uses Delta Sharing or clean rooms; direct raw bucket access blocked.

### 6. Dashboards, SQL Warehouses & BI (`dashboards_and_sql.rego`)
- **Prod SQL Warehouses (High):** Must use compute policies (max size, timeouts, tagging).
- **Embedded Credentials (Critical):** Dashboards with embedded credentials cannot be shared with ALL_USERS.
- **External BI Tools (High):** Must use service principals/managed identities.

### 7. Apps, Genie Spaces & Conversational (`apps_and_genie.rego`)
- **Apps in Enterprise Prod (High):** Must be on platform allowlist.
- **Apps in Domain Prod (High):** Require CI/CD deployment and review.
- **App Idle Cleanup (Medium):** Stopped after 30 days inactivity; archived after 60-90 days.
- **Genie Spaces Prod Data (High):** Linked to domain workspaces, owned by groups.
- **Conversational Data Export (Critical):** Direct export of sensitive data blocked.

## How to Contribute
Adding or editing a policy is straightforward because we use Open Policy Agent (OPA) and the Rego policy language.

1. **Add/Edit Policy:** Open the relevant `.rego` file in `backend/policies/` (e.g. `compute_and_jobs.rego`). 
2. **Add Violation Logic:** Add a new `violation_reasons[msg]` block with your logic. Example:
   ```rego
   violation_reasons[msg] {
       input.resource.type == "job"
       input.resource.idle_days > 90
       msg := "Job has not been run in over 90 days."
   }
   ```
3. **Commit:** The Enforcement Sentinel and Agent Policy Tools will dynamically pick up any changes or new `.rego` files automatically. The `common.rego` library handles all the boilerplate for formatting the output and processing allowlist exceptions.

## Future Enhancements
- **Policy Editing UI:** Build a frontend interface to allow administrators to write, test, and deploy Rego policies directly from the Self-Service Center without modifying the source code.
- **External OPA Hosting:** Currently, policies are evaluated using a local OPA binary. In the future, this should be moved to a centralized, external OPA Server (e.g. a dedicated container or Databricks Model Serving endpoint) so that other systems and Databricks workspaces can query the exact same central policy definitions.
