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
    GIT_USERNAME: str = "Ops Bot"
    GIT_EMAIL: str = "ops-bot@example.com"
    GIT_SSH_KEY_PATH: str = "" # Path to SSH key for git operations
    
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
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()

