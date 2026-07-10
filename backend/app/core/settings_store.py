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
two dynamic groups (feature flags + navigation tabs). Everything the API and
the frontend render is derived from here.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings, _yaml_config

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

EDITABLE_FIELDS: List[Dict[str, Any]] = [
    # --- Branding & Appearance ------------------------------------------
    {"group": "Branding & Appearance", "key": "BRAND_NAME", "label": "Brand name",
     "type": "string", "help": "Display name shown in the header, page title, and emails."},
    {"group": "Branding & Appearance", "key": "BRAND_SHORT_NAME", "label": "Short name",
     "type": "string", "help": "Compact identifier used for the header logo label and generated slugs."},
    {"group": "Branding & Appearance", "key": "BRAND_LOGO_URL", "label": "Logo URL",
     "type": "string", "help": "URL of your wordmark/logo. Leave blank to show the name only."},
    {"group": "Branding & Appearance", "key": "BRAND_COLOR_PRIMARY", "label": "Primary color", "type": "color"},
    {"group": "Branding & Appearance", "key": "BRAND_COLOR_SECONDARY", "label": "Secondary color", "type": "color"},
    {"group": "Branding & Appearance", "key": "BRAND_COLOR_INFO", "label": "Info color", "type": "color"},
    {"group": "Branding & Appearance", "key": "BRAND_COLOR_ALERT", "label": "Alert color", "type": "color"},
    {"group": "Branding & Appearance", "key": "BRAND_COLOR_WARNING", "label": "Warning color", "type": "color"},
    {"group": "Branding & Appearance", "key": "BRAND_COLOR_SUCCESS", "label": "Success color", "type": "color"},

    # --- Notifications & Governance -------------------------------------
    {"group": "Notifications & Governance", "key": "GOVERNANCE_EMAIL_GROUP", "label": "Governance admin recipients",
     "type": "string",
     "help": "Who receives Enforcement Sentinel alerts + the daily digest. Comma-separate multiple addresses."},
    {"group": "Notifications & Governance", "key": "APP_BASE_URL", "label": "App base URL (for email links)",
     "type": "string",
     "help": "Public URL of this app, e.g. https://your-app.databricksapps.com. Used for the 'Review' button in governance emails. Blank omits the button."},
    {"group": "Notifications & Governance", "key": "ENFORCEMENT_DIGEST_HOUR_LOCAL", "label": "Daily digest hour (0-23)",
     "type": "int", "min": 0, "max": 23,
     "help": "Local hour the once-per-day governance digest is sent (anchored to the timezone below)."},
    {"group": "Notifications & Governance", "key": "ENFORCEMENT_DIGEST_TIMEZONE", "label": "Digest timezone",
     "type": "string", "help": "IANA timezone the digest hour is evaluated in, e.g. America/Los_Angeles."},
    {"group": "Notifications & Governance", "key": "ENFORCEMENT_SENTINEL_STALE_MINUTES", "label": "Stale-run threshold (min)",
     "type": "int", "min": 1,
     "help": "A stuck sentinel run older than this no longer blocks the schedule."},
    {"group": "Notifications & Governance", "key": "SENTINEL_SCAN_CONCURRENCY", "label": "Sentinel scan concurrency",
     "type": "int", "min": 1,
     "help": "Max concurrent units of work during a sentinel scan. 1 = fully serialized."},

    # --- Agent ----------------------------------------------------------
    {"group": "Agent", "key": "AGENT_MAX_ITERATIONS", "label": "Max tool iterations",
     "type": "int", "min": 1, "help": "Max reasoning/tool loops the agent runs per turn."},
    {"group": "Agent", "key": "AGENT_TIMEOUT_SECONDS", "label": "Turn timeout (seconds)",
     "type": "int", "min": 1, "help": "Wall-clock cap for a single agent turn."},
    {"group": "Agent", "key": "AGENT_MAX_TOOL_OUTPUT_CHARS", "label": "Max tool output (chars)",
     "type": "int", "min": 1000, "help": "Per-tool output cap so one chatty tool can't blow the context window."},
    {"group": "Agent", "key": "AGENT_TOOL_OPA_ENFORCE", "label": "Enforce agent-tool OPA policy",
     "type": "bool", "help": "On = deny/approval gates halt mutating tools. Off = shadow (log only)."},
    {"group": "Agent", "key": "WORKFLOW_AUTHORING_LOCKED", "label": "Lock workflow authoring",
     "type": "bool",
     "help": "On = no in-place workflow editing; workflows change only via bundle import. Usually locked in prod."},

    # --- Data & AI ------------------------------------------------------
    {"group": "Data & AI", "key": "yaml:links.genie_full_experience_url", "label": "Genie full-experience URL",
     "type": "string", "help": "Deep link surfaced in Ask Your Data. Blank falls back to the workspace /one entry."},
    {"group": "Data & AI", "key": "yaml:tools.ask_your_data.default_genie_space_id", "label": "Default Genie space id",
     "type": "string", "help": "Pin all Ask Your Data traffic to one curated Genie Space. Blank = ask everything."},
    {"group": "Data & AI", "key": "yaml:tools.ask_your_data.poll_timeout_seconds", "label": "Genie poll timeout (seconds)",
     "type": "int", "min": 5, "help": "How long the chat polls for a Genie answer before surfacing a timeout."},

    # --- System Banner --------------------------------------------------
    {"group": "System Banner", "key": "yaml:banner.active", "label": "Show banner",
     "type": "bool", "help": "Display the banner at the top of every page for all users."},
    {"group": "System Banner", "key": "yaml:banner.type", "label": "Banner style",
     "type": "select", "options": ["info", "warning", "alert", "success"],
     "help": "info = blue, warning = yellow, alert = red, success = green."},
    {"group": "System Banner", "key": "yaml:banner.message", "label": "Banner message",
     "type": "textarea", "help": "The message shown in the banner. Keep it short; shown verbatim."},

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
     "help": "Each workspace the app monitors and its service principal. The SP key fields name the client-id/secret keys inside the scope above (never the secret values). Workspaces that share an SP just reference the same key names; leave them blank to use the app's own SP. Adding a row does not grant access — the SP must already be authorized on the workspace in Databricks.",
     "columns": [
         {"key": "name", "label": "Name", "type": "string", "required": True,
          "placeholder": "prod-domain-a"},
         {"key": "host", "label": "Host URL", "type": "string", "required": True,
          "placeholder": "https://adb-123....azuredatabricks.net"},
         {"key": "environment", "label": "Environment", "type": "string", "required": True,
          "placeholder": "prod"},
         {"key": "client_id_key", "label": "Client ID secret key", "type": "string",
          "placeholder": "sp_prod_client_id", "help": "Secret key NAME — not the value"},
         {"key": "client_secret_key", "label": "Client secret key", "type": "string",
          "placeholder": "sp_prod_client_secret", "help": "Secret key NAME — not the value"},
         {"key": "token_key", "label": "Token key", "type": "string", "placeholder": "(optional)",
          "help": "Secret key NAME — not the value"},
     ]},

    # --- Web Lookup -----------------------------------------------------
    {"group": "Web Lookup", "key": "yaml:web_search.allowed_domains", "label": "Allowed domains",
     "type": "string_list", "add_label": "Add domain",
     "help": "Domains the agent may fetch pages from (suffix-matched). docs.databricks.com is always allowed."},
    {"group": "Web Lookup", "key": "yaml:web_search.sitemaps", "label": "Sitemaps",
     "type": "string_list", "add_label": "Add sitemap",
     "help": "Sitemap URLs used for keyless doc discovery. Each must be on an allowed domain."},
    {"group": "Web Lookup", "key": "yaml:web_search.algolia.app_id", "label": "Algolia app id",
     "type": "string", "help": "Optional. Public DocSearch app id. Set all three Algolia fields for full-text search; otherwise sitemap discovery is used."},
    {"group": "Web Lookup", "key": "yaml:web_search.algolia.api_key", "label": "Algolia API key",
     "type": "string", "help": "Optional. Public, search-only DocSearch key (safe to store — not a secret)."},
    {"group": "Web Lookup", "key": "yaml:web_search.algolia.index_name", "label": "Algolia index",
     "type": "string", "help": "Optional. DocSearch index name."},
    {"group": "Web Lookup", "key": "yaml:web_search.max_results", "label": "Max results",
     "type": "int", "min": 1, "help": "Max search hits returned per query."},
    {"group": "Web Lookup", "key": "yaml:web_search.fetch_timeout_seconds", "label": "Fetch timeout (seconds)",
     "type": "int", "min": 1, "help": "Per-request HTTP timeout when reading a page."},
    {"group": "Web Lookup", "key": "yaml:web_search.max_fetch_chars", "label": "Max fetch chars",
     "type": "int", "min": 1000, "help": "Cap on extracted page text handed to the model."},

    # --- Catalogs & Content ---------------------------------------------
    {"group": "Catalogs & Content", "key": "yaml:self_service_center", "label": "Self-Service Center",
     "type": "catalog", "kind": "self_service", "add_label": "Add category",
     "help": "The catalog of quick-action cards shown on the landing page. Each category holds cards that either seed the Assistant with a prompt or navigate to an in-app route."},
    {"group": "Catalogs & Content", "key": "yaml:community_links", "label": "Community Links",
     "type": "catalog", "kind": "community_links", "add_label": "Add category",
     "help": "The Community Links page — categories of curated external resources and tools."},
    {"group": "Catalogs & Content", "key": "yaml:embedded_apps", "label": "Embedded Apps",
     "type": "catalog", "kind": "embedded_apps", "add_label": "Add app",
     "help": "External web apps surfaced inside this app via an iframe. Each adds a sidebar link opening at /embedded/<id>. Note: targets that send X-Frame-Options/CSP frame-ancestors may render blank."},
]


# Read-only, deploy-time settings shown for visibility. These are managed via
# databricks.yml (and take effect only on redeploy/restart) or are secret-scope
# names — never editable here. Secret *values* are never included.
READONLY_FIELDS: List[Dict[str, Any]] = [
    {"group": "Environment", "key": "ENVIRONMENT", "label": "Environment"},
    {"group": "Environment", "key": "DB_SCHEMA", "label": "Postgres schema"},
    {"group": "Databricks", "key": "DATABRICKS_WORKSPACE_URL", "label": "Workspace URL"},
    {"group": "Databricks", "key": "DATABRICKS_HOST", "label": "Databricks host"},
    {"group": "Databricks", "key": "DATABRICKS_WAREHOUSE_ID", "label": "SQL warehouse id"},
    {"group": "Databricks", "key": "DATABRICKS_JOB_CLUSTER_ID", "label": "Job cluster id"},
    {"group": "Databricks", "key": "MODEL_SERVING_AGENT_LLM_ENDPOINT", "label": "Model serving endpoint"},
    {"group": "Databricks", "key": "AI_GATEWAY_ENDPOINT", "label": "AI Gateway endpoint"},
    {"group": "Databricks", "key": "DATA_QUALITY_TABLE", "label": "Data quality table"},
    {"group": "Identity & Email", "key": "IDENTITY_PROVIDER", "label": "Identity provider"},
    {"group": "Identity & Email", "key": "NOTIFICATION_EMAIL_PROVIDER", "label": "Email provider"},
    {"group": "Identity & Email", "key": "NOTIFICATION_EMAIL_SES_REGION", "label": "SES region"},
    {"group": "Identity & Email", "key": "NOTIFICATION_EMAIL_SES_SOURCE", "label": "SES source address"},
    {"group": "Scheduling", "key": "EVENT_SYNC_CRON", "label": "Calendar sync cron"},
    {"group": "Scheduling", "key": "DATA_ASSET_SYNC_CRON", "label": "Data asset sync cron"},
    {"group": "Scheduling", "key": "ENFORCEMENT_SENTINEL_CRON", "label": "Sentinel cron"},
    {"group": "GitOps", "key": "GITOPS_MODE", "label": "GitOps mode"},
    {"group": "GitOps", "key": "INFRA_REPO_URL", "label": "Infra repo URL"},
    {"group": "GitOps", "key": "INFRA_REPO_BRANCH", "label": "Infra repo branch"},
    {"group": "GitOps", "key": "GITHUB_ORG", "label": "GitHub org"},
]

_EDITABLE_BY_KEY = {f["key"]: f for f in EDITABLE_FIELDS}


# Short "what is this group" blurbs shown under the section heading in the UI.
GROUP_DESCRIPTIONS: Dict[str, str] = {
    "Branding & Appearance": "How the app presents itself — display name, logo, and the accent colors used across the UI and emails.",
    "Features": (
        "A feature flag turns an entire capability area of the app on or off. Disabling one hides its "
        "UI, its API, and the agent tools it powers — a clean way to tailor the app to what your org "
        "actually uses. Changes apply immediately for new page loads."
    ),
    "Navigation": (
        "Controls which items appear in the sidebar and top navigation. Hiding a tab only removes it "
        "from the menu — it does not disable the underlying capability (use Features for that)."
    ),
    "Notifications & Governance": "Where governance alerts go and when the daily Enforcement Sentinel digest is sent.",
    "Agent": "Guardrails and behavior for the AI agent — iteration/timeout limits, tool-policy enforcement, and authoring locks.",
    "Data & AI": "Data-answer experience: Genie deep links and Ask Your Data behavior.",
    "System Banner": "A site-wide message shown at the top of every page — handy for maintenance windows, policy notices, or outages. Turn it off to hide it entirely.",
    "Target Workspaces": (
        "One secret scope per installation, plus the workspaces the app monitors — each with its own "
        "service-principal key names inline. This stores names/coordinates only; the actual secret values "
        "live in the scope and access grants are made in Databricks. Changes apply immediately."
    ),
    "Web Lookup": (
        "Controls the agent's documentation search & fetch tools: which domains it may read, the sitemaps "
        "used for discovery, optional Algolia DocSearch keys, and result/fetch guardrails."
    ),
    "Catalogs & Content": (
        "The curated, data-driven content surfaces: the landing-page Self-Service Center cards, the "
        "Community Links resource page, and any embedded (iframed) apps in the sidebar. Edits apply immediately."
    ),
}


# Per-feature descriptions surfaced as helper text next to each feature toggle.
# Keyed by the configuration.yaml feature flag name. A flag with no entry simply
# renders without a description (safe for flags added later).
FEATURE_DESCRIPTIONS: Dict[str, str] = {
    "core": "Base platform capabilities. Keep this on — turning it off disables the core app experience.",
    "governance": "Governance suite: data certification (ODCS/ODPS), tag management, the Enforcement Sentinel, and allowlist exceptions.",
    "finops": "Cost & efficiency insights — spend summaries, forecasts, and resource-efficiency metrics.",
    "self_service": "The Self-Service Center: the catalog of quick-action request cards on the landing page.",
    "workflows": "Request workflows (data access, provisioning, etc.) that route through approvals and automation.",
    "data_discovery": "Browse and search the synced Unity Catalog data catalog. Also enables the background data-asset sync.",
    "calendar": "The event calendar page and its background calendar sync.",
    "sentinel": "The scheduled Enforcement Sentinel background scan (policy evaluation, safe auto-remediation, and the governance digest). Off = manual scans only.",
    "ask_your_data": "\u201cAsk Your Data\u201d — natural-language data questions answered by Databricks Genie.",
    "run_sql": "Lets the agent run read-only SQL it composes itself, on-behalf-of the user, feeding the in-chat charts.",
    "genie_summarize_answer": "Rewords Genie's answer through the agent (adds latency/cost). Off = show Genie's grounded answer verbatim.",
    "context_catalog": "Context Catalog: a curated knowledge base the agent retrieves from.",
    "workflow_authoring": "No-code, database-backed workflow authoring in the admin Workflow Studio.",
    "onboarding_suggestions": "Personalized, clickable starter prompts on the home page at login.",
    "web_search": "Lets the agent search and cite Databricks documentation (and any approved domains).",
    "feedback": "In-app feedback / feature-request / bug-report capture, triaged in the admin panel.",
    "tool_registry": "Data-driven agent-tool governance: enable tools per surface, set allowed roles, and pick SP/OBO identity.",
    "training_admin": "Admin authoring of Training tracks and courses (the learner Training page is always available).",
    "skills": "Agent Skills the agent can load at runtime from the user's Workspace folder and readable UC Volumes.",
    "enhanced_landing_page": "Shows the verbose landing header (brand title, greeting, view toggle). Off = a clean, minimal landing page.",
}


_ACRONYMS = {"sql", "api", "ai", "odcs", "odps", "llm", "mcp", "obo", "ui", "sp"}


def _prettify(name: str) -> str:
    """Turn a snake_case flag/tab key into a human label (with acronym casing)."""
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

    if kind == "self_service":
        value = value or {}
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object")
        categories = []
        for cat in (value.get("categories") or []):
            if not isinstance(cat, dict):
                continue
            title = _s(cat.get("title"))
            if not title:
                continue
            cards = []
            for card in (cat.get("cards") or []):
                if not isinstance(card, dict):
                    continue
                ct = _s(card.get("title"))
                if not ct:
                    continue
                row: Dict[str, Any] = {"title": ct}
                desc = _s(card.get("description"))
                route = _s(card.get("route"))
                prompt = _s(card.get("prompt"))
                if desc:
                    row["description"] = desc
                # route wins over prompt (matches the frontend handler).
                if route:
                    row["route"] = route
                elif prompt:
                    row["prompt"] = prompt
                personas = _coerce_personas(card.get("allowed_personas"))
                if personas:
                    row["allowed_personas"] = personas
                cards.append(row)
            cat_out: Dict[str, Any] = {"title": title, "cards": cards}
            icon = _s(cat.get("icon"))
            if icon:
                cat_out["icon"] = icon
            categories.append(cat_out)
        return {"enabled": _coerce_bool(value.get("enabled", True)), "categories": categories}

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
            cat_out = {"name": name, "links": links}
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
    # string / color / textarea
    return "" if value is None else str(value)


def _validate_key(key: str) -> Dict[str, Any]:
    """Resolve a storage key to its field spec, allowing dynamic groups."""
    if key in _EDITABLE_BY_KEY:
        return _EDITABLE_BY_KEY[key]
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
# Public API
# ---------------------------------------------------------------------------

def load_overrides(db: Session) -> int:
    """Apply every persisted override to the live config. Call once at startup.

    Returns the number of overrides applied. Never raises — a bad row is logged
    and skipped so a single malformed override can't block boot.
    """
    from app.db.app_setting import AppSettingModel

    applied = 0
    try:
        rows = db.query(AppSettingModel).all()
    except Exception as e:  # noqa: BLE001 - table may not exist yet on first boot
        logger.warning("Settings override load skipped: %s", e)
        return 0

    for row in rows:
        try:
            field = _validate_key(row.key)
            _apply(row.key, _coerce(field, row.value))
            applied += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Skipping invalid settings override %s: %s", row.key, e)

    if applied:
        logger.info("Applied %d settings override(s) from the database.", applied)
    return applied


def set_many(db: Session, changes: Dict[str, Any], updated_by: Optional[str] = None) -> Dict[str, Any]:
    """Validate, apply live, and persist a batch of overrides. Returns new state."""
    from app.db.app_setting import AppSettingModel

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

    Feature flags and navigation tabs are emitted as dynamic bool groups built
    from the current configuration so newly-added flags appear automatically.
    """
    fields: List[Dict[str, Any]] = []
    for f in EDITABLE_FIELDS:
        fields.append({**f, "value": _current_value(f["key"])})

    for name, val in sorted((_yaml_config.get("features") or {}).items()):
        fields.append({
            "group": "Features", "key": f"features.{name}", "label": _prettify(name),
            "type": "bool", "value": _coerce_bool(val),
            "help": FEATURE_DESCRIPTIONS.get(name, ""),
        })

    for name, val in sorted(((_yaml_config.get("ui") or {}).get("tabs") or {}).items()):
        fields.append({
            "group": "Navigation", "key": f"ui.tabs.{name}", "label": _prettify(name),
            "type": "bool", "value": _coerce_bool(val),
        })

    readonly = [{**f, "value": getattr(settings, f["key"], "")} for f in READONLY_FIELDS]

    # Group order for the UI sidebar (roles are rendered client-side).
    group_order = [
        "Branding & Appearance", "System Banner", "Features", "Navigation",
        "Notifications & Governance", "Agent", "Data & AI", "Catalogs & Content",
        "Web Lookup", "Target Workspaces",
    ]
    return {
        "fields": fields,
        "readonly": readonly,
        "group_order": group_order,
        "group_descriptions": GROUP_DESCRIPTIONS,
    }
