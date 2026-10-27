"""Runtime, admin-editable settings overrides.

This module turns a curated subset of configuration into "change-on-the-fly"
settings backed by the ``app_settings`` table. The design goals:

* **Service, not codebase.** A Platform Admin edits these in the UI; no code
  edit, file sync, or redeploy is required for them to take effect.
* **DB overrides layered over deploy-time defaults.** databricks.yml env vars
  and the in-code defaults (default_config.py) stay as the *defaults*; a DB row
  overrides one.
* **Live application.** Every editable field is one that consumers read at call
  time (``settings.X`` attributes or the live ``_yaml_config`` dicts), so
  applying an override mutates those in place and the change is visible without
  a restart. Restart-required settings (crons, providers) and secrets/infra are
  deliberately excluded and only surfaced read-only.

The single source of truth for what is editable is ``EDITABLE_FIELDS`` plus the
dynamic feature-flag and navigation-tab toggles (arranged by ``CAPABILITIES``).
``SECTIONS`` lays the pages out to mirror the app sidebar. Everything the API
and the frontend render is derived from here.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings, _yaml_config
from app.services.rbac.layout import SECURABLE_TYPES as RBAC_SECURABLE_TYPES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Field specifications
# ---------------------------------------------------------------------------
# Each editable scalar field maps to a storage ``key`` whose prefix determines
# how it is applied and read:
#   * "features.<name>"  -> _yaml_config features flag (bool)
#   * "ui.tabs.<name>"   -> _yaml_config ui.tabs flag (bool)
#   * "yaml:<dot.path>"  -> arbitrary _yaml_config path
#   * "<ATTR>"           -> a Settings attribute (settings.<ATTR>)
#
# ``type`` drives the input widget and coercion: bool | int | string | color.
# Optional ``section`` is a sub-heading within the group's page. Optional
# ``requires`` lists feature/tab keys that must be on for the field to matter;
# the UI shows it read-only under "Inactive settings" otherwise.

EDITABLE_FIELDS: List[Dict[str, Any]] = [
    # ===================================================================
    # General
    # ===================================================================
    # --- Appearance -----------------------------------------------------
    {"group": "Appearance", "section": "Brand", "key": "BRAND_NAME", "label": "Brand name",
     "type": "string", "help": "Display name shown in the header, page title, and emails."},
    {"group": "Appearance", "section": "Brand", "key": "BRAND_SHORT_NAME", "label": "Short name",
     "type": "string", "help": "Compact identifier used for the header logo label and generated slugs."},
    {"group": "Appearance", "section": "Brand", "key": "BRAND_LOGO_URL", "label": "Logo URL",
     "type": "string", "help": "URL of your wordmark/logo. Leave blank to show the name only."},
    {"group": "Appearance", "section": "Colors", "key": "BRAND_COLOR_PRIMARY", "label": "Primary color", "type": "color"},
    {"group": "Appearance", "section": "Colors", "key": "BRAND_COLOR_SECONDARY", "label": "Secondary color", "type": "color"},
    {"group": "Appearance", "section": "Colors", "key": "BRAND_COLOR_NAV", "label": "Sidebar color", "type": "color",
     "help": "Background of the left navigation sidebar. Hover, border, and selected-item shades are "
             "derived from it automatically. Pick a dark color — sidebar text is white."},
    {"group": "Appearance", "section": "Colors", "key": "BRAND_COLOR_INFO", "label": "Info color", "type": "color"},
    {"group": "Appearance", "section": "Colors", "key": "BRAND_COLOR_ALERT", "label": "Alert color", "type": "color"},
    {"group": "Appearance", "section": "Colors", "key": "BRAND_COLOR_WARNING", "label": "Warning color", "type": "color"},
    {"group": "Appearance", "section": "Colors", "key": "BRAND_COLOR_SUCCESS", "label": "Success color", "type": "color"},
    {"group": "Appearance", "section": "System banner", "key": "yaml:banner.active", "label": "Show banner",
     "type": "bool", "help": "Display the banner at the top of every page for all users."},
    {"group": "Appearance", "section": "System banner", "key": "yaml:banner.type", "label": "Banner style",
     "type": "select", "options": ["info", "warning", "alert", "success"],
     "help": "info = blue, warning = yellow, alert = red, success = green."},
    {"group": "Appearance", "section": "System banner", "key": "yaml:banner.message", "label": "Banner message",
     "type": "textarea", "help": "The message shown in the banner. Keep it short; shown verbatim."},

    # --- Notifications --------------------------------------------------
    {"group": "Notifications", "section": "Email delivery", "key": "NOTIFICATION_EMAIL_PROVIDER",
     "label": "Email provider", "type": "select", "options": ["ses", "smtp", "mock"],
     "help": "How notification emails are delivered. ses = AWS SES (IAM creds from the deploy-time "
             "secret scope); smtp = the SMTP host set in the environment; mock = log only, nothing is sent."},
    {"group": "Notifications", "section": "Email delivery", "key": "NOTIFICATION_EMAIL_SES_SOURCE",
     "label": "SES sender address", "type": "string",
     "help": "From address for all notification emails when the provider is ses. Must be a verified "
             "identity in SES for the region below, or sends will fail."},
    {"group": "Notifications", "section": "Email delivery", "key": "NOTIFICATION_EMAIL_SES_REGION",
     "label": "SES region", "type": "string", "help": "AWS region of the SES identity, e.g. us-west-2."},
    {"group": "Notifications", "section": "Email delivery", "key": "APP_BASE_URL",
     "label": "App base URL (for email links)", "type": "string",
     "help": "Public URL of this app, e.g. https://your-app.databricksapps.com. Used for the 'Review' "
             "button in emails. Blank omits the button."},
    {"group": "Notifications", "section": "Rejection emails", "key": "REJECTION_NOTIFY_REQUESTER",
     "label": "Tell requesters when a request is denied", "type": "bool",
     "help": "On (recommended) = when an approver denies a request, the requester is emailed the "
             "reason. A workflow's rejection path ends the request, so nothing else closes this "
             "loop. Off = the denial is recorded and visible in the app, but nobody is told."},
    {"group": "Notifications", "section": "Rejection emails", "key": "REJECTION_NOTIFY_SUBJECT",
     "label": "Rejection email subject", "type": "string",
     "help": "Supports {{request_title}}, {{request_id}}, {{request_type}}, {{reason}}, "
             "{{rejected_by}}, {{brand_name}}, and {{app_url}}."},
    {"group": "Notifications", "section": "Rejection emails", "key": "REJECTION_NOTIFY_BODY",
     "label": "Rejection email body (HTML)", "type": "textarea",
     "help": "Sent as HTML, with the same {{...}} tokens as the subject. {{reason}} is the note the "
             "approver typed; it reads 'No reason was recorded.' when they left it blank."},

    # ===================================================================
    # Discover & Analyze
    # ===================================================================
    # --- Agent ----------------------------------------------------------
    {"group": "Agent", "section": "Model", "key": "MODEL_SERVING_AGENT_LLM_ENDPOINT",
     "label": "Model serving endpoint", "type": "string",
     "help": "The Databricks Model Serving endpoint (the underlying LLM) the agent calls directly. Used "
             "when no AI Gateway endpoint is set below. "
             "Read per turn — a change applies to the next agent request, no restart needed."},
    {"group": "Agent", "section": "Model", "key": "AI_GATEWAY_ENDPOINT", "label": "AI Gateway model",
     "type": "string",
     "help": "Optional. When set, agent LLM calls are routed through the AI Gateway's chat/completions route "
             "(/ai-gateway/mlflow/v1/chat/completions) with this value sent as the model — use a 'system.ai.*' "
             "reference such as 'system.ai.gpt-5-6-luna' (preferred over the legacy 'databricks-*' names). Leave "
             "BLANK to call the Model serving endpoint above directly. Read per turn — applies to the next request."},
    {"group": "Agent", "section": "Model", "key": "AGENT_LLM_REASONING_EFFORT", "label": "Reasoning effort",
     "type": "select", "options": ["", "none", "low", "medium", "high"],
     "help": "For reasoning models only (e.g. gpt-5-6-luna). Set to 'none' so the agent's function tools work — "
             "these models reject tools combined with any other reasoning effort on chat/completions. Leave BLANK "
             "for non-reasoning models (Claude, Llama), which would error on an unexpected reasoning_effort."},
    {"group": "Agent", "section": "Limits & guardrails", "key": "AGENT_MAX_ITERATIONS",
     "label": "Max tool iterations", "type": "int", "min": 1,
     "help": "Max reasoning/tool loops the agent runs per turn."},
    {"group": "Agent", "section": "Limits & guardrails", "key": "AGENT_MAX_LOOKUP_CALLS",
     "label": "Max lookup calls per tool", "type": "int", "min": 1,
     "help": "Per-turn cap on knowledge lookups (internal guidance search, Databricks docs). "
             "Repeated calls with the same arguments are skipped. When the cap is reached "
             "the agent answers from results it already has instead of searching again."},
    {"group": "Agent", "section": "Limits & guardrails", "key": "AGENT_MAX_RESPONSE_TOKENS",
     "label": "Max response tokens per turn", "type": "int", "min": 256,
     "help": "Output ceiling for one LLM turn, including tool-call arguments. Saving a "
             "workflow sends the whole graph plus its playbook in one call — if this is "
             "too low the arguments arrive cut off and the tool reports missing fields."},
    {"group": "Agent", "section": "Limits & guardrails", "key": "AGENT_TIMEOUT_SECONDS",
     "label": "Turn timeout (seconds)", "type": "int", "min": 1,
     "help": "Wall-clock cap for a single agent turn."},
    {"group": "Agent", "section": "Limits & guardrails", "key": "AGENT_MAX_TOOL_OUTPUT_CHARS",
     "label": "Max tool output (chars)", "type": "int", "min": 1000,
     "help": "Per-tool output cap so one chatty tool can't blow the context window."},
    {"group": "Agent", "section": "Limits & guardrails", "key": "AGENT_TOOL_OPA_ENFORCE",
     "label": "Enforce agent-tool OPA policy", "type": "bool",
     "help": "On = deny/approval gates halt mutating tools. Off = shadow (log only)."},
    {"group": "Agent", "section": "Limits & guardrails", "key": "CHAT_SESSION_RETENTION_DAYS",
     "label": "Chat history retention (days)", "type": "int", "min": 1,
     "help": "Server-side chat transcripts older than this are pruned by the background poller."},
    {"group": "Agent", "section": "User context", "key": "USER_CONTEXT_SECTIONS", "label": "User context sections",
     "type": "string", "requires": ["features.user_context"],
     "help": "Comma-separated sections of the user model to assemble and show the agent, in order. "
             "'identity' (roles, persona) and 'activity' (open requests, pending approvals, recent asks) are "
             "fast database reads; 'groups' calls the identity provider and is the slow one. Remove a section "
             "to stop collecting it entirely."},
    {"group": "Agent", "section": "User context", "key": "USER_CONTEXT_TTL_MINUTES",
     "label": "User context TTL (minutes)", "type": "int", "min": 1, "requires": ["features.user_context"],
     "help": "How long a cached user profile stays valid before it is rebuilt in the background."},
    {"group": "Agent", "section": "User context", "key": "USER_CONTEXT_REFRESH_AHEAD_PCT",
     "label": "Refresh-ahead (% of TTL)", "type": "int", "min": 1, "max": 100,
     "requires": ["features.user_context"],
     "help": "Rebuild a profile once it is older than this share of the TTL instead of waiting for it to expire. "
             "This is what lets a page load leave the profile fresh before the user's first message. "
             "100 = only refresh after expiry."},
    {"group": "Agent", "section": "User context", "key": "USER_CONTEXT_MIN_REFRESH_SECONDS",
     "label": "Min seconds between refreshes", "type": "int", "min": 0, "requires": ["features.user_context"],
     "help": "Floor between two rebuilds of the same profile. Warming fires from app boot, chat mount, and the "
             "poller, so this stops a reload-happy user from hammering the identity provider."},
    {"group": "Agent", "section": "User context", "key": "USER_CONTEXT_PREWARM_DAYS",
     "label": "Pre-warm window (days)", "type": "int", "min": 0, "requires": ["features.user_context"],
     "help": "The background poller refreshes profiles for users seen within this many days, so returning users "
             "are already warm at login. 0 = no pre-warm sweep."},
    {"group": "Agent", "section": "User context", "key": "USER_CONTEXT_ACTIVITY_LIMIT",
     "label": "Activity items per section", "type": "int", "min": 1, "requires": ["features.user_context"],
     "help": "How many recent requests, pending approvals, and recent chat topics to summarize for the agent."},
    {"group": "Agent", "section": "User context", "key": "USER_CONTEXT_MAX_CHARS",
     "label": "Max user context (chars)", "type": "int", "min": 200, "requires": ["features.user_context"],
     "help": "Cap on the user-context block added to the system prompt, so a user in hundreds of groups can't "
             "crowd out the rest of the prompt. Overflow is truncated and the agent is told to call "
             "get_user_context for the full picture."},
    {"group": "Agent", "section": "Web lookup", "key": "yaml:web_search.allowed_domains", "label": "Allowed domains",
     "type": "string_list", "add_label": "Add domain", "requires": ["features.web_search"],
     "help": "Domains the agent may fetch pages from (suffix-matched). docs.databricks.com is always allowed."},
    {"group": "Agent", "section": "Web lookup", "key": "yaml:web_search.sitemaps", "label": "Sitemaps",
     "type": "string_list", "add_label": "Add sitemap", "requires": ["features.web_search"],
     "help": "Sitemap URLs the documentation search ranks pages from. Each must be on an allowed domain."},
    {"group": "Agent", "section": "Web lookup", "key": "yaml:web_search.max_results", "label": "Max results",
     "type": "int", "min": 1, "requires": ["features.web_search"],
     "help": "Max search hits returned per query."},
    {"group": "Agent", "section": "Web lookup", "key": "yaml:web_search.fetch_timeout_seconds",
     "label": "Fetch timeout (seconds)", "type": "int", "min": 1, "requires": ["features.web_search"],
     "help": "Per-request HTTP timeout when reading a page."},
    {"group": "Agent", "section": "Web lookup", "key": "yaml:web_search.max_fetch_chars", "label": "Max fetch chars",
     "type": "int", "min": 1000, "requires": ["features.web_search"],
     "help": "Cap on extracted page text handed to the model."},
    {"group": "Agent", "section": "Duplicate requests", "key": "DUPLICATE_CHECK_ENABLED",
     "label": "Check for duplicate requests", "type": "bool",
     "help": "On (recommended) = before the agent submits a request it looks for open or recently finished "
             "requests of the same workflow for the same resource, by this user or anyone else. If it finds "
             "one it shows it and only submits once the user says to go ahead anyway. Other people's "
             "requests are described without their ID or requester unless the user is a Platform Admin. "
             "Off = requests are submitted without the check."},
    {"group": "Agent", "section": "Duplicate requests", "key": "DUPLICATE_CHECK_LOOKBACK_DAYS",
     "label": "Finished-request lookback (days)", "type": "int", "min": 0,
     "help": "How long a finished request (completed, or rejected/failed if listed below) still counts as a "
             "duplicate after its last update. Open requests count at any age. 0 = only open requests count."},
    {"group": "Agent", "section": "Duplicate requests", "key": "DUPLICATE_CHECK_STATUSES",
     "label": "Statuses that count as duplicates", "type": "string_list", "add_label": "Add status",
     "help": "Request statuses that count as a possible duplicate. Options: pending, manager_approval, "
             "data_owner_approval, training_pending, manual_task_pending, provisioning, completed, rejected, "
             "failed. Rejected and failed are left out by default because resubmitting is the usual next step."},

    # --- Data Catalog ---------------------------------------------------
    {"group": "Data Catalog", "key": "SCAN_CATALOGS", "label": "Scanned catalogs",
     "type": "string",
     "help": "Comma-separated Unity Catalog allowlist that scopes governed data, "
             "e.g. 'enterprise_prod, finance_prod'. This one list drives data certification "
             "(dataset-tag discovery) AND the data-asset cache sync that powers the catalog page — "
             "so only these catalogs' assets show up there. "
             "Spaces around each name are trimmed. Leave BLANK to include every catalog the service "
             "principal can see (excluding system/samples). Applies on the next scan/sync — no restart needed."},
    {"group": "Data Catalog", "key": "DATA_ASSET_OWNER_TAG", "label": "Data owner tag",
     "type": "string", "requires": ["features.data_discovery"],
     "help": "Tag name used to identify the human or team owner of a data asset (e.g. 'data_owner'). "
             "When this tag is present on an asset, its value is displayed as the owner instead of "
             "the direct Unity Catalog object owner. If the tag is not set or this setting is blank, "
             "falls back to the direct owner. Applies immediately — no restart needed."},
    {"group": "Data Catalog", "key": "DATA_ASSET_LINEAGE_LOOKBACK_DAYS",
     "label": "Dashboard lineage lookback (days)", "type": "int", "min": 1, "max": 365,
     "requires": ["features.data_discovery"],
     "help": "How many days of system.access.table_lineage the data-asset sync searches for "
             "dashboards that read each metric view. A dashboard nobody opened in this window isn't "
             "listed. Lineage keeps up to 365 days. Applies on the next sync."},
    {"group": "Data Catalog", "key": "DISCOVER_PREVIEW_ROW_LIMIT",
     "label": "Data preview rows", "type": "int", "min": 1, "max": 5000,
     "requires": ["features.data_discovery"],
     "help": "Most rows a user sees when they load the Data preview of a table or view on the catalog "
             "page. The preview runs SELECT ... LIMIT on the SQL warehouse as the signed-in user, so "
             "it only shows rows they're allowed to read; search, sort and filter then work on just "
             "these rows in the browser. Higher values make previews slower and use more warehouse "
             "time. Applies immediately."},
    {"group": "Data Catalog", "key": "DISCOVER_MAX_PINS",
     "label": "Pinned assets per user", "type": "int", "min": 1, "max": 500,
     "requires": ["features.data_discovery"],
     "help": "Most tables, views, metric views and dashboards one person can pin to the top of the "
             "catalog page. Existing pins over a lowered limit are kept; the user just can't add "
             "more until they unpin some. Applies immediately."},
    {"group": "Data Catalog", "key": "DISCOVER_HIDDEN_TAG_KEYS",
     "label": "Hide from Discover tag keys", "type": "string_list", "add_label": "Add tag key",
     "requires": ["features.data_discovery"],
     "help": "Tag keys that keep an asset out of the catalog page and the agent's catalog search, e.g. "
             "'hide_from_discover'. An asset is hidden when one of these keys is on its catalog, its schema, "
             "the table, view or metric view itself, or any of its columns; dashboards, Genie spaces and "
             "apps are checked against their own workspace tags, and jobs against their job tags. Only the "
             "key matters (any value, any case). Hidden assets leave browse, search, domain counts, pins "
             "and lineage. Hiding isn't access control: anyone with access can still open the asset in "
             "Databricks. Empty = nothing hidden. Changing this list applies immediately; tags added or "
             "removed in Unity Catalog apply after the next data asset sync."},
    {"group": "Data Catalog", "key": "DATA_ASSET_SYNC_CRON", "label": "Data asset sync cron",
     "type": "cron", "requires": ["features.data_discovery"],
     "help": "How often the local data-asset cache (and the cached workspace list, if on) is refreshed "
             "from Unity Catalog (5-field cron, UTC). Leave BLANK to disable."},
    {"group": "Data Catalog", "key": "WORKSPACE_CACHE_ENABLED", "label": "Cache the account's workspace list",
     "type": "bool", "requires": ["features.data_discovery"],
     "help": "On = each data asset sync also saves the account's workspace list (system.access.workspaces_latest, "
             "read by the same service principal), so the agent can list workspaces or check whether one already "
             "exists instantly. Off = every lookup queries that table live as the signed-in user, which can take "
             "a minute or more while the SQL warehouse starts. Applies immediately."},
    {"group": "Data Catalog", "key": "WORKSPACE_CACHE_MAX_AGE_MINUTES", "label": "Workspace list max age (minutes)",
     "type": "int", "min": 5, "requires": ["features.data_discovery"],
     "help": "How old the cached workspace list may get before the agent ignores it and queries live. Keep this "
             "above the data asset sync interval (hourly by default) so the normal gap between syncs never "
             "triggers a slow live query. Applies immediately."},

    # ===================================================================
    # Requests & Approvals
    # ===================================================================
    # --- Group Management (LMWS) ----------------------------------------
    {"group": "Group Management (LMWS)", "key": "LMWS_NATIVE", "label": "Run LMWS natively (in-app)",
     "type": "bool",
     "help": "On (recommended) = all LMWS operations (lookups, membership add/remove/update, and group/SPAC "
             "lifecycle) call the FWS-API gateway directly from the app — no Databricks job, lower latency. "
             "Off = fall back to the serverless (job-backed) notebook harness, for gateways only reachable from a "
             "network-pinned cluster. Read per call, so a change here applies immediately without a redeploy. "
             "When off, the 'Run LMWS jobs on serverless' setting below selects the job's compute."},
    {"group": "Group Management (LMWS)", "key": "LMWS_USE_SERVERLESS", "label": "Run LMWS jobs on serverless",
     "type": "bool",
     "help": "On (recommended) = LMWS group/user jobs run on serverless compute — cheaper and no cold-start, "
             "since the notebook is API-only (no Spark). Off = run on classic compute using the Databricks "
             "Job settings (cluster id / instance pool / node type) — use this only if the LMWS/FWS-API "
             "gateway is reachable solely from a network-pinned classic cluster. Applies to the next LMWS run."},
    {"group": "Group Management (LMWS)", "key": "LMWS_AUTHN_URL", "label": "LMWS authn URL",
     "type": "string",
     "help": "FWS-API authentication base URL passed into the LMWS job "
             "(e.g. https://<gateway>/iam/v1/lmwsrest-authn). Blank = LMWS actions fail with a clear "
             "'not configured' error. Read per run, so a change here applies to the next LMWS run without a redeploy."},
    {"group": "Group Management (LMWS)", "key": "LMWS_REST_URL", "label": "LMWS REST URL",
     "type": "string",
     "help": "FWS-API REST base URL for list/member operations "
             "(e.g. https://<gateway>/iam/v1/lmws-rest/publicAPIrest). Applies to the next LMWS run."},
    {"group": "Group Management (LMWS)", "key": "LMWS_CACHE_URL", "label": "LMWS list-cache URL",
     "type": "string",
     "help": "FWS-API list-cache-info base URL "
             "(e.g. https://<gateway>/iam/v1/lmws-rest/listCacheInfo). Applies to the next LMWS run."},
    {"group": "Group Management (LMWS)", "key": "LMWS_FWS_URL", "label": "LMWS FWS entitlement URL",
     "type": "string",
     "help": "FWS-API entitlement base URL "
             "(e.g. https://<gateway>/iam/v1/fws-api/entitlement). Applies to the next LMWS run."},
    {"group": "Group Management (LMWS)", "key": "LMWS_NOTIFICATION_EMAIL_DOMAIN", "label": "LMWS notification email domain",
     "type": "string",
     "help": "Email domain appended to the requester's CN for the FWS-API notificationCallBack "
             "(e.g. example.com). Required to create SP groups. Applies to the next LMWS run."},
    {"group": "Group Management (LMWS)", "key": "LMWS_SERVICE_USERNAME", "label": "Native LMWS service account",
     "type": "string",
     "help": "Service-account username used when LMWS runs natively (in-app) — see 'Run LMWS natively' above. "
             "The matching password is read at runtime from the same secret scope the notebook uses (below) via "
             "the app's own service principal — no plaintext, no separate secret."},
    {"group": "Group Management (LMWS)", "key": "LMWS_PASSWORD_SECRET_KEY", "label": "Native LMWS password key",
     "type": "string",
     "help": "Key name (within the LMWS secret scope, LMWS_SECRET_SCOPE) holding the service-account password "
             "the native LMWS path reads at runtime. Defaults to 'edhapisvc' to match the vendored notebook. "
             "The app's service principal needs READ on that scope; nothing is injected as plaintext."},
    {"group": "Group Management (LMWS)", "key": "LMWS_NATIVE_VERIFY_TLS", "label": "Verify TLS for native LMWS",
     "type": "bool",
     "help": "On = verify the gateway's TLS certificate for the native (in-app) LMWS calls. Off (default) "
             "matches the vendored notebook, which trusts the internal gateway CA without verification. Turn on "
             "where the app runtime trusts the gateway's certificate chain."},

    # --- App Code Review ------------------------------------------------
    {"group": "App Code Review", "key": "APP_CODE_REVIEW_RUBRIC", "label": "Reviewer rubric",
     "type": "textarea",
     "help": "What the review_databricks_app_code step judges and how strictly. Reword freely: the "
             "report format and the rule that repository content is untrusted are fixed in code. "
             "Whatever this says, a service principal reading data, or a committed secret, is "
             "always flagged for discussion."},
    {"group": "App Code Review", "key": "APP_CODE_REVIEW_MODEL", "label": "Reviewer model",
     "type": "string",
     "help": "Model the reviewer uses, routed like the agent's: an AI Gateway model reference (e.g. "
             "'system.ai.claude-sonnet-5') when an AI Gateway model is set under Agent, otherwise a Model "
             "serving endpoint name. BLANK = the agent's model and reasoning effort. Pick a model that "
             "can reason while calling tools; a fast chat model with reasoning off reviews shallowly."},
    {"group": "App Code Review", "key": "APP_CODE_REVIEW_REASONING_EFFORT", "label": "Reviewer reasoning effort",
     "type": "select", "options": ["", "none", "low", "medium", "high"],
     "help": "Only used when a Reviewer model is set. For reasoning models that accept tools with "
             "reasoning on. BLANK omits the parameter (right for Claude and Llama). gpt-5-6-luna rejects "
             "tools with anything but 'none'."},
    {"group": "App Code Review", "key": "APP_CODE_REVIEW_MAX_TURNS", "label": "Reviewer tool rounds",
     "type": "int", "min": 4, "max": 60,
     "help": "Upper bound on rounds of reading files (several files can be read per round). The "
             "verdict is held back until the files that bear on the decision are read; if this runs "
             "out first, the unread files are listed and lower confidence."},
    {"group": "App Code Review", "key": "APP_CODE_REVIEW_TIME_LIMIT_SECONDS", "label": "Reviewer time limit (s)",
     "type": "int", "min": 60, "max": 3600,
     "help": "Safety net for a stuck or slow model: after this long the reviewer must give its verdict "
             "with what it has read. A normal review finishes well before it."},
    {"group": "App Code Review", "key": "APP_CODE_REVIEW_MAX_ARCHIVE_MB", "label": "Max repository archive (MB)",
     "type": "int", "min": 1,
     "help": "Larger repositories fail the step rather than being partly reviewed."},
    {"group": "App Code Review", "key": "APP_CODE_REVIEW_MAX_TOTAL_KB", "label": "Max source reviewed (KB)",
     "type": "int", "min": 100,
     "help": "Text kept for review after skipping dependencies, build output, and binaries. Past this, "
             "files are left out and the report says so (and confidence drops)."},
    {"group": "App Code Review", "key": "APP_CODE_REVIEW_MAX_FILE_KB", "label": "Max single file (KB)",
     "type": "int", "min": 10,
     "help": "Files larger than this are skipped (listed in the report), usually generated or vendored code."},

    # ===================================================================
    # Learn & Share
    # ===================================================================
    # --- Links & Embedded Apps -----------------------------------------
    {"group": "Links & Embedded Apps", "key": "yaml:community_links", "label": "Community Links",
     "type": "catalog", "kind": "community_links", "add_label": "Add category",
     "requires": ["ui.tabs.community_links"],
     "help": "The Community Links page — categories of curated external resources and tools."},
    {"group": "Links & Embedded Apps", "section": "Support contact", "key": "yaml:support.name",
     "label": "Contact name", "type": "string",
     "help": "The person or team to contact for day-to-day help, for example 'Data Platform Support'."},
    {"group": "Links & Embedded Apps", "section": "Support contact", "key": "yaml:support.email",
     "label": "Contact email", "type": "string",
     "help": "Shown to users on a dataset's Team & Support tab, and given to the assistant to answer "
             "'who do I contact for support?'. Leave the name and email blank to show nothing."},
    {"group": "Links & Embedded Apps", "section": "Support contact", "key": "yaml:support.notes",
     "label": "Hours or channel", "type": "textarea",
     "help": "Optional: support hours, a chat channel, or a link to the support portal. Shown verbatim."},
    {"group": "Links & Embedded Apps", "key": "yaml:embedded_apps", "label": "Embedded Apps",
     "type": "catalog", "kind": "embedded_apps", "add_label": "Add app",
     "help": "External web apps surfaced inside this app via an iframe. Each adds a sidebar link opening at /embedded/<id>. Note: targets that send X-Frame-Options/CSP frame-ancestors may render blank."},

    # --- Calendar -------------------------------------------------------
    # All schedules are standard 5-field cron in UTC and applied by the in-process
    # poller thread, which re-reads them every cycle — edits take effect on the
    # next poll (no redeploy). Blank disables a schedule. A bad expression is
    # rejected on save so a typo can't silently break a schedule.
    {"group": "Calendar", "key": "EVENT_CALENDAR_URL", "label": "Calendar feed URL",
     "type": "string",
     "help": "A published ICS (iCalendar) feed the app can download without signing in; the Event Calendar "
             "page is built from it. Outlook: in Outlook on the web open Calendar → Settings → Shared calendars, "
             "under 'Publish a calendar' pick the calendar and 'Can view all details', select Publish, and copy "
             "the ICS link (ends in calendar.ics) — not the HTML link. If Publish is missing, your Microsoft 365 "
             "admin has turned off calendar publishing. Google Calendar: use the calendar's 'Secret address in "
             "iCal format'. webcal:// links work too. Blank = no events are synced. Applies on the next sync — "
             "use Sync on the Event Calendar page to pull it now."},
    {"group": "Calendar", "key": "EVENT_SYNC_CRON", "label": "Calendar sync cron",
     "type": "cron",
     "help": "How often calendar/events are synced (5-field cron, UTC). Leave BLANK to disable."},

    # --- Training (Academy catalog) ---------------------------------------
    {"group": "Training", "section": "Academy catalog", "key": "TRAINING_ACADEMY_URL", "label": "Academy URL",
     "type": "string",
     "help": "The Databricks Academy site whose public course catalog is synced. Must be on databricks.com. "
             "Training Studio's course picker and linked courses use this catalog."},
    {"group": "Training", "section": "Academy catalog", "key": "TRAINING_ACADEMY_SYNC_CRON",
     "label": "Catalog sync cron", "type": "cron",
     "help": "How often the Academy catalog is re-read and linked courses refreshed (5-field cron, UTC). "
             "Leave BLANK to sync only when Sync Academy catalog is clicked in Training Studio."},
    {"group": "Training", "section": "Academy catalog", "key": "TRAINING_ACADEMY_LANGUAGES",
     "label": "Languages", "type": "string_list", "add_label": "Add language",
     "help": "Which course languages to keep, as Academy names them (english, japanese, korean, spanish, "
             "french, pt-br, simplified_chinese). Empty keeps every language."},

    # ===================================================================
    # Watch Tower
    # ===================================================================
    # --- OmniGuard ------------------------------------------------------
    {"group": "OmniGuard", "section": "Alerts & digest", "key": "GOVERNANCE_EMAIL_GROUP",
     "label": "Governance admin recipients", "type": "string",
     "help": "Who receives OmniGuard alerts + the daily digest. Comma-separate multiple addresses. Also the "
             "default recipient for workflow notification steps that don't name one."},
    {"group": "OmniGuard", "section": "Alerts & digest", "key": "GOVERNANCE_CONTACT",
     "label": "Governance contact (for enforcement emails)", "type": "string",
     "requires": ["features.sentinel"],
     "help": "Team name, email, or DL shown in the 'Questions? Contact ...' line of automated enforcement emails to app owners. Blank omits the line."},
    {"group": "OmniGuard", "section": "Alerts & digest", "key": "ENFORCEMENT_DIGEST_HOUR_LOCAL",
     "label": "Daily digest hour (0-23)", "type": "int", "min": 0, "max": 23,
     "requires": ["features.sentinel"],
     "help": "Local hour the once-per-day governance digest is sent (anchored to the timezone below)."},
    {"group": "OmniGuard", "section": "Alerts & digest", "key": "ENFORCEMENT_DIGEST_TIMEZONE",
     "label": "Digest timezone", "type": "string", "requires": ["features.sentinel"],
     "help": "IANA timezone the digest hour is evaluated in, e.g. America/Los_Angeles."},
    {"group": "OmniGuard", "section": "Schedule", "key": "ENFORCEMENT_SENTINEL_CRON", "label": "OmniGuard scan cron",
     "type": "cron", "requires": ["features.sentinel"],
     "help": "How often OmniGuard scans every target workspace (5-field cron, UTC). "
             "Leave BLANK to disable the scheduled scan — manual runs from the OmniGuard page still work."},
    {"group": "OmniGuard", "section": "Schedule", "key": "ENFORCEMENT_SENTINEL_STALE_MINUTES",
     "label": "Stale-run threshold (min)", "type": "int", "min": 1, "requires": ["features.sentinel"],
     "help": "A stuck OmniGuard run older than this no longer blocks the schedule."},
    {"group": "OmniGuard", "section": "Scanning", "key": "SENTINEL_SCAN_CONCURRENCY",
     "label": "Scan concurrency", "type": "int", "min": 1, "requires": ["features.sentinel"],
     "help": "Max concurrent units of work WITHIN one workspace scan (resource "
             "handlers + per-resource OPA evaluation). 1 = fully serialized."},
    {"group": "OmniGuard", "section": "Scanning", "key": "SENTINEL_WORKSPACE_CONCURRENCY",
     "label": "Workspace concurrency", "type": "int", "min": 1, "requires": ["features.sentinel"],
     "help": "How many target workspaces to scan at the SAME TIME. Higher makes a "
             "run's wall-clock closer to the slowest single workspace instead of "
             "the sum of all of them, at the cost of more peak memory and "
             "simultaneous Databricks API load. 1 = scan workspaces one at a time."},
    {"group": "OmniGuard", "section": "Scanning", "key": "SENTINEL_SCAN_NOTEBOOKS", "label": "Scan notebooks",
     "type": "bool", "requires": ["features.sentinel"],
     "help": "OFF by default. Notebook discovery recursively walks the entire "
             "workspace tree (/Users + /Shared) and is by far the most expensive "
             "part of a scan. Turn on only if you have policies that evaluate "
             "notebooks; expect substantially longer scans."},
    {"group": "OmniGuard", "section": "Scanning", "key": "SENTINEL_WORKSPACE_SCAN_TIMEOUT_SECONDS",
     "label": "Per-workspace scan timeout (sec)", "type": "int", "min": 0, "requires": ["features.sentinel"],
     "help": "Wall-clock cap (seconds) on one workspace's scan before it's "
             "ABANDONED as a timeout failure — contributing ZERO findings for that "
             "workspace. DEFAULT 0 (no limit): a large workspace can legitimately "
             "take many minutes, and a cap that's too low makes OmniGuard report "
             "a fraction of real violations. True hangs are already bounded per-call "
             "by the OmniGuard SDK timeout, so leave this 0 unless you must bound a "
             "specific runaway workspace (then use a generous value like 3600)."},
    {"group": "OmniGuard", "section": "Scanning", "key": "SENTINEL_SDK_HTTP_TIMEOUT_SECONDS",
     "label": "Per-call SDK timeout (sec)", "type": "int", "min": 0, "requires": ["features.sentinel"],
     "help": "Per-HTTP-call timeout for OmniGuard's own workspace clients — "
             "longer than the app-wide Databricks SDK timeout because remote "
             "workspaces can be slow. Bounds ONE call, not the whole scan (that's "
             "the per-workspace timeout above). Safe because OmniGuard runs on its "
             "own thread pool. 0 = use the app-wide default."},
    {"group": "OmniGuard", "section": "Auto-enforcement", "key": "SENTINEL_AUTO_ENFORCE_APPS",
     "label": "Auto-enforce App policy", "type": "bool", "requires": ["features.sentinel"],
     "help": "OFF by default. When enabled, non-compliant Databricks Apps in production enterprise workspaces are automatically stopped and have their permissions revoked to admins only. All other resource types remain manual Review & Act only."},
    {"group": "OmniGuard", "section": "Auto-enforcement", "key": "SENTINEL_AUTO_ENFORCE_MAX_APPS_PER_RUN",
     "label": "Max auto-stopped apps per run", "type": "int", "min": 1, "max": 20,
     "requires": ["features.sentinel"],
     "help": "Circuit breaker cap on how many non-compliant apps can be stopped automatically in a single OmniGuard run. Prevents mass outages if a policy or allowlist misfires."},
    {"group": "OmniGuard", "section": "Auto-enforcement", "key": "SENTINEL_PROTECTED_APP_NAMES",
     "label": "Protected app names/patterns", "type": "string", "requires": ["features.sentinel"],
     "help": "Comma-separated list of app names or glob patterns (e.g. 'edh-ssc*, mcp-server*, custom-app') that are protected and can never be stopped or revoked by automated enforcement."},

    # --- Data Certification ---------------------------------------------
    {"group": "Data Certification", "key": "DATA_QUALITY_TABLE", "label": "Data quality table",
     "type": "string", "requires": ["features.governance"],
     "help": "Fully-qualified table (catalog.schema.table) holding the ADOC data-quality history used "
             "for certification checks. Applies on the next scan — no restart needed."},
    {"group": "Data Certification", "key": "DATA_QUALITY_ADOC_SCHEMA", "label": "ADOC history schema",
     "type": "string", "requires": ["features.governance"],
     "help": "The catalog.schema holding THIS environment's ADOC *_history tables (adoc_dq_history, "
             "adoc_freshness_history, ...), e.g. 'enterprise_prod.data_quality'. Must point at the same "
             "environment you are certifying — reading another environment's history would certify on the "
             "wrong data. Leave BLANK to skip data-quality checks entirely; datasets then report DQ as "
             "'not fetched' and cannot be certified. Applies on the next scan — no restart needed."},
    {"group": "Data Certification", "key": "CONTRACT_SYNC_CRON", "label": "Data contract sync cron",
     "type": "cron", "requires": ["features.governance", "features.data_discovery"],
     "help": "Auto-rediscovers 'dataset'-tagged tables and redrafts their ODCS contracts on this schedule "
             "(5-field cron, UTC). Leave BLANK to disable (contracts then only refresh when you click "
             "'Sync Data Contracts'). This drafts a contract per dataset via the LLM, so prefer an off-peak, "
             "low frequency such as '0 6 * * *' (daily 06:00 UTC). The scheduled run also needs the data "
             "catalog turned on."},
    {"group": "Data Certification", "key": "SENTINEL_DATA_CERT_WORKSPACE",
     "label": "Data certification workspace",
     "type": "string",
     "help": "OmniGuard scans every target workspace for compute/apps/jobs, but data certification is Unity Catalog (metastore) scoped, so it runs ONCE against a single workspace. Enter the NAME of the target workspace that should run it, or leave blank to use the app's own home workspace. This workspace's service principal is ALSO the governance identity for other metastore-global reads — notably the data-asset cache sync that powers the data catalog — so it must have BROWSE on the scanned catalogs and CAN USE on the SQL warehouse. The DQ table + ADOC schema always come from the settings above."},

    # --- Metadata Manager -----------------------------------------------
    {"group": "Metadata Manager", "section": "Reconciler", "key": "METADATA_RECONCILE_MODE",
     "label": "Reconcile mode", "type": "select", "options": ["off", "plan", "enforce"],
     "requires": ["features.metadata_v2"],
     "help": "What the reconciler does with the difference between the desired tags and descriptions and what "
             "Unity Catalog holds. Plan (default) works out every statement and shows it on the Drift and Runs "
             "tabs without changing anything. Enforce applies them, including removing tags that aren't in the "
             "desired state, but only once Catalogs to reconcile lists the catalogs this deployment manages. Off "
             "stops it entirely. Review the drift report in plan before switching to enforce."},
    {"group": "Metadata Manager", "section": "Reconciler", "key": "METADATA_RECONCILE_CRON",
     "label": "Full sweep schedule", "type": "cron", "requires": ["features.metadata_v2"],
     "help": "When the whole snapshot is re-read from information_schema and every managed object is "
             "reconciled (5-field cron, UTC). Saved changes don't wait for this: they are applied within a poll "
             "cycle. Leave BLANK to run sweeps only with Run now."},
    {"group": "Metadata Manager", "section": "Reconciler", "key": "METADATA_RECONCILE_CATALOGS",
     "label": "Catalogs to reconcile", "type": "string_list", "add_label": "Add catalog",
     "requires": ["features.metadata_v2"],
     "help": "The catalogs this deployment manages: the only ones it changes, adopts new tables in, or accepts "
             "edits to. Names or patterns, such as sales_dev or *_dev; * means every catalog. Blank = it reads "
             "and plans every governed catalog but changes none, and enforce doesn't run. On a dev or stage "
             "deployment that shares the metastore with production, list only that environment's catalogs."},
    {"group": "Metadata Manager", "section": "Reconciler", "key": "METADATA_NEW_OBJECT_POLL_SECONDS",
     "label": "New-table check interval (seconds)", "type": "int", "min": 0,
     "requires": ["features.metadata_v2"],
     "help": "How often newly created or altered tables and views are looked for, so tags declared before a "
             "table existed are applied soon after it's created. 0 = only the sweep and explicit reconcile "
             "calls pick them up."},
    {"group": "Metadata Manager", "section": "Reconciler", "key": "METADATA_NEW_OBJECT_POLICY",
     "label": "New objects with no desired state", "type": "select", "options": ["adopt", "ignore"],
     "requires": ["features.metadata_v2"],
     "help": "Adopt (default) = a new table's create-time tags and description become its desired state, so tags "
             "a pipeline sets in CREATE ... TAGS are kept. Ignore = the table stays unmanaged until someone edits "
             "it here. Only applies in the catalogs listed under Catalogs to reconcile."},
    {"group": "Metadata Manager", "section": "Reconciler", "key": "METADATA_EXEMPT_KEYS",
     "label": "Exempt tag keys", "type": "string_list", "add_label": "Add key",
     "requires": ["features.metadata_v2"],
     "help": "Keys the reconciler never sets or removes and that edits can't change. Keep "
             "system.certification_status here: OmniGuard owns it."},
    {"group": "Metadata Manager", "section": "Reconciler", "key": "METADATA_MAX_REMOVALS_PER_RUN",
     "label": "Max removals per run", "type": "int", "min": 0,
     "requires": ["features.metadata_v2"],
     "help": "A run that would remove more tags, or clear more descriptions, than this applies nothing, is marked "
             "paused on the Runs tab, and alerts governance. Guards against an import or adopt gone wrong "
             "stripping tags in bulk. 0 = every removal waits for review, so the reconciler only adds unless you "
             "resume the run."},
    {"group": "Metadata Manager", "section": "Reconciler", "key": "METADATA_APPLY_CONCURRENCY",
     "label": "Statements in parallel", "type": "int", "min": 1, "max": 32,
     "requires": ["features.metadata_v2"],
     "help": "How many tag and description statements the reconciler runs at once, each on a different table. "
             "Higher applies large changes faster but puts more load on the SQL warehouse."},
    {"group": "Metadata Manager", "section": "Reconciler", "key": "METADATA_ORPHAN_RETENTION_DAYS",
     "label": "Keep dropped objects (days)", "type": "int", "min": 1,
     "requires": ["features.metadata_v2"],
     "help": "Desired state for a dropped table is kept this long, and re-applied if the table is recreated. "
             "After that it is removed (still visible in History)."},
    {"group": "Metadata Manager", "section": "Permissions", "key": "METADATA_OWNER_TAG_KEY",
     "label": "Owner tag key", "type": "string", "requires": ["features.metadata_v2"],
     "help": "Members of the group named by this tag may edit the object's tags and description. When the object "
             "has no such tag, its table's, schema's or catalog's applies. Several groups may be listed, "
             "comma-separated. Changing the tag itself always needs approval."},
    {"group": "Metadata Manager", "section": "Permissions", "key": "METADATA_UC_OWNER_IS_AUTHORITY",
     "label": "Unity Catalog owner can edit", "type": "bool", "requires": ["features.metadata_v2"],
     "help": "On = the object's owner in Unity Catalog (a user or group) may also edit it."},
    {"group": "Metadata Manager", "section": "Permissions", "key": "METADATA_EDITOR_ROLES",
     "label": "Editor roles", "type": "string_list", "add_label": "Add role",
     "requires": ["features.metadata_v2"],
     "help": "App roles, besides Platform Admin, that may edit every object. Their sensitive changes still need "
             "approval."},
    {"group": "Metadata Manager", "section": "Permissions", "key": "METADATA_BROWSE_ROLES",
     "label": "Browse roles", "type": "string_list", "add_label": "Add role",
     "requires": ["features.metadata_v2"],
     "help": "App roles that may search and view every object's tags and descriptions. Platform Admins and "
             "editor roles always can. Anyone else sees only the objects they own (by owner tag, or Unity Catalog "
             "owner when that counts); with nothing owned, they can't open it. Blank = admins and editor roles "
             "only."},
    {"group": "Metadata Manager", "section": "Permissions", "key": "METADATA_GOVERNANCE_APPROVER_GROUP",
     "label": "Governance approver group", "type": "string", "requires": ["features.metadata_v2"],
     "help": "Exact display name of the Databricks group that approves sensitive changes and changes to objects "
             "with no owner tag. Blank = Platform Admins only."},
    {"group": "Metadata Manager", "section": "Tag policy", "key": "METADATA_KEY_MODE",
     "label": "Allowed keys", "type": "select", "options": ["open", "known_only"],
     "requires": ["features.metadata_v2"],
     "help": "Open (default) = any key may be set. Known only = only keys listed below."},
    {"group": "Metadata Manager", "section": "Tag policy", "key": "METADATA_KEY_POLICIES",
     "label": "Key policies", "type": "collection", "unique": "key", "add_label": "Add key",
     "requires": ["features.metadata_v2"],
     "help": "Rules per tag key, checked on every preview and save. Applies to: comma-separated catalog, schema, "
             "table, column (blank = all). Allowed values: comma-separated (blank = any). Pattern: a regular "
             "expression the whole value must match. Editor groups: comma-separated; when set, only those groups "
             "and admins may change the key. Sensitive: every change to the key needs approval. Required: "
             "objects of the listed kinds are flagged when the key is missing.",
     "columns": [
         {"key": "key", "label": "Key", "type": "string", "required": True, "placeholder": "classification"},
         {"key": "applies_to", "label": "Applies to", "type": "string", "placeholder": "table, column"},
         {"key": "allowed_values", "label": "Allowed values", "type": "string", "placeholder": "public, internal"},
         {"key": "pattern", "label": "Pattern", "type": "string", "placeholder": "^[a-z_]+$"},
         {"key": "editor_groups", "label": "Editor groups", "type": "string", "placeholder": "data-stewards"},
         {"key": "sensitive", "label": "Sensitive", "type": "bool"},
         {"key": "required", "label": "Required", "type": "bool"},
         {"key": "description", "label": "Description", "type": "string"},
     ]},
    {"group": "Metadata Manager", "section": "Tag policy", "key": "METADATA_CLASSIFICATION_KEY",
     "label": "Classification key", "type": "string", "requires": ["features.metadata_v2"],
     "help": "Lowering or removing this key's value needs approval. Blank = no downgrade check."},
    {"group": "Metadata Manager", "section": "Tag policy", "key": "METADATA_CLASSIFICATION_LEVELS",
     "label": "Classification levels", "type": "string_list", "add_label": "Add level",
     "requires": ["features.metadata_v2"],
     "help": "The classification values from least to most sensitive. Moving a value down this list is a "
             "downgrade."},
    {"group": "Metadata Manager", "section": "Tag policy", "key": "METADATA_PII_KEYS",
     "label": "PII flag keys", "type": "string_list", "add_label": "Add key",
     "requires": ["features.metadata_v2"],
     "help": "Removing one of these tags, or setting it to false, no or 0, needs approval."},
    {"group": "Metadata Manager", "section": "Environments", "key": "METADATA_ENVIRONMENTS",
     "label": "Environments", "type": "collection", "unique": "key", "add_label": "Add environment",
     "requires": ["features.metadata_v2"],
     "help": "Which catalogs belong to which environment. A catalog belongs to the first row that lists it in "
             "Catalogs (comma-separated) or whose pattern matches it. {base} in the pattern also finds the same "
             "object in other environments, so a change can be copied or promoted (sales_dev under {base}_dev "
             "becomes sales_prd under {base}_prd). Approval: sensitive = only sensitive changes need approval; "
             "all = every change in this environment does.",
     "columns": [
         {"key": "key", "label": "Key", "type": "string", "required": True, "placeholder": "dev"},
         {"key": "catalog_pattern", "label": "Catalog pattern", "type": "string", "placeholder": "{base}_dev"},
         {"key": "catalogs", "label": "Catalogs", "type": "string", "placeholder": "sandbox, scratch"},
         {"key": "approval", "label": "Approval", "type": "select", "required": True,
          "options": ["sensitive", "all"]},
     ]},
    {"group": "Metadata Manager", "section": "Limits", "key": "METADATA_MAX_OBJECTS_PER_CHANGE",
     "label": "Max objects per change", "type": "int", "min": 1, "max": 100000,
     "requires": ["features.metadata_v2"],
     "help": "The most objects (tables, columns, schemas, catalogs) one change can touch. A system principal's "
             "own limit can be higher."},
    {"group": "Metadata Manager", "section": "Tag policy", "key": "METADATA_ABAC_CHECK",
     "label": "Check access policies in previews", "type": "bool",
     "requires": ["features.metadata_v2"],
     "help": "A preview reads the column masks and row filters on the changed objects and warns when a tag "
             "they key on changes, since that changes who can see the data. Needs READ METADATA for the "
             "governance service principal; turn off if previews are slow or it can't read them."},
    {"group": "Metadata Manager", "section": "Tag policy", "key": "METADATA_LINT_CHECK",
     "label": "Warn about suspected typos in previews", "type": "bool",
     "requires": ["features.metadata_v2"],
     "help": "A preview compares the tag keys and values you set with the ones already in use and warns about "
             "near-misses (such as `clasification` for `classification`), differences only in capitalisation, "
             "stray spaces and unusual characters. It only warns; it never blocks a change."},
    {"group": "Metadata Manager", "section": "Limits", "key": "METADATA_MIN_REASON_LENGTH",
     "label": "Minimum reason length (direct changes)", "type": "int", "min": 0,
     "requires": ["features.metadata_v2"],
     "help": "Characters required in the reason for a change saved directly. 0 = optional."},
    {"group": "Metadata Manager", "section": "Limits", "key": "METADATA_MIN_JUSTIFICATION_LENGTH",
     "label": "Minimum justification length (approvals)", "type": "int", "min": 0,
     "requires": ["features.metadata_v2"],
     "help": "Characters required in the justification of a change that needs approval."},

    # ===================================================================
    # Control Tower
    # ===================================================================
    # --- Workflow Studio ------------------------------------------------
    {"group": "Workflow Studio", "key": "WORKFLOW_AUTHORING_LOCKED", "label": "Lock workflow authoring",
     "type": "bool",
     "help": "On = no in-place workflow editing; workflows change only via bundle import. Usually locked in prod."},
    {"group": "Workflow Studio", "key": "AGENT_AUTHORING_MAX_ITERATIONS",
     "label": "Assistant max tool iterations", "type": "int", "min": 1,
     "help": "Separate, larger budget for the workflow-authoring assistant. One design turn "
             "spends ~9 calls (research, preview, validate, save, save tests, run tests, fix, "
             "re-save); too low and it stops mid-design after saving."},
    {"group": "Workflow Studio", "section": "Tests", "key": "WORKFLOW_TESTS_ENABLED", "label": "Enable workflow tests",
     "type": "bool",
     "help": "On = admins can run a workflow's test cases from Workflow Studio. Each case starts a real agent "
             "conversation with every mutating tool sandboxed (nothing is provisioned), so it costs model calls. "
             "Off = the Tests tab is read-only."},
    {"group": "Workflow Studio", "section": "Tests", "key": "WORKFLOW_TESTS_AUTO_RUN",
     "label": "Assistant runs tests automatically", "type": "bool",
     "help": "On (default) = after the Workflow Studio assistant writes test cases it runs them and waits for the "
             "verdicts before replying, then fixes what fails — thorough, but a run can take several minutes. "
             "Off = it writes and saves the cases and replies right away; run them yourself with Run all in the "
             "Tests tab. Useful for demos."},
    {"group": "Workflow Studio", "section": "Tests", "key": "WORKFLOW_TEST_CONCURRENCY", "label": "Cases run in parallel",
     "type": "int", "min": 1, "max": 10,
     "help": "How many cases of one 'Run all' execute at the same time. Each is a full agent turn, so raising "
             "this multiplies load on the model endpoint."},
    {"group": "Workflow Studio", "section": "Tests", "key": "WORKFLOW_TEST_TIMEOUT_SECONDS",
     "label": "Per-case timeout (seconds)", "type": "int", "min": 30,
     "help": "Wall-clock cap for one case (agent run plus judge). A case that exceeds it is recorded as an error "
             "rather than holding the run open."},
    {"group": "Workflow Studio", "section": "Tests", "key": "WORKFLOW_TEST_PASS_THRESHOLD",
     "label": "Pass threshold (score)", "type": "int", "min": 0, "max": 100,
     "help": "Judge score at or above which a case counts as passing. The judge also returns its own verdict; "
             "this is the numeric bar applied to it."},
    {"group": "Workflow Studio", "section": "Tests", "key": "WORKFLOW_TEST_RUNS_PER_HOUR",
     "label": "Max cases per admin per hour", "type": "int", "min": 1,
     "help": "Rate limit on this agent-invocation surface, counted per admin across run groups."},
    {"group": "Workflow Studio", "section": "Tests", "key": "WORKFLOW_TESTS_BLOCK_PUBLISH",
     "label": "Block publish on failing tests", "type": "bool",
     "help": "On = a workflow with a failing or never-run enabled case cannot be published. Off (default) = the "
             "publish confirmation warns instead, since the judge is non-deterministic."},

    # --- Context Catalog: document freshness ------------------------------
    {"group": "Context Catalog", "section": "Document freshness", "key": "CONTEXT_CATALOG_STALE_AFTER_DAYS",
     "label": "Stale after (days)", "type": "int", "min": 1,
     "help": "A published document is marked Stale when it hasn't been edited or marked reviewed for this many "
             "days. Drives the Stale badge in the Context Catalog and the Teams reminders. Applies immediately."},
    {"group": "Context Catalog", "section": "Teams reminders", "key": "CONTEXT_CATALOG_STALE_REMINDERS_ENABLED",
     "label": "Send stale-document reminders", "type": "bool",
     "help": "On = on the schedule below, post the stale published documents to a Microsoft Teams channel, "
             "mentioning each owner (the document owner, else the domain's primary owner). Needs the Teams "
             "webhook secret — see Platform Administration. Off (default) = nothing is posted."},
    {"group": "Context Catalog", "section": "Teams reminders", "key": "CONTEXT_CATALOG_STALE_REMINDER_CRON",
     "label": "Reminder schedule", "type": "cron",
     "help": "When reminders are posted (5-field cron, UTC). Default: Mondays 15:00 UTC. Leave BLANK to post only "
             "with Run now in the Context Catalog."},
    {"group": "Context Catalog", "section": "Teams reminders", "key": "CONTEXT_CATALOG_STALE_RENOTIFY_DAYS",
     "label": "Re-remind after (days)", "type": "int", "min": 1,
     "help": "A document that was in a reminder isn't mentioned again until this many days have passed, so a "
             "schedule more frequent than this doesn't repeat itself. Editing or marking it reviewed resets it."},
    {"group": "Context Catalog", "section": "Teams reminders", "key": "CONTEXT_CATALOG_STALE_MAX_DOCS_PER_OWNER",
     "label": "Documents listed per owner", "type": "int", "min": 1, "max": 50,
     "help": "Longer lists end with '…and N more'; the unlisted documents are listed first next time."},

    # --- Context Catalog: Git sync (app/services/context_catalog_git.py) --
    {"group": "Context Catalog", "section": "Git sync", "key": "CONTEXT_CATALOG_GIT_ENABLED",
     "label": "Sync with Git", "type": "bool",
     "help": "On = this environment's catalog syncs with its branch below: saves are committed by the editor "
             "within seconds, and changes merged in Git are imported on the schedule or with Sync from Git. "
             "Needs a GitHub token with contents read/write on the repo (see Platform Administration). "
             "Off (default) = the catalog lives only in this environment's database."},
    {"group": "Context Catalog", "section": "Git sync", "key": "CONTEXT_CATALOG_GIT_REPO",
     "label": "Repository", "type": "string", "placeholder": "my-org/context-catalog",
     "help": "owner/repo, or the repo's https URL (GitHub Enterprise Server hosts work too)."},
    {"group": "Context Catalog", "section": "Git sync", "key": "CONTEXT_CATALOG_GIT_BRANCHES",
     "label": "Branch per environment", "type": "collection", "unique": "environment",
     "add_label": "Add environment",
     "help": "Which branch each environment syncs with. Environment is the deployment's ENVIRONMENT value; "
             "aliases are other spellings of it, comma-separated. Push on = saves in that environment are "
             "committed to the branch. Push off = it only reads the branch and mirrors it, discarding edits "
             "made in the app (stage reads prod this way, so it can never commit to prod). An environment "
             "with no row doesn't sync.",
     "columns": [
         {"key": "environment", "label": "Environment", "type": "string", "required": True, "placeholder": "dev"},
         {"key": "aliases", "label": "Aliases", "type": "string", "placeholder": "development"},
         {"key": "branch", "label": "Branch", "type": "string", "required": True, "placeholder": "dev"},
         {"key": "push", "label": "Push", "type": "bool"},
     ]},
    {"group": "Context Catalog", "section": "Git sync", "key": "CONTEXT_CATALOG_GIT_BASE_PATH",
     "label": "Folder in the repo", "type": "string", "placeholder": "context-catalog",
     "help": "Repo folder holding the catalog: one sub-folder per domain, one markdown file per document. "
             "Blank = the repo root. Changing it (or the repo or branch) starts a fresh first sync."},
    {"group": "Context Catalog", "section": "Git sync", "key": "CONTEXT_CATALOG_GIT_POLL_CRON",
     "label": "Pull schedule", "type": "cron",
     "help": "How often the branch is checked for changes merged in Git (5-field cron, UTC). Saves in the app "
             "are pushed within seconds regardless. Leave BLANK to pull only with Sync from Git."},
    {"group": "Context Catalog", "section": "Git sync", "key": "CONTEXT_CATALOG_DIRECT_EDITING_ENABLED",
     "label": "Allow editing in the app", "type": "bool",
     "help": "Off = documents and domains can't be created, edited, uploaded, imported or deleted in this "
             "environment; admins see a link to the branch and make changes through pull requests instead. "
             "Sync from Git keeps working. On (default) = edit in the app as usual."},

    # --- Preview Features -----------------------------------------------
    {"group": "Preview Features", "key": "PREVIEW_FEATURE_SYNC_CRON", "label": "Sync cron",
     "type": "cron", "requires": ["features.preview_features"],
     "help": "How often previews are re-read from every target workspace and the docs feed, and approved "
             "requests re-verified (5-field cron, UTC). Leave BLANK to sync only when Sync now is clicked."},
    {"group": "Preview Features", "key": "PREVIEW_FEATURE_FEED_URL", "label": "Release-notes feed URL",
     "type": "string", "requires": ["features.preview_features"],
     "help": "The Databricks docs RSS feed used for account-level previews, announcements and docs links. "
             "Use your cloud's feed (aws, azure or gcp in the path). Must be on docs.databricks.com. "
             "Blank skips the feed."},
    {"group": "Preview Features", "key": "PREVIEW_FEATURE_SITEMAP_URL", "label": "Docs sitemap URL",
     "type": "string", "requires": ["features.preview_features"],
     "help": "The docs sitemap used to suggest a documentation page for previews that have none. "
             "Must be on docs.databricks.com. Blank skips suggestions."},
    {"group": "Preview Features", "key": "PREVIEW_FEATURE_SYNC_CONCURRENCY", "label": "Reads in parallel per workspace",
     "type": "int", "min": 1, "max": 32, "requires": ["features.preview_features"],
     "help": "How many preview values are read at the same time from one workspace (each workspace lists "
             "around 150 previews)."},
    {"group": "Preview Features", "key": "PREVIEW_FEATURE_RETIRE_AFTER_SYNCS", "label": "Retire after missed syncs",
     "type": "int", "min": 1, "requires": ["features.preview_features"],
     "help": "A preview no workspace lists for this many syncs in a row is removed from the list (kept "
             "for history), and any open request for it is closed."},
    {"group": "Preview Features", "section": "Approvals", "key": "PREVIEW_FEATURE_WORKSPACE_APPROVER_EMAIL",
     "label": "Workspace preview approvers", "type": "string", "requires": ["features.preview_features"],
     "help": "Who is emailed, and assigned the approval, when someone requests a workspace-level preview. "
             "Comma-separate addresses (a distribution list is fine). Blank = no email; Platform Admins "
             "still see it in Pending Approvals. Takes effect on the next request."},
    {"group": "Preview Features", "section": "Approvals", "key": "PREVIEW_FEATURE_ACCOUNT_APPROVER_EMAIL",
     "label": "Account preview approvers", "type": "string", "requires": ["features.preview_features"],
     "help": "Who is emailed, and assigned the approval, when someone requests an account-level preview. "
             "Comma-separate addresses (a distribution list is fine). Blank = no email; Platform Admins "
             "still see it in Pending Approvals. Takes effect on the next request."},

    # --- Access Rules ----------------------------------------------------
    {"group": "Access Rules", "section": "Environments & export", "key": "RBAC_ENVIRONMENTS",
     "label": "Environments", "type": "collection", "unique": "key", "add_label": "Add environment",
     "help": "One row per environment in the access-rules workbook. The sheet suffix builds that environment's "
             "sheet names from the patterns below (e.g. Dev → UC_Securables_RBAC_Dev). Aliases are other "
             "spellings of the environment, comma-separated. The export path is the folder its CSVs are "
             "written to (a UC Volume or Workspace path); blank = its sheets are not exported. Workspaces "
             "(comma-separated) are the names offered for WorkspaceName, and for SecurableName on workspace and "
             "workspace entitlement rows, on its workspace sheet; Catalogs (comma-separated) are the names "
             "offered for Catalog on its UC sheet. When set, a request or edit can add only those names "
             "(requests are blocked, direct edits warned); blank = typed freely. Self-service "
             "off = requests can't change it. Write privileges off = requests can't add MODIFY or other write "
             "privileges. Service principals off = only groups can be granted. Two-person rule on = a direct "
             "edit becomes a request another approver must approve.",
     "columns": [
         {"key": "key", "label": "Key", "type": "string", "required": True, "placeholder": "dev"},
         {"key": "aliases", "label": "Aliases", "type": "string", "placeholder": "development"},
         {"key": "sheet_suffix", "label": "Sheet suffix", "type": "string", "required": True, "placeholder": "Dev"},
         {"key": "export_path", "label": "Export path", "type": "string",
          "placeholder": "/Volumes/catalog/schema/volume/rbac/dev"},
         {"key": "workspaces", "label": "Workspaces", "type": "string", "placeholder": "ws-dev, ws-dev-analytics"},
         {"key": "catalogs", "label": "Catalogs", "type": "string", "placeholder": "sales_dev, finance_dev"},
         {"key": "self_service", "label": "Self-service", "type": "bool"},
         {"key": "write_privileges", "label": "Write privileges", "type": "bool"},
         {"key": "service_principals", "label": "Service principals", "type": "bool"},
         {"key": "two_person_rule", "label": "Two-person rule", "type": "bool"},
     ]},
    {"group": "Access Rules", "section": "Environments & export", "key": "RBAC_UC_SHEET_PATTERN",
     "label": "UC sheet name pattern", "type": "string",
     "help": "Name of each environment's UC securables sheet. {suffix} is the environment's sheet suffix."},
    {"group": "Access Rules", "section": "Environments & export", "key": "RBAC_WS_SHEET_PATTERN",
     "label": "Workspace sheet name pattern", "type": "string",
     "help": "Name of each environment's workspace securables sheet. {suffix} is the environment's sheet suffix."},
    {"group": "Access Rules", "section": "Environments & export", "key": "RBAC_METASTORE_SHEET",
     "label": "Metastore sheet name", "type": "string",
     "help": "Name of the single sheet that holds metastore-level rules for every environment."},
    {"group": "Access Rules", "section": "Environments & export", "key": "RBAC_METASTORE_EXPORT_PATH",
     "label": "Metastore export path", "type": "string",
     "help": "Folder the metastore sheet's CSV is written to. It belongs to no single environment, so it has "
             "its own path. Blank = not exported."},
    {"group": "Access Rules", "section": "Environments & export", "key": "RBAC_EXPORT_CRON",
     "label": "Export schedule", "type": "cron",
     "help": "When every sheet is written to its CSV (5-field cron, UTC). A run is skipped when nothing has "
             "changed since the last successful export. Leave BLANK to export only with Export now."},
    {"group": "Access Rules", "section": "Environments & export", "key": "RBAC_EXPORT_ON_CHANGE",
     "label": "Also export after every change", "type": "bool",
     "help": "On = an export also runs shortly after any change is recorded, instead of waiting for the schedule."},
    {"group": "Access Rules", "section": "Environments & export", "key": "RBAC_CSV_BOM",
     "label": "Write a byte-order mark", "type": "bool",
     "help": "Off (default) matches Excel's 'CSV UTF-8' export without a byte-order mark. Turn on only if "
             "Compare with a manual export reports a missing byte-order mark."},
    {"group": "Access Rules", "section": "Environments & export", "key": "RBAC_CSV_LINE_ENDING",
     "label": "Line ending", "type": "select", "options": ["crlf", "lf"],
     "help": "CRLF (default) is what Excel writes, after every row including the last. Change only if "
             "Compare with a manual export reports a line-ending difference."},
    {"group": "Access Rules", "section": "Environments & export", "key": "RBAC_EXPORT_ARCHIVE_DAYS",
     "label": "Keep exported files (days)", "type": "int", "min": 0,
     "help": "How long each exported CSV stays downloadable from the Exports tab. 0 = forever."},
    {"group": "Access Rules", "section": "Approvers & access", "key": "RBAC_UC_APPROVER_GROUP",
     "label": "UC securables approver group", "type": "string",
     "help": "Exact display name of the Databricks group that approves requests for UC securables (catalogs, "
             "schemas, tables, views, volumes, functions) and edits those rows directly. Add approvers to this "
             "group directly; membership through a nested group may not be recognised. Blank = Platform Admins "
             "only. Takes effect on the next request."},
    {"group": "Access Rules", "section": "Approvers & access", "key": "RBAC_METASTORE_APPROVER_GROUP",
     "label": "Metastore approver group", "type": "string",
     "help": "Exact display name of the Databricks group that approves requests for metastore-level objects "
             "(connections, external locations, storage credentials) and edits the metastore sheet directly. "
             "Add approvers to this group directly; membership through a nested group may not be recognised. "
             "Blank = Platform Admins only."},
    {"group": "Access Rules", "section": "Approvers & access", "key": "RBAC_WS_APPROVER_GROUP",
     "label": "Workspace securables approver group", "type": "string",
     "help": "Exact display name of the Databricks group that approves requests for workspace assignments, "
             "entitlements and other workspace securables, and edits those rows directly. Add approvers to this "
             "group directly; membership through a nested group may not be recognised. Blank = Platform Admins "
             "only."},
    {"group": "Access Rules", "section": "Approvers & access", "key": "RBAC_VIEWER_ROLES",
     "label": "Viewer roles", "type": "string_list", "add_label": "Add role",
     "help": "App roles that can see every sheet, its history and its exports without editing."},
    {"group": "Access Rules", "section": "Approvers & access", "key": "RBAC_MAX_CHANGES_PER_COMMIT",
     "label": "Max row changes per save", "type": "int", "min": 1, "max": 5000,
     "help": "The most rows a single direct edit, revert or request can change."},
    {"group": "Access Rules", "section": "Approvers & access", "key": "RBAC_MIN_REASON_LENGTH",
     "label": "Minimum reason length (direct edits)", "type": "int", "min": 0,
     "help": "Characters required in the reason for a direct edit or a revert."},
    {"group": "Access Rules", "section": "Policy", "key": "RBAC_MIN_JUSTIFICATION_LENGTH",
     "label": "Minimum justification length (requests)", "type": "int", "min": 0,
     "help": "Characters required in a request's business justification."},
    {"group": "Access Rules", "section": "Policy", "key": "RBAC_ALLOWED_PRIVILEGES",
     "label": "Allowed privileges (allow only)", "type": "collection", "unique": "securable_type",
     "add_label": "Add securable type",
     "help": "The only privileges a request or direct edit can add, per securable type (comma-separated). A "
             "type with no row can't be granted anything new, and a privilege Databricks adds later can't be "
             "granted until a Platform Admin adds it here. The Access Rules grid offers these as its choices. "
             "Removing privileges is always allowed, and existing rows are left as they are, even where they "
             "hold a privilege that isn't listed.",
     "columns": [
         {"key": "securable_type", "label": "Securable type", "type": "select", "required": True,
          "options": list(RBAC_SECURABLE_TYPES)},
         {"key": "privileges", "label": "Privileges", "type": "string", "placeholder": "USE_SCHEMA, SELECT"},
     ]},
    {"group": "Access Rules", "section": "Policy", "key": "RBAC_SYSTEM_GROUPS",
     "label": "System groups", "type": "string_list", "add_label": "Add group",
     "help": "Groups no request or direct edit may add to or remove from any rule (case-insensitive)."},
    {"group": "Access Rules", "section": "Policy", "key": "RBAC_DEFAULT_OWNER",
     "label": "Default owner", "type": "string",
     "help": "Owner written on new UC and metastore rows. Blank = the owner already used on that catalog, "
             "otherwise the sheet's most common owner."},
    {"group": "Access Rules", "section": "Policy", "key": "RBAC_NAMING_RULES",
     "label": "Naming rules", "type": "collection", "add_label": "Add rule",
     "help": "Checks on new or changed values. The pattern is a regular expression the whole value must match; "
             "{env} stands for the row's environment key. Securable types limits the rule (comma-separated, "
             "blank = all). Exceptions are values that always pass (comma-separated). Deny blocks requests and "
             "edits; warn only flags them. Imports are always flagged, never blocked.",
     "columns": [
         {"key": "field", "label": "Field", "type": "select", "required": True,
          "options": ["principal_name", "catalog", "schema_name", "object_name", "workspace_name", "owner"]},
         {"key": "pattern", "label": "Pattern", "type": "string", "required": True,
          "placeholder": "^grp_.*_{env}$"},
         {"key": "securable_types", "label": "Securable types", "type": "string", "placeholder": "schema,view"},
         {"key": "exceptions", "label": "Exceptions", "type": "string"},
         {"key": "severity", "label": "Severity", "type": "select", "required": True,
          "options": ["warn", "deny"]},
         {"key": "message", "label": "Message", "type": "string"},
     ]},
    {"group": "Access Rules", "section": "Import", "key": "RBAC_HEADER_ALIASES",
     "label": "Header aliases", "type": "collection", "add_label": "Add alias",
     "help": "Other header spellings the workbook import should recognise, if a column is ever renamed. The "
             "export always writes the header text captured at import.",
     "columns": [
         {"key": "field", "label": "Field", "type": "select", "required": True,
          "options": ["environment_value", "securable_type", "catalog", "schema_name", "object_name",
                      "workspace_name", "owner", "principal_type", "principal_name", "privilege"]},
         {"key": "header", "label": "Header text", "type": "string", "required": True},
     ]},
    {"group": "Access Rules", "section": "Import", "key": "RBAC_IMPORT_REMOVE_DUPLICATES",
     "label": "Remove duplicate rows on import", "type": "bool",
     "help": "On (default) = a row identical to an earlier row on the same sheet isn't imported; the preview "
             "lists each one removed. Off = identical rows are kept, so the export matches the workbook byte for "
             "byte (Compare with a manual export otherwise shows each removed row as only in the manual file). "
             "Applies to the next preview."},
    {"group": "Access Rules", "section": "Import", "key": "RBAC_ALLOW_RESET",
     "label": "Allow reset and re-import", "type": "bool",
     "help": "On = a Platform Admin can replace every rule with a fresh workbook import. That discards every "
             "change made in the app since the seed import (it can be reverted from History). Keep off in "
             "production."},

    # --- Approvals --------------------------------------------------------
    {"group": "Approvals", "key": "APPROVALS_DENY_SELF_APPROVAL",
     "label": "Block approving your own request", "type": "bool",
     "help": "On (default) = nobody can approve a request they submitted, in any workflow, including Platform "
             "Admins; another approver must. Rejecting your own request (withdrawing it) is still allowed."},

    # ===================================================================
    # Platform
    # ===================================================================
    # --- Target Workspaces & Service Principals -------------------------
    # No-code config for which workspaces the app monitors and the per-env
    # service-principal *key names*. Secret VALUES and scope grants stay in
    # Databricks (create the scope, populate secrets, grant the app SP READ,
    # and grant each SP access to its workspaces) — those cannot be self-served.
    {"group": "Target Workspaces", "key": "TARGET_WORKSPACE_SP_SECRET_SCOPE",
     "label": "Secret scope",
     "type": "string",
     "help": "The single Databricks secret scope for this installation. It holds every target-workspace service principal's credentials. The app's own SP needs READ on this scope. A value here overrides databricks.yml at runtime (no redeploy)."},
    {"group": "Target Workspaces", "key": "collection:target_workspaces",
     "label": "Target workspaces",
     "type": "collection", "unique": "name", "add_label": "Add workspace",
     "help": "Each workspace the app monitors and its service principal. The SP key fields name the client-id/secret keys inside the scope above (never the secret values). Workspaces that share an SP just reference the same key names; leave them blank to use the app's own SP. Adding a row does not grant access — the SP must already be authorized on the workspace in Databricks. Workspace type 'enterprise' turns on the stricter OmniGuard rules (no apps, Genie spaces or Lakebase in enterprise prod); Auto treats a workspace as enterprise only if its name contains 'enterprise'.",
     "columns": [
         {"key": "name", "label": "Name", "type": "string", "required": True,
          "placeholder": "prod-domain-a"},
         {"key": "host", "label": "Host URL", "type": "string", "required": True,
          "placeholder": "https://adb-123....azuredatabricks.net"},
         {"key": "environment", "label": "Environment", "type": "string", "required": True,
          "placeholder": "prod"},
         {"key": "type", "label": "Workspace type", "type": "select",
          "options": ["", "enterprise", "domain"], "placeholder": "Auto (from name)",
          "help": "Enterprise = stricter rules"},
         {"key": "client_id_key", "label": "Client ID secret key", "type": "string",
          "placeholder": "sp_prod_client_id", "help": "Secret key NAME — not the value"},
         {"key": "client_secret_key", "label": "Client secret key", "type": "string",
          "placeholder": "sp_prod_client_secret", "help": "Secret key NAME — not the value"},
     ]},

    # --- Infrastructure (editable part; the read-only deploy-time panel is
    # READONLY_FIELDS, rendered below these on the same page) -------------
    {"group": "Infrastructure", "section": "Timeouts", "key": "yaml:tools.ask_your_data.poll_timeout_seconds",
     "label": "Chat answer timeout (seconds)", "type": "int", "min": 5,
     "help": "How long the chat keeps waiting for an answer that arrives asynchronously — a Genie data "
             "question or a long-running external tool — before it shows a timeout. Each check is a short "
             "request, so this is not a Databricks limit."},
    {"group": "Infrastructure", "section": "Terramate provisioning", "key": "TERRAMATE_API_URL",
     "label": "Terramate API URL", "type": "string",
     "help": "Base URL of the Terramate Provisioning API (Databricks App or local dev URL) that opens "
             "GitOps pull requests for infrastructure changes."},
    {"group": "Infrastructure", "section": "Terramate provisioning", "key": "TERRAMATE_HTTP_TIMEOUT_SECONDS",
     "label": "HTTP timeout (sec)", "type": "int", "min": 1,
     "help": "Timeout in seconds for Terramate API HTTP requests."},
]


# Whole groups that only matter while a capability is on. Merged into every
# field's ``requires`` in the group (on top of any per-field ``requires``).
GROUP_REQUIRES: Dict[str, List[str]] = {
    "Calendar": ["features.calendar"],
    "Metadata Manager": ["features.governance"],
    "Workflow Studio": ["features.workflow_authoring"],
    "Preview Features": ["features.preview_features"],
    "Access Rules": ["features.rbac"],
    "Context Catalog": ["features.context_catalog"],
    # Mappings are only shown in Discover and the agent's data-asset search.
    "Legacy Dashboards": ["features.data_discovery"],
}


# Read-only, deploy-time settings shown for visibility. These are managed via
# databricks.yml (and take effect only on redeploy/restart) or are secret-scope
# names — never editable here. Secret *values* are never included. Rendered
# below the editable fields on the Infrastructure page.
READONLY_FIELDS: List[Dict[str, Any]] = [
    {"group": "Environment", "key": "ENVIRONMENT", "label": "Environment"},
    {"group": "Environment", "key": "DB_SCHEMA", "label": "Postgres schema"},
    {"group": "Environment", "key": "LAKEBASE_CDF_TABLES", "label": "Lakebase CDF tables",
     "help": "Tables given REPLICA IDENTITY FULL at startup so a Lakebase Change Data Feed "
             "on the Postgres schema captures them. \"none\" = off; \"default\" = requests, "
             "events, approvals, enforcement audit, the access-rule ledger and the Metadata "
             "Manager's desired tags, descriptions and change log."},
    {"group": "Databricks", "key": "DATABRICKS_WORKSPACE_URL", "label": "Workspace URL"},
    {"group": "Databricks", "key": "DATABRICKS_HOST", "label": "Databricks host"},
    {"group": "Databricks", "key": "DATABRICKS_WAREHOUSE_ID", "label": "SQL warehouse id"},
    {"group": "Databricks", "key": "DATABRICKS_JOB_CLUSTER_ID", "label": "Job cluster id"},
    {"group": "Databricks", "key": "DATABRICKS_HTTP_TIMEOUT_SECONDS", "label": "SDK HTTP timeout (sec)",
     "type": "int", "min": 0,
     "help": "Per-request timeout applied to every Databricks SDK call. The SDK "
             "has no default, so a stalled connection otherwise hangs forever and "
             "can exhaust the worker pool, freezing the whole app. 0 = SDK default "
             "(unbounded); 60 is a safe value."},
    {"group": "Databricks", "key": "DATABRICKS_RETRY_TIMEOUT_SECONDS", "label": "SDK retry timeout (sec)",
     "type": "int", "min": 0,
     "help": "Max total seconds the SDK will keep retrying a transient failure "
             "before giving up. 0 = SDK default."},
    {"group": "Identity & Email", "key": "IDENTITY_PROVIDER", "label": "Identity provider"},
    {"group": "Identity & Email", "key": "NOTIFICATION_EMAIL_SES_SECRET_SCOPE", "label": "SES IAM secret scope"},
    {"group": "Context Catalog", "key": "CONTEXT_CATALOG_TEAMS_WEBHOOK_SECRET_SCOPE",
     "label": "Teams webhook secret scope",
     "help": "Secret scope holding the Teams Workflows webhook URL for stale-document reminders. "
             "CONTEXT_CATALOG_TEAMS_WEBHOOK_URL in the environment takes precedence (local dev)."},
    {"group": "Context Catalog", "key": "CONTEXT_CATALOG_TEAMS_WEBHOOK_SECRET_KEY",
     "label": "Teams webhook secret key", "help": "Key NAME within the scope above — not the URL."},
    {"group": "Context Catalog", "key": "CONTEXT_CATALOG_GIT_TOKEN_SECRET_KEY",
     "label": "Git sync token secret key",
     "help": "Key NAME, in the GitHub PAT's secret scope (GIT_TOKEN_SECRET_SCOPE), of the token Git sync uses. "
             "Blank = the shared GitHub PAT. CONTEXT_CATALOG_GIT_TOKEN in the environment takes precedence "
             "(local dev)."},
    {"group": "GitOps", "key": "GITOPS_MODE", "label": "GitOps mode"},
    {"group": "GitOps", "key": "INFRA_REPO_URL", "label": "Infra repo URL"},
    {"group": "GitOps", "key": "INFRA_REPO_BRANCH", "label": "Infra repo branch"},
    {"group": "GitOps", "key": "GITHUB_ORG", "label": "GitHub org"},
]

_EDITABLE_BY_KEY = {f["key"]: f for f in EDITABLE_FIELDS}


# Settings that used to be editable and were retired. A leftover DB override
# for one of these is skipped quietly at startup (and dropped from a save),
# rather than failing, so removing a setting never breaks boot or the page.
RETIRED_KEYS = frozenset({
    "yaml:self_service_center",
    "features.self_service",
    "features.finops",
    "features.workflows",
    "ui.tabs.training_upload",
    "yaml:web_search.algolia.app_id",
    "yaml:web_search.algolia.api_key",
    "yaml:web_search.algolia.index_name",
    "yaml:links.genie_full_experience_url",
    "yaml:tools.ask_your_data.default_genie_space_id",
    # Replaced by the RBAC_ALLOWED_PRIVILEGES allow list.
    "RBAC_HARD_DENIED_PRIVILEGES",
    # GitOps tag changes, replaced by the Metadata Manager's desired-state tables (ADR-0006).
    "GOVERNANCE_TAGS_LOCAL_MODE",
    "GOVERNANCE_TAGS_REPO",
    "GOVERNANCE_TAGS_BASE_BRANCH",
    "GOVERNANCE_TAGS_PATH",
    "GOVERNANCE_TAGS_LEDGER_TABLE",
    # Catalog claim markers; Catalogs to reconcile bounds each deployment instead.
    "METADATA_CLAIM_ENABLED",
    "METADATA_CLAIM_TAG_KEY",
    "METADATA_INSTANCE_ID",
})


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------
# The Settings sub-nav mirrors the app sidebar: each section holds pages
# (groups). CLIENT_GROUPS are rendered by the client rather than from fields
# (the capability switches below, the role manager, the legacy dashboard
# mappings and the training-completion upload); "Infrastructure" also shows
# READONLY_FIELDS under its editable fields.
FEATURES_GROUP = "Features & Navigation"
ROLES_GROUP = "Roles & Access"
LEGACY_DASHBOARDS_GROUP = "Legacy Dashboards"
TRAINING_GROUP = "Training"
CLIENT_GROUPS = frozenset({FEATURES_GROUP, ROLES_GROUP, LEGACY_DASHBOARDS_GROUP, TRAINING_GROUP})

SECTIONS: List[Dict[str, Any]] = [
    {"title": "General", "groups": ["Appearance", FEATURES_GROUP, ROLES_GROUP, "Notifications"]},
    {"title": "Discover & Analyze", "groups": ["Agent", "Data Catalog", LEGACY_DASHBOARDS_GROUP]},
    {"title": "Requests & Approvals", "groups": ["Approvals", "Group Management (LMWS)", "App Code Review"]},
    {"title": "Learn & Share", "groups": ["Links & Embedded Apps", "Calendar", TRAINING_GROUP]},
    {"title": "Watch Tower", "groups": ["OmniGuard", "Target Workspaces", "Data Certification", "Metadata Manager",
                                        "Access Rules"]},
    {"title": "Control Tower", "groups": ["Workflow Studio", "Context Catalog", "Preview Features"]},
    {"title": "Platform", "groups": ["Infrastructure"]},
]


# Short "what is this group" blurbs shown under the page heading in the UI.
# Every key must be a group in SECTIONS (enforced by a unit test).
GROUP_DESCRIPTIONS: Dict[str, str] = {
    "Appearance": "How the app presents itself — name, logo, accent colors, and the optional site-wide banner.",
    FEATURES_GROUP: (
        "Turn capabilities on or off and choose what appears in the sidebar, organized the same way as the "
        "sidebar. Turning a capability off disables its pages, API, background jobs, and agent tools, and "
        "hides it from the sidebar. Changes apply on the next page load."
    ),
    ROLES_GROUP: "Who holds which role, and what each role can see and do.",
    "Notifications": "How email is delivered, the base URL used in email links, and what a requester is told when a request is denied.",
    "Agent": "The AI agent's model, limits and guardrails, what it knows about the user up front, where it may look things up on the web, and how it checks for duplicate requests.",
    "Data Catalog": "Which Unity Catalog catalogs are governed and how often the searchable data catalog is refreshed from them.",
    LEGACY_DASHBOARDS_GROUP: (
        "Map dashboards people used before (e.g. in Tableau) to the metric views that replace them. People see "
        "these in Discover under Legacy Dashboards, on each metric view, and when they search by the old "
        "dashboard name. A temporary aid for the migration."
    ),
    "Group Management (LMWS)": (
        "How the app runs LMWS/FWS-API group & user management — natively in-app, or as a Databricks job "
        "against a vendored notebook on serverless or classic compute. Applies to the next run."
    ),
    "App Code Review": "How the automated Databricks App code-review step judges a repository, and its size and time limits.",
    "Approvals": "Rules that apply to every approval, in every workflow.",
    "Access Rules": (
        "The group-to-securable access rules that replace the RBAC workbook: which environments and sheets "
        "exist, where and when each sheet's CSV is written for the Terraform pipeline, which Databricks group "
        "approves and edits each type of rule, and the policy every request and direct edit is checked against."
    ),
    "Links & Embedded Apps": "The Community Links resource page, the external apps embedded (iframed) in the sidebar, and who users contact for day-to-day support. Edits apply immediately.",
    "Calendar": "Where the event calendar's events come from, and how often they are synced.",
    TRAINING_GROUP: (
        "Where course details come from and how people's completions get in. Courses linked to the Databricks "
        "Academy catalog keep their codes, links and durations current on every sync. Completions exported from "
        "your learning system as a CSV update progress on the Training page and satisfy any workflow step that "
        "requires a course."
    ),
    "OmniGuard": (
        "The policy scanner for your target workspaces: who is alerted, when scans run, how hard they work, "
        "and what may be remediated automatically."
    ),
    "Data Certification": "Where data-quality history comes from, how often data contracts are redrafted, and which workspace runs certification.",
    "Metadata Manager": (
        "Tag changes are committed as SQL to a governance repo and applied by that repo's workflow on merge "
        "(or applied directly in local mode). Point the app at the repo, this environment's branch, and the "
        "ledger table it reads to confirm each apply. Column, catalog and schema tags, and catalog and schema "
        "descriptions, can only be changed in local mode."
    ),
    "Workflow Studio": "Whether workflows can be edited in place, the authoring assistant's budget, and how workflow test cases run.",
    "Context Catalog": (
        "When a published document counts as stale, and the Microsoft Teams reminders that ask each "
        "document's owner to update it or mark it reviewed. Git sync keeps each environment's catalog in "
        "a branch of a GitHub repository, so changes can be reviewed and promoted with pull requests."
    ),
    "Preview Features": (
        "How often Databricks Beta and Public Preview features are re-read from your target workspaces, "
        "where announcements and docs links come from, and who is emailed for workspace-level vs "
        "account-level preview requests. The two approval gates themselves live on the Preview Feature "
        "Request workflow in Workflow Studio."
    ),
    "Target Workspaces": (
        "The workspaces the app monitors, mainly scanned by OmniGuard (Data Certification's workspace "
        "setting also refers to them by name), plus the one secret scope holding each workspace's "
        "service-principal credentials. This stores names/coordinates only; the actual secret values "
        "live in the scope and access grants are made in Databricks. Changes apply immediately."
    ),
    "Infrastructure": (
        "Runtime timeouts and the Terramate provisioning connection, followed by the deploy-time settings "
        "managed in databricks.yml."
    ),
}


# ---------------------------------------------------------------------------
# Capabilities (the Features & Navigation page)
# ---------------------------------------------------------------------------
# Each capability is one row on the page, placed under the sidebar section it
# belongs to. ``feature`` is the ``features.<name>`` flag that turns it on/off;
# ``tabs`` are the ``ui.tabs.<name>`` keys for the sidebar items it owns. The
# row's main switch is the feature (or its only tab when it has no feature);
# tabs become nested "show in sidebar" switches. A tab whose capability's
# feature is off is hidden from the sidebar (see ``effective_ui_tabs``).
#
# Any flag or tab not listed here still appears, under "Other".
OTHER_SECTION = "Other"

CAPABILITIES: List[Dict[str, Any]] = [
    # --- Discover & Analyze --------------------------------------------
    {"id": "agent", "section": "Discover & Analyze", "group": "Pages", "label": "Agent (Ask Anything)",
     "tabs": ["home"],
     "description": "The chat agent's sidebar link. The landing page always opens the chat, even when this is hidden."},
    {"id": "data_discovery", "section": "Discover & Analyze", "group": "Pages",
     "label": "Data catalog (View & Search Catalog)", "feature": "data_discovery", "tabs": ["data_discovery"],
     "description": "Browse and search the synced Unity Catalog data catalog, the agent's data-asset search, "
                    "and the background data-asset sync."},
    {"id": "reports", "section": "Discover & Analyze", "group": "Pages", "label": "Reports",
     "tabs": ["reports"], "description": "The Reports page."},
    {"id": "home_data_catalog", "section": "Discover & Analyze", "group": "Home page",
     "label": "Data sections on the home page", "feature": "home_data_catalog"},
    {"id": "enhanced_landing_page", "section": "Discover & Analyze", "group": "Home page",
     "label": "Welcome header", "feature": "enhanced_landing_page"},
    {"id": "ask_your_data", "section": "Discover & Analyze", "group": "Agent capabilities",
     "label": "Ask Your Data (Genie)", "feature": "ask_your_data",
     "tabs": [{"key": "ask_your_data", "label": "Answer in the chat only",
               "help": "Off = the sidebar also shows an external 'Analyze and Explore' link to Databricks Genie."}]},
    {"id": "genie_summarize_answer", "section": "Discover & Analyze", "group": "Agent capabilities",
     "label": "Reword Genie answers", "feature": "genie_summarize_answer"},
    {"id": "run_sql", "section": "Discover & Analyze", "group": "Agent capabilities",
     "label": "Run SQL", "feature": "run_sql"},
    {"id": "user_context", "section": "Discover & Analyze", "group": "Agent capabilities",
     "label": "User context", "feature": "user_context"},
    {"id": "web_search", "section": "Discover & Analyze", "group": "Agent capabilities",
     "label": "Web lookup", "feature": "web_search"},
    {"id": "skills", "section": "Discover & Analyze", "group": "Agent capabilities",
     "label": "Skills", "feature": "skills"},
    {"id": "onboarding_suggestions", "section": "Discover & Analyze", "group": "Agent capabilities",
     "label": "Starter prompts", "feature": "onboarding_suggestions"},
    {"id": "next_step_suggestions", "section": "Discover & Analyze", "group": "Agent capabilities",
     "label": "Suggest next steps", "feature": "next_step_suggestions"},

    # --- Requests & Approvals ------------------------------------------
    {"id": "my_requests", "section": "Requests & Approvals", "label": "My Requests",
     "tabs": ["my_requests"], "description": "The requester's list of their own requests."},
    {"id": "pending_approvals", "section": "Requests & Approvals", "label": "Pending Approvals",
     "tabs": ["pending_approvals"],
     "description": "The approver's queue. The notifications bell still links to it when hidden."},

    # --- Learn & Share --------------------------------------------------
    {"id": "training", "section": "Learn & Share", "label": "Training", "tabs": ["training"],
     "description": "The learner Training page."},
    {"id": "calendar", "section": "Learn & Share", "label": "Event Calendar", "feature": "calendar",
     "tabs": ["event_calendar"]},
    {"id": "templates_assets", "section": "Learn & Share", "label": "Templates & Assets",
     "tabs": ["templates_assets"], "description": "The Templates & Assets page."},
    {"id": "community_links", "section": "Learn & Share", "label": "Community Links",
     "tabs": ["community_links"],
     "description": "The Community Links page. Its content is edited under Links & Embedded Apps."},
    {"id": "guide", "section": "Learn & Share", "label": "Guide", "tabs": ["guide"],
     "description": "The in-app user guide, opened from the account menu. Hiding it removes the Guide link "
                    "in the account menu and the help links on other pages; the page stays reachable at /guide."},

    # --- Watch Tower ----------------------------------------------------
    {"id": "governance", "section": "Watch Tower", "label": "Data governance", "feature": "governance",
     "tabs": [
         {"key": "certification", "label": "Show Data Certification (ODCS)"},
         {"key": "odps", "label": "Show Data Products (ODPS)"},
         {"key": "allowlist", "label": "Show Allowlist"},
     ]},
    {"id": "sentinel", "section": "Watch Tower", "label": "OmniGuard", "feature": "sentinel", "tabs": ["sentinel"]},
    {"id": "rbac", "section": "Watch Tower", "label": "Access Rules", "feature": "rbac", "tabs": ["access_rules"]},
    {"id": "metadata_v2", "section": "Watch Tower", "label": "Metadata Manager", "feature": "metadata_v2",
     "tabs": [{"key": "tag_management", "label": "Show Metadata Manager"}],
     "description": "Desired tags and descriptions kept in the app's database, applied to Unity Catalog by the "
                    "reconciler, with owner-based editing, the metadata API and its agent and MCP tools."},

    # --- Control Tower --------------------------------------------------
    {"id": "admin", "section": "Control Tower", "label": "Admin", "tabs": ["admin"],
     "warning": "Hiding Admin removes the sidebar link to these settings. They stay reachable at /admin/settings."},
    {"id": "context_catalog", "section": "Control Tower", "label": "Context Catalog",
     "feature": "context_catalog", "tabs": ["context_catalog"]},
    {"id": "workflow_studio", "section": "Control Tower", "label": "Workflow Studio",
     "feature": "workflow_authoring", "tabs": ["workflows"]},
    {"id": "tool_registry", "section": "Control Tower", "label": "Tool Registry",
     "feature": "tool_registry", "tabs": ["tool_registry"]},
    {"id": "training_studio", "section": "Control Tower", "label": "Training Studio",
     "feature": "training_admin", "tabs": ["training_admin"]},
    {"id": "preview_features", "section": "Control Tower", "label": "Preview Features",
     "feature": "preview_features", "tabs": ["preview_features"]},
    {"id": "feedback", "section": "Control Tower", "label": "Feedback", "feature": "feedback",
     "tabs": [{"key": "feedback", "label": "Show 'Send feedback' in the account menu",
               "help": "Also shows the Feedback tab in Admin."}]},

    # --- Platform -------------------------------------------------------
    {"id": "core", "section": "Platform", "label": "Core platform tools", "feature": "core",
     "warning": "Keep this on. Off removes the agent's built-in platform tools (workspace and URL checks, "
                "LMWS probes, feedback capture) after the next restart."},
]

CAPABILITY_SECTIONS: List[str] = [
    "Discover & Analyze", "Requests & Approvals", "Learn & Share", "Watch Tower",
    "Control Tower", "Platform", OTHER_SECTION,
]


# Per-feature descriptions surfaced as helper text next to each feature toggle.
# Keyed by the feature flag name. A flag with no entry simply renders without a
# description (safe for flags added later).
FEATURE_DESCRIPTIONS: Dict[str, str] = {
    "core": "Base platform capabilities. Keep this on — turning it off disables the core app experience.",
    "governance": "The data governance pages: data certification (ODCS), data products (ODPS) and the allowlist.",
    "metadata_v2": (
        "Metadata Manager: desired tags and descriptions kept in the app's database and applied to Unity Catalog "
        "by the reconciler, with owner-based editing, approvals for sensitive changes, the metadata API and its "
        "agent and MCP tools. Off = no reconciler runs and the API refuses requests."
    ),
    "data_discovery": "Browse and search the synced Unity Catalog data catalog. Also enables the background data-asset sync.",
    "calendar": "The event calendar page and its background calendar sync.",
    "sentinel": (
        "OmniGuard: policy scans of your target workspaces, safe auto-remediation, and the governance digest. "
        "Off = no scheduled scans and the page is hidden. For manual scans only, leave this on and blank the "
        "scan schedule under OmniGuard settings."
    ),
    "ask_your_data": "\u201cAsk Your Data\u201d — natural-language data questions answered by Databricks Genie.",
    "run_sql": "Lets the agent run read-only SQL it composes itself, on-behalf-of the user, feeding the in-chat charts.",
    "genie_summarize_answer": "Rewords Genie's answer through the agent (adds latency/cost). Off = show Genie's grounded answer verbatim.",
    "context_catalog": "Context Catalog: a curated knowledge base the agent retrieves from.",
    "workflow_authoring": "No-code, database-backed workflow authoring in the admin Workflow Studio.",
    "onboarding_suggestions": "Personalized, clickable starter prompts on the home page at login.",
    "next_step_suggestions": "After each agent reply, show 2\u20134 clickable next steps tailored to the conversation (for example, the options of a question the agent just asked). Clicking one sends it as the user's next message. Off = no chips and no extra instruction in the agent prompt.",
    "home_data_catalog": "The Pinned Items, Data Products, and Datasets sections below the chat on the home / new chat page. Off = a chat-only landing. The data catalog page is unaffected.",
    "user_context": "Tells the agent up front who the user is \u2014 their roles, open requests, pending approvals, and group memberships \u2014 so it asks fewer questions. Cached per user and refreshed in the background.",
    "web_search": "Lets the agent search and cite Databricks documentation (and any approved domains).",
    "feedback": "In-app feedback / feature-request / bug-report capture, triaged in the admin panel.",
    "tool_registry": "Data-driven agent-tool governance: enable tools per surface, set allowed roles, and pick SP/OBO identity.",
    "training_admin": "Admin authoring of Training tracks and courses (the learner Training page is always available).",
    "skills": "Agent Skills the agent can load at runtime from the user's Workspace folder and readable UC Volumes.",
    "preview_features": (
        "Tracks Databricks Beta and Public Preview features across your target workspaces, and lets admins "
        "request turning them on or off through an approval workflow. Off = no sync and the page is hidden."
    ),
    "rbac": (
        "Access Rules: the group-to-securable rules (UC, metastore and workspace) that replace the RBAC "
        "workbook, the access-change request workflow, direct edits by each type's approver group, and the "
        "scheduled CSV export the Terraform pipeline reads. Off = no export, no requests, and the page is hidden."
    ),
    "enhanced_landing_page": "Shows a welcome header (brand title and greeting) above the chat on the landing page. Off = a clean, minimal landing page.",
}

# Descriptions for tabs that land in "Other" (not owned by any capability).
TAB_DESCRIPTIONS: Dict[str, str] = {}


_ACRONYMS = {"sql", "api", "ai", "odcs", "odps", "llm", "mcp", "obo", "ui", "sp"}

# Flag/tab keys whose display name differs from the key itself.
_DISPLAY_NAMES = {"sentinel": "OmniGuard"}


def _prettify(name: str) -> str:
    """Turn a snake_case flag/tab key into a human label (with acronym casing)."""
    if name in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[name]
    words = name.replace("-", " ").replace("_", " ").split()
    return " ".join(w.upper() if w.lower() in _ACRONYMS else w.capitalize() for w in words)


# ---------------------------------------------------------------------------
# Nested-dict helpers (operate in place on the live config dicts)
# ---------------------------------------------------------------------------

def _dig_get(root: Dict[str, Any], path: str) -> Any:
    node: Any = root
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _dig_set(root: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = root
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _feature_flags_config() -> Optional[Dict[str, Any]]:
    """The ``_yaml_config`` dict feature_flags reads (or None).

    feature_flags now imports the same live ``_yaml_config`` object from
    config, so this is the same dict we mutate directly — the mirror writes
    below are harmless no-ops kept defensively in case that ever diverges.
    """
    try:
        import app.core.feature_flags as ff
        return ff._yaml_config
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Coercion + validation
# ---------------------------------------------------------------------------

def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_collection(field: Dict[str, Any], value: Any) -> List[Dict[str, Any]]:
    """Validate + normalize a list-of-rows field against its ``columns`` schema.

    Required columns must be non-empty; empty optional columns are dropped so the
    stored config stays tidy. Unknown columns are ignored. An optional
    ``unique`` column key enforces no duplicate values across rows.
    """
    label = field.get("label", field["key"])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of rows")

    columns = field.get("columns") or []
    unique_key = field.get("unique")
    seen: set = set()
    cleaned: List[Dict[str, Any]] = []

    for idx, row in enumerate(value, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"{label}: row {idx} must be an object")
        out: Dict[str, Any] = {}
        for col in columns:
            ck = col["key"]
            ctype = col.get("type", "string")
            raw = row.get(ck)
            if ctype == "bool":
                cval: Any = _coerce_bool(raw)
            elif ctype == "int":
                if raw in (None, ""):
                    cval = None
                else:
                    try:
                        cval = int(raw)
                    except (TypeError, ValueError):
                        raise ValueError(f"{label}: '{col['label']}' must be an integer (row {idx})")
            elif ctype == "select":
                cval = "" if raw is None else str(raw).strip()
                if cval not in col.get("options", []):
                    raise ValueError(
                        f"{label}: '{col['label']}' must be one of "
                        f"{[o for o in col.get('options', []) if o]} (row {idx})"
                    )
            else:
                cval = "" if raw is None else str(raw).strip()

            is_empty = cval is None or (isinstance(cval, str) and cval == "")
            if col.get("required") and is_empty:
                raise ValueError(f"{label}: '{col['label']}' is required (row {idx})")
            # Keep required cells (even if bool/0) and non-empty optional cells.
            if col.get("required") or not is_empty:
                out[ck] = cval

        if unique_key:
            uval = out.get(unique_key)
            if uval in seen:
                raise ValueError(f"{label}: duplicate '{unique_key}' value '{uval}'")
            seen.add(uval)
        cleaned.append(out)

    return cleaned


def _s(value: Any) -> str:
    """Trimmed string ("" for None)."""
    return "" if value is None else str(value).strip()


def _coerce_string_list(field: Dict[str, Any], value: Any) -> List[str]:
    """A flat list of non-empty, trimmed, de-duplicated strings."""
    label = field.get("label", field["key"])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    out: List[str] = []
    for item in value:
        s = _s(item)
        if s and s not in out:
            out.append(s)
    return out


def _coerce_personas(value: Any) -> List[str]:
    """Normalize an allowed_personas value (list or comma string) to a list."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
    elif isinstance(value, list):
        parts = [_s(p) for p in value]
    else:
        return []
    return [p for p in parts if p]


def _coerce_catalog(field: Dict[str, Any], value: Any) -> Any:
    """Validate + normalize a catalog to the exact shape the frontend consumes.

    Kept lenient (drops blank rows, supplies defaults) but enforces the few
    fields that make an entry usable. Empty optional fields are dropped.
    """
    kind = field.get("kind")
    label = field.get("label", field["key"])

    if kind == "community_links":
        value = value or {}
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object")
        categories = []
        for cat in (value.get("categories") or []):
            if not isinstance(cat, dict):
                continue
            name = _s(cat.get("name"))
            if not name:
                continue
            links = []
            for link in (cat.get("links") or []):
                # Accept structured objects (preferred) or "Title | URL | icon | desc".
                if isinstance(link, str):
                    parts = [p.strip() for p in link.split("|")]
                    link = {
                        "title": parts[0] if len(parts) > 0 else "",
                        "url": parts[1] if len(parts) > 1 else "",
                        "icon": parts[2] if len(parts) > 2 else "",
                        "description": parts[3] if len(parts) > 3 else "",
                    }
                if not isinstance(link, dict):
                    continue
                lt = _s(link.get("title"))
                lu = _s(link.get("url"))
                if not lt or not lu:
                    continue
                lrow: Dict[str, Any] = {"title": lt, "url": lu}
                licon = _s(link.get("icon"))
                ldesc = _s(link.get("description"))
                if licon:
                    lrow["icon"] = licon
                if ldesc:
                    lrow["description"] = ldesc
                links.append(lrow)
            cat_out: Dict[str, Any] = {"name": name, "links": links}
            icon = _s(cat.get("icon"))
            if icon:
                cat_out["icon"] = icon
            categories.append(cat_out)
        return {"enabled": _coerce_bool(value.get("enabled", True)), "categories": categories}

    if kind == "embedded_apps":
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"{label} must be a list")
        apps = []
        seen_ids: set = set()
        for raw in value:
            if not isinstance(raw, dict):
                continue
            url = _s(raw.get("url"))
            app_id = _s(raw.get("id")) or _s(raw.get("title"))
            if not url or not app_id:
                continue
            if app_id in seen_ids:
                raise ValueError(f"{label}: duplicate id '{app_id}'")
            seen_ids.add(app_id)
            app: Dict[str, Any] = {"id": app_id, "url": url, "title": _s(raw.get("title")) or app_id}
            for opt in ("icon", "group", "description"):
                v = _s(raw.get(opt))
                if v:
                    app[opt] = v
            personas = _coerce_personas(raw.get("allowed_personas"))
            if personas:
                app["allowed_personas"] = personas
            apps.append(app)
        return apps

    raise ValueError(f"Unknown catalog kind: {kind}")


def _coerce(field: Dict[str, Any], value: Any) -> Any:
    ftype = field.get("type", "string")
    if ftype == "collection":
        return _coerce_collection(field, value)
    if ftype == "string_list":
        return _coerce_string_list(field, value)
    if ftype == "catalog":
        return _coerce_catalog(field, value)
    if ftype == "bool":
        return _coerce_bool(value)
    if ftype == "int":
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field['key']} must be an integer")
        if "min" in field and ivalue < field["min"]:
            raise ValueError(f"{field['label']} must be >= {field['min']}")
        if "max" in field and ivalue > field["max"]:
            raise ValueError(f"{field['label']} must be <= {field['max']}")
        return ivalue
    if ftype == "select":
        sval = "" if value is None else str(value)
        options = field.get("options") or []
        if options and sval not in options:
            raise ValueError(f"{field.get('label', field['key'])} must be one of: {', '.join(options)}")
        return sval
    if ftype == "cron":
        # Blank is allowed and disables the schedule; a non-blank value must be a
        # valid 5-field cron expression so a typo can't silently break a schedule.
        sval = "" if value is None else str(value).strip()
        if sval:
            from croniter import croniter

            if not croniter.is_valid(sval):
                raise ValueError(
                    f"{field.get('label', field['key'])} is not a valid cron expression "
                    "(expected 5 fields, e.g. '*/30 * * * *'). Leave blank to disable."
                )
        return sval
    # string / color / textarea
    return "" if value is None else str(value)


def _validate_key(key: str) -> Dict[str, Any]:
    """Resolve a storage key to its field spec, allowing dynamic groups."""
    if key in _EDITABLE_BY_KEY:
        return _EDITABLE_BY_KEY[key]
    if key in RETIRED_KEYS:
        raise ValueError(f"Setting {key} has been retired and is no longer editable")
    if key.startswith("features.") or key.startswith("ui.tabs."):
        # Dynamic bool toggles — only accept keys that already exist in config
        # so we don't let arbitrary flags be invented.
        name = key.split(".")[-1]
        if key.startswith("features."):
            known = (_yaml_config.get("features") or {})
        else:
            known = ((_yaml_config.get("ui") or {}).get("tabs") or {})
        if name in known:
            return {"key": key, "type": "bool"}
    raise ValueError(f"Unknown or non-editable setting: {key}")


# ---------------------------------------------------------------------------
# Apply / read
# ---------------------------------------------------------------------------

# Cron settings whose scheduler caches a "next run" time. When the expression is
# edited we invalidate that cache so the new schedule is honored on the very next
# poll cycle instead of only after the currently-scheduled run fires. Maps the
# setting key to the (module, module-global) holding the cached next-run.
_CRON_SCHEDULE_TARGETS: Dict[str, tuple] = {
    "ENFORCEMENT_SENTINEL_CRON": ("app.workers.poller", "_next_sentinel_time"),
    "DATA_ASSET_SYNC_CRON": ("app.workers.tasks.sync_data_assets", "_next_sync_time"),
    "EVENT_SYNC_CRON": ("app.workers.tasks.sync_calendar", "_next_sync_time"),
    "CONTRACT_SYNC_CRON": ("app.workers.tasks.sync_contracts", "_next_contract_sync_time"),
    "PREVIEW_FEATURE_SYNC_CRON": ("app.services.preview_features.sync", "_next_preview_sync_time"),
    "RBAC_EXPORT_CRON": ("app.services.rbac.csv_export", "_next_export_time"),
    "METADATA_RECONCILE_CRON": ("app.services.metadata.reconciler", "_next_sweep_time"),
    "TRAINING_ACADEMY_SYNC_CRON": ("app.services.training_catalog", "_next_sync_time"),
    "CONTEXT_CATALOG_STALE_REMINDER_CRON": ("app.workers.tasks.context_catalog_reminders", "_next_reminder_time"),
    "CONTEXT_CATALOG_GIT_POLL_CRON": ("app.services.context_catalog_git", "_next_poll_time"),
}


def _reset_cron_schedule(key: str) -> None:
    """Invalidate a scheduler's cached next-run so an edited cron applies now.

    Only touches modules already imported (the poller thread is running by the
    time settings are edited); a not-yet-imported module has no cached value to
    clear, and would recompute correctly on first use anyway.
    """
    target = _CRON_SCHEDULE_TARGETS.get(key)
    if not target:
        return
    import sys

    mod_name, attr = target
    mod = sys.modules.get(mod_name)
    if mod is not None:
        try:
            setattr(mod, attr, None)
            logger.info("Cron schedule '%s' changed. The next run will be recomputed immediately.", key)
        except Exception as e:  # noqa: BLE001 - best effort; applies after next fire regardless
            logger.debug("Could not reset cron schedule cache for %s: %s", key, e)


def _apply(key: str, coerced: Any) -> None:
    """Apply a coerced value to the live in-process config so it takes effect."""
    if key == "collection:target_workspaces":
        # Whole-list replacement; workspaces.py reads this on every call.
        _yaml_config["target_workspaces"] = coerced
        return
    if key.startswith("features."):
        name = key[len("features."):]
        features = _yaml_config.setdefault("features", {})
        features[name] = coerced
        ff = _feature_flags_config()
        if ff is not None:
            ff.setdefault("features", {})[name] = coerced
        return
    if key.startswith("ui.tabs."):
        name = key[len("ui.tabs."):]
        ui = _yaml_config.setdefault("ui", {})
        ui.setdefault("tabs", {})[name] = coerced
        return
    if key.startswith("yaml:"):
        path = key[len("yaml:"):]
        _dig_set(_yaml_config, path, coerced)
        if path.startswith("tools."):
            ff = _feature_flags_config()
            if ff is not None:
                _dig_set(ff, path, coerced)
        return
    # Plain Settings attribute.
    setattr(settings, key, coerced)
    _reset_cron_schedule(key)


def _current_value(key: str) -> Any:
    if key == "collection:target_workspaces":
        return _yaml_config.get("target_workspaces") or []
    if key.startswith("features."):
        return (_yaml_config.get("features") or {}).get(key[len("features."):])
    if key.startswith("ui.tabs."):
        return ((_yaml_config.get("ui") or {}).get("tabs") or {}).get(key[len("ui.tabs."):])
    if key.startswith("yaml:"):
        return _dig_get(_yaml_config, key[len("yaml:"):])
    return getattr(settings, key, None)


# ---------------------------------------------------------------------------
# Capabilities + requirements
# ---------------------------------------------------------------------------

def _features_cfg() -> Dict[str, Any]:
    return _yaml_config.get("features") or {}


def _tabs_cfg() -> Dict[str, Any]:
    return (_yaml_config.get("ui") or {}).get("tabs") or {}


def _capability_tabs(cap: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A capability's tabs as ``{name, label, help}`` dicts (spec allows bare names)."""
    out = []
    for t in cap.get("tabs") or []:
        if isinstance(t, str):
            out.append({"name": t, "label": "Show in sidebar", "help": ""})
        else:
            out.append({"name": t["key"], "label": t.get("label") or "Show in sidebar", "help": t.get("help", "")})
    return out


def effective_ui_tabs() -> Dict[str, Any]:
    """``ui.tabs`` as the sidebar should apply them.

    A tab owned by a capability whose feature flag is off is reported hidden,
    so turning a capability off also removes its sidebar item and route. The
    stored tab value is untouched, so turning the feature back on restores it.
    """
    tabs = dict(_tabs_cfg())
    features = _features_cfg()
    for cap in CAPABILITIES:
        feature = cap.get("feature")
        if not feature or feature not in features or _coerce_bool(features[feature]):
            continue
        for t in _capability_tabs(cap):
            if t["name"] in tabs:
                tabs[t["name"]] = False
    return tabs


def capability_state() -> List[Dict[str, Any]]:
    """Rows for the Features & Navigation page, in display order.

    Flags/tabs missing from the live config are dropped from their capability
    (and a capability left with nothing is skipped). Every flag or tab that no
    capability claims is appended under ``OTHER_SECTION``.
    """
    features = _features_cfg()
    tabs_cfg = _tabs_cfg()
    claimed_features: set = set()
    claimed_tabs: set = set()
    rows: List[Dict[str, Any]] = []

    for cap in CAPABILITIES:
        feature = cap.get("feature")
        if feature and feature not in features:
            feature = None
        tabs = [t for t in _capability_tabs(cap) if t["name"] in tabs_cfg]
        if not feature and not tabs:
            continue
        if feature:
            claimed_features.add(feature)
            primary = f"features.{feature}"
            nested = tabs
        else:
            primary = f"ui.tabs.{tabs[0]['name']}"
            nested = tabs[1:]
        claimed_tabs.update(t["name"] for t in tabs)
        rows.append({
            "id": cap["id"],
            "section": cap["section"],
            "group": cap.get("group", ""),
            "label": cap["label"],
            "description": cap.get("description") or FEATURE_DESCRIPTIONS.get(feature or "", ""),
            "warning": cap.get("warning", ""),
            "primary": primary,
            "feature": f"features.{feature}" if feature else None,
            "tabs": [{"key": f"ui.tabs.{t['name']}", "label": t["label"], "help": t["help"]} for t in nested],
            "keys": [primary] + [f"ui.tabs.{t['name']}" for t in nested],
        })

    for name in sorted(n for n in features if n not in claimed_features):
        rows.append({
            "id": f"feature:{name}", "section": OTHER_SECTION, "group": "", "label": _prettify(name),
            "description": FEATURE_DESCRIPTIONS.get(name, ""), "warning": "",
            "primary": f"features.{name}", "feature": f"features.{name}", "tabs": [],
            "keys": [f"features.{name}"],
        })
    for name in sorted(n for n in tabs_cfg if n not in claimed_tabs):
        rows.append({
            "id": f"tab:{name}", "section": OTHER_SECTION, "group": "",
            "label": f"{_prettify(name)} (navigation tab)",
            "description": TAB_DESCRIPTIONS.get(name, ""), "warning": "",
            "primary": f"ui.tabs.{name}", "feature": None, "tabs": [],
            "keys": [f"ui.tabs.{name}"],
        })
    return rows


def _field_requires(field: Dict[str, Any]) -> List[str]:
    """Group-level plus per-field requirements, de-duplicated, in order."""
    out: List[str] = []
    for key in GROUP_REQUIRES.get(field.get("group", ""), []) + list(field.get("requires") or []):
        if key not in out:
            out.append(key)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_overrides(db: Session) -> int:
    """Apply every persisted override to the live config. Call once at startup.

    Returns the number of overrides applied. Never raises — a bad row is logged
    and skipped so a single malformed override can't block boot. Rows for
    retired settings (``RETIRED_KEYS``) are left in the table but ignored.
    """
    from app.db.app_setting import AppSettingModel

    applied = 0
    try:
        rows = db.query(AppSettingModel).all()
    except Exception as e:  # noqa: BLE001 - table may not exist yet on first boot
        logger.warning("Settings override load skipped: %s", e)
        return 0

    retired: List[str] = []
    for row in rows:
        if row.key in RETIRED_KEYS:
            retired.append(row.key)
            continue
        try:
            field = _validate_key(row.key)
            _apply(row.key, _coerce(field, row.value))
            applied += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Skipping invalid settings override %s: %s", row.key, e)

    if retired:
        logger.info(
            "Ignoring %d settings override(s) for retired settings: %s",
            len(retired), ", ".join(sorted(retired)),
        )
    if applied:
        logger.info("Applied %d settings override(s) from the database.", applied)
    return applied


def set_many(db: Session, changes: Dict[str, Any], updated_by: Optional[str] = None) -> Dict[str, Any]:
    """Validate, apply live, and persist a batch of overrides. Returns new state.

    Retired keys (e.g. from a browser tab opened before an upgrade) are dropped
    rather than failing the whole save.
    """
    from app.db.app_setting import AppSettingModel

    retired = sorted(k for k in changes if k in RETIRED_KEYS)
    if retired:
        logger.info("Ignoring retired setting(s) in save: %s", ", ".join(retired))
        changes = {k: v for k, v in changes.items() if k not in RETIRED_KEYS}

    # Validate + coerce everything first so a bad value fails the whole batch
    # (no partial application).
    coerced: Dict[str, Any] = {}
    for key, value in changes.items():
        field = _validate_key(key)
        coerced[key] = _coerce(field, value)

    for key, value in coerced.items():
        _apply(key, value)
        row = db.query(AppSettingModel).filter(AppSettingModel.key == key).first()
        if row is None:
            db.add(AppSettingModel(key=key, value=value, updated_by=updated_by))
        else:
            row.value = value
            row.updated_by = updated_by
    db.commit()
    return get_state()


def get_state() -> Dict[str, Any]:
    """Return the full editable spec + current values + read-only fields.

    Feature flags and navigation tabs are emitted as dynamic bool fields in the
    Features & Navigation group, built from the current configuration so
    newly-added flags appear automatically; ``capabilities`` says how the page
    arranges them. Each field carries its resolved ``requires`` list.
    """
    fields: List[Dict[str, Any]] = []
    for f in EDITABLE_FIELDS:
        fields.append({**f, "requires": _field_requires(f), "value": _current_value(f["key"])})

    for name, val in sorted(_features_cfg().items()):
        fields.append({
            "group": FEATURES_GROUP, "key": f"features.{name}", "label": _prettify(name),
            "type": "bool", "value": _coerce_bool(val), "requires": [],
            "help": FEATURE_DESCRIPTIONS.get(name, ""),
        })

    for name, val in sorted(_tabs_cfg().items()):
        fields.append({
            "group": FEATURES_GROUP, "key": f"ui.tabs.{name}", "label": _prettify(name),
            "type": "bool", "value": _coerce_bool(val), "requires": [],
            "help": TAB_DESCRIPTIONS.get(name, ""),
        })

    readonly = [{**f, "value": getattr(settings, f["key"], "")} for f in READONLY_FIELDS]

    return {
        "fields": fields,
        "readonly": readonly,
        "sections": SECTIONS,
        "group_descriptions": GROUP_DESCRIPTIONS,
        "group_requires": GROUP_REQUIRES,
        "capabilities": capability_state(),
        "capability_sections": CAPABILITY_SECTIONS,
    }
