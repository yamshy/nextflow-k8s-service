"""Service that calculates weighted workflow progress from Nextflow tasks."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from ..models import NextflowTask, TaskStatus
from ..progress_models import (
    OverallProgress,
    PhaseStatus,
    ResourceMetrics,
    TaskProgress,
    WorkflowHealth,
    WorkflowPhase,
    WorkflowProgress,
)

logger = logging.getLogger(__name__)


class ProgressCalculator:
    """Calculates comprehensive workflow progress from task states."""

    # Phase weights for the demo workflow
    PHASE_WEIGHTS = {
        "GENERATE": 0.3,  # 30% of total work
        "ANALYZE": 0.6,  # 60% of total work
        "REPORT": 0.1,  # 10% of total work
    }

    # Expected task counts per batch (for demo workflow)
    TASKS_PER_BATCH = {
        "GENERATE": 1,  # 1 GENERATE task per batch
        "ANALYZE": 1,  # 1 ANALYZE task per batch
    }

    def __init__(self, batch_count: int = 5):
        """Initialize progress calculator.

        Args:
            batch_count: Number of batches in the workflow (affects task expectations)
        """
        self.batch_count = batch_count
        self._phase_start_times: dict[str, datetime] = {}
        self._phase_end_times: dict[str, datetime] = {}
        self._peak_pods = 0
        self._total_pods_spawned = 0

    def calculate_progress(
        self,
        task_states: dict[str, NextflowTask],
        executor_info: Optional[str] = None,
        active_pods: int = 0,
    ) -> WorkflowProgress:
        """Calculate comprehensive workflow progress.

        Args:
            task_states: Dictionary of task_id -> NextflowTask
            executor_info: Executor information string (e.g., "k8s (11)")
            active_pods: Currently active pod count

        Returns:
            WorkflowProgress with all calculated metrics
        """
        # Initialize phase data
        phase_data = self._group_tasks_by_phase(task_states)

        # Build phase objects with timing
        phases = self._build_phases(phase_data)

        # Calculate overall progress
        overall = self._calculate_overall_progress(phases)

        # Build task details (optional, for detailed view)
        tasks = self._build_task_list(task_states, phase_data)

        # Calculate resource metrics
        resources = self._calculate_resources(executor_info, active_pods)

        # Validate workflow health
        health = self._validate_workflow(phases, task_states)

        return WorkflowProgress(
            overall_progress=overall,
            phases=phases,
            tasks=tasks,
            resources=resources,
            health=health,
        )

    def _group_tasks_by_phase(self, task_states: dict[str, NextflowTask]) -> dict[str, dict[str, Any]]:
        """Group tasks by their phase.

        Args:
            task_states: Dictionary of task_id -> NextflowTask

        Returns:
            Dictionary with phase statistics
        """
        phase_data = {
            "GENERATE": {
                "completed": 0,
                "total": 0,
                "running": 0,
                "failed": 0,
                "tasks": [],
            },
            "ANALYZE": {
                "completed": 0,
                "total": 0,
                "running": 0,
                "failed": 0,
                "tasks": [],
            },
            "REPORT": {
                "completed": 0,
                "total": 0,
                "running": 0,
                "failed": 0,
                "tasks": [],
            },
        }

        for task_id, task in task_states.items():
            phase_name = task.name.upper()
            if phase_name not in phase_data:
                continue

            phase = phase_data[phase_name]
            phase["tasks"].append((task_id, task))
            phase["total"] += 1

            if task.status == TaskStatus.COMPLETED:
                phase["completed"] += 1
            elif task.status == TaskStatus.RUNNING:
                phase["running"] += 1
            elif task.status == TaskStatus.FAILED:
                phase["failed"] += 1

            # Track phase timing
            if task.status in [TaskStatus.RUNNING, TaskStatus.COMPLETED]:
                if phase_name not in self._phase_start_times:
                    self._phase_start_times[phase_name] = datetime.now()
                if task.status == TaskStatus.COMPLETED and phase["completed"] == phase["total"]:
                    self._phase_end_times[phase_name] = datetime.now()

        return phase_data

    def _build_phases(self, phase_data: dict[str, dict]) -> list[WorkflowPhase]:
        """Build WorkflowPhase objects from phase data.

        Args:
            phase_data: Dictionary with phase statistics

        Returns:
            List of WorkflowPhase objects
        """
        phases = []

        for phase_name in ["GENERATE", "ANALYZE", "REPORT"]:
            data = phase_data[phase_name]

            # Determine expected total for the phase
            if phase_name == "REPORT":
                expected_total = 1  # Always 1 REPORT task
            else:
                expected_total = self.batch_count * self.TASKS_PER_BATCH.get(phase_name, 1)

            # Always use expected total for accurate progress calculation
            # (actual task count might be less if still being submitted)
            total = expected_total

            # Determine phase status
            if data["total"] == 0:
                status = PhaseStatus.PENDING
                percent = 0.0
            elif data["failed"] > 0:
                status = PhaseStatus.RUNNING  # Keep as running even with failures
                percent = (data["completed"] / total) * 100
            elif data["completed"] >= total:
                status = PhaseStatus.COMPLETED
                percent = 100.0
            elif data["running"] > 0 or data["completed"] > 0:
                status = PhaseStatus.RUNNING
                percent = (data["completed"] / total) * 100
            else:
                status = PhaseStatus.PENDING
                percent = 0.0

            # Calculate estimated completion for running phases
            estimated_completion = None
            if status == PhaseStatus.RUNNING and phase_name in self._phase_start_times:
                start_time = self._phase_start_times[phase_name]
                elapsed = (datetime.now() - start_time).total_seconds()
                if data["completed"] > 0:
                    avg_time_per_task = elapsed / data["completed"]
                    remaining_tasks = total - data["completed"]
                    estimated_seconds = remaining_tasks * avg_time_per_task
                    estimated_completion = datetime.now() + timedelta(seconds=estimated_seconds)

            phases.append(
                WorkflowPhase(
                    name=phase_name,
                    status=status,
                    tasks_completed=data["completed"],
                    tasks_total=total,
                    percent=round(percent, 1),
                    started_at=self._phase_start_times.get(phase_name),
                    finished_at=self._phase_end_times.get(phase_name),
                    estimated_completion=estimated_completion,
                )
            )

        return phases

    def _calculate_overall_progress(self, phases: list[WorkflowPhase]) -> OverallProgress:
        """Calculate weighted overall progress.

        Args:
            phases: List of WorkflowPhase objects

        Returns:
            OverallProgress with calculated metrics
        """
        total_weighted_progress = 0.0
        total_tasks = 0
        completed_tasks = 0
        current_phase = None
        phase_progress = 0.0

        for phase in phases:
            # Calculate weighted contribution
            phase_completion = phase.tasks_completed / max(phase.tasks_total, 1)
            weight = self.PHASE_WEIGHTS.get(phase.name, 0)
            total_weighted_progress += phase_completion * weight

            # Track totals
            total_tasks += phase.tasks_total
            completed_tasks += phase.tasks_completed

            # Identify current phase
            if phase.status == PhaseStatus.RUNNING and current_phase is None:
                current_phase = phase.name
                phase_progress = phase.percent

        return OverallProgress(
            percent=round(total_weighted_progress * 100, 1),
            completed_tasks=completed_tasks,
            total_tasks=total_tasks,
            current_phase=current_phase,
            phase_progress=round(phase_progress, 1),
        )

    def _build_task_list(
        self,
        task_states: dict[str, NextflowTask],
        phase_data: dict[str, dict],
    ) -> list[TaskProgress]:
        """Build detailed task list (optional for UI).

        Args:
            task_states: Dictionary of task_id -> NextflowTask
            phase_data: Dictionary with phase statistics

        Returns:
            List of TaskProgress objects
        """
        tasks = []

        # Limit to most recent/relevant tasks to avoid message size issues
        for phase_name in ["GENERATE", "ANALYZE", "REPORT"]:
            phase_tasks = phase_data[phase_name]["tasks"]

            # Take only the most recent 5 tasks per phase for detailed view
            for task_id, task in phase_tasks[:5]:
                tasks.append(
                    TaskProgress(
                        task_id=task_id,
                        phase=phase_name,
                        name=task.name,
                        tag=task.tag,
                        progress={
                            "completed": task.completed,
                            "total": task.total,
                            "percent": round((task.completed / max(task.total, 1)) * 100),
                        },
                        status=task.status.value,
                    )
                )

        return tasks

    def _calculate_resources(self, executor_info: Optional[str], active_pods: int) -> ResourceMetrics:
        """Calculate resource usage metrics.

        Args:
            executor_info: Executor string (e.g., "k8s (11)")
            active_pods: Currently active pods

        Returns:
            ResourceMetrics with usage data
        """
        # Track peak pods
        if active_pods > self._peak_pods:
            self._peak_pods = active_pods

        # Estimate total pods (this would be tracked in log streamer)
        if active_pods > 0:
            self._total_pods_spawned = max(self._total_pods_spawned, active_pods)

        # Calculate CPU/memory based on pod counts and known limits
        # Controller: 2 CPU + 4Gi, Workers: 1 CPU + 4GB each
        cpu_usage = 2 + active_pods  # Controller + workers
        memory_usage_gb = 4 + (active_pods * 4)  # Controller + workers

        # Calculate as percentage of homelab capacity
        cpu_percent = (cpu_usage / 14) * 100  # 14 CPU total

        return ResourceMetrics(
            active_pods=active_pods,
            total_pods_spawned=self._total_pods_spawned,
            peak_pods=self._peak_pods,
            cpu_usage_percent=round(cpu_percent, 1),
            memory_usage_gb=round(memory_usage_gb, 1),
            executor=executor_info,
        )

    def _validate_workflow(self, phases: list[WorkflowPhase], task_states: dict[str, NextflowTask]) -> WorkflowHealth:
        """Validate workflow execution order and dependencies.

        Args:
            phases: List of WorkflowPhase objects
            task_states: Dictionary of task_id -> NextflowTask

        Returns:
            WorkflowHealth with validation results
        """
        warnings = []
        workflow_valid = True
        tasks_in_order = True

        # Get phases by name
        phase_map = {p.name: p for p in phases}

        # Check REPORT dependencies
        report = phase_map.get("REPORT")
        if report and report.status != PhaseStatus.PENDING:
            generate = phase_map.get("GENERATE")
            analyze = phase_map.get("ANALYZE")

            # REPORT should only start after GENERATE and ANALYZE complete
            if generate and generate.status != PhaseStatus.COMPLETED:
                warnings.append("REPORT started before GENERATE completed")
                workflow_valid = False
                tasks_in_order = False

            if analyze and analyze.status != PhaseStatus.COMPLETED:
                warnings.append("REPORT started before ANALYZE completed")
                workflow_valid = False
                tasks_in_order = False

        # Check ANALYZE dependencies
        analyze = phase_map.get("ANALYZE")
        generate = phase_map.get("GENERATE")

        if analyze and analyze.status == PhaseStatus.RUNNING:
            if generate and generate.tasks_completed == 0:
                warnings.append("ANALYZE started before any GENERATE tasks completed")
                # This is a warning but not necessarily invalid
                # (ANALYZE can start as GENERATE produces output)

        # Check for task failures
        failed_tasks = sum(1 for t in task_states.values() if t.status == TaskStatus.FAILED)
        if failed_tasks > 0:
            warnings.append(f"{failed_tasks} task(s) failed during execution")

        return WorkflowHealth(
            workflow_valid=workflow_valid,
            tasks_in_order=tasks_in_order,
            warnings=warnings,
        )
