# EDAS Hub Governance Framework

## Overview
The Enterprise Data and Analytics Services (EDAS) Hub is designed to empower Qualcomm employees with self-service access to data and infrastructure while maintaining a rigorous security and governance posture. This document outlines our approach to balancing speed and innovation with the principles of least privilege and data integrity.

---

## 1. Intelligent Self-Service & Agentic Governance
We operate on the principle of **Least Privilege by Default**, augmented by an **AI-Driven Agentic Layer**. The agent acts as the first line of defense, ensuring that users are guided toward the most efficient and compliant path.

### The Role of the AI Agent
Our intelligent agent provides continuous governance and FinOps support by:
*   **Intent Investigation**: Probing user requests (e.g., "Why do you need Admin access?") to downgrade them to the appropriate least-privilege role (e.g., "Contributor").
*   **FinOps Guardrails**: Identifying high-cost requests (like large clusters or PROD provisioning) and suggesting more cost-effective alternatives or requiring "Rock Solid" justifications.
*   **Justification Refinement**: Ensuring every request includes a clear, logical business reason that a manager can actually approve, preventing "rubber-stamping."

### Just-in-Time (JIT) Entitlements
Instead of broad, persistent administrative roles, the EDAS Hub facilitates granular access requests:
*   **Granular Scoping**: Access is requested at the Catalog, Schema, or Table level rather than the entire Workspace.
*   **Time-Bound Access**: Where applicable, access is granted for a specific duration and reviewed periodically.
*   **Identity-Centric**: All permissions are tied to individual corporate identities or managed service principals, ensuring a clear audit trail.

---

## 2. Approval Guardrails & Automated Workflows
To maintain control without becoming a bottleneck, we categorize requests based on risk:

### Low-Risk (Automated)
*   Access to public or internal-only non-sensitive datasets.
*   Standard developer-tier workspace access (with restricted permissions).

### High-Risk (Human-in-the-Loop)
*   **Production Environment Access**: Any request involving `PROD` environments requires explicit manager and platform admin approval.
*   **Sensitive Data**: Access to PII, HR, or Finance-restricted catalogs requires data owner sign-off.
*   **Administrative Roles**: Requests for "Workspace Admin" or "Account Admin" are heavily scrutinized and require a strong business justification and specific time-limits.

---

## 3. Preventing Data Proliferation & Agentic Discovery
A core goal of EDAS Hub is to prevent the "Wild West" of data where multiple copies of the same dataset exist in different locations. Our AI agent actively monitors for potential data overlap.

### Agentic Data Overlap Prevention
The agent intervenes during the request process to maintain data integrity:
*   **Intelligent Suggestions**: When a user describes a dataset they need, the agent "mocks" or performs a lookup against the **Unity Catalog** and **Marketplace** to suggest existing sources before a new one is created.
*   **Duplicate Detection**: If a user attempts to create a catalog or schema that mirrors an existing one (e.g., `sales_data_2`), the agent flags the overlap and redirects the user to request access to the original source.
*   **Asset Promotion**: The agent proactively suggests **Reusable Assets** and **Templates** (e.g., ETL pipelines, Power BI templates) that are already available, reducing the need for "from scratch" development.

### Discoverability Over Duplication
*   **Unity Catalog**: We leverage Unity Catalog as a single source of truth for metadata. Users are encouraged to search existing catalogs before requesting new ones.
*   **Delta Sharing**: Instead of copying data across workspaces, we use Delta Sharing to provide secure, live access to data in-place.
*   **Certification**: The "Marketplace Certification" process identifies authoritative datasets. Users should always prioritize using "Certified" data over creating local copies of raw data.

### Data Lifecycle Management
*   **Approval Validation**: During the request process, the AI agent and reviewers check if the requested data already exists in an accessible format.
*   **Audit & Cleanup**: Automated scripts identify redundant datasets or those with no active usage for decommissioning.

---

## 4. Balancing Innovation with Guardrails
Databricks is a powerful tool for innovation. Our governance model is designed to be a "guardrail, not a roadblock."

### The "Sandbox" Approach
*   **Innovation Workspaces**: We provide dedicated sandbox environments where users have higher local permissions to experiment with new libraries and data processing techniques.
*   **Promotion Path**: As projects mature from "Innovation" to "Production," the governance requirements scale accordingly, requiring more robust testing and tighter access controls.
*   **Reusable Assets**: We provide templates and best-practice examples (via the "Templates & Assets" section) to ensure innovation follows corporate security standards from day one.

---

## 5. Continuous Audit & Compliance
Governance is not a one-time event. The EDAS Hub periodically audits:
*   **Orphaned Permissions**: Users who have changed roles or left the company.
*   **Unused Resources**: Workspaces or clusters that are costing money without providing value (FinOps integration).
*   **Justification Validity**: Ensuring that "Rock Solid" justifications provided at the time of request still hold true.
