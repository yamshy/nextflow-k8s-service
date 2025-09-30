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
    nextflow_image: str = Field(default="nextflow/nextflow:25.04.7")
    job_active_deadline_seconds: int = Field(default=3600)
    job_backoff_limit: int = Field(default=0)
    job_ttl_seconds_after_finished: int = Field(default=900)
    cleanup_grace_period_seconds: int = Field(default=300)
    log_fetch_interval_seconds: float = Field(default=2.0)
    log_batch_interval_seconds: float = Field(default=0.75)
    progress_broadcast_interval_seconds: float = Field(default=2.0)
    monitor_poll_interval_seconds: float = Field(default=2.5)
    log_tail_lines: int = Field(default=150)
    max_websocket_connections: int = Field(default=100)
    run_history_limit: int = Field(default=20)
    run_ttl_minutes: int = Field(default=60 * 12)
    redis_url: Optional[str] = Field(default=None)
    kube_context: Optional[str] = Field(default=None)
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])

    # Resource limits for Nextflow controller pod
    controller_cpu_request: str = Field(default="500m")
    controller_cpu_limit: str = Field(default="2")
    controller_memory_request: str = Field(default="1Gi")
    controller_memory_limit: str = Field(default="4Gi")

    # Resource limits for Nextflow worker pods (pipeline tasks)
    worker_cpu_request: str = Field(default="100m")
    worker_cpu_limit: str = Field(default="1")
    worker_memory_request: str = Field(default="256 MB")
    worker_memory_limit: str = Field(default="1 GB")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
