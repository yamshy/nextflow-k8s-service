"""Configuration models for the Nextflow pipeline controller."""
from functools import lru_cache
from typing import Optional

from pydantic import BaseSettings, Field


class Settings(BaseSettings):
    environment: str = Field("development", env="ENVIRONMENT")
    nextflow_namespace: str = Field("nextflow", env="NEXTFLOW_NAMESPACE")
    nextflow_service_account: str = Field("nextflow-runner", env="NEXTFLOW_SERVICE_ACCOUNT")
    nextflow_image: str = Field("nextflow/nextflow:latest", env="NEXTFLOW_IMAGE")
    job_active_deadline_seconds: int = Field(3600, env="JOB_ACTIVE_DEADLINE_SECONDS")
    job_backoff_limit: int = Field(0, env="JOB_BACKOFF_LIMIT")
    cleanup_grace_period_seconds: int = Field(300, env="CLEANUP_GRACE_PERIOD_SECONDS")
    log_fetch_interval_seconds: float = Field(2.0, env="LOG_FETCH_INTERVAL_SECONDS")
    run_history_limit: int = Field(20, env="RUN_HISTORY_LIMIT")
    run_ttl_minutes: int = Field(60 * 12, env="RUN_TTL_MINUTES")
    redis_url: Optional[str] = Field(None, env="REDIS_URL")
    kube_context: Optional[str] = Field(None, env="KUBE_CONTEXT")
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
