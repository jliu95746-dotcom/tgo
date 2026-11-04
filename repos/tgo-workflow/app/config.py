from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):
    PROJECT_NAME: str = "TGO Workflow Engine"
    API_V1_STR: str = "/v1"
    
    # CORS
    ALLOWED_ORIGINS: List[str] = ["*"]
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/tgo_workflow"
    DATABASE_ECHO: bool = False
    
    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    WORKFLOW_QUEUE_NAME: str = Field(
        default="workflow", min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    TIMER_EXECUTION_ENABLED: bool = False
    WEBHOOK_EXECUTION_ENABLED: bool = False
    
    @property
    def CELERY_BROKER_URL(self) -> str:
        return self.REDIS_URL
    
    @property
    def CELERY_RESULT_BACKEND(self) -> str:
        return self.REDIS_URL
    
    # Integrations
    AI_SERVICE_URL: str = "http://localhost:8000"
    API_INTERNAL_SERVICE_URL: str = "http://localhost:8001"
    SECRET_KEY: SecretStr | None = None
    WORKFLOW_EVENT_RECEIPT_KEYS: SecretStr | None = None
    WORKFLOW_EVENT_RECEIPT_ACTIVE_KEY_ID: str | None = None
    WORKFLOW_WEBHOOK_KEYS: SecretStr | None = None
    WORKFLOW_WEBHOOK_ACTIVE_KEY_ID: str | None = None
    WORKFLOW_APPROVAL_KEYS: SecretStr | None = None
    WORKFLOW_APPROVAL_ACTIVE_KEY_ID: str | None = None
    WORKFLOW_EVENT_INPUT_KEYS: SecretStr | None = None
    WORKFLOW_EVENT_INPUT_ACTIVE_KEY_ID: str | None = None
    

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

settings = Settings()

