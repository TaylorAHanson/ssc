# EDAS Hub Governance Framework

## Overview
The Enterprise Data and Analytics Services (EDAS) Hub is designed to empower Qualcomm employees with self-service access to data and infrastructure while maintaining a rigorous security and governance posture. This framework is built upon the **Databricks Well-Architected Framework** and the **Databricks AI Governance Framework (DAGF)**, balancing agility with the principles of least privilege, cost optimization, and data integrity.

---

## 1. Intelligent Self-Service & Agentic Governance
We operate on the principle of **Least Privilege by Default**, augmented by an **AI-Driven Agentic Layer**. The agent acts as the first line of defense and a "Governance Coach," ensuring that users are guided toward the most efficient and compliant path.

### The Role of the AI Agent
Our intelligent agent provides continuous governance and FinOps support by:
*   **Intent Investigation**: Probing user requests (e.g., "Why do you need Admin access?") to downgrade them to the appropriate least-privilege role (e.g., "Contributor" or "Data Reader").
*   **FinOps Guardrails**: Identifying high-cost requests (like large clusters, PROD provisioning, or expensive SQL Warehouses) and suggesting more cost-effective alternatives (e.g., serverless compute, autoscaling).
*   **Justification Refinement**: Ensuring every request includes a "Rock Solid," clear, and logical business reason that a manager can actually approve, preventing "rubber-stamping" and ensuring auditability.
*   **Policy Guarding**: Real-time checking of requests against established Governance and Security policies before they even reach a human reviewer.

### Just-in-Time (JIT) & Granular Entitlements
Instead of broad, persistent administrative roles, the EDAS Hub facilitates granular access:
*   **3-Level Namespace**: All data access is governed via Unity Catalog using the `catalog.schema.table` structure.
*   **Attribute-Based Access Control (ABAC)**: Where possible, access is determined by user attributes (department, project) rather than manual assignment.
*   **Time-Bound Access**: Temporary access is preferred for troubleshooting or one-time analysis, with automated revocation.

---

## 2. Approval Guardrails & Risk-Based Workflows
To maintain control without becoming a bottleneck, we categorize requests based on risk levels defined in our Center of Excellence (CoE) policies.

### Low-Risk (Automated)
*   Access to public or internal-only non-sensitive datasets.
*   Standard developer-tier workspace access with restricted **Cluster Policies**.
*   Requests that match the user's existing cost-center and project scope.

### High-Risk (Human-in-the-Loop)
*   **Production Environment Access**: Any request involving `PROD` environments requires explicit manager and platform admin approval.
*   **Sensitive Data**: Access to PII, HR, or Finance-restricted catalogs requires data owner sign-off and may trigger additional privacy reviews.
*   **Administrative Roles**: Requests for "Workspace Admin" or "Account Admin" are heavily scrutinized and require specific expiration dates.

---

## 3. Preventing Data Proliferation & Agentic Discovery
A core goal of EDAS Hub is to prevent "Data Swamps" where multiple copies of the same dataset exist. We follow the Databricks principle of **Unifying Data and AI Governance**.

### Agentic Data Overlap Prevention
The agent intervenes during the request process to maintain data integrity:
*   **Intelligent Lookup**: When a user describes their data needs, the agent performs a lookup against the **Unity Catalog** and **Marketplace** to suggest existing sources, preventing duplicate ingestion.
*   **Asset Promotion**: The agent proactively suggests **Reusable Assets** and **Templates** (e.g., ETL pipelines, Power BI templates, or Feature Store entries) that are already available.
*   **Lineage Awareness**: The agent uses data lineage to explain the "provenance" of data, helping users understand if they are requesting raw, silver, or gold-certified data.
*   **Proactive Join Suggestions**: If a user requests access to two disparate datasets, the agent may suggest existing "Certified Joins" or common keys used by other teams, preventing incorrect data merging.

### Discoverability Over Duplication
*   **Unity Catalog**: The single source of truth for all metadata. Users are encouraged to search UC before requesting new storage.
*   **Delta Sharing**: Secure, live access to data in-place across workspaces and organizations, eliminating the need for ETL-based copying.
*   **Certified Data**: The "Marketplace Certification" process identifies authoritative datasets. Users must prioritize "Gold"/"Platinum" certified data for production reporting.

---

## 4. Cost Management & FinOps Discipline
Governance includes financial responsibility. We treat cost management as an operational requirement, supported by agentic oversight.

### FinOps Standards
*   **Governed Tagging**: All resources (workspaces, clusters, warehouses) must be tagged with mandatory keys: `CostCenter`, `Project`, `Environment`, and `Owner`. The agent enforces these during the request flow.
*   **Compute Policies**: Standardized cluster policies are applied to prevent over-provisioning (e.g., enforcing auto-termination, limiting instance types).

### Agentic FinOps Oversight
*   **Cost Anomaly Alerts**: The agent monitors daily usage trends and proactively notifies owners if a specific request or workspace shows a sudden, unexplained cost spike.
*   **Optimization Recommendations**: Periodically, the agent reviews active clusters and suggests moving from "Personal" compute to "Shared" or "SQL Warehouses" for more efficient resource utilization.
*   **Zombie Resource Identification**: The agent flags resources that are provisioned but show zero queries or jobs for 30+ days, suggesting decommissioning.

---

## 5. AI Governance & Responsible AI
As we scale AI capabilities, the EDAS Hub follows the **Databricks AI Governance Framework (DAGF)** with agent-guided oversight:
*   **Model Governance**: All ML models must be registered in the Unity Catalog Model Registry with clear ownership and lineage.
*   **Feature Store Reuse**: The agent proactively checks the Feature Store during "Create Model" inquiries to prevent redundant feature engineering.
*   **Monitoring**: Promoting the use of Lakehouse Monitoring for data and model drift. The agent can summarize drift reports and suggest retraining triggers.

---

## 6. Agent-Augmented Continuous Audit & Compliance
Governance is an ongoing cycle of monitoring and refinement, moving from manual checks to proactive agentic oversight.

### Proactive Compliance Monitoring
*   **Entitlement "Nudges"**: The agent periodically reaches out to users with high-privilege access (e.g. `OWNER` or `ADMIN`) to verify if that access is still required for their current project.
*   **Justification Auditing**: The agent cross-references active access with original business justifications. If a project is marked as "Completed" in Jira/System, the agent flags the associated access for review.
*   **Security Posture Improvement**: The agent identifies users with unrotated API keys or over-provisioned permissions and guides them through the remediation process.

### Operational Logging
*   **Audit Logging**: Every action in the EDAS Hub (request, approval, provisioning) is logged and stored in a system table for compliance reviews.
*   **Orphaned Permission Cleanup**: Automated monthly reviews to identify and remove access for users who have changed roles or left the company.
