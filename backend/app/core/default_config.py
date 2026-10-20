"""In-code application configuration defaults.

This module is the single source of truth for the app's structured, non-secret
configuration that used to live in ``configuration.yaml``. Scalar defaults
(brand name/colors, governance recipients, web-search limits) are surfaced as
``Settings`` attributes in ``config.py``; this dict backs the richer, tree-shaped
config that consumers read as live dicts: feature flags, navigation tabs, the
agent tool registry, the Community Links catalog, the site banner, embedded
apps, and per-environment service-principal coordinates.

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
        "nav_color": "#001E3C",
        "info_color": "#007BFF",
        "alert_color": "#D32F2F",
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
    # under Admin -> Settings -> Appearance (persisted as DB overrides).
    "banner": {
        "active": False,
        # info (blue), warning (yellow), alert (red), success (green).
        "type": "info",
        "message": "",
    },

    # -------------------------------------------------------------------
    # Feature flags. Each turns an entire capability (its UI, API, and tools)
    # on or off. Editable live under Admin -> Settings -> Features & Navigation,
    # where settings_store.CAPABILITIES pairs each flag with the sidebar tabs it
    # owns (a tab is hidden while its capability's flag is off).
    # -------------------------------------------------------------------
    "features": {
        "core": True,
        "governance": True,
        "data_discovery": True,
        "calendar": True,
        # OmniGuard: gates the SCHEDULED scan in the background poller
        # (ENFORCEMENT_SENTINEL_CRON) and the OmniGuard page. For manual runs
        # only, leave this on and blank the cron.
        "sentinel": True,
        "ask_your_data": True,
        "run_sql": True,
        # False = surface Genie's grounded answer verbatim (no extra LLM turn).
        "genie_summarize_answer": False,
        "context_catalog": True,
        "workflow_authoring": True,
        "onboarding_suggestions": True,
        # Clickable next-step replies under the agent's latest message. Off
        # also drops the instruction from the agent prompt.
        "next_step_suggestions": True,
        # Pinned Items / Data Products / Datasets rails under the chat on the
        # home (new chat) page. Discover is unaffected.
        "home_data_catalog": True,
        # Eagerly tells the agent who the caller is (roles, open requests,
        # pending approvals, group memberships) so it stops asking.
        "user_context": True,
        "web_search": True,
        "feedback": True,
        "tool_registry": True,
        "training_admin": True,
        "skills": True,
        # Preview Features tracker: the daily preview sync and its page.
        "preview_features": True,
        # Access Rules: the group-to-securable ledger, its scheduled CSV export,
        # the rbac_access_change workflow and the Access Rules page.
        "rbac": True,
        # Metadata Manager (ADR-0006): desired tags and descriptions in Lakebase,
        # the reconciler, the /metadata API and its agent and MCP tools.
        "metadata_v2": True,
        # Verbose landing header (brand title and greeting). False = clean,
        # minimal landing page.
        "enhanced_landing_page": False,
    },

    # Navigation tab visibility. Hiding a tab only removes it from the menu; it
    # does not disable the underlying capability (use features for that). A tab
    # owned by a capability whose feature flag is off is also hidden.
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
            "guide": True,
            "admin": True,
            "reports": False,
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
            "preview_features": True,
            "access_rules": True,
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
        # Preview Features: explain a preview and its per-workspace status before a request.
        "find_preview_features": True,
        # Access Rules: who holds what in the ledger, and a dry run of a change
        # before an rbac_access_change request.
        "find_access": True,
        "preview_access_change": True,
        # Metadata Manager: search and read desired tags and descriptions, then
        # preview and submit a change (also published over MCP).
        "search_metadata": True,
        "get_metadata": True,
        "preview_metadata_change": True,
        "submit_metadata_change": True,
        # SDK "ping" of target workspaces to prove cross-VPC reachability.
        "ping_workspaces": False,
        "ping_url": True,
        "list_github_templates": True,
        "list_github_teams": True,
        "list_workspaces": True,
        "search_events": True,
        "search_requests": True,
        # Run before every workflow submission; execute_workflow repeats it as a gate.
        "check_duplicate_requests": True,
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
        # Reads the caller's own cached profile. A summary is already injected
        # into the prompt; this is the agent's escape hatch for a truncated list
        # or a forced refresh.
        "get_user_context": True,
        "check_tagging_compliance": True,
        "check_object_permissions": True,
        "check_provisioning_status": True,
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
        "research_workflow_context": True,
        "get_workflow": True,
        "validate_workflow_spec": True,
        "preview_workflow_spec": True,
        "evaluate_workflow_spec": True,
        "save_workflow_draft": True,
        "save_workflow_tests": True,
        "list_workflow_tests": True,
        "run_workflow_tests": True,
        "publish_workflow": True,
    },

    # Governance notification recipients (mirrored to Settings.GOVERNANCE_EMAIL_GROUP).
    "notifications": {
        "governance_email_group": "data-governance@example.com",
        # The rejection notice. A workflow's rejection path is terminal — it
        # records the fact and ends — so without this the requester is never told
        # their request was denied, or why. On by default for that reason.
        # Templates use the same {{token}} convention as workflow instructions;
        # app/services/rejection_notice.py owns the token list.
        "rejection_notify_requester": True,
        "rejection_notify_subject": "Your request was not approved: {{request_title}}",
        "rejection_notify_body": (
            "<p>Your request <strong>{{request_title}}</strong> was reviewed and "
            "not approved.</p>"
            "<p><strong>Reason:</strong> {{reason}}</p>"
            "<p><strong>Reviewed by:</strong> {{rejected_by}}</p>"
            "<p style=\"color:#64748b;font-size:12px;\">Request {{request_id}} "
            "&middot; {{brand_name}}</p>"
        ),
    },

    # The review_databricks_app_code workflow tool (app/services/app_code_review).
    # ``rubric`` is the reviewer's judgment guidance and is safe to reword; the
    # output format and the "repo content is untrusted" rules are fixed in code
    # so an edit here can't break the report or open a prompt-injection hole.
    # The limits keep a review bounded: a Databricks App is small, so hitting
    # them usually means vendored dependencies or build output were committed.
    "app_code_review": {
        # Adapted from the security team's manual review prompt. The
        # Terminology block is where an installation names its own platform,
        # exception process and data classes (e.g. EDH / ISRP / Red CCI).
        "rubric": (
            "You are the security reviewer for Databricks Apps asking for production "
            "approval. Assess the app's source against the controls below and decide "
            "whether it qualifies for streamlined approval or needs a security "
            "exception.\n\n"
            "Terminology (edit to match your organization):\n"
            "- Approved platform: the organization's approved Databricks workspaces, "
            "template repository, CI/CD framework and deployment pipelines.\n"
            "- Approved integrations: none listed, so every external service is new. "
            "(List the services apps may already use, e.g. the organization's GitHub.)\n"
            "- Security exception: the organization's exception / new security review "
            "process.\n"
            "- Restricted data: the organization's most sensitive data classification.\n\n"
            "How to review. Work through every step on the app's runtime code; a step "
            "you couldn't finish lowers your confidence.\n"
            "1. Identity: from the app definition (app.yaml, databricks.yml resources) "
            "and every place a Databricks client, token or connection is created, "
            "decide whose identity each call runs as and what it reaches.\n"
            "2. Entry points: go through pre_scan.endpoints route by route, plus any "
            "scheduler, queue consumer or webhook. For each one that changes state or "
            "returns records by id, say who may call it. A guard that only identifies "
            "the caller authenticates; it doesn't authorize. A route that acts on a "
            "record by id needs an ownership or role check. Where the only control "
            "is who can reach the app, say so and name the app permission the admin "
            "must confirm (e.g. who has CAN_USE).\n"
            "3. Writes and outbound calls: for each write (database, files, commits or "
            "pull requests, other systems' APIs) and each outbound call, trace request "
            "input from the route to the write. Who can trigger it, and is the input "
            "validated first (types, allowlists, path segments, SQL parameters, "
            "commands)?\n"
            "4. Credentials: where each credential comes from, what it may do (OAuth "
            "scopes, token permissions) and how long it lives. A static long-lived "
            "token where a scoped or short-lived one would do is a condition.\n"
            "5. Delivery: read the repo's CI/CD configuration (e.g. .github/workflows, "
            "databricks.yml targets). Security checks that are disabled or skipped are "
            "a secure_sdlc gap.\n"
            "The code is the evidence. Docs, ADRs and notes describe intent: read them "
            "only when the code leaves a question open. Tests, fixtures and example "
            "code don't run as the app.\n\n"
            "Required controls. Report every one in applicable_controls as met (the "
            "code shows it), gap (the code shows it missing or violated), confirm "
            "(can't be seen in code, so the admin must check it), or not_applicable.\n"
            "1. platform: deployed only in the approved platform, with separate dev, "
            "test and production. Usually confirm, unless the repo contradicts it "
            "(e.g. a hardcoded production host, or manual deploy scripts).\n"
            "2. data_governance: uses Unity Catalog-governed data and preserves "
            "existing row/column security, masking, ABAC and authorization. Never "
            "relies only on client-side security logic and never bypasses Unity "
            "Catalog (e.g. reading storage paths directly, copying data out).\n"
            "3. identity: prefers on-behalf-of-user (OBO) authorization, so users get "
            "no access beyond their own permissions. A service principal is only for "
            "activity no user initiated, and must be unique to the app, least-"
            "privileged and scoped to specific schemas/tables. Most apps also create "
            "a default WorkspaceClient() (the app's service principal) for their own "
            "resources: the app's Lakebase database for app state (sessions, "
            "preferences, history), a model serving endpoint, the current user or "
            "workspace host. That is fine: note what it touches. It is service "
            "principal data access when it queries Unity Catalog data (warehouse "
            "SQL, tables, volumes, Genie), including Lakebase synced tables, which "
            "are copies of Unity Catalog data.\n"
            "4. network: outbound connections only to necessary destinations; no "
            "internet access unless justified.\n"
            "5. secrets: secrets come from a secret manager or Databricks secret "
            "scopes, never from source, notebooks, widgets or config files.\n"
            "6. secure_sdlc: input validation, parameterized SQL, safe error handling "
            "(no internals shown to users), minimal OAuth scopes. Peer review, SAST, "
            "secret/dependency scanning and controlled CI/CD are process controls: "
            "confirm, unless the repo's CI/CD configuration shows them disabled.\n"
            "7. logging: logs authentication, authorization failures, admin actions, "
            "data exports, service principal activity and application errors, in a "
            "form SIEM monitoring can use.\n"
            "8. file_uploads: only if the app accepts uploads: restricted file types, "
            "malware scanning, governed storage, activity logging, defined retention.\n\n"
            "Exception triggers (each is a blocker finding) when the code shows any of:\n"
            "- Public or internet-facing access, or broad outbound connectivity.\n"
            "- External SaaS integrations not in Approved integrations, external AI "
            "services, or customer-managed LLMs. Databricks-hosted model serving "
            "endpoints are not external.\n"
            "- Agentic write actions: an LLM that can change data or systems.\n"
            "- AI processing of restricted data, or exporting restricted data outside "
            "approved controls.\n"
            "- Service principal data access that bypasses OBO.\n"
            "- Write-back beyond the app's own state: e.g. commits or pull requests "
            "that change infrastructure, writes to Unity Catalog tables, other "
            "systems' write APIs. Also new file-handling patterns.\n"
            "- Hardcoded secrets (they must be removed and rotated before approval).\n"
            "- Any other material deviation from the controls above.\n\n"
            "Severity and recommendation:\n"
            "- blocker: an exception trigger. Any blocker means needs_discussion "
            "(Exception required).\n"
            "- concern: a code-visible gap fixable without an exception, e.g. a "
            "missing authorization check, SQL built by string concatenation, request "
            "input reaching a write unvalidated, logging gaps, broader than needed "
            "scopes or permissions. With no blocker, any concern means "
            "approve_with_notes (Compliant with conditions). Give each a concrete "
            "remediation.\n"
            "- minor: worth noting, doesn't change the outcome. Only minor findings "
            "means approve (Compliant).\n\n"
            "Weighting:\n"
            "- Identity and data governance decide most outcomes. An OBO-only app on "
            "governed data with minor issues is Compliant.\n"
            "- In an app that writes, who can trigger each write and whether its "
            "input is validated are the findings that matter most.\n"
            "- Missing app-level audit logging in a read-only OBO app is minor (the "
            "platform's audit logs record who accessed what). It is a concern when "
            "the app writes data, exports data, or performs admin actions.\n"
            "- confirm controls never lower the recommendation on their own; list "
            "them so the admin can check them.\n"
            "- Be pragmatic: these are small internal apps. Style, structure and "
            "minor robustness issues are minor findings, never escalations.\n"
            "- Data classification, and whether code was AI-generated or reviewed, "
            "can't be seen in code. If it matters to the outcome, name it as "
            "something to confirm instead of guessing.\n\n"
            "confidence: how likely your recommendation is right. Above 0.9 only when "
            "you finished every review step; lower it for each step you couldn't "
            "finish and for anything you took from docs rather than code.\n\n"
            "approval_path: one or two sentences for the admin: streamlined approval, "
            "approval once the listed conditions are fixed, or which exception is "
            "needed and why."
        ),
        # Cap on the gzipped archive download, the extracted text kept for the
        # reviewer, and any single file (larger files are skipped, not truncated).
        "max_archive_mb": 25,
        "max_total_kb": 3000,
        "max_file_kb": 200,
        # Reviewer tool-call rounds, and a wall-clock safety net, after which it
        # must submit with what it has read (unread files lower confidence).
        # Neither is a target: the verdict is held back until the files that
        # bear on the decision have been read. The poller heartbeats the
        # request's lock for as long as the step runs.
        "max_turns": 20,
        "time_limit_seconds": 900,
        # The reviewer's model. Blank = the agent's model and reasoning effort.
        # Worth setting separately: a chat model tuned for fast tool use (e.g.
        # gpt-5-6-luna, which needs reasoning off for tools) reviews shallowly.
        "model": "",
        "reasoning_effort": "",
    },

    # Duplicate-request check: the check_duplicate_requests tool and the gate in
    # execute_workflow. Open statuses count at any age; finished ones (completed,
    # rejected, failed) only within lookback_days of their last update. Rejected
    # and failed are left out by default, since resubmitting after either is the
    # normal next step.
    "duplicate_check": {
        "enabled": True,
        "lookback_days": 14,
        "statuses": [
            "pending",
            "manager_approval",
            "data_owner_approval",
            "training_pending",
            "manual_task_pending",
            "provisioning",
            "completed",
        ],
    },

    # Access Rules (Admin -> Settings -> Access Rules). The ledger replaces the
    # RBAC workbook; each managed sheet is exported as its own CSV, identical to
    # a manual "Save as CSV" from Excel, for the Terraform pipeline to read.
    "rbac": {
        # One row per environment. ``sheet_suffix`` builds the per-environment
        # sheet names from the patterns below; ``aliases`` match Environment
        # cells and request wording. A blank export path means that
        # environment's sheets are not exported (the Exports tab says so).
        # ``workspaces`` / ``catalogs`` (comma-separated) are the names the grid
        # offers, and the only ones a change may add; blank = typed freely.
        "environments": [
            {"key": "dev", "aliases": "development", "sheet_suffix": "Dev", "export_path": "",
             "workspaces": "", "catalogs": "",
             "self_service": True, "write_privileges": True, "service_principals": True,
             "two_person_rule": False},
            {"key": "tst", "aliases": "test,qa", "sheet_suffix": "Tst", "export_path": "",
             "workspaces": "", "catalogs": "",
             "self_service": True, "write_privileges": True, "service_principals": True,
             "two_person_rule": False},
            {"key": "stg", "aliases": "stage,staging", "sheet_suffix": "Stg", "export_path": "",
             "workspaces": "", "catalogs": "",
             "self_service": True, "write_privileges": True, "service_principals": True,
             "two_person_rule": False},
            {"key": "prd", "aliases": "prod,production", "sheet_suffix": "Prd", "export_path": "",
             "workspaces": "", "catalogs": "",
             "self_service": True, "write_privileges": False, "service_principals": True,
             "two_person_rule": False},
        ],
        "uc_sheet_pattern": "UC_Securables_RBAC_{suffix}",
        "ws_sheet_pattern": "WS_Securables_RBAC_{suffix}",
        "metastore_sheet": "UC_metastore_RBAC",
        # The metastore sheet covers every environment, so it has its own path.
        "metastore_export_path": "",
        # Hourly, like the original. A run with no ledger change since the last
        # successful export is skipped. Blank = export only on demand.
        "export_cron": "0 * * * *",
        "export_on_change": False,
        # Excel writes UTF-8 CSV without a byte-order mark and ends every row,
        # including the last, with CRLF. Change these only if Compare with a
        # manual export points at them.
        "csv_bom": False,
        "csv_line_ending": "crlf",
        # How long each exported file is kept for download. 0 = forever.
        "export_archive_days": 90,
        # Databricks group display names. Members approve requests of that
        # type and edit its rows. Blank = Platform Admins only.
        "uc_approver_group": "",
        "metastore_approver_group": "",
        "ws_approver_group": "",
        # App roles that can view (not edit) every sheet.
        "viewer_roles": ["Governance Admin"],
        # Reset + re-import discards every change made since the seed import.
        "allow_reset": False,
        "max_changes_per_commit": 200,
        "min_reason_length": 10,
        "min_justification_length": 20,
        # Allow only: a request or direct edit can add only the privileges listed
        # for its securable type, so a type with no row (or a privilege
        # Databricks adds later) can't be granted until an admin lists it.
        # Removing privileges is always allowed. The defaults are the in-code
        # registry (policy.PRIVILEGES) without ALL_PRIVILEGES, MANAGE,
        # SET_SHARE_PERMISSION and the metastore's CREATE_CATALOG / _EXTERNAL_LOCATION /
        # _STORAGE_CREDENTIAL / _CONNECTION / _SHARE / _RECIPIENT / _PROVIDER;
        # recipients and providers take no grants.
        "allowed_privileges": [
            {"securable_type": "catalog", "privileges": (
                "APPLY_TAG, BROWSE, CREATE_FUNCTION, CREATE_MATERIALIZED_VIEW, CREATE_MODEL, CREATE_SCHEMA, "
                "CREATE_TABLE, CREATE_VOLUME, EXECUTE, EXTERNAL_USE_SCHEMA, MODIFY, READ_VOLUME, REFRESH, "
                "SELECT, USE_CATALOG, USE_SCHEMA, WRITE_VOLUME")},
            {"securable_type": "schema", "privileges": (
                "APPLY_TAG, CREATE_FUNCTION, CREATE_MATERIALIZED_VIEW, CREATE_MODEL, CREATE_TABLE, "
                "CREATE_VOLUME, EXECUTE, EXTERNAL_USE_SCHEMA, MODIFY, READ_VOLUME, REFRESH, SELECT, "
                "USE_SCHEMA, WRITE_VOLUME")},
            {"securable_type": "table", "privileges": "APPLY_TAG, MODIFY, SELECT"},
            {"securable_type": "view", "privileges": "APPLY_TAG, MODIFY, SELECT"},
            {"securable_type": "materialized view", "privileges": "APPLY_TAG, REFRESH, SELECT"},
            {"securable_type": "streaming table", "privileges": "APPLY_TAG, REFRESH, SELECT"},
            {"securable_type": "volume", "privileges": "APPLY_TAG, READ_VOLUME, WRITE_VOLUME"},
            {"securable_type": "function", "privileges": "EXECUTE"},
            {"securable_type": "model", "privileges": "APPLY_TAG, EXECUTE"},
            {"securable_type": "registered model", "privileges": "APPLY_TAG, EXECUTE"},
            {"securable_type": "metastore", "privileges": (
                "CREATE_CLEAN_ROOM, CREATE_SERVICE_CREDENTIAL, MANAGE_ALLOWLIST, USE_MARKETPLACE_ASSETS, "
                "USE_PROVIDER, USE_RECIPIENT, USE_SHARE")},
            {"securable_type": "connection", "privileges": "CREATE_FOREIGN_CATALOG, USE_CONNECTION"},
            {"securable_type": "external location", "privileges": (
                "BROWSE, CREATE_EXTERNAL_TABLE, CREATE_EXTERNAL_VOLUME, CREATE_FOREIGN_SECURABLE, "
                "CREATE_MANAGED_STORAGE, EXTERNAL_USE_LOCATION, READ_FILES, WRITE_FILES")},
            {"securable_type": "storage credential", "privileges": "CREATE_EXTERNAL_TABLE, READ_FILES, WRITE_FILES"},
            {"securable_type": "service credential", "privileges": "ACCESS"},
            {"securable_type": "share", "privileges": "SELECT"},
            {"securable_type": "workspace", "privileges": "ADMIN, USER"},
            {"securable_type": "workspace entitlement", "privileges": (
                "allow_cluster_create, allow_instance_pool_create, databricks_sql_access, workspace_access, "
                "workspace_consume")},
            {"securable_type": "cluster", "privileges": "CAN_ATTACH_TO, CAN_MANAGE, CAN_RESTART"},
            {"securable_type": "cluster policy", "privileges": "CAN_USE"},
            {"securable_type": "instance pool", "privileges": "CAN_ATTACH_TO, CAN_MANAGE"},
            {"securable_type": "job", "privileges": "CAN_MANAGE, CAN_MANAGE_RUN, CAN_VIEW, IS_OWNER"},
            {"securable_type": "pipeline", "privileges": "CAN_MANAGE, CAN_RUN, CAN_VIEW, IS_OWNER"},
            {"securable_type": "sql warehouse", "privileges": "CAN_MANAGE, CAN_MONITOR, CAN_USE, CAN_VIEW, IS_OWNER"},
            {"securable_type": "notebook", "privileges": "CAN_EDIT, CAN_MANAGE, CAN_READ, CAN_RUN"},
            {"securable_type": "directory", "privileges": "CAN_EDIT, CAN_MANAGE, CAN_READ, CAN_RUN"},
            {"securable_type": "folder", "privileges": "CAN_EDIT, CAN_MANAGE, CAN_READ, CAN_RUN"},
            {"securable_type": "repo", "privileges": "CAN_EDIT, CAN_MANAGE, CAN_READ, CAN_RUN"},
            {"securable_type": "dashboard", "privileges": "CAN_EDIT, CAN_MANAGE, CAN_READ, CAN_RUN"},
            {"securable_type": "genie space", "privileges": "CAN_EDIT, CAN_MANAGE, CAN_RUN, CAN_VIEW"},
            {"securable_type": "secret scope", "privileges": "READ, WRITE"},
            {"securable_type": "serving endpoint", "privileges": "CAN_MANAGE, CAN_QUERY, CAN_VIEW"},
            {"securable_type": "service principal", "privileges": (
                "CAN_MANAGE, CAN_USE, roles/servicePrincipal.manager, roles/servicePrincipal.user")},
            {"securable_type": "app", "privileges": "CAN_MANAGE, CAN_USE"},
        ],
        # Groups self-service never adds to or removes from (case-insensitive).
        "system_groups": ["account users", "users", "admins"],
        # Owner written on new UC / metastore rows. Blank = the owner already
        # used on that catalog, else the sheet's most common owner.
        "default_owner": "",
        # [{"field", "pattern" (regex, may use {env}), "securable_types"
        #   (comma-separated, blank = all), "severity" (deny | warn), "message"}]
        "naming_rules": [],
        # Extra header spellings: [{"field": "<canonical field>", "header": "<header text>"}]
        "header_aliases": [],
    },

    # Metadata Manager (Admin -> Settings -> Metadata Manager; ADR-0006). Desired
    # tags and descriptions live in Lakebase and the reconciler applies them to
    # Unity Catalog through the governance service principal.
    "metadata": {
        # off = never touch UC; plan = compute and record diffs, write nothing;
        # enforce = apply. Starts in plan so the drift report can be reviewed
        # before anything is removed (ADR-0006 D11). Lower-environment
        # deployments stay in plan unless they enforce a sandbox list below.
        "reconcile_mode": "plan",
        # Full sweep: refresh the snapshot from information_schema and reconcile
        # every managed object. Hourly at :30, off the data-asset sync's slot.
        "reconcile_cron": "30 * * * *",
        # Catalogs this instance manages: names or patterns (*_dev; * = all). The
        # only catalogs it writes to, adopts in or accepts edits for. Blank = read
        # and plan every governed catalog, write none (enforce needs a list).
        "reconcile_catalogs": [],
        # How often new or altered tables are looked for. 0 = only the sweep and
        # explicit reconcile calls pick them up.
        "new_object_poll_seconds": 60,
        # A new object with no desired row: adopt its create-time tags and comment
        # (so CREATE ... TAGS isn't stripped), or ignore it (left unmanaged).
        "new_object_policy": "adopt",
        # Keys the reconciler never sets or removes, and edits can't touch.
        # OmniGuard owns certification, so enforcement must leave it alone.
        "exempt_keys": ["system.certification_status"],
        # More removals (tags or descriptions) than this in one run pauses
        # enforcement and alerts governance. 0 = every removal waits for review.
        "max_removals_per_run": 200,
        # Statements run in parallel across relations (one relation's stay in order).
        "apply_concurrency": 8,
        # Rows whose object was dropped are purged after this many days.
        "orphan_retention_days": 30,
        # Catalog tag naming the instance that enforces the catalog, so two
        # deployments never fight over one. Blank instance id = the app name.
        "claim_enabled": True,
        "claim_tag_key": "metadata_manager_instance",
        "instance_id": "",
        # Members of the group(s) named by this tag (on the object, else its
        # table, schema or catalog) may edit the object.
        "owner_tag_key": "data_owner",
        # Also treat the object's Unity Catalog owner as an editor.
        "uc_owner_is_authority": False,
        # Roles (besides Platform Admin) that may edit every object.
        "editor_roles": ["Governance Admin"],
        # Roles that may browse. Blank = every signed-in user, like Discover.
        "browse_roles": [],
        # open = any key; known_only = only keys with a policy row below.
        "key_mode": "open",
        # Per-key rules. applies_to: comma-separated catalog, schema, table,
        # column (blank = all). allowed_values: comma-separated (blank = any).
        # editor_groups: comma-separated; when set, only those groups (and
        # admins) may change the key. sensitive: changes need approval.
        "key_policies": [
            {"key": "dataset", "applies_to": "table", "allowed_values": "",
             "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", "editor_groups": "", "sensitive": False,
             "required": False, "description": "Logical dataset this object belongs to."},
            {"key": "data_owner", "applies_to": "catalog, schema, table", "allowed_values": "",
             "pattern": "^[A-Za-z0-9][A-Za-z0-9 ._@,-]{0,255}$", "editor_groups": "", "sensitive": True,
             "required": False, "description": "Group(s) accountable for the data; members may edit its tags."},
            {"key": "approver_group", "applies_to": "table", "allowed_values": "",
             "pattern": "^[A-Za-z0-9][A-Za-z0-9 ._@-]{0,127}$", "editor_groups": "", "sensitive": True,
             "required": False, "description": "Group that approves access requests for this dataset."},
            {"key": "access_group", "applies_to": "table", "allowed_values": "",
             "pattern": "^[A-Za-z0-9][A-Za-z0-9 ._@-]{0,127}$", "editor_groups": "", "sensitive": True,
             "required": False, "description": "Group granted access once a request is approved."},
            {"key": "reliability_window", "applies_to": "table", "allowed_values": "",
             "pattern": "^[0-9]+(m|h|d|w)$", "editor_groups": "", "sensitive": False,
             "required": False, "description": "Freshness commitment, e.g. 24h or 7d."},
            {"key": "classification", "applies_to": "", "allowed_values": "public, internal, confidential, restricted",
             "pattern": "", "editor_groups": "", "sensitive": False,
             "required": False, "description": "Sensitivity classification."},
        ],
        # Lowering this key along the ordered levels (least to most sensitive),
        # or removing it, needs approval.
        "classification_key": "classification",
        "classification_levels": ["public", "internal", "confidential", "restricted"],
        # Removing one of these flags (or setting it to false/no/0) needs approval.
        "pii_keys": ["pii"],
        # One row per environment. A catalog belongs to the first row that lists
        # it or whose pattern matches it; {base} in the pattern also defines its
        # counterpart in other environments (sales_dev -> sales_prd).
        # approval: sensitive = only sensitive changes need approval; all = every change.
        "environments": [
            {"key": "dev", "catalog_pattern": "{base}_dev", "catalogs": "", "approval": "sensitive"},
            {"key": "tst", "catalog_pattern": "{base}_tst", "catalogs": "", "approval": "sensitive"},
            {"key": "stg", "catalog_pattern": "{base}_stg", "catalogs": "", "approval": "sensitive"},
            {"key": "prd", "catalog_pattern": "{base}_prd", "catalogs": "", "approval": "sensitive"},
        ],
        "max_objects_per_change": 5000,
        # Preview reads the column masks and row filters keyed on the changed tags.
        "abac_check": True,
        "min_reason_length": 0,
        # Characters required in the justification of a change that needs approval.
        "min_justification_length": 10,
        # Approves changes to objects with no owner tag anywhere above them, and
        # runs the governance gate on sensitive changes. Blank = Platform and
        # Governance Admins.
        "governance_approver_group": "",
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
        "max_results": 8,
        "fetch_timeout_seconds": 15,
        "max_fetch_chars": 20000,
    },
}
