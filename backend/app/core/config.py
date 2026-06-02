"""
Application configuration settings.

All secrets and sensitive configuration should be set in the .env file.
See .env.example for required variables.

The Settings class uses pydantic-settings which automatically loads from:
1. Environment variables
2. .env file (if present)
3. Default values (if provided)
"""
from pydantic_settings import BaseSettings
from pydantic import field_validator, Field
from typing import List, Union, Any, Optional
import os
import json
import yaml

def load_config_yaml():
    paths = ["configuration.yaml", "../configuration.yaml"]
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, 'r') as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
    return {}

_yaml_config = load_config_yaml()
_branding = _yaml_config.get("branding", {})
_notifications = _yaml_config.get("notifications", {})

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
    
    # API Settings
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = f"{_branding.get('name', 'ATLAS')} API"
    DESCRIPTION: str = "Agentic Control Tower for Lakehouse Automation & Self-Service Experience"
    VERSION: str = "1.0.0"
    LOG_LEVEL: str = "INFO"
    
    # Branding Settings
    # Loaded from configuration.yaml with fallback to environment variables
    BRAND_NAME: str = _branding.get("name", os.getenv("BRAND_NAME", "ATLAS"))
    BRAND_LOGO_URL: str = _branding.get("logo_url", os.getenv("BRAND_LOGO_URL", ""))
    BRAND_COLOR_PRIMARY: str = _branding.get("primary_color", os.getenv("BRAND_COLOR_PRIMARY", "#FF3621"))
    BRAND_COLOR_SECONDARY: str = _branding.get("secondary_color", os.getenv("BRAND_COLOR_SECONDARY", "#1B5162"))
    BRAND_COLOR_INFO: str = _branding.get("info_color", os.getenv("BRAND_COLOR_INFO", "#1B5162"))
    BRAND_COLOR_ALERT: str = _branding.get("alert_color", os.getenv("BRAND_COLOR_ALERT", "#98102A"))
    BRAND_COLOR_WARNING: str = _branding.get("warning_color", os.getenv("BRAND_COLOR_WARNING", "#FFAB00"))
    BRAND_COLOR_SUCCESS: str = _branding.get("success_color", os.getenv("BRAND_COLOR_SUCCESS", "#00A972"))
    
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
    DATABASE_USER: str = "atlas_app"
    DATABASE_PASSWORD: str = ""  # SECRET: Set in .env
    
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
    
    # Poller Settings
    POLLER_INTERVAL_SECONDS: int = 5  # How often to poll for new requests
    POLLER_BATCH_SIZE: int = 50  # Max requests to process per poll cycle
    POLLER_MAX_CONCURRENT: int = 10  # Max parallel request processing
    POLLER_LOCK_TIMEOUT_MINUTES: int = 5  # Lock timeout for normal operations
    POLLER_LOCK_TIMEOUT_LONG_RUNNING_MINUTES: int = 30  # Lock timeout for provisioning
    POLLER_HEARTBEAT_INTERVAL_SECONDS: int = 300  # How often to heartbeat locks (5 minutes)
    
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
    
    # Calendar Settings
    EVENT_CALENDAR_URL: str = ""
    EVENT_SYNC_CRON: str = "0 * * * *"
    
    # Data Asset Settings
    DATA_ASSET_SYNC_CRON: str = "0 * * * *"
    
    # Sentinel Settings
    ENFORCEMENT_SENTINEL_CRON: str = "*/30 * * * *"  # Cron schedule to automatically run sentinel (empty = disabled)
    
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
    GIT_USERNAME: str = "ATLAS Bot"
    GIT_EMAIL: str = "atlas-bot@databricks.com"
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
    GITOPS_VOLUME_PATH: str = "/Volumes/atlas_dev_catalog/atlas/gitops_requests"  # UC or local path
    
    # GitHub App Authentication (blocked by org IP allowlist, kept for future)
    GITHUB_APP_ID: str = "" # GitHub App ID
    GITHUB_APP_PRIVATE_KEY: str = "" # PEM-encoded private key (store in secrets)
    GITHUB_APP_INSTALLATION_ID: str = "" # Optional: specific installation ID
    GITHUB_APP_PRIVATE_KEY_SECRET_SCOPE: str = "atlas-hub"  # Databricks secret scope
    GITHUB_APP_PRIVATE_KEY_SECRET_KEY: str = "github-app-private-key"  # Secret key name
    
    # IDP (Identity Provider) Settings
    # SECRET: Set in .env file
    IDP_BASE_URL: str = ""  # Base URL for IDP API
    IDP_API_KEY: str = ""  # SECRET: Set in .env

    # LMWS / FWS-API Group Management
    # Group/user management runs as a Databricks job (classic compute) against
    # the vendored LMWS notebook. The notebook reads the service-account
    # credentials from this Databricks secret scope (keys: username/password)
    # cluster-side; the app never reads these creds itself. See
    # app/providers/lmws/.
    LMWS_SECRET_SCOPE: str = "lmws"  # Databricks secret scope (keys: username, password)
    LMWS_JOB_TIMEOUT_SECONDS: int = 1800  # Job-level timeout for an LMWS action run
    LMWS_DEFAULT_JUSTIFICATION: str = "Automated via Databricks job"
    LMWS_DEFAULT_CLONE_SOURCE: str = "qcc.dsf.eccn.reference"  # Default clone source for createSPGroup
    # Inline (agent-tool) read path polling: how long a stateless tool will
    # wait for a job-backed read (list_retrieve / member_retrieve) before
    # giving up. State-machine writes poll across ticks instead.
    LMWS_INLINE_POLL_INTERVAL_SECONDS: int = 5
    LMWS_INLINE_MAX_WAIT_SECONDS: int = 300
    
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
    # Databricks secret scope + keys holding the AWS IAM credentials.
    NOTIFICATION_EMAIL_SES_SECRET_SCOPE: str = ""  # Databricks secret scope
    NOTIFICATION_EMAIL_SES_ACCESS_KEY_ID_SECRET_KEY: str = ""  # key for AWS access key id
    NOTIFICATION_EMAIL_SES_SECRET_ACCESS_KEY_SECRET_KEY: str = ""  # key for AWS secret access key
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
    
    # Mock User Settings (for local dev when auth headers are missing)
    MOCK_USER_EMAIL: str = "dev@example.com"
    MOCK_USER_NAME: str = "dev_user"
    MOCK_USER_ID: str = "dev_user_id"
    MOCK_USER_TOKEN: str = "" # SECRET: Set in .env (for testing OBO/PAT locally)
    
    # Cache for runtime-fetched secrets
    _github_app_private_key_cached: str = ""
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

        scope = self.NOTIFICATION_EMAIL_SES_SECRET_SCOPE
        access_key_id_key = self.NOTIFICATION_EMAIL_SES_ACCESS_KEY_ID_SECRET_KEY
        secret_access_key_key = self.NOTIFICATION_EMAIL_SES_SECRET_ACCESS_KEY_SECRET_KEY

        if not (scope and access_key_id_key and secret_access_key_key):
            return None

        import logging
        logger = logging.getLogger(__name__)
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
    
    def get_github_app_private_key(self) -> str:
        """
        Get GitHub App private key, fetching from Databricks secrets at runtime if needed.
        This handles the case where valueFrom doesn't properly resolve multi-line secrets.
        """
        # If already set via env var (valueFrom worked), use it
        if self.GITHUB_APP_PRIVATE_KEY:
            return self.GITHUB_APP_PRIVATE_KEY
        
        # If we already fetched it, return cached value
        if self._github_app_private_key_cached:
            return self._github_app_private_key_cached
        
        # Try to fetch from Databricks secrets at runtime
        if self.GITHUB_APP_PRIVATE_KEY_SECRET_SCOPE and self.GITHUB_APP_PRIVATE_KEY_SECRET_KEY:
            try:
                from databricks.sdk import WorkspaceClient
                w = WorkspaceClient()
                secret = w.secrets.get_secret(
                    scope=self.GITHUB_APP_PRIVATE_KEY_SECRET_SCOPE,
                    key=self.GITHUB_APP_PRIVATE_KEY_SECRET_KEY
                )
                if secret and secret.value:
                    import base64
                    import logging
                    logger = logging.getLogger(__name__)
                    
                    key_value = secret.value
                    
                    # Databricks SDK returns secrets base64 encoded - decode if needed
                    if not key_value.startswith('-----BEGIN'):
                        try:
                            key_value = base64.b64decode(key_value).decode('utf-8')
                            logger.info("Decoded base64-encoded private key from Databricks secrets")
                        except Exception as e:
                            logger.warning(f"Failed to base64 decode secret, using as-is: {e}")
                    
                    # Handle case where newlines were stored as literal \n
                    if '\\n' in key_value and '\n' not in key_value:
                        key_value = key_value.replace('\\n', '\n')
                    
                    self._github_app_private_key_cached = key_value
                    logger.info(
                        f"Fetched GitHub App private key from secrets/{self.GITHUB_APP_PRIVATE_KEY_SECRET_SCOPE}/{self.GITHUB_APP_PRIVATE_KEY_SECRET_KEY} (length: {len(key_value)}, starts_with: {key_value[:30] if len(key_value) > 30 else key_value})"
                    )
                    return self._github_app_private_key_cached
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to fetch GitHub App private key from secrets: {e}")
        
        return ""
    
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
        }

    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "extra": "ignore"
    }


settings = Settings()

