"""
Seed the Context Catalog with neutral, industry-agnostic placeholder content.

These are made-up starter docs to exercise the catalog and the agent retrieval
path; replace them with your organization's real content (e.g. from Confluence)
later.

Talks to the running API (stdlib only, no venv needed). Idempotent: domains are
matched by name and documents by (domain, title), so re-running won't duplicate.

Usage:
    python3 backend/scripts/seed_context_catalog.py
    API_BASE=http://localhost:8000/api/v1 DEV_ROLE="Platform Admin" python3 backend/scripts/seed_context_catalog.py
"""
import json
import os
import urllib.error
import urllib.request

API_BASE = os.environ.get("API_BASE", "http://localhost:8000/api/v1")
DEV_ROLE = os.environ.get("DEV_ROLE", "Platform Admin")

HEADERS = {
    "Content-Type": "application/json",
    # Dev-only role override so writes pass the Platform/Governance Admin gate.
    "X-Dev-Role-Override": DEV_ROLE,
}


def _request(method: str, path: str, body=None):
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {path} failed: HTTP {e.code} {detail}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Cannot reach API at {url}: {e}. Is the backend running?")


def list_domains():
    return _request("GET", "/context/domains") or []


def ensure_domain(existing_by_name, *, name, **fields):
    if name in existing_by_name:
        return existing_by_name[name]
    created = _request("POST", "/context/domains", {"name": name, **fields})
    existing_by_name[name] = created
    print(f"  + domain: {name}")
    return created


def ensure_document(domain, existing_titles, *, title, body_markdown, tags=None):
    if title in existing_titles:
        return
    _request(
        "POST",
        f"/context/domains/{domain['id']}/documents",
        {"title": title, "body_markdown": body_markdown, "tags": tags or [], "status": "published"},
    )
    existing_titles.add(title)
    print(f"      - doc: {title}")


def existing_doc_titles(domain_id):
    detail = _request("GET", f"/context/domains/{domain_id}")
    return {d["title"] for d in (detail.get("documents") or [])}


# --------------------------------------------------------------------------- data

DOMAINS = [
    {
        "name": "New to Databricks",
        "description": "Front-door guides for employees who have never used Databricks.",
        "domain_type": "system",
        "categories": ["onboarding", "getting-started"],
        "primary_owner": "data-enablement@example.com",
    },
    {
        "name": "Data Platform & Governance",
        "description": "How data access, classification, and Unity Catalog governance work internally.",
        "categories": ["governance", "access"],
        "primary_owner": "data-governance@example.com",
        "reviewers": ["uc-stewards@example.com"],
    },
    {
        "name": "Sales & GTM Operations",
        "description": "Go-to-market processes: deal desk, quoting, and sales operations.",
        "categories": ["sales", "ops"],
        "primary_owner": "gtm-ops@example.com",
    },
    {
        "name": "Engineering & Product",
        "description": "Product engineering data practices and project onboarding.",
        "categories": ["engineering", "product"],
        "primary_owner": "eng-data@example.com",
    },
    {
        "name": "Supply Chain & Operations",
        "description": "Operations, logistics, and manufacturing analytics data.",
        "categories": ["operations", "supply-chain"],
        "primary_owner": "ops-analytics@example.com",
    },
    {
        "name": "Finance & Reporting",
        "description": "Financial reporting, billing, and revenue data.",
        "categories": ["finance", "reporting"],
        "primary_owner": "finance-analytics@example.com",
    },
]

# Sub-domains: (parent name, child spec)
SUBDOMAINS = [
    ("Sales & GTM Operations", {
        "name": "Sales Engineering",
        "description": "Pre-sales technical support and solution scoping.",
        "categories": ["sales-engineering"],
    }),
]

# Documents keyed by domain name.
DOCUMENTS = {
    "New to Databricks": [
        {
            "title": "Getting Started: Your First Week on Databricks",
            "tags": ["onboarding"],
            "body_markdown": (
                "# Getting Started on Databricks\n\n"
                "Welcome! This guide is for employees who have never used Databricks.\n\n"
                "## 1. Get access\n"
                "Everyone starts in the **enterprise_analytics** workspace. If you can't log in, "
                "request workspace access through the Enterprise Data Hub agent — just say "
                "\"I need access to Databricks\".\n\n"
                "## 2. Key concepts in plain language\n"
                "- **Unity Catalog**: the company-wide catalog of governed data (think of it as a "
                "library card catalog for tables).\n"
                "- **Catalog > Schema > Table**: the three-level naming for data, e.g. "
                "`enterprise_prod.sales.orders`.\n"
                "- **Genie**: ask questions about data in plain English.\n\n"
                "## 3. What to do first\n"
                "1. Browse the catalog to see what data exists.\n"
                "2. Request read access to a dataset you need.\n"
                "3. Complete the **Databricks Fundamentals** training in the Training tab.\n\n"
                "If you ever feel lost, ask the agent \"I'm new here, where do I start?\"."
            ),
        },
        {
            "title": "Glossary: Common Databricks & Data Terms",
            "tags": ["glossary"],
            "body_markdown": (
                "# Glossary\n\n"
                "- **Workspace**: your environment for notebooks, queries, and jobs.\n"
                "- **Catalog**: top-level container for schemas in Unity Catalog.\n"
                "- **Lakehouse**: combined data lake + warehouse architecture.\n"
                "- **Job/Workflow**: a scheduled or triggered pipeline.\n"
                "- **Cost Center**: the billing code charged for infrastructure you provision.\n"
                "- **Identity group**: the group used for ownership and access (not a personal email).\n"
                "- **PII**: personally identifiable information; governed under the data classification standard."
            ),
        },
    ],
    "Data Platform & Governance": [
        {
            "title": "Requesting Access to Unity Catalog Data",
            "tags": ["access"],
            "body_markdown": (
                "# Requesting Access to Data\n\n"
                "All data access is request-based and time-bound by default.\n\n"
                "## How to request\n"
                "Ask the Enterprise Data Hub agent for access to the specific table, schema, or volume "
                "(e.g. `enterprise_prod.sales.orders`). Provide a **business justification** and a "
                "**cost center** if the request provisions infrastructure.\n\n"
                "## Approval\n"
                "- Read access to non-sensitive data: approved by the dataset's data owner.\n"
                "- Access to **Confidential** or **PII** data: requires data owner **and** the "
                "Data Governance group.\n\n"
                "## Duration\n"
                "Access defaults to **90 days**. Request permanent access only with a documented reason; "
                "it requires additional review."
            ),
        },
        {
            "title": "Data Classification & Tagging Standard",
            "tags": ["classification", "tags"],
            "body_markdown": (
                "# Data Classification Standard\n\n"
                "Every governed table must carry a `classification` tag.\n\n"
                "| Level | Examples | Handling |\n"
                "| --- | --- | --- |\n"
                "| Public | Press releases | No restrictions |\n"
                "| Internal | Org charts, roadmaps | Employees only |\n"
                "| Confidential | Unreleased product plans, pricing | Need-to-know + approval |\n"
                "| Restricted/PII | Customer/employee personal data | Strict, audited access |\n\n"
                "Tags are managed via the **Tag Management** GitOps flow — never `ALTER TABLE ... SET TAGS` "
                "by hand. Untagged production tables are flagged by the governance scan."
            ),
        },
    ],
    "Sales Engineering": [
        {
            "title": "Solution Scoping Checklist",
            "tags": ["pre-sales"],
            "body_markdown": (
                "# Solution Scoping Checklist\n\n"
                "Before committing to a customer POC, capture:\n\n"
                "1. **Use case** and success metric.\n"
                "2. **Data sources** and rough volume.\n"
                "3. **Target platforms** involved.\n"
                "4. **Timeline** and key stakeholders.\n"
                "5. **Compliance constraints** (e.g. customer NDA).\n\n"
                "Log the scoped opportunity in the GTM deal desk before requesting any internal data access."
            ),
        },
    ],
    "Sales & GTM Operations": [
        {
            "title": "Deal Desk & Quoting Process",
            "tags": ["quoting", "deal-desk"],
            "body_markdown": (
                "# Deal Desk & Quoting\n\n"
                "## Creating a quote\n"
                "Open the **Deal Desk** workspace and submit the pricing form with the customer, "
                "product line, and term.\n\n"
                "## Discount approvals\n"
                "- Up to 10%: sales rep self-serve.\n"
                "- 10–20%: regional sales director.\n"
                "- Above 20%: **VP of Sales** approval required.\n\n"
                "## SLAs\n"
                "Standard quotes are turned around in **2 business days**. Escalate urgent deals "
                "through the deal-desk Slack channel."
            ),
        },
    ],
    "Engineering & Product": [
        {
            "title": "Project Data Onboarding",
            "tags": ["onboarding", "projects"],
            "body_markdown": (
                "# Project Data Onboarding\n\n"
                "Raw project data lands in `eng_raw.<project>` via the ingestion jobs.\n\n"
                "## Steps\n"
                "1. Register the project with the engineering data team.\n"
                "2. Producers publish CSV/Parquet to the landing volume.\n"
                "3. Nightly jobs curate into `eng_curated.<project>.*` with units normalized.\n\n"
                "Raw project data is **Confidential** by default. Request access through the standard flow."
            ),
        },
        {
            "title": "Internal Naming Conventions",
            "tags": ["naming"],
            "body_markdown": (
                "# Internal Naming Conventions\n\n"
                "Use stable internal codenames for pre-release projects in data tables — never "
                "external marketing names — so unannounced work stays confidential.\n\n"
                "Follow `<area>_<env>_<domain>` for catalog naming, e.g. `eng_prod_telemetry`."
            ),
        },
    ],
    "Supply Chain & Operations": [
        {
            "title": "Operations Metrics Glossary",
            "tags": ["operations", "metrics"],
            "body_markdown": (
                "# Operations Metrics Glossary\n\n"
                "- **Lead time**: time from order to delivery.\n"
                "- **Throughput**: units processed per period.\n"
                "- **Yield**: percent of good units produced.\n"
                "- **DPPM**: defective parts per million, a quality metric.\n\n"
                "Partner-sourced data lands in `ops_prod.*` and may be partner-confidential under NDA — "
                "access is tightly restricted."
            ),
        },
    ],
    "Finance & Reporting": [
        {
            "title": "Revenue Reporting Data Pipeline Overview",
            "tags": ["revenue", "finance"],
            "body_markdown": (
                "# Revenue Reporting Pipeline\n\n"
                "Source reports are ingested monthly into `finance_prod.revenue.reports`.\n\n"
                "## Flow\n"
                "1. Source systems submit reports via the partner portal.\n"
                "2. Reports are validated against contract terms.\n"
                "3. Curated tables feed finance dashboards and audit.\n\n"
                "Revenue data is **Restricted** — access requires finance leadership approval and is fully audited."
            ),
        },
    ],
}


def main():
    print(f"Seeding Context Catalog at {API_BASE} (role={DEV_ROLE})\n")
    existing = list_domains()
    by_name = {d["name"]: d for d in existing}

    # Top-level domains
    for spec in DOMAINS:
        ensure_domain(by_name, **spec)

    # Sub-domains (parents must exist first)
    for parent_name, child in SUBDOMAINS:
        parent = by_name.get(parent_name)
        if not parent:
            print(f"  ! parent '{parent_name}' missing, skipping sub-domain '{child['name']}'")
            continue
        ensure_domain(by_name, parent_id=parent["id"], **child)

    # Documents
    for domain_name, docs in DOCUMENTS.items():
        domain = by_name.get(domain_name)
        if not domain:
            print(f"  ! domain '{domain_name}' missing, skipping its documents")
            continue
        titles = existing_doc_titles(domain["id"])
        print(f"  domain: {domain_name}")
        for doc in docs:
            ensure_document(domain, titles, **doc)

    print("\nDone. Open Governance → Context Catalog to review.")


if __name__ == "__main__":
    main()
