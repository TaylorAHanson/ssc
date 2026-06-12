# ATLAS: Agentic Control Tower for Lakehouse Automation & Self-Service Experience

ATLAS turns the everyday work of "I need access," "I need a workspace," "is this data safe to use," and "please set this up for me" into a fast, self-serve conversation — while keeping every action inside your organization's rules.

It runs as an application inside your Databricks workspace and gives your people an intelligent assistant that understands what they're trying to do, gathers the right information, routes anything risky to the right approver, and then carries out the work automatically. Requests that used to take days of back-and-forth tickets become minutes of guided self-service.

> **Databricks Labs project.** ATLAS is provided as-is, as an open, customizable starting point for organizations that want to stand up governed self-service on Databricks. It is not a formally supported Databricks product; help is best-effort through the project's GitHub issues. You are encouraged to adapt its branding, workflows, and policies to your environment.

---

## The problem it solves

As Databricks adoption grows, platform and governance teams get squeezed from both sides:

- **Requests pile up.** Access grants, new workspaces, repositories, data products, and cost approvals all funnel through a small number of admins, creating long queues and frustrated users.
- **Tickets lose context.** A request arrives with too little information, so admins spend their time chasing details and second-guessing intent instead of doing higher-value work.
- **Governance drifts.** When the official path is slow, people find workarounds. Standards for least-privilege access, tagging, cost controls, and data quality erode quietly over time.
- **Knowledge is trapped.** The rules for "how we do things here" live in people's heads and scattered wikis, so outcomes depend on who happens to handle the request.

ATLAS fills the gap between *"give everyone self-service"* (fast but risky) and *"make everyone file a ticket"* (safe but slow). It delivers self-service that is **guided, governed, and auditable by default.**

---

## Business value

- **Faster delivery.** Common requests are resolved in minutes through a conversation, not days through a queue. Low-risk actions can complete automatically.
- **Fewer bottlenecks.** Admins stop being a human router. They set the rules once, and the platform handles the routine while escalating only what truly needs a human decision.
- **Governance that holds.** Every request follows the same approved path. Risky actions pause for the right approver, required tags and standards are enforced, and nothing bypasses policy — even when the assistant is helping.
- **Lower, more predictable cost.** Cost-aware guidance and automated cleanup of unused resources curb sprawl before it shows up on the bill.
- **A complete audit trail.** Who asked for what, why, who approved it, and what was done — all captured automatically for compliance and reporting.
- **Owned by your team, not your vendors.** Administrators define and change the self-service workflows themselves — no code, no redeploy — and promote vetted changes safely from test to production.
- **Consistent, institutional knowledge.** The platform's guidance and house rules are written down, editable, and applied the same way every time.

---

## Who it's for

- **Platform & governance teams** who want to scale self-service without losing control, and who want to own and tune the experience day to day.
- **Data, analytics, and engineering teams** who just want to get unblocked quickly and correctly.
- **Leadership** who want adoption to grow while risk, cost, and compliance stay in check.

---

## How it works, at a glance

1. **Ask.** A user describes what they need in plain language.
2. **Guide.** The assistant clarifies intent, suggests the least-risky option, and gathers exactly the information required.
3. **Govern.** The request follows a pre-defined workflow: safe steps proceed automatically; sensitive ones pause for the right approver.
4. **Act.** Once approved, the work is carried out automatically, with standards and tags applied.
5. **Audit.** Every step is recorded for transparency and reporting.

Behind the scenes, continuous checks keep the environment tidy and compliant — flagging drift, duplicate data, and unused resources.

---

## Documentation & Guides

Pick the guide that matches what you're trying to do:

* 📘 **[Platform Setup Guide](./docs/PLATFORM_SETUP.md)** — for administrators deploying ATLAS into a Databricks workspace.
* 📗 **[Platform Administration Guide](./docs/PLATFORM_ADMINISTRATION.md)** — for the people who own and operate ATLAS day to day: creating and changing the no-code self-service workflows, testing them, and promoting them across environments.
* 📙 **[Governance & Policies](./docs/GOVERNANCE.md)** — how the guardrails, approvals, and automated compliance checks work, and how to tune them.
* 📕 **[Developer Quick Start](./docs/DEVELOPER_QUICK_START.md)** — for developers who want to run ATLAS locally and contribute.
* 📐 **[Architecture Deep Dive](./docs/ARCHITECTURE.md)** — a technical overview of how the system is built.

---

## Support

ATLAS is a Databricks Labs project and is provided as-is, without formal Databricks support. Please use the project's GitHub issues for questions, bug reports, and contributions. Because the platform is designed to be customized, your branding, workflows, and policies can all be adapted to fit your organization.
