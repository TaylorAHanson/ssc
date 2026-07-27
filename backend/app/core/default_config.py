"""In-code application configuration defaults.

This module is the single source of truth for the app's structured, non-secret
configuration that used to live in ``configuration.yaml``. Scalar defaults
(brand name/colors, governance recipients, web-search limits) are surfaced as
``Settings`` attributes in ``config.py``; this dict backs the richer, tree-shaped
config that consumers read as live dicts: feature flags, navigation tabs, the
agent tool registry, the Self-Service Center / Community Links catalogs, the
site banner, embedded apps, and per-environment service-principal coordinates.

Layering (lowest precedence first):
  1. These defaults.
  2. Environment variables / databricks.yml (for the scalar ``Settings`` fields).
  3. Database overrides applied at startup by ``settings_store.load_overrides``
     (what a Platform Admin edits live under Admin -> Settings).

``config.py`` deep-copies this dict into the process-wide ``_yaml_config`` so the
settings store can mutate the live copy in place without touching these
pristine defaults. Infrastructure/secret wiring (secret scopes, hosts, warehouse
ids, crons) is intentionally NOT here — that stays in databricks.yml.
"""
from __future__ import annotations

from typing import Any, Dict


DEFAULT_CONFIG: Dict[str, Any] = {
    # -------------------------------------------------------------------
    # Target workspaces the app monitors/operates on. One secret scope per
    # installation (settings.TARGET_WORKSPACE_SP_SECRET_SCOPE, from databricks.yml
    # or Admin -> Settings); each entry names, inline, the secret KEYS of the
    # service principal it uses. Empty by default. Edited live under
    # Admin -> Settings -> Target Workspaces. Each entry:
    #   {name, host, environment, client_id_key, client_secret_key}
    # Blank SP keys fall back to the app's own SP (settings.DATABRICKS_*).
    # -------------------------------------------------------------------
    "target_workspaces": [],

    # -------------------------------------------------------------------
    # Branding. Scalar mirror lives in Settings.BRAND_*; these are the
    # defaults an unbranded deploy renders (env vars / DB overrides win).
    # -------------------------------------------------------------------
    "branding": {
        "name": "Self-Service Hub",
        "logo_url": "",
        # Accent blue aligned with the companion Command Center app's tokens.
        "primary_color": "#007BFF",
        "secondary_color": "#001E3C",
        "info_color": "#007BFF",
        "alert_color": "#D32F2F",
    },

    # -------------------------------------------------------------------
    # Self-Service Center: catalog of quick-action cards on the landing page.
    # Per card: title (+ optional description) and exactly one of prompt/route
    # (route wins if both). Optional allowed_personas gates by role.
    # -------------------------------------------------------------------
    "self_service_center": {
        "enabled": True,
        "categories": [
            {
                "title": "Data Access",
                "icon": "Database",
                "cards": [
                    {
                        "title": "Request Data Access",
                        "description": "Get read/write access to a catalog, schema, table, or volume.",
                        "prompt": "I need to request access to a data asset.",
                    },
                    {
                        "title": "REST API Access",
                        "description": "Request access to a REST API or external endpoint.",
                        "prompt": "I need REST API access.",
                    },
                    {
                        "title": "My Groups",
                        "description": "See the identity groups you belong to.",
                        "prompt": "What groups am I a member of?",
                    },
                    {
                        "title": "My Current Access",
                        "description": "Review the data access you already have.",
                        "prompt": "Show me my current data access.",
                    },
                ],
            },
            {
                "title": "Enterprise Data",
                "icon": "Search",
                "cards": [
                    {
                        "title": "Discover Enterprise Data",
                        "description": "Browse the catalog of available data products and datasets.",
                        "route": "/discovery",
                    },
                    {
                        "title": "Marketplace Certification",
                        "description": "Learn about certified data products (ODCS).",
                        "prompt": "Tell me about data product certification.",
                    },
                    {
                        "title": "Learn About Data Quality",
                        "description": "Understand data quality scores and checks.",
                        "prompt": "How does data quality scoring work here?",
                    },
                ],
            },
            {
                "title": "Platform Services",
                "icon": "Boxes",
                "cards": [
                    {
                        "title": "Workspace Access",
                        "description": "Request access to a Databricks workspace.",
                        "prompt": "I need access to a workspace.",
                    },
                    {
                        "title": "Provision Workspace",
                        "description": "Request a new workspace to be provisioned.",
                        "prompt": "I need a new workspace provisioned.",
                    },
                    {
                        "title": "Create Catalog or Schema",
                        "description": "Request a new Unity Catalog catalog or schema.",
                        "prompt": "I want to create a new catalog or schema.",
                    },
                    {
                        "title": "Service Principal",
                        "description": "Request a new service principal.",
                        "prompt": "I need a service principal created.",
                    },
                    {
                        "title": "GitHub Repository",
                        "description": "Request a new GitHub repository.",
                        "prompt": "I need a new GitHub repository.",
                    },
                ],
            },
        ],
    },

    # -------------------------------------------------------------------
    # Community Links page. Each link is a compact one-line string:
    # "Title | URL | icon | description" (Title + URL required).
    # -------------------------------------------------------------------
    "community_links": {
        "enabled": True,
        "categories": [
            {
                "name": "Documentation & Knowledge",
                "icon": "BookOpen",
                "links": [
                    "Confluence | https://databricks.atlassian.net | FileText | Internal docs, wikis, and knowledge base",
                    "Databricks Documentation | https://docs.databricks.com | BookOpen | Official Databricks docs and guides",
                ],
            },
            {
                "name": "Training & Learning",
                "icon": "GraduationCap",
                "links": [
                    "Databricks Academy | https://www.databricks.com/learn/training/home | GraduationCap | Courses, certifications, and learning",
                ],
            },
            {
                "name": "Development & Code",
                "icon": "Code",
                "links": [
                    "GitHub | https://github.com/example | Code | Source code repositories and version control",
                    "Jenkins | https://jenkins.example.com | Settings | CI/CD pipeline management and automation",
                ],
            },
            {
                "name": "Monitoring & Observability",
                "icon": "Eye",
                "links": [
                    "Datadog UI | https://datadog.example.com | Activity | App performance monitoring and observability",
                    "Acceldata UI | https://acceldata.example.com | BarChart | Data observability and quality monitoring",
                    "Finout UI | https://finout.example.com | DollarSign | Cloud cost management and optimization",
                ],
            },
        ],
    },

    # External web apps surfaced inside the app via an iframe. Each entry adds a
    # sidebar link opening at /embedded/<id>. Empty = no embedded apps.
    "embedded_apps": [],

    # External deep links surfaced in-app.
    "links": {
        # "Open in Databricks" link on the Ask Your Data page. Blank hides it
        # (frontend falls back to the workspace /one entry when configured).
        "genie_full_experience_url": "",
    },

    # Site-wide banner. Deploy-time defaults; a Platform Admin edits these live
    # under Admin -> Settings -> System Banner (persisted as DB overrides).
    "banner": {
        "active": False,
        # info (blue), warning (yellow), alert (red), success (green).
        "type": "info",
        "message": "",
    },

    # -------------------------------------------------------------------
    # Feature flags. Each turns an entire capability (its UI, API, and tools)
    # on or off. Editable live under Admin -> Settings -> Features.
    # -------------------------------------------------------------------
    "features": {
        "core": True,
        "governance": True,
        "finops": True,
        "self_service": True,
        "workflows": True,
        "data_discovery": True,
        "calendar": True,
        # Gates the SCHEDULED Enforcement Sentinel run in the background poller
        # (ENFORCEMENT_SENTINEL_CRON). Off = manual runs only. The
        # ui.tabs.sentinel flag only controls tab visibility, not the schedule.
        "sentinel": True,
        "ask_your_data": True,
        "run_sql": True,
        # False = surface Genie's grounded answer verbatim (no extra LLM turn).
        "genie_summarize_answer": False,
        "context_catalog": True,
        "workflow_authoring": True,
        "onboarding_suggestions": True,
        "web_search": True,
        "feedback": True,
        "tool_registry": True,
        "training_admin": True,
        "skills": True,
        # Verbose landing header (brand title, greeting, view toggle). False =
        # clean, minimal landing page.
        "enhanced_landing_page": False,
    },

    # Navigation tab visibility. Hiding a tab only removes it from the menu; it
    # does not disable the underlying capability (use features for that).
    "ui": {
        "tabs": {
            "home": True,
            "data_discovery": True,
            "ask_your_data": True,
            "my_requests": True,
            "pending_approvals": True,
            "training": True,
            "event_calendar": True,
            "templates_assets": False,
            "community_links": True,
            "admin": True,
            "reports": False,
            "training_upload": True,
            "certification": True,
            "odps": True,
            "allowlist": True,
            "sentinel": True,
            "tag_management": True,
            "context_catalog": True,
            "workflows": True,
            "tool_registry": True,
            "feedback": True,
            "training_admin": True,
        },
    },

    # -------------------------------------------------------------------
    # Agent tool registry. A tool entry is a bare bool or a nested dict with an
    # explicit ``enabled`` key. Gated further by their owning feature flag.
    # -------------------------------------------------------------------
    "tools": {
        "check_allowlist_status": True,
        "check_github_repo": True,
        "check_github_user": True,
        "request_github_access": True,
        "check_orphaned_assets": True,
        "does_service_principal_exist": True,
        "get_workflow_instructions": True,
        "evaluate_policy": True,
        "execute_workflow": True,
        "get_catalog_list": True,
        "get_cost_summary": True,
        "get_forecasted_spend": True,
        "get_resource_efficiency_metrics": True,
        "get_schema_list": True,
        "get_table_list": True,
        "get_volume_list": True,
        "search_data_assets": True,
        "get_credential_list": True,
        "check_workspace_path": True,
        "get_target_workspaces": True,
        # SDK "ping" of target workspaces to prove cross-VPC reachability.
        "ping_workspaces": False,
        "ping_url": True,
        "list_github_templates": True,
        "list_github_teams": True,
        "list_workspaces": True,
        "search_events": True,
        "search_requests": True,
        "group_lookup": True,
        "member_lookup": True,
        # Platform-admin diagnostics for the N2K-aware LMWS membership endpoints.
        # The LMWS secret scope and the gateway network path only exist in the
        # app runtime, so these tools are the only way to exercise the endpoints
        # — hence enabled. The add probe is admin-only and defaults to dry_run.
        "lmws_probe_config": True,
        "lmws_probe_read": True,
        "lmws_probe_membership_add": True,
        "check_resource_access": True,
        "search_approvals": True,
        "search_user_entitlements": True,
        "check_tagging_compliance": True,
        "check_object_permissions": True,
        "audit_user_access": True,
        "search_audit_logs": True,
        "check_overprovisioned_users": True,
        "check_asset_quality": True,
        "find_owner": True,
        "check_training_status": True,
        "list_skills": True,
        "get_skill": True,
        "list_context_domains": True,
        "search_context_catalog": True,
        "get_context_document": True,
        "search_databricks_docs": True,
        "fetch_doc_page": True,
        "submit_feedback": True,
        # Databricks Genie via Managed MCP. default_genie_space_id: blank = ask
        # everything; set to pin all traffic to one curated Genie Space.
        "ask_your_data": {
            "enabled": True,
            "default_genie_space_id": "",
            # Client-side poll window before "Genie did not respond" (seconds).
            "poll_timeout_seconds": 300,
        },
        "render_chart": True,
        "run_sql": True,
        "draft_odcs_contract": True,
        "draft_odps_document": True,
        "list_workflow_building_blocks": True,
        "search_similar_workflows": True,
        "get_workflow": True,
        "validate_workflow_spec": True,
        "preview_workflow_spec": True,
        "evaluate_workflow_spec": True,
        "save_workflow_draft": True,
        "publish_workflow": True,
    },

    # Governance notification recipients (mirrored to Settings.GOVERNANCE_EMAIL_GROUP).
    "notifications": {
        "governance_email_group": "data-governance@example.com",
    },

    # Web lookup config for the search_databricks_docs / fetch_doc_page tools.
    "web_search": {
        # Suffix-matched allowed fetch domains. docs.databricks.com is always
        # permitted even when this list is empty.
        "allowed_domains": [
            "docs.databricks.com",
        ],
        "sitemaps": [
            "https://docs.databricks.com/aws/en/sitemap.xml",
        ],
        # Optional Algolia DocSearch public (search-only) credentials. When all
        # three are set, full-text search is used; otherwise sitemap discovery.
        "algolia": {
            "app_id": "",
            "api_key": "",
            "index_name": "",
        },
        "max_results": 8,
        "fetch_timeout_seconds": 15,
        "max_fetch_chars": 20000,
    },
}
