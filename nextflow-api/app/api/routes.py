"""REST API routes for pipeline management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..models import ActiveRunStatus, CancelResponse, RunHistoryEntry, RunRequest, RunResponse
from ..services.pipeline_manager import PipelineManager

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


def get_pipeline_manager(request: Request) -> PipelineManager:
    return request.app.state.pipeline_manager


@router.post("/run", response_model=RunResponse)
async def start_run(
    run_request: RunRequest,
    manager: PipelineManager = Depends(get_pipeline_manager),
) -> RunResponse:
    return await manager.start_or_attach_run(run_request)


@router.get("/status", response_model=ActiveRunStatus)
async def current_status(manager: PipelineManager = Depends(get_pipeline_manager)) -> ActiveRunStatus:
    return await manager.current_status()


@router.get("/active", response_model=ActiveRunStatus)
async def active_run(manager: PipelineManager = Depends(get_pipeline_manager)) -> ActiveRunStatus:
    return await manager.is_active()


@router.delete("/cancel", response_model=CancelResponse)
async def cancel_run(manager: PipelineManager = Depends(get_pipeline_manager)) -> CancelResponse:
    return await manager.cancel_active_run()


@router.get("/history", response_model=list[RunHistoryEntry])
async def run_history(
    limit: int = Query(10, ge=1, le=100),
    manager: PipelineManager = Depends(get_pipeline_manager),
) -> list[RunHistoryEntry]:
    return await manager.get_history(limit=limit)
