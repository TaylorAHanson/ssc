"""
Application configuration settings.

All secrets and sensitive configuration should be set in the .env file.
See .env.example for required variables.

The Settings class uses pydantic-settings which automatically loads from:
1. Environment variables
2. .env file (if present)
3. Default values (if provided)

Structured, non-secret application config (feature flags, navigation tabs, the
agent tool registry, branding, the Self-Service Center / Community Links
catalogs, banner, etc.) is defined in code in ``default_config.py`` and exposed
here as ``_yaml_config``. There is no external ``configuration.yaml``: a Platform
Admin edits the change-on-the-fly subset live under Admin -> Settings (persisted
as DB overrides applied at startup by ``settings_store.load_overrides``).
"""
from pydantic_settings import BaseSettings
from pydantic import field_validator, Field
from typing import List, Union, Any, Optional
import os
import re
import json
import copy

from app.core.default_config import DEFAULT_CONFIG

# Process-wide live config. A deep copy of the in-code defaults so the settings
# store can mutate this in place (applying DB overrides) without touching the
# pristine defaults.
_yaml_config = copy.deepcopy(DEFAULT_CONFIG)
_branding = _yaml_config.get("branding", {})
_notifications = _yaml_config.get("notifications", {})
_web_search = _yaml_config.get("web_search", {}) or {}


def _slugify_brand(value: str) -> str:
    """Identifier-safe slug of a brand name (lowercase, hyphen-separated).

    Used for the contexts where the brand surfaces as an identifier rather than
    display text (git author, repo-name prefixes, etc.) so nothing is hardcoded
    to a single deployment's name.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "app"


# Resolved once at import: the configurable brand the whole app renders. Falls
# back to env then a neutral default so an unbranded deploy still works.
_brand_name = _branding.get("name", os.getenv("BRAND_NAME", "Self Service Hub"))
# Short form for identifier-y contexts (repo prefixes, git bot). Defaults to the
# full name when not separately configured.
_brand_short_name = _branding.get("short_name", os.getenv("BRAND_SHORT_NAME", _brand_name))
_brand_slug = _slugify_brand(_brand_short_name)

class Settings(BaseSettings):
    """
    Application settings.
    
    IMPORTANT: All secrets should be set in .env file, not hardcoded here.
    Copy .env.example to .env and fill in your values.
    """
    
    # Environment
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")
    
    # Platform Admin
    PLATFORM_ADMIN_EMAIL: str | None = os.getenv("PLATFORM_ADMIN_EMAIL", "admin@example.com")

    # Public base URL of this app (e.g. https://edh-ssc-....databricksapps.com).
    # Used to build absolute links in outbound emails (e.g. the Enforcement
    # Sentinel "Review" button in the governance digest). Blank omits the link.
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "")
    
    # API Settings
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = f"{_brand_name} API"
    DESCRIPTION: str = "Agentic Control Tower for Lakehouse Automation & Self-Service Experience"
    VERSION: str = "1.0.0"
    LOG_LEVEL: str = "INFO"
    
    # Branding Settings
    # In-code defaults (default_config.py) with fallback to environment variables
    BRAND_NAME: str = _brand_name
    # Short, identifier-friendly brand used where the name appears as a slug
    # rather than display text (e.g. provisioned repo-name prefixes, git author).
    BRAND_SHORT_NAME: str = _brand_short_name
    BRAND_LOGO_URL: str = _branding.get("logo_url", os.getenv("BRAND_LOGO_URL", ""))
    BRAND_COLOR_PRIMARY: str = _branding.get("primary_color", os.getenv("BRAND_COLOR_PRIMARY", "#FF3621"))
    BRAND_COLOR_SECONDARY: str = _branding.get("secondary_color", os.getenv("BRAND_COLOR_SECONDARY", "#1B5162"))
    BRAND_COLOR_INFO: str = _branding.get("info_color", os.getenv("BRAND_COLOR_INFO", "#1B5162"))
    BRAND_COLOR_ALERT: str = _branding.get("alert_color", os.getenv("BRAND_COLOR_ALERT", "#98102A"))
    BRAND_COLOR_WARNING: str = _branding.get("warning_color", os.getenv("BRAND_COLOR_WARNING", "#FFAB00"))
    BRAND_COLOR_SUCCESS: str = _branding.get("success_color", os.getenv("BRAND_COLOR_SUCCESS", "#00A972"))

    # Context Catalog
    # Curated knowledge base the agent retrieves from. Retrieval is lightweight
    # keyword search over chunked document text (no vector DB).
    CONTEXT_CATALOG_CHUNK_SIZE: int = int(os.getenv("CONTEXT_CATALOG_CHUNK_SIZE", "1200"))
    CONTEXT_CATALOG_SEARCH_LIMIT: int = int(os.getenv("CONTEXT_CATALOG_SEARCH_LIMIT", "6"))
    CONTEXT_CATALOG_MAX_UPLOAD_MB: int = int(os.getenv("CONTEXT_CATALOG_MAX_UPLOAD_MB", "20"))
    # Optional UC Volume path for storing uploaded originals (docx/pdf/pptx).
    # When empty, originals are not persisted (only the extracted text is kept).
    CONTEXT_CATALOG_VOLUME_PATH: str = os.getenv("CONTEXT_CATALOG_VOLUME_PATH", "")

    # Training / lightweight LMS
    # Admin-authored learning tracks + courses, with media/docs stored on a UC
    # Volume (never in the DB) and per-learner consumption tracked in Postgres.
    # TRAINING_VOLUME_PATH is the UC Volume root under which uploaded media is
    # written (e.g. /Volumes/main/default/training_media). When empty, uploads
    # fall back to a local directory (TRAINING_LOCAL_MEDIA_DIR) for development.
    TRAINING_VOLUME_PATH: str = os.getenv("TRAINING_VOLUME_PATH", "")
    TRAINING_LOCAL_MEDIA_DIR: str = os.getenv("TRAINING_LOCAL_MEDIA_DIR", "backend/training_media")
    TRAINING_MAX_UPLOAD_MB: int = int(os.getenv("TRAINING_MAX_UPLOAD_MB", "512"))
    # Fraction of a video that must be watched for it to count as "completed".
    TRAINING_COMPLETION_THRESHOLD: float = float(os.getenv("TRAINING_COMPLETION_THRESHOLD", "0.9"))
    # Public Databricks training catalog scraped by the "Sync from Catalog"
    # admin action. No customer-facing Academy API exists; this scrapes the
    # public catalog for course titles + stable course-detail deeplinks.
    TRAINING_CATALOG_URL: str = os.getenv("TRAINING_CATALOG_URL", "https://www.databricks.com/training/catalog")

    # Agent Skills (author-once, governed, OBO-discovered)
    # A "skill" is a folder containing a SKILL.md (YAML frontmatter + markdown
    # instructions) that the agent can load. Skills live in two OBO-scoped
    # places: (1) the user's personal Databricks Workspace folder, and (2) any
    # ``.skills`` directory inside a UC Volume the user can read/write — so
    # skills can be shared/domain-scoped by where they're stored.
    SKILLS_DIR_NAME: str = os.getenv("SKILLS_DIR_NAME", ".skills")
    # Personal workspace root for skills. Empty -> derived per user as
    # ``/Workspace/Users/<email>/.skills``.
    SKILLS_PERSONAL_WORKSPACE_DIR: str = os.getenv("SKILLS_PERSONAL_WORKSPACE_DIR", "")
    SKILLS_MAX_BYTES: int = int(os.getenv("SKILLS_MAX_BYTES", str(512 * 1024)))
    # Bounds on the cross-schema UC Volume scan that discovers shared ``.skills``
    # folders (the scan runs OBO, so a user only ever sees what they can access;
    # these caps keep the metastore walk from being unbounded at enterprise
    # scale). Optionally restrict to specific catalogs via SKILLS_SCAN_CATALOGS.
    SKILLS_SCAN_CATALOGS: str = os.getenv("SKILLS_SCAN_CATALOGS", "")
    SKILLS_SCAN_MAX_CATALOGS: int = int(os.getenv("SKILLS_SCAN_MAX_CATALOGS", "25"))
    SKILLS_SCAN_MAX_SCHEMAS: int = int(os.getenv("SKILLS_SCAN_MAX_SCHEMAS", "50"))
    SKILLS_SCAN_MAX_VOLUMES: int = int(os.getenv("SKILLS_SCAN_MAX_VOLUMES", "50"))

    # CORS (can be overridden in .env as JSON array or comma-separated)
    # Example in .env: CORS_ORIGINS=["http://localhost:5173","http://localhost:3000"]
    # Or: CORS_ORIGINS=http://localhost:5173,http://localhost:3000
    # Using str type to prevent pydantic-settings from auto-parsing as JSON
    CORS_ORIGINS: str = Field(
        default='["http://localhost:5173","http://localhost:3000","http://127.0.0.1:5173"]',
        description="CORS allowed origins as JSON array or comma-separated string"
    )
    
    @field_validator('CORS_ORIGINS', mode='before')
    @classmethod
    def parse_cors_origins(cls, v: Any) -> str:
        """Parse CORS_ORIGINS from various formats and return as JSON string."""
        # If already a list, convert to JSON string
        if isinstance(v, list):
            return json.dumps([str(item) for item in v])
        
        # If None or empty string, return default as JSON string
        if not v or (isinstance(v, str) and not v.strip()):
            return json.dumps([
                "http://localhost:5173",
                "http://localhost:3000",
                "http://127.0.0.1:5173",
            ])
        
        # If string, validate and return as-is (or normalize)
        if isinstance(v, str):
            v = v.strip()
            # If it's already JSON, validate it
            if v.startswith('[') and v.endswith(']'):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return json.dumps([str(item) for item in parsed])
                except (json.JSONDecodeError, ValueError):
                    # Invalid JSON, treat as comma-separated
                    pass
            
            # If comma-separated, convert to JSON
            if ',' in v:
                origins = [origin.strip() for origin in v.split(',') if origin.strip()]
                if origins:
                    return json.dumps(origins)
            
            # Single value, wrap in JSON array
            if v:
                return json.dumps([v])
        
        # Default fallback
        return json.dumps([
            "http://localhost:5173",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
        ])
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Get CORS_ORIGINS as a list."""
        try:
            parsed = json.loads(self.CORS_ORIGINS)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
        
        # Fallback: try comma-separated
        if ',' in self.CORS_ORIGINS:
            origins = [origin.strip() for origin in self.CORS_ORIGINS.split(',') if origin.strip()]
            if origins:
                return origins
        
        # Final fallback
        return [
            "http://localhost:5173",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
        ]
    
    # Database (Lakebase - PostgreSQL)
    DATABASE_URL: str = ""  # Full connection string (alternative to individual components below)
    DATABASE_HOST: str = ""  # SECRET: Set in .env
    DATABASE_INSTANCE_NAME: str = "" # From databricks.yml
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = "databricks_postgres"
    DATABASE_USER: str = "app_user"  # Native Postgres role (override via env/binding)
    DATABASE_PASSWORD: str = ""  # SECRET: Set in .env
    # Postgres schema the app's tables live in (search_path + auto-created on
    # connect). Defaults to a dedicated app-owned schema rather than "public"
    # because PG 15+ revokes CREATE on "public" for non-owner roles (Lakebase
    # roles hit this), so the app creates/owns its own schema. Override per
    # deployment (e.g. DB_SCHEMA=edh_ssc). Must be a bare SQL identifier.
    DB_SCHEMA: str = "selfservice"
    
    # Databricks Settings
    # SECRET: Set in .env file
    DATABRICKS_HOST: str = ""
    DATABRICKS_TOKEN: str = ""  # SECRET: Set in .env
    DATABRICKS_WORKSPACE_URL: str = ""
    DATABRICKS_WAREHOUSE_ID: str = "" # SQL Warehouse ID for running queries
    DATA_QUALITY_TABLE: str = "" # Table with ADOC DQ history
    # catalog.schema that holds the ADOC `*_history` tables (adoc_dq_history,
    # adoc_freshness_history, adoc_data_drift_history, adoc_profile_anomaly_history,
    # adoc_schema_drift_history). Defaults to the real customer environment; override
    # via env (e.g. a personal build catalog) for local/dev where these live elsewhere.
    DATA_QUALITY_ADOC_SCHEMA: str = "enterprise_stg.data_quality"
    
    # Databricks MWS (Account-level) Settings for Workspace Provisioning
    # SECRET: Set in .env file
    DATABRICKS_ACCOUNT_ID: str = ""  # SECRET: Set in .env - Account ID for MWS workspace provisioning
    DATABRICKS_CLIENT_ID: str = ""  # SECRET: Set in .env - Service principal client ID for MWS
    DATABRICKS_CLIENT_SECRET: str = ""  # SECRET: Set in .env - Service principal client secret for MWS

    # The single, install-wide secret scope holding every target-workspace SP's
    # credentials. Each target workspace names its own SP key pair inline (see
    # target_workspaces in Admin -> Settings). Settable in databricks.yml or
    # edited live in the admin Settings page; read at runtime (never injected as
    # plaintext). Blank => target workspaces fall back to the app's own SP.
    TARGET_WORKSPACE_SP_SECRET_SCOPE: str = ""

    # Databricks Job Runner (classic compute) — used by BaseDatabricksJobStateMachine
    # for workloads that need control-plane network connectivity (email, LDAP, etc.).
    # The defaults below describe a small single-node job cluster. Override the node
    # type and spark version for your cloud/region. To skip cold-starts entirely,
    # set DATABRICKS_JOB_CLUSTER_ID (always-on) or DATABRICKS_JOB_INSTANCE_POOL_ID
    # (warm pool). Precedence: cluster_id > instance_pool_id > new job cluster.
    DATABRICKS_JOB_SPARK_VERSION: str = "15.4.x-scala2.12"
    DATABRICKS_JOB_NODE_TYPE_ID: str = "i3.xlarge"
    DATABRICKS_JOB_NUM_WORKERS: int = 0  # 0 = single-node
    DATABRICKS_JOB_CLUSTER_ID: str = ""  # Optional: pin to an always-on cluster
    DATABRICKS_JOB_INSTANCE_POOL_ID: str = ""  # Optional: pull from instance pool
    
    # Model Serving (Databricks)
    # SECRET: Set in .env file
    MODEL_SERVING_AGENT_LLM_ENDPOINT: str = ""
    MODEL_SERVING_CLASSIFIER_ENDPOINT: str = ""
    MODEL_SERVING_API_KEY: str = ""  # SECRET: Set in .env
    MODEL_SERVING_TIMEOUT_SECONDS: float = 300.0

    # Databricks AI Gateway (best practice).
    # When set, the agent routes LLM calls through this gateway endpoint instead
    # of MODEL_SERVING_AGENT_LLM_ENDPOINT. The gateway is where model routing /
    # A-B traffic split, centralized rate + cost limits, and INPUT guardrails
    # (PII/safety) are configured by an admin -- no app code change to swap models.
    # NOTE: output guardrails are intentionally NOT used so SSE token streaming
    # stays always-on (a blocking output filter would buffer the stream).
    # When set, this is the model reference sent (in the request body) to the
    # AI Gateway's MLflow chat/completions route -- e.g. "system.ai.gpt-5-6-luna".
    # Prefer the "system.ai.*" model references over the legacy pay-per-token
    # "databricks-*" serving-endpoint names.
    AI_GATEWAY_ENDPOINT: str = ""

    # Reasoning effort for the agent LLM (reasoning models only, e.g. gpt-5-*).
    # Reasoning models reject function tools combined with a non-"none"
    # reasoning_effort on /v1/chat/completions -- set this to "none" for
    # gpt-5-6-luna so the agent's tool calls work. Blank => the parameter is
    # omitted entirely, which is correct for non-reasoning models (e.g. Claude,
    # Llama) that would 400 on an unexpected reasoning_effort.
    AGENT_LLM_REASONING_EFFORT: str = ""  # "" | none | low | medium | high

    # MLflow tracing / observability (best practice).
    MLFLOW_TRACING_ENABLED: bool = False
    MLFLOW_TRACKING_URI: str = "databricks"  # "databricks" in-workspace; "" disables remote
    MLFLOW_EXPERIMENT: str = ""  # e.g. /Shared/selfservice-agent; blank => default
    # Delta table (catalog.schema.table) for agent feedback keyed by trace_id.
    MLFLOW_FEEDBACK_TABLE: str = ""
    
    # Poller Settings
    POLLER_INTERVAL_SECONDS: int = 5  # How often to poll for new requests
    POLLER_BATCH_SIZE: int = 50  # Max requests to process per poll cycle
    POLLER_MAX_CONCURRENT: int = 10  # Max parallel request processing
    POLLER_LOCK_TIMEOUT_MINUTES: int = 5  # Lock timeout for normal operations
    POLLER_LOCK_TIMEOUT_LONG_RUNNING_MINUTES: int = 30  # Lock timeout for provisioning
    POLLER_HEARTBEAT_INTERVAL_SECONDS: int = 300  # How often to heartbeat locks (5 minutes)
    # When True, elect a single poller leader cluster-wide via a Postgres
    # advisory lock so only one replica processes work (no thundering herd /
    # double cron spawns). No-op on SQLite (single process). Set False to force
    # every replica to poll (legacy behavior; correctness still held by locks).
    POLLER_LEADER_ELECTION: bool = True

    # Recycle pooled DB connections after this many seconds. Default 30 min,
    # comfortably under Lakebase's OAuth token lifetime so connections are
    # refreshed (with a new token) before the server-side auth lapses.
    DB_POOL_RECYCLE_SECONDS: int = 1800
    
    # Agent Settings
    AGENT_ENABLED: bool = True
    AGENT_MAX_ITERATIONS: int = 10
    AGENT_TIMEOUT_SECONDS: int = 60
    # Per-tool output cap (chars). Prevents a single chatty tool (e.g. system
    # tables, audit logs, table lists) from blowing the model's context window.
    # ~25k chars ≈ ~6-8k tokens, generous enough for most "get_*" responses.
    AGENT_MAX_TOOL_OUTPUT_CHARS: int = 25000
    # Soft cap for total prompt size across all messages. When exceeded, the
    # runner replaces the oldest tool messages with placeholders before the
    # next LLM call. ~600k chars ≈ ~150k tokens, well under typical 1M limits.
    AGENT_MAX_PROMPT_CHARS: int = 600000
    # V2 ToolExecutor: when False (default) the agent-tool OPA package
    # (`data.agent.tools`) runs in SHADOW mode — decisions are evaluated and
    # logged but never block a tool call. Flip to True to ENFORCE (deny +
    # approval gates actually halt mutating tools). Kept off until the
    # `data.agent.tools` policy is tuned against real traffic.
    AGENT_TOOL_OPA_ENFORCE: bool = False

    # Agent profiles (authored in the Command Center Agent Studio) may pin a
    # ``model``. Routing a turn to an arbitrary serving endpoint bypasses the AI
    # Gateway's guardrails / rate + cost limits, so a profile's model is honored
    # ONLY if it appears in this comma-separated allowlist of endpoint names.
    # Empty (default) = ignore profile models entirely and always use the
    # gateway/default routing. Use "*" to allow any endpoint (NOT recommended).
    AGENT_PROFILE_MODEL_ALLOWLIST: str = ""

    @property
    def agent_profile_model_allowlist(self) -> set:
        raw = (self.AGENT_PROFILE_MODEL_ALLOWLIST or "").strip()
        if not raw:
            return set()
        return {m.strip() for m in raw.split(",") if m.strip()}

    # No-code workflow (Workflow) authoring lock. When True, all in-place authoring
    # of workflows is disabled — create/update/publish/unpublish/delete/rollback
    # via the API, and the agent's `save_workflow_draft`/`publish_workflow` tools.
    # The ONLY way to change workflows in a locked environment is an all-or-nothing
    # bundle IMPORT (the env-promotion path); reads, export, validate, and dry-run
    # stay available. Set True in production so workflows are built+proven in lower
    # envs and promoted as a vetted bundle, never hand-edited live.
    WORKFLOW_AUTHORING_LOCKED: bool = False

    # Open Policy Agent (governance / Rego)
    # Empty OPA_URL → app starts an embedded `opa run --server` child process
    # (see `app.providers.opa.server_manager`) and routes evaluations there.
    # Set OPA_URL (e.g. http://opa:8181) to disable the embedded server and
    # talk to an externally managed OPA. Set OPA_EMBEDDED_ENABLED=false to
    # opt out of the embedded server entirely and force per-call CLI mode.
    OPA_URL: str = ""
    OPA_BINARY_PATH: str = ""
    OPA_EMBEDDED_ENABLED: bool = True
    OPA_POLICIES_DIR: str = "policies"
    # Require a reachable OPA server (embedded or remote) — refuse to fall back
    # to per-call CLI evaluation. Leave False for local dev; set True in any
    # non-dev environment so a missing/failed OPA server fails loudly instead of
    # silently degrading to a slow, event-loop-blocking per-call CLI.
    OPA_REQUIRE_SERVER: bool = False
    
    # Calendar Settings
    EVENT_CALENDAR_URL: str = ""
    EVENT_SYNC_CRON: str = "0 * * * *"
    
    # Data Asset Settings
    # Comma-separated list of Unity Catalog catalogs to scan for governed data
    # (dataset-tag discovery + the data-asset cache sync). BLANK = scan every
    # catalog the service principal can see (minus system/samples), which is the
    # historical behavior. Set this to pin scanning to specific catalogs, e.g.
    # "enterprise_prod, finance_prod". Whitespace around each name is trimmed.
    # Editable in Admin -> Settings.
    SCAN_CATALOGS: str = os.getenv("SCAN_CATALOGS", "")
    DATA_ASSET_SYNC_CRON: str = "0 * * * *"
    # Data contract (ODCS) sync. Rediscovers 'dataset'-tagged tables and redrafts
    # their ODCS contracts. This calls the LLM once per dataset, so it is heavier
    # than the data-asset cache sync — default OFF (empty = manual "Sync Data
    # Contracts" button only). Set a cron (e.g. "0 6 * * *" for daily 6am UTC) to
    # keep contracts fresh automatically. Editable in Admin -> Settings.
    CONTRACT_SYNC_CRON: str = os.getenv("CONTRACT_SYNC_CRON", "")
    
    # Sentinel Settings
    ENFORCEMENT_SENTINEL_CRON: str = "*/30 * * * *"  # Cron schedule to automatically run sentinel (empty = disabled)
    # Safeguard: a scheduled sentinel run is skipped while a prior run is still
    # in flight (PENDING/PROCESSING). If a run gets orphaned (worker died, lock
    # expired) it would otherwise stall scheduling forever. A run whose lock has
    # expired AND that hasn't been updated within this many minutes is treated as
    # stale and no longer blocks new scheduled runs.
    ENFORCEMENT_SENTINEL_STALE_MINUTES: int = int(os.getenv("ENFORCEMENT_SENTINEL_STALE_MINUTES", "45"))
    # Governance digest: HIGH-severity violations email governance admins
    # immediately (on transition); everything else is rolled into a once-per-day
    # digest. The digest is "anchored" — it goes out on the first sentinel run at or
    # after this local hour on a new calendar day (in ENFORCEMENT_DIGEST_TIMEZONE),
    # so cadence changes (e.g. */30) don't produce duplicate or skipped digests.
    ENFORCEMENT_DIGEST_HOUR_LOCAL: int = int(os.getenv("ENFORCEMENT_DIGEST_HOUR_LOCAL", "7"))
    ENFORCEMENT_DIGEST_TIMEZONE: str = os.getenv("ENFORCEMENT_DIGEST_TIMEZONE", "America/Los_Angeles")
    # Max concurrent units of work during a sentinel scan (resource handler
    # discovery + per-resource OPA evaluation). Bounds fan-out so we don't spawn
    # an unbounded number of OPA subprocesses / SDK calls at once. Set to 1 to
    # fully serialize (the pre-parallelization behavior).
    SENTINEL_SCAN_CONCURRENCY: int = int(os.getenv("SENTINEL_SCAN_CONCURRENCY", "5"))
    # A single sentinel run scans every configured target workspace for
    # workspace-scoped resources (compute, jobs, apps, ...). Data certification is
    # catalog/metastore-scoped (Unity Catalog is not workspace-specific), so it
    # runs ONCE against a single "certification" workspace rather than per
    # workspace. This names the target workspace whose client runs that pass;
    # blank = the app's own home workspace (settings.DATABRICKS_*). The DQ
    # warehouse + ADOC schema always come from the global settings.
    SENTINEL_DATA_CERT_WORKSPACE: str = os.getenv("SENTINEL_DATA_CERT_WORKSPACE", "")
    
    # Retry Settings
    DEFAULT_MAX_RETRIES: int = 3
    TERRAFORM_MAX_RETRIES: int = 2
    API_MAX_RETRIES: int = 3
    DB_MAX_RETRIES: int = 5
    
    # Terraform Settings
    TERRAFORM_WORKSPACE_BASE_DIR: str = "/tmp/terraform"  # Base directory for Terraform workspaces
    TERRAFORM_TEMPLATE_DIR: str = ""  # Path to Terraform template directory (defaults to project root/terrarform_temp)
    
    # Terraform GitOps Settings
    INFRA_REPO_URL: str = "" # URL of the infrastructure git repository (used for direct Git mode)
    INFRA_REPO_BRANCH: str = "main" # Main branch for infrastructure repo
    DEFAULT_ENVIRONMENT: str = "dev" # Default environment for GitOps (dev, staging, prod)
    # Git commit author for bot-authored PRs/commits (visible to customers in
    # their git history). Defaults are brand-derived so nothing hardcodes a name.
    GIT_USERNAME: str = _branding.get("git_username", os.getenv("GIT_USERNAME", f"{_brand_short_name} Bot"))
    GIT_EMAIL: str = _branding.get("git_email", os.getenv("GIT_EMAIL", f"{_brand_slug}-bot@databricks.com"))
    GIT_SSH_KEY_PATH: str = "" # Path to SSH key for git operations
    GIT_TOKEN: str = "" # GitHub personal access token for HTTPS auth (fallback)
    GIT_TOKEN_SECRET_SCOPE: str = ""  # Databricks secret scope for PAT
    GIT_TOKEN_SECRET_KEY: str = ""  # Secret key name for PAT
    
    # Volume-based GitOps Settings (recommended - avoids IP allowlist issues)
    # When GITOPS_MODE is "volume", requests are written to a Unity Catalog Volume
    # and a GitHub Actions workflow polls the volume to create PRs
    GITOPS_MODE: str = "volume"  # "volume" (recommended) or "direct" (requires Git access)
    # Use a UC path (e.g. /Volumes/catalog/schema/gitops_requests) when running ON Databricks.
    # For local dev or non-Databricks runs, use a local directory (e.g. ./gitops_volume or /tmp/gitops_volume).
    GITOPS_VOLUME_PATH: str = "/Volumes/main/default/gitops_requests"  # UC or local path

    # IDP (Identity Provider) Settings
    # SECRET: Set in .env file
    IDP_BASE_URL: str = ""  # Base URL for IDP API
    IDP_API_KEY: str = ""  # SECRET: Set in .env

    # Identity-group provider (vendor-neutral membership management).
    # noop (default, records-only) | rest (SCIM/REST) | lmws (Qualcomm legacy).
    IDENTITY_PROVIDER: str = "noop"
    # UC tag keys that map an asset to its access/approver group (configurable so
    # customers use their own tagging conventions instead of hardcoded names).
    ACCESS_GROUP_TAG_KEY: str = "access_group"
    APPROVER_GROUP_TAG_KEY: str = "approver_group"
    # Generic REST/SCIM identity backend (used when IDENTITY_PROVIDER=rest).
    IDENTITY_REST_BASE_URL: str = ""
    IDENTITY_REST_TOKEN: str = ""
    IDENTITY_REST_ADD_PATH: str = ""
    IDENTITY_REST_REMOVE_PATH: str = ""
    IDENTITY_REST_GROUP_PATH: str = ""
    IDENTITY_REST_MEMBER_PATH: str = ""

    # LMWS / FWS-API Group Management (legacy; only used when IDENTITY_PROVIDER=lmws)
    # Group/user management runs as a Databricks job (classic compute) against
    # the vendored LMWS notebook. The notebook reads the service-account
    # credentials from this Databricks secret scope (keys: username/password)
    # cluster-side; the app never reads these creds itself. See
    # app/providers/lmws/.
    LMWS_SECRET_SCOPE: str = "lmws"  # Databricks secret scope (keys: username, password)
    # LMWS / FWS-API base URLs. Optional and NOT hardcoded in the notebook — the
    # app passes them to the job as widgets. Empty by default; set per deployment
    # via databricks.yml (see the dev target). When blank the LMWS actions that
    # need them fail with a clear "not configured" error.
    LMWS_AUTHN_URL: str = ""   # e.g. https://<gateway>/iam/v1/lmwsrest-authn
    LMWS_REST_URL: str = ""    # e.g. https://<gateway>/iam/v1/lmws-rest/publicAPIrest
    LMWS_CACHE_URL: str = ""   # e.g. https://<gateway>/iam/v1/lmws-rest/listCacheInfo
    LMWS_FWS_URL: str = ""     # e.g. https://<gateway>/iam/v1/fws-api/entitlement
    # Compute target for LMWS jobs. The notebook is API-only (no Spark), so
    # serverless is the cheaper, faster default and avoids classic cold-starts.
    # Flip to False (runtime-editable in Admin -> Settings) to force classic
    # compute — e.g. if the LMWS/FWS-API gateway is only reachable from a
    # network-pinned classic cluster. When False, the DATABRICKS_JOB_* settings
    # (cluster id / instance pool / node type) select the classic target.
    LMWS_USE_SERVERLESS: bool = True
    LMWS_JOB_TIMEOUT_SECONDS: int = 1800  # Job-level timeout for an LMWS action run
    LMWS_DEFAULT_JUSTIFICATION: str = "Automated via Databricks job"
    LMWS_DEFAULT_CLONE_SOURCE: str = ""  # Default clone source for createSPGroup (set per-deployment)
    # Inline (agent-tool) read path polling: how long a stateless tool will
    # wait for a job-backed read (list_retrieve / member_retrieve) before
    # giving up. State-machine writes poll across ticks instead.
    LMWS_INLINE_POLL_INTERVAL_SECONDS: int = 5
    LMWS_INLINE_MAX_WAIT_SECONDS: int = 300

    # --- Native (in-app) LMWS read path (experimental) ------------------
    # When the LMWS/FWS-API gateway is reachable directly from the app's
    # runtime (not only from a job cluster), the read-only lookup tools can
    # call it in-process instead of submitting a Databricks job. This removes
    # the job cold-start/poll latency for `member_lookup` / `group_lookup`.
    # The service-account username matches the vendored notebook; the password
    # is NOT read from the Databricks secret scope by the app (the REST Secrets
    # API doesn't return secret values), so inject it into the app environment
    # (e.g. a databricks.yml app resource secret binding the `lmws` scope key
    # `edhapisvc`). Blank password => the native tools fail with a clear
    # "not configured" error and callers should keep using the serverless path.
    LMWS_SERVICE_USERNAME: str = os.getenv("LMWS_SERVICE_USERNAME", "edhapisvc")
    LMWS_SERVICE_PASSWORD: str = os.getenv("LMWS_SERVICE_PASSWORD", "")
    # TLS verification for the direct calls. Defaults to False to match the
    # vendored notebook (the internal Qualcomm gateway presents an internal CA);
    # flip on where the gateway chain is trusted by the app runtime.
    LMWS_NATIVE_VERIFY_TLS: bool = False
    LMWS_NATIVE_TIMEOUT_SECONDS: int = 30  # Per-request HTTP timeout for direct calls
    
    # Notification Settings
    GOVERNANCE_EMAIL_GROUP: str = _notifications.get("governance_email_group", os.getenv("GOVERNANCE_EMAIL_GROUP", "data-governance@example.com"))
    
    # Email Provider Selection
    NOTIFICATION_EMAIL_PROVIDER: str = "smtp" # "smtp", "ses", "mock"
    
    # SMTP Settings
    NOTIFICATION_EMAIL_SMTP_HOST: str = ""  # SMTP host for email notifications
    NOTIFICATION_EMAIL_SMTP_PORT: int = 587
    NOTIFICATION_EMAIL_SMTP_USER: str = ""  # SECRET: Set in .env
    NOTIFICATION_EMAIL_SMTP_PASSWORD: str = ""  # SECRET: Set in .env
    
    # SES Settings
    # Email is sent in-process via boto3. Authentication uses IAM credentials
    # (access key id + secret access key) stored in a Databricks secret scope —
    # NOT dbutils service credentials, which are unavailable from a serverless
    # Databricks App. See get_ses_aws_credentials() below.
    NOTIFICATION_EMAIL_SES_REGION: str = "us-west-2"
    NOTIFICATION_EMAIL_SES_SOURCE: str = "" # Source email address (must be SES-verified)
    # Databricks secret scope holding the AWS IAM credentials. Only the SCOPE is
    # environment-specific (passed via databricks.yml per target); the key names
    # follow a fixed convention so they never have to be configured.
    NOTIFICATION_EMAIL_SES_SECRET_SCOPE: str = ""  # Databricks secret scope
    NOTIFICATION_EMAIL_SES_ACCESS_KEY_ID_SECRET_KEY: str = "aws_access_key_id"  # convention
    NOTIFICATION_EMAIL_SES_SECRET_ACCESS_KEY_SECRET_KEY: str = "aws_secret_access_key"  # convention
    NOTIFICATION_EMAIL_SES_SESSION_TOKEN_SECRET_KEY: str = ""  # optional key for AWS session token (temp creds)
    # Legacy: Databricks service credential name (dbutils-based). No longer used
    # by the in-app boto3 path; kept for backward compatibility / reference.
    NOTIFICATION_EMAIL_SES_CREDENTIAL: str = ""
    
    NOTIFICATION_SLACK_WEBHOOK_URL: str = ""  # SECRET: Set in .env
    NOTIFICATION_TEAMS_WEBHOOK_URL: str = ""  # SECRET: Set in .env
    
    # GitHub Settings
    # SECRET: Set in .env file
    GITHUB_TOKEN: str = ""  # SECRET: Set in .env
    GITHUB_ORG: str = ""  # GitHub organization name
    # Web base URL for user-facing GitHub deep-links (native "request access" /
    # "request to join team" pages). Enterprise Cloud = https://github.com;
    # override for a self-hosted GitHub Enterprise Server host.
    GITHUB_WEB_BASE_URL: str = "https://github.com"

    # Governance Tag Management (GitOps for UC tag changes)
    # The app opens PRs against this repo; a GitHub Action in the repo runs the
    # generated ALTER...SET/UNSET TAGS SQL per environment on merge.
    GOVERNANCE_TAGS_REPO: str = ""  # "owner/repo" or bare repo name (resolved against GITHUB_ORG)
    GOVERNANCE_TAGS_BASE_BRANCH: str = "main"  # base branch PRs target
    GOVERNANCE_TAGS_PATH: str = "tags/migrations"  # path prefix for generated .sql files
    
    # Mock User Settings (for local dev when auth headers are missing)
    MOCK_USER_EMAIL: str = "dev@example.com"
    MOCK_USER_NAME: str = "dev_user"
    MOCK_USER_ID: str = "dev_user_id"
    MOCK_USER_TOKEN: str = "" # SECRET: Set in .env (for testing OBO/PAT locally)
    
    # Cache for runtime-fetched secrets
    _git_token_cached: str = ""
    _ses_aws_credentials_cached: Optional[dict] = None

    def get_ses_aws_credentials(self) -> Optional[dict]:
        """
        Fetch AWS IAM credentials for SES from a Databricks secret scope.

        Returns a dict suitable for boto3.client(...) / boto3.Session(...):
            {
                "aws_access_key_id": "...",
                "aws_secret_access_key": "...",
                "aws_session_token": "...",   # only if a session-token key is configured
            }
        or None if the scope/keys aren't configured or the secrets can't be read.

        Replaces the dbutils service-credential provider shown in notebooks,
        which is unavailable from a serverless Databricks App.
        """
        if self._ses_aws_credentials_cached:
            return self._ses_aws_credentials_cached

        import logging
        logger = logging.getLogger(__name__)

        scope = self.NOTIFICATION_EMAIL_SES_SECRET_SCOPE
        access_key_id_key = self.NOTIFICATION_EMAIL_SES_ACCESS_KEY_ID_SECRET_KEY
        secret_access_key_key = self.NOTIFICATION_EMAIL_SES_SECRET_ACCESS_KEY_SECRET_KEY

        if not (scope and access_key_id_key and secret_access_key_key):
            logger.warning(
                "SES IAM credential lookup skipped: scope/keys not fully configured "
                "(NOTIFICATION_EMAIL_SES_SECRET_SCOPE=%r, ACCESS_KEY_ID_SECRET_KEY=%r, "
                "SECRET_ACCESS_KEY_SECRET_KEY=%r). boto3 will use the ambient AWS "
                "credential chain, which typically fails on a serverless App with "
                "'Unable to locate credentials'.",
                scope or "", access_key_id_key or "", secret_access_key_key or "",
            )
            return None

        try:
            from databricks.sdk import WorkspaceClient
            import base64

            def _read(key: str) -> str:
                secret = WorkspaceClient().secrets.get_secret(scope=scope, key=key)
                if not secret or not secret.value:
                    return ""
                # Databricks SDK returns secret values base64-encoded.
                return base64.b64decode(secret.value).decode("utf-8").strip()

            creds = {
                "aws_access_key_id": _read(access_key_id_key),
                "aws_secret_access_key": _read(secret_access_key_key),
            }

            session_token_key = self.NOTIFICATION_EMAIL_SES_SESSION_TOKEN_SECRET_KEY
            if session_token_key:
                token = _read(session_token_key)
                if token:
                    creds["aws_session_token"] = token

            if not (creds["aws_access_key_id"] and creds["aws_secret_access_key"]):
                logger.warning(
                    f"SES IAM credentials in secrets/{scope} are incomplete; "
                    "falling back to ambient AWS credentials."
                )
                return None

            self._ses_aws_credentials_cached = creds
            logger.info(f"Fetched SES IAM credentials from secrets/{scope}")
            return self._ses_aws_credentials_cached
        except Exception as e:
            logger.warning(f"Failed to fetch SES IAM credentials from secrets: {e}")
            return None
    
    def get_git_token(self) -> str:
        """
        Get GitHub PAT, fetching from Databricks secrets at runtime if needed.
        """
        # If already set via env var, use it
        if self.GIT_TOKEN:
            return self.GIT_TOKEN
        
        # If we already fetched it, return cached value
        if self._git_token_cached:
            return self._git_token_cached
        
        # Try to fetch from Databricks secrets at runtime
        if self.GIT_TOKEN_SECRET_SCOPE and self.GIT_TOKEN_SECRET_KEY:
            try:
                from databricks.sdk import WorkspaceClient
                import base64
                import logging
                logger = logging.getLogger(__name__)
                
                w = WorkspaceClient()
                secret = w.secrets.get_secret(
                    scope=self.GIT_TOKEN_SECRET_SCOPE,
                    key=self.GIT_TOKEN_SECRET_KEY
                )
                if secret and secret.value:
                    # Databricks SDK returns secrets base64 encoded
                    token_value = base64.b64decode(secret.value).decode('utf-8').strip()
                    self._git_token_cached = token_value
                    logger.info(
                        f"Fetched GitHub PAT from secrets/{self.GIT_TOKEN_SECRET_SCOPE}/{self.GIT_TOKEN_SECRET_KEY}"
                    )
                    return self._git_token_cached
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to fetch GitHub PAT from secrets: {e}")
        
        return ""
    
    # ------------------------------------------------------------------
    # Web lookup (search_databricks_docs / fetch_doc_page)
    # ------------------------------------------------------------------
    # docs.databricks.com is always permitted so the docs tools work even
    # if an operator leaves `allowed_domains` empty.
    WEB_SEARCH_DEFAULT_DOMAIN: str = "docs.databricks.com"

    def web_search_config(self) -> dict:
        """Normalized web-lookup config from the in-code defaults.

        Returns a dict with always-sane defaults so callers don't have to
        guard against missing keys. ``allowed_domains`` always includes the
        Databricks docs host.
        """
        cfg = _web_search or {}
        domains = [
            str(d).strip().lower()
            for d in (cfg.get("allowed_domains") or [])
            if str(d).strip()
        ]
        if self.WEB_SEARCH_DEFAULT_DOMAIN not in domains:
            domains.append(self.WEB_SEARCH_DEFAULT_DOMAIN)

        sitemaps = [
            str(s).strip()
            for s in (cfg.get("sitemaps") or [])
            if str(s).strip()
        ]
        if not sitemaps:
            sitemaps = [f"https://{self.WEB_SEARCH_DEFAULT_DOMAIN}/aws/en/sitemap.xml"]

        algolia = cfg.get("algolia") or {}
        return {
            "allowed_domains": domains,
            "sitemaps": sitemaps,
            "algolia": {
                "app_id": str(algolia.get("app_id", "") or "").strip(),
                "api_key": str(algolia.get("api_key", "") or "").strip(),
                "index_name": str(algolia.get("index_name", "") or "").strip(),
            },
            "max_results": int(cfg.get("max_results", 8) or 8),
            "fetch_timeout_seconds": float(cfg.get("fetch_timeout_seconds", 15) or 15),
            "max_fetch_chars": int(cfg.get("max_fetch_chars", 20000) or 20000),
        }

    def opa_provider_config(self) -> dict:
        """Build kwargs for OpaProvider from application settings.

        Resolution order for the OPA endpoint:
          1. Explicit ``OPA_URL`` setting (user-managed external server)
          2. Embedded OPA server URL if it's running (started in main.lifespan)
          3. Fall back to local CLI per-call evaluation
        """
        explicit_url = (self.OPA_URL or "").strip()
        embedded_url = None
        if not explicit_url:
            # Imported lazily to avoid pulling httpx into config import paths
            # in unit tests that don't need the embedded server.
            try:
                from app.providers.opa.server_manager import get_opa_url
                embedded_url = get_opa_url()
            except Exception:
                embedded_url = None

        url = explicit_url or embedded_url or None
        return {
            "opa_url": url,
            "opa_binary": (self.OPA_BINARY_PATH or "").strip() or None,
            "use_local_binary": not bool(url),
            "policies_dir": (self.OPA_POLICIES_DIR or "policies").strip(),
            "require_server": bool(self.OPA_REQUIRE_SERVER),
        }

    @property
    def brand_slug(self) -> str:
        """Identifier-safe slug of the (short) brand name."""
        return _slugify_brand(self.BRAND_SHORT_NAME or self.BRAND_NAME)

    def apply_brand_tokens(self, text: Optional[str]) -> Optional[str]:
        """Substitute brand placeholders in served text (e.g. workflow instructions).

        Lets bundled, customer-editable content stay brand-neutral: authors write
        ``{{brand_name}}`` / ``{{brand_short_name}}`` / ``{{brand_slug}}`` and the
        configured brand is filled in at serve time, so no deployment's name is
        baked into the defaults.
        """
        if not text:
            return text
        return (
            text.replace("{{brand_name}}", self.BRAND_NAME)
            .replace("{{brand_short_name}}", self.BRAND_SHORT_NAME or self.BRAND_NAME)
            .replace("{{brand_slug}}", self.brand_slug)
        )

    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "extra": "ignore"
    }


settings = Settings()


# Environments where dev-only conveniences (the "Dev Persona Mode" role override,
# mock entitlements, auto Platform-Admin fallback) are permitted. This is an
# ALLOWLIST of local/dev-flavored names, NOT a denylist of prod spellings: a
# denylist misses the many prod tokens in the wild ("prod", "prd", "production"),
# which is exactly how role override once leaked into a prod deploy. Anything not
# listed here (incl. stage/prod and any unknown value) is treated as non-dev.
DEV_FEATURE_ENVIRONMENTS = frozenset({"development", "dev", "local", "test", "testing"})


def dev_features_allowed() -> bool:
    """True only in an explicitly local/dev-flavored ``ENVIRONMENT``.

    Single source of truth for gating dev-only conveniences (role override,
    mock/admin fallbacks, and hiding the frontend toggle) so they can never fire
    in stage/prod regardless of how the environment token is spelled. Read at
    call time so an Admin -> Settings override takes effect without a restart.
    """
    return (settings.ENVIRONMENT or "").strip().lower() in DEV_FEATURE_ENVIRONMENTS


def get_scan_catalogs() -> list:
    """Parse ``SCAN_CATALOGS`` into a clean list of catalog names.

    Splits on commas and trims surrounding whitespace, dropping empties. Returns
    ``[]`` when unset/blank — callers treat that as "scan everything the service
    principal can see". Read at call time so Admin -> Settings overrides take
    effect without a restart.
    """
    raw = getattr(settings, "SCAN_CATALOGS", "") or ""
    return [c.strip() for c in raw.split(",") if c.strip()]

