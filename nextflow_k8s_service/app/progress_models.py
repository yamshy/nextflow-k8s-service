"""Backend-calculated workflow progress models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PhaseStatus(str, Enum):
    """Status of a workflow phase."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"


class WorkflowPhase(BaseModel):
    """Progress information for a single workflow phase."""

    name: str = Field(..., description="Phase name (GENERATE, ANALYZE, REPORT)")
    status: PhaseStatus = Field(..., description="Current status of the phase")
    tasks_completed: int = Field(0, description="Number of tasks completed in this phase")
    tasks_total: int = Field(0, description="Total number of tasks in this phase")
    percent: float = Field(0.0, description="Completion percentage for this phase")
    started_at: Optional[datetime] = Field(None, description="When this phase started")
    finished_at: Optional[datetime] = Field(None, description="When this phase finished")
    estimated_completion: Optional[datetime] = Field(None, description="Estimated completion time for running phases")


class OverallProgress(BaseModel):
    """Overall workflow progress calculated by the backend."""

    percent: float = Field(0.0, description="Total weighted completion percentage")
    completed_tasks: int = Field(0, description="Total tasks completed across all phases")
    total_tasks: int = Field(0, description="Total tasks in the workflow")
    current_phase: Optional[str] = Field(None, description="Currently active phase")
    phase_progress: float = Field(0.0, description="Progress within current phase (%)")


class TaskProgress(BaseModel):
    """Individual task progress information."""

    task_id: str = Field(..., description="Nextflow task ID")
    phase: str = Field(..., description="Phase this task belongs to")
    name: str = Field(..., description="Task name")
    tag: Optional[str] = Field(None, description="Task tag/label")
    progress: dict[str, int] = Field(..., description="Task progress (completed, total, percent)")
    status: str = Field(..., description="Task status")
    started_at: Optional[datetime] = Field(None, description="Task start time")
    pod_name: Optional[str] = Field(None, description="Associated Kubernetes pod")


class ResourceMetrics(BaseModel):
    """Resource usage metrics for the workflow."""

    active_pods: int = Field(0, description="Currently active pods")
    total_pods_spawned: int = Field(0, description="Total pods created")
    peak_pods: int = Field(0, description="Maximum concurrent pods")
    cpu_usage_percent: float = Field(0.0, description="CPU usage percentage")
    memory_usage_gb: float = Field(0.0, description="Memory usage in GB")
    executor: Optional[str] = Field(None, description="Executor info (e.g., 'k8s (11)')")


class WorkflowHealth(BaseModel):
    """Workflow health and validation status."""

    workflow_valid: bool = Field(True, description="Are dependencies respected?")
    tasks_in_order: bool = Field(True, description="Are phases executing in sequence?")
    warnings: list[str] = Field(default_factory=list, description="Any anomalies detected")


class WorkflowProgress(BaseModel):
    """Comprehensive workflow progress message."""

    overall_progress: OverallProgress = Field(..., description="Overall progress metrics")
    phases: list[WorkflowPhase] = Field(..., description="Progress by phase")
    tasks: list[TaskProgress] = Field(default_factory=list, description="Individual task details")
    resources: ResourceMetrics = Field(..., description="Resource usage metrics")
    health: WorkflowHealth = Field(..., description="Workflow health status")

    @classmethod
    def calculate_from_tasks(cls, tasks: dict[str, dict]) -> "WorkflowProgress":
        """Calculate workflow progress from raw task data.

        Args:
            tasks: Dictionary of task_id -> task_data

        Returns:
            WorkflowProgress with calculated metrics
        """
        # Phase weights for demo workflow
        PHASE_WEIGHTS = {
            "GENERATE": 0.3,  # 30% of total work
            "ANALYZE": 0.6,  # 60% of total work
            "REPORT": 0.1,  # 10% of total work
        }

        # Initialize phase tracking
        phase_data = {
            "GENERATE": {"completed": 0, "total": 0, "tasks": []},
            "ANALYZE": {"completed": 0, "total": 0, "tasks": []},
            "REPORT": {"completed": 0, "total": 0, "tasks": []},
        }

        # Group tasks by phase
        for task_id, task_info in tasks.items():
            phase_name = task_info.get("name", "").upper()
            if phase_name in phase_data:
                phase = phase_data[phase_name]
                phase["tasks"].append(task_info)
                phase["total"] += 1
                if task_info.get("status") == "completed":
                    phase["completed"] += 1

        # Build phase objects
        phases = []
        current_phase = None
        total_weighted_progress = 0.0

        for phase_name in ["GENERATE", "ANALYZE", "REPORT"]:  # Ordered execution
            phase_info = phase_data[phase_name]

            if phase_info["total"] == 0:
                # Phase hasn't started yet
                status = PhaseStatus.PENDING
                percent = 0.0
            elif phase_info["completed"] == phase_info["total"]:
                # Phase is complete
                status = PhaseStatus.COMPLETED
                percent = 100.0
            else:
                # Phase is running
                status = PhaseStatus.RUNNING
                percent = (phase_info["completed"] / phase_info["total"]) * 100
                current_phase = phase_name

            # Calculate weighted contribution
            phase_completion = phase_info["completed"] / max(phase_info["total"], 1)
            weighted_contribution = phase_completion * PHASE_WEIGHTS.get(phase_name, 0)
            total_weighted_progress += weighted_contribution

            phases.append(
                WorkflowPhase(
                    name=phase_name,
                    status=status,
                    tasks_completed=phase_info["completed"],
                    tasks_total=phase_info["total"],
                    percent=percent,
                )
            )

        # Calculate overall progress
        total_tasks = sum(p["total"] for p in phase_data.values())
        completed_tasks = sum(p["completed"] for p in phase_data.values())

        overall = OverallProgress(
            percent=round(total_weighted_progress * 100, 1),
            completed_tasks=completed_tasks,
            total_tasks=total_tasks,
            current_phase=current_phase,
            phase_progress=phases[["GENERATE", "ANALYZE", "REPORT"].index(current_phase)].percent
            if current_phase
            else 0.0,
        )

        # Validate workflow order
        health = cls._validate_workflow_order(phases)

        # Build resource metrics (placeholder - will be populated from executor info)
        resources = ResourceMetrics()

        return cls(
            overall_progress=overall,
            phases=phases,
            tasks=[],  # Detailed tasks optional for now
            resources=resources,
            health=health,
        )

    @staticmethod
    def _validate_workflow_order(phases: list[WorkflowPhase]) -> WorkflowHealth:
        """Validate that workflow phases are executing in proper order.

        Args:
            phases: List of workflow phases

        Returns:
            WorkflowHealth with validation results
        """
        warnings = []
        workflow_valid = True
        tasks_in_order = True

        # Find phases by name
        phase_map = {p.name: p for p in phases}

        # Check if REPORT started before prerequisites completed
        report = phase_map.get("REPORT")
        if report and report.status != PhaseStatus.PENDING:
            generate = phase_map.get("GENERATE")
            analyze = phase_map.get("ANALYZE")

            if generate and generate.status != PhaseStatus.COMPLETED:
                warnings.append("REPORT started before GENERATE completed")
                workflow_valid = False
                tasks_in_order = False

            if analyze and analyze.status != PhaseStatus.COMPLETED:
                warnings.append("REPORT started before ANALYZE completed")
                workflow_valid = False
                tasks_in_order = False

        # Check if ANALYZE started before GENERATE had progress
        analyze = phase_map.get("ANALYZE")
        if analyze and analyze.status == PhaseStatus.RUNNING:
            generate = phase_map.get("GENERATE")
            if generate and generate.tasks_completed == 0:
                warnings.append("ANALYZE started before any GENERATE tasks completed")
                # This might be ok in some workflows, so just warn

        return WorkflowHealth(
            workflow_valid=workflow_valid,
            tasks_in_order=tasks_in_order,
            warnings=warnings,
        )
