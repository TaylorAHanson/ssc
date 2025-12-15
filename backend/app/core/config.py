"""
Application configuration settings.
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings."""
    
    # API Settings
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "EDAS Hub API"
    
    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ]
    
    # Database (Lakebase - PostgreSQL)
    DATABASE_URL: str = ""  # Lakebase connection string
    DATABASE_HOST: str = ""
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = "edas_hub"
    DATABASE_USER: str = ""
    DATABASE_PASSWORD: str = ""
    
    # Databricks Settings
    DATABRICKS_HOST: str = ""
    DATABRICKS_TOKEN: str = ""
    DATABRICKS_WORKSPACE_URL: str = ""
    
    # Model Serving (Databricks)
    MODEL_SERVING_AGENT_LLM_ENDPOINT: str = ""
    MODEL_SERVING_CLASSIFIER_ENDPOINT: str = ""
    MODEL_SERVING_API_KEY: str = ""
    
    # ARQ/Redis Settings
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    
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

