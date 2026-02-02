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
from typing import List, Union, Any
import os
import json


class Settings(BaseSettings):
    """
    Application settings.
    
    IMPORTANT: All secrets should be set in .env file, not hardcoded here.
    Copy .env.example to .env and fill in your values.
    """
    
    # API Settings
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "ATLAS API"
    DESCRIPTION: str = "Agentic Control Tower for Lakehouse Automation & Self-Service Experience"
    VERSION: str = "1.0.0"
    LOG_LEVEL: str = "INFO"
    
    # Branding Settings
    # We explicitly pull from os.getenv to ensure they are picked up, 
    # as sometimes pydantic-settings case sensitivity can be tricky.
    BRAND_NAME: str = os.getenv("BRAND_NAME", "ATLAS")
    BRAND_LOGO_URL: str = os.getenv("BRAND_LOGO_URL", "")
    BRAND_COLOR_PRIMARY: str = os.getenv("BRAND_COLOR_PRIMARY", "#FF3621")
    BRAND_COLOR_SECONDARY: str = os.getenv("BRAND_COLOR_SECONDARY", "#1B5162")
    BRAND_COLOR_INFO: str = os.getenv("BRAND_COLOR_INFO", "#1B5162")
    BRAND_COLOR_ALERT: str = os.getenv("BRAND_COLOR_ALERT", "#98102A")
    BRAND_COLOR_WARNING: str = os.getenv("BRAND_COLOR_WARNING", "#FFAB00")
    BRAND_COLOR_SUCCESS: str = os.getenv("BRAND_COLOR_SUCCESS", "#00A972")
    
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
    # SECRET: Set in .env file
    DATABASE_URL: str = ""  # Full connection string (alternative to individual components below)
    DATABASE_HOST: str = ""  # SECRET: Set in .env
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = "edas_hub"
    DATABASE_USER: str = ""  # SECRET: Set in .env
    DATABASE_PASSWORD: str = ""  # SECRET: Set in .env
    
    # Databricks Settings
    # SECRET: Set in .env file
    DATABRICKS_HOST: str = ""
    DATABRICKS_TOKEN: str = ""  # SECRET: Set in .env
    DATABRICKS_WORKSPACE_URL: str = ""
    DATABRICKS_WAREHOUSE_ID: str = "" # SQL Warehouse ID for running queries
    
    # Databricks MWS (Account-level) Settings for Workspace Provisioning
    # SECRET: Set in .env file
    DATABRICKS_ACCOUNT_ID: str = ""  # SECRET: Set in .env - Account ID for MWS workspace provisioning
    DATABRICKS_CLIENT_ID: str = ""  # SECRET: Set in .env - Service principal client ID for MWS
    DATABRICKS_CLIENT_SECRET: str = ""  # SECRET: Set in .env - Service principal client secret for MWS
    
    # Model Serving (Databricks)
    # SECRET: Set in .env file
    MODEL_SERVING_AGENT_LLM_ENDPOINT: str = ""
    MODEL_SERVING_CLASSIFIER_ENDPOINT: str = ""
    MODEL_SERVING_API_KEY: str = ""  # SECRET: Set in .env
    
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
    
    # Calendar Settings
    EVENT_CALENDAR_URL: str = ""
    EVENT_SYNC_INTERVAL_MINUTES: int = 60
    
    # Retry Settings
    DEFAULT_MAX_RETRIES: int = 3
    TERRAFORM_MAX_RETRIES: int = 2
    API_MAX_RETRIES: int = 3
    DB_MAX_RETRIES: int = 5
    
    # Terraform Settings
    TERRAFORM_WORKSPACE_BASE_DIR: str = "/tmp/terraform"  # Base directory for Terraform workspaces
    TERRAFORM_TEMPLATE_DIR: str = ""  # Path to Terraform template directory (defaults to project root/terrarform_temp)
    
    # Terraform GitOps Settings
    INFRA_REPO_URL: str = "" # URL of the infrastructure git repository
    INFRA_REPO_BRANCH: str = "main" # Main branch for infrastructure repo
    DEFAULT_ENVIRONMENT: str = "dev" # Default environment for GitOps (dev, staging, prod)
    GIT_USERNAME: str = "ATLAS Bot"
    GIT_EMAIL: str = "atlas-bot@databricks.com"
    GIT_SSH_KEY_PATH: str = "" # Path to SSH key for git operations
    GIT_TOKEN: str = "" # GitHub personal access token for HTTPS auth (fallback)
    GIT_TOKEN_SECRET_SCOPE: str = ""  # Databricks secret scope for PAT
    GIT_TOKEN_SECRET_KEY: str = ""  # Secret key name for PAT
    
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
    
    # Notification Settings
    NOTIFICATION_EMAIL_SMTP_HOST: str = ""  # SMTP host for email notifications
    NOTIFICATION_EMAIL_SMTP_PORT: int = 587
    NOTIFICATION_EMAIL_SMTP_USER: str = ""  # SECRET: Set in .env
    NOTIFICATION_EMAIL_SMTP_PASSWORD: str = ""  # SECRET: Set in .env
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
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()

