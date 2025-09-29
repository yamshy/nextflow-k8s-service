"""Configuration models for the Nextflow pipeline controller."""
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    environment: str = Field(default="development")
    nextflow_namespace: str = Field(default="nextflow")
    nextflow_service_account: str = Field(default="nextflow-runner")
    nextflow_image: str = Field(default="nextflow/nextflow:latest")
    job_active_deadline_seconds: int = Field(default=3600)
    job_backoff_limit: int = Field(default=0)
    cleanup_grace_period_seconds: int = Field(default=300)
    log_fetch_interval_seconds: float = Field(default=2.0)
    run_history_limit: int = Field(default=20)
    run_ttl_minutes: int = Field(default=60 * 12)
    redis_url: Optional[str] = Field(default=None)
    kube_context: Optional[str] = Field(default=None)
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])


@lru_cache()
def get_settings() -> Settings:
    return Settings()
