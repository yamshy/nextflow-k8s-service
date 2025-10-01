"""REST API routes for demo portfolio showcase.

This module contains ONLY the demo-specific endpoints for the portfolio
data pipeline orchestrator. All legacy/generic pipeline routes have been
removed to create a focused, purpose-built application.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..models import ActiveRunStatus, CancelResponse, DemoRunRequest, RunHistoryEntry, RunResponse
from ..services.pipeline_manager import PipelineManager

# Primary demo router - this is the ONLY router for this application
demo_router = APIRouter(prefix="/demo", tags=["demo"])


def get_pipeline_manager(request: Request) -> PipelineManager:
    return request.app.state.pipeline_manager


@demo_router.post("/run", response_model=RunResponse)
async def start_demo_run(
    run_request: DemoRunRequest,
    manager: PipelineManager = Depends(get_pipeline_manager),
) -> RunResponse:
    """Start a new demo pipeline run with specified batch count."""
    return await manager.start_demo_run(run_request)


@demo_router.get("/preview")
async def preview_demo_run(
    batch_count: int = Query(5, ge=1, le=12, description="Number of batches to process"),
) -> dict:
    """Preview what the demo run will execute without actually running it."""
    return {
        "workflow": "Portfolio Demo Pipeline",
        "description": "Parallel data processing demonstration",
        "batch_count": batch_count,
        "expected_pods": batch_count * 2,  # GENERATE + ANALYZE for each batch
        "estimated_runtime_seconds": "45-60",
        "resource_usage": {
            "worker_cpu_per_pod": "1",
            "worker_memory_per_pod": "4GB",
            "controller_cpu": "2",
            "controller_memory": "4Gi",
            "total_workers": batch_count * 2,
        },
        "homelab_quota": {
            "available_memory": "50Gi",
            "available_cpu": "14",
            "optimized_for": f"{batch_count} batches",
        },
    }


@demo_router.get("/status", response_model=ActiveRunStatus)
async def current_status(manager: PipelineManager = Depends(get_pipeline_manager)) -> ActiveRunStatus:
    """Get current status of the active demo run."""
    return await manager.current_status()


@demo_router.get("/active", response_model=ActiveRunStatus)
async def active_run(manager: PipelineManager = Depends(get_pipeline_manager)) -> ActiveRunStatus:
    """Check if there's an active demo run."""
    return await manager.is_active()


@demo_router.delete("/cancel", response_model=CancelResponse)
async def cancel_run(manager: PipelineManager = Depends(get_pipeline_manager)) -> CancelResponse:
    """Cancel the currently active demo run."""
    return await manager.cancel_active_run()


@demo_router.get("/history", response_model=list[RunHistoryEntry])
async def run_history(
    limit: int = Query(10, ge=1, le=100),
    manager: PipelineManager = Depends(get_pipeline_manager),
) -> list[RunHistoryEntry]:
    """Get history of recent demo runs."""
    return await manager.get_history(limit=limit)
