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


class PipelineParameters(BaseModel):
    pipeline: str = Field(..., description="Name of the Nextflow pipeline to run")
    workdir: Optional[str] = Field(None, description="Working directory for pipeline execution")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary Nextflow parameters")


class RunRequest(BaseModel):
    parameters: PipelineParameters
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


class ActiveRunStatus(BaseModel):
    active: bool
    run: Optional[RunInfo] = None


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
