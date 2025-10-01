"""Pydantic models shared across the application."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class DemoWorkflowParameters(BaseModel):
    """Parameters specific to the demo data processing pipeline."""

    batch_count: int = Field(
        default=5,
        ge=1,
        le=12,
        description="Number of data batches to process in parallel (1-12, optimized for 50Gi/14 CPU quota)",
    )


class DemoRunRequest(BaseModel):
    """Request to start the demo pipeline."""

    batch_count: int = Field(default=5, ge=1, le=12, description="Number of data batches to process in parallel")
    triggered_by: Optional[str] = Field(
        None, description="Identifier for the caller that triggered the run (e.g., 'portfolio-visitor', 'admin')"
    )


# Legacy models - kept for backward compatibility during transition
class PipelineParameters(BaseModel):
    pipeline: str = Field(..., description="Name of the Nextflow pipeline to run")
    workdir: Optional[str] = Field(None, description="Working directory for pipeline execution")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary Nextflow parameters")


class RunRequest(PipelineParameters):
    triggered_by: Optional[str] = Field(None, description="Identifier for the caller that triggered the run")


class RunInfo(BaseModel):
    run_id: str
    status: RunStatus
    started_at: datetime
    finished_at: Optional[datetime]
    job_name: Optional[str]
    message: Optional[str]


class RunResponse(BaseModel):
    run_id: str
    status: RunStatus
    attached: bool
    job_name: Optional[str] = None
    websocket_url: Optional[str] = None


class DemoResultMetrics(BaseModel):
    """Metrics extracted from demo pipeline results."""

    total_batches: int = Field(description="Number of batches processed")
    total_records: int = Field(description="Total records across all batches")
    total_sum: int = Field(description="Sum of all values")
    average_value: float = Field(description="Average value across all records")
    worker_pods_spawned: int = Field(description="Number of worker pods spawned during execution")
    execution_time_seconds: float = Field(description="Total execution time")
    report_path: str = Field(description="Path to the generated report.json")


class ActiveRunStatus(BaseModel):
    active: bool
    run: Optional[RunInfo] = None
    progress_percent: Optional[float] = None
    log_preview: list[str] = Field(default_factory=list)
    websocket_url: Optional[str] = None
    connected_clients: int = 0
    last_update: Optional[datetime] = None
    # Demo-specific enhancements
    batches_generated: Optional[int] = Field(None, description="Number of GENERATE processes completed")
    batches_analyzed: Optional[int] = Field(None, description="Number of ANALYZE processes completed")
    estimated_completion: Optional[datetime] = Field(
        None, description="Estimated completion time (based on 45-60s runtime)"
    )
    parallel_workers_active: Optional[int] = Field(None, description="Current number of active worker pods")
    demo_metrics: Optional[DemoResultMetrics] = Field(None, description="Final metrics after completion")


class RunHistoryEntry(BaseModel):
    run_id: str
    status: RunStatus
    started_at: datetime
    finished_at: Optional[datetime]
    duration_seconds: Optional[float]
    triggered_by: Optional[str]
    job_name: Optional[str]


class CancelResponse(BaseModel):
    run_id: Optional[str]
    status: RunStatus
    cancelled: bool
    detail: Optional[str]


class LogChunk(BaseModel):
    run_id: str
    timestamp: datetime
    message: str
    stream: str = Field("stdout", description="stdout or stderr")


class StreamMessageType(str, Enum):
    STATUS = "status"
    PROGRESS = "progress"
    TASK_PROGRESS = "task_progress"
    RESOURCE_USAGE = "resource_usage"
    WORKFLOW_PROGRESS = "workflow_progress"  # New unified progress message
    LOG = "log"
    COMPLETE = "complete"
    ERROR = "error"


class StreamMessage(BaseModel):
    type: StreamMessageType
    data: Dict[str, Any]
    timestamp: datetime
    run_id: str


class TaskStatus(str, Enum):
    """Status of individual Nextflow tasks."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class NextflowTask(BaseModel):
    """Individual Nextflow task progress information."""

    task_id: str = Field(..., description="Nextflow task ID (e.g., 'db/820120')")
    name: str = Field(..., description="Task name (e.g., 'GENERATE', 'ANALYZE', 'REPORT')")
    tag: Optional[str] = Field(None, description="Task tag/label (e.g., '(2)' for batch 2)")
    completed: int = Field(0, description="Number of task instances completed")
    total: int = Field(1, description="Total number of task instances")
    status: TaskStatus = Field(TaskStatus.PENDING, description="Current task status")


class TaskProgressData(BaseModel):
    """Data payload for task_progress messages."""

    tasks: list[NextflowTask] = Field(default_factory=list, description="List of tracked tasks")
    executor_info: Optional[str] = Field(None, description="Executor info (e.g., 'k8s (11)')")


class ResourceUsageData(BaseModel):
    """Data payload for resource_usage messages."""

    active_pods: int = Field(0, description="Currently running pods")
    total_pods_spawned: int = Field(0, description="Total pods created during run")
    cpu_usage: Optional[str] = Field(None, description="CPU usage (e.g., '4.5 cores')")
    memory_usage: Optional[str] = Field(None, description="Memory usage (e.g., '8.2 GB')")
