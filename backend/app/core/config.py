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
from typing import List


class Settings(BaseSettings):
    """
    Application settings.
    
    IMPORTANT: All secrets should be set in .env file, not hardcoded here.
    Copy .env.example to .env and fill in your values.
    """
    
    # API Settings
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "EDAS Hub API"
    
    # CORS (can be overridden in .env as comma-separated or space-separated)
    CORS_ORIGINS: List[str] = [
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
    
    # Retry Settings
    DEFAULT_MAX_RETRIES: int = 3
    TERRAFORM_MAX_RETRIES: int = 2
    API_MAX_RETRIES: int = 3
    DB_MAX_RETRIES: int = 5
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

