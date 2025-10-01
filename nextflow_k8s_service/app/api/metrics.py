"""Portfolio-focused metrics API for demo runs."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from ..models import DemoResultMetrics
from ..parsers.demo_results import parse_report_json
from ..services.pipeline_manager import PipelineManager
from ..services.state_store import StateStore

router = APIRouter(prefix="/demo", tags=["demo-metrics"])


def get_pipeline_manager(request: Request) -> PipelineManager:
    return request.app.state.pipeline_manager


def get_state_store(request: Request) -> StateStore:
    return request.app.state.state_store


@router.get("/metrics")
async def aggregate_metrics(
    state_store: StateStore = Depends(get_state_store),
) -> dict:
    """Get aggregate statistics across all demo runs.

    Returns portfolio-ready metrics including:
    - Total runs executed
    - Success/failure rates
    - Average execution time
    - Total data processed
    """
    history = await state_store.get_history(limit=100)

    if not history:
        return {
            "total_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "success_rate": 0.0,
            "average_execution_time_seconds": 0.0,
            "total_batches_processed": 0,
            "total_records_processed": 0,
        }

    successful_runs = [r for r in history if r.status.value == "succeeded"]
    failed_runs = [r for r in history if r.status.value == "failed"]

    total_runs = len(history)
    success_count = len(successful_runs)
    failure_count = len(failed_runs)
    success_rate = (success_count / total_runs * 100) if total_runs > 0 else 0.0

    # Calculate average execution time from successful runs
    execution_times = [r.duration_seconds for r in successful_runs if r.duration_seconds is not None]
    avg_execution_time = sum(execution_times) / len(execution_times) if execution_times else 0.0

    return {
        "total_runs": total_runs,
        "successful_runs": success_count,
        "failed_runs": failure_count,
        "success_rate": round(success_rate, 2),
        "average_execution_time_seconds": round(avg_execution_time, 2),
        "min_execution_time_seconds": round(min(execution_times), 2) if execution_times else 0.0,
        "max_execution_time_seconds": round(max(execution_times), 2) if execution_times else 0.0,
        "recent_runs": [
            {
                "run_id": r.run_id,
                "status": r.status.value,
                "started_at": r.started_at.isoformat(),
                "duration_seconds": r.duration_seconds,
                "triggered_by": r.triggered_by,
            }
            for r in history[:10]
        ],
    }


@router.get("/results/{run_id}")
async def get_demo_results(
    run_id: str,
    state_store: StateStore = Depends(get_state_store),
) -> Optional[DemoResultMetrics]:
    """Get parsed results for a specific demo run.

    Args:
        run_id: The run ID to get results for

    Returns:
        DemoResultMetrics if the run completed successfully and results are available

    Raises:
        HTTPException: If run not found or results not available
    """
    # Get run from history
    history = await state_store.get_history(limit=100)
    run = next((r for r in history if r.run_id == run_id), None)

    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if run.status.value != "succeeded":
        raise HTTPException(
            status_code=400, detail=f"Run {run_id} did not complete successfully (status: {run.status.value})"
        )

    if run.duration_seconds is None:
        raise HTTPException(status_code=400, detail=f"Run {run_id} duration not available")

    # Parse results from run-specific report.json
    # Note: The workflow publishDir should be updated to include run_id
    # For now, use the default path with a note
    report_path = f"/workspace/results/{run_id}/report.json"

    # Estimate worker pods based on typical batch count
    # TODO: Store batch_count in run metadata for accurate tracking
    # For now, estimate: default is 5 batches = 10 workers (5 GENERATE + 5 ANALYZE)
    worker_pods = 10  # Conservative estimate

    metrics = parse_report_json(
        report_path=report_path,
        execution_time_seconds=run.duration_seconds,
        worker_pods_spawned=worker_pods,
    )

    if not metrics:
        raise HTTPException(status_code=404, detail=f"Results not found for run {run_id}")

    return metrics


@router.get("/showcase")
async def showcase_summary(
    state_store: StateStore = Depends(get_state_store),
) -> dict:
    """Portfolio-ready summary with all-time statistics.

    This endpoint provides a comprehensive showcase of the demo pipeline's
    capabilities, optimized for display on a portfolio website.
    """
    history = await state_store.get_history(limit=100)

    successful_runs = [r for r in history if r.status.value == "succeeded"]
    execution_times = [r.duration_seconds for r in successful_runs if r.duration_seconds is not None]

    return {
        "demo_info": {
            "name": "Parallel Data Pipeline Orchestrator",
            "description": "Cloud-native data processing demonstration",
            "workflow_type": "Nextflow scatter-gather pattern",
            "github_url": "https://github.com/yourusername/nextflow-k8s-service",
        },
        "infrastructure": {
            "platform": "Kubernetes",
            "homelab_specs": {
                "total_memory": "50Gi",
                "total_cpu": 14,
                "available_for_workers": {
                    "memory": "46Gi",
                    "cpu": 12,
                },
            },
            "controller_resources": {
                "cpu": "2",
                "memory": "4Gi",
            },
            "worker_resources": {
                "cpu_per_worker": "1",
                "memory_per_worker": "4GB",
                "max_parallel_workers": 12,
            },
        },
        "performance": {
            "total_runs": len(history),
            "successful_runs": len(successful_runs),
            "average_runtime_seconds": (
                round(sum(execution_times) / len(execution_times), 2)
                if execution_times and len(execution_times) > 0
                else 0.0
            ),
            "min_runtime_seconds": round(min(execution_times), 2) if execution_times else 0.0,
            "max_runtime_seconds": round(max(execution_times), 2) if execution_times else 0.0,
            "typical_runtime": "45-60 seconds",
        },
        "capabilities": {
            "real_time_streaming": "WebSocket-based log streaming",
            "parallel_processing": "10 worker pods for default 5-batch run",
            "resource_optimization": "Fits 12 parallel workers within 50Gi/14 CPU quota",
            "monitoring": "Live progress tracking and metrics extraction",
        },
        "tech_stack": {
            "backend": "FastAPI (Python 3.12+)",
            "orchestration": "Nextflow + Kubernetes",
            "streaming": "WebSocket (async)",
            "state_management": "In-memory with optional Redis",
            "containerization": "Docker + K8s Jobs",
        },
        "demo_narrative": (
            "Built a cloud-native data pipeline orchestrator on Kubernetes that dynamically "
            "spawns 10 parallel worker pods to process data batches in under 60 seconds. "
            "The system demonstrates resource optimization (46Gi/12 CPU within 50Gi/14 CPU quota), "
            "real-time WebSocket streaming, and production-ready FastAPI architecture. "
            "Complete with metrics extraction, JSON result aggregation, and live progress tracking—"
            "perfect for showcasing DevOps and data engineering skills."
        ),
    }
