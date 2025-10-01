"""Configuration for the demo portfolio orchestrator.

This configuration is optimized for the specific demo workflow running
on a homelab Kubernetes cluster with 50Gi memory and 14 CPU quota.
"""

import os
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Homelab infrastructure constants
HOMELAB_TOTAL_MEMORY_GI = 50
HOMELAB_TOTAL_CPU = 14

# Demo workflow constants
DEMO_WORKFLOW_PATH = "/app/workflows/demo.nf"
DEMO_WORKFLOW_CONFIG_PATH = "/app/workflows/nextflow.config"


def _infer_app_image() -> str:
    """Infer the app image for the init container.

    In production, APP_IMAGE should be set to ensure the init container
    uses the same image as the API. During development/testing, we use
    a default value.
    """
    return os.environ.get("APP_IMAGE", "ghcr.io/yamshy/nextflow-k8s-service:latest")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    environment: str = Field(default="development")
    nextflow_namespace: str = Field(default="nextflow")
    nextflow_service_account: str = Field(default="nextflow-runner")
    nextflow_image: str = Field(default="nextflow/nextflow:25.04.7")
    app_image: str = Field(
        default_factory=_infer_app_image,
        description="App image containing bundled workflows",
    )
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
    run_history_limit: int = Field(default=5, description="Keep last 5 runs for debugging")
    run_ttl_minutes: int = Field(default=30, description="Keep run history for 30 minutes")
    redis_url: Optional[str] = Field(default=None)
    kube_context: Optional[str] = Field(default=None)
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])

    # Demo workflow configuration
    default_batch_count: int = Field(default=5, description="Default number of batches to process")
    max_batch_count: int = Field(default=12, description="Maximum batches allowed (quota: 12*2 workers = 24 pods)")

    @property
    def workflow_path(self) -> str:
        """Path to demo workflow - hardcoded constant."""
        return DEMO_WORKFLOW_PATH

    @property
    def homelab_memory_quota_gi(self) -> int:
        """Total homelab memory quota in Gi - hardcoded constant."""
        return HOMELAB_TOTAL_MEMORY_GI

    @property
    def homelab_cpu_quota(self) -> int:
        """Total homelab CPU quota - hardcoded constant."""
        return HOMELAB_TOTAL_CPU

    # Resource limits for Nextflow controller pod - optimized for demo
    # Controller needs 2 CPU + 4Gi for managing up to 12 parallel workers
    controller_cpu_request: str = Field(default="1")
    controller_cpu_limit: str = Field(default="2", description="Fixed: Demo controller needs 2 CPU")
    controller_memory_request: str = Field(default="2Gi")
    controller_memory_limit: str = Field(default="4Gi", description="Fixed: Demo controller needs 4Gi")

    # Resource limits for Nextflow worker pods - optimized for homelab (50Gi/14 CPU)
    # With 50Gi memory and 14 CPU quota:
    # - Controller uses: 2 CPU + 4Gi
    # - Remaining for workers: 12 CPU + 46Gi
    # - Per worker: 1 CPU + 4GB allows 12 parallel workers (max batch_count=12)
    # - 5 batches (default) = 10 workers = 10 CPU + 40GB (fits within quota)
    worker_cpu_request: str = Field(default="500m")
    worker_cpu_limit: str = Field(default="1", description="Fixed: 1 CPU per worker")
    worker_memory_request: str = Field(default="2 GB")
    worker_memory_limit: str = Field(default="4 GB", description="Fixed: 4GB per worker")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
