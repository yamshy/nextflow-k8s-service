"""Tests for the progress calculator service."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from app.models import NextflowTask, TaskStatus
from app.progress_models import PhaseStatus, WorkflowHealth
from app.services.progress_calculator import ProgressCalculator


class TestProgressCalculator:
    """Test suite for ProgressCalculator."""

    @pytest.fixture
    def calculator(self):
        """Create a calculator with 5 batches."""
        return ProgressCalculator(batch_count=5)

    def test_empty_progress(self, calculator):
        """Test progress calculation with no tasks."""
        progress = calculator.calculate_progress({})

        assert progress.overall_progress.percent == 0.0
        assert progress.overall_progress.completed_tasks == 0
        assert progress.overall_progress.current_phase is None

        # All phases should be pending with expected totals
        for phase in progress.phases:
            assert phase.status == PhaseStatus.PENDING
            assert phase.percent == 0.0
            if phase.name == "GENERATE":
                assert phase.tasks_total == 5  # 5 batches
            elif phase.name == "ANALYZE":
                assert phase.tasks_total == 5  # 5 batches
            elif phase.name == "REPORT":
                assert phase.tasks_total == 1  # Always 1

    def test_generate_phase_progress(self, calculator):
        """Test progress during GENERATE phase."""
        tasks = {
            "task1": NextflowTask(
                task_id="aa/111111",
                name="GENERATE",
                tag="1",
                completed=1,
                total=1,
                status=TaskStatus.COMPLETED,
            ),
            "task2": NextflowTask(
                task_id="bb/222222",
                name="GENERATE",
                tag="2",
                completed=1,
                total=1,
                status=TaskStatus.COMPLETED,
            ),
            "task3": NextflowTask(
                task_id="cc/333333",
                name="GENERATE",
                tag="3",
                completed=0,
                total=1,
                status=TaskStatus.RUNNING,
            ),
        }

        progress = calculator.calculate_progress(tasks)

        # GENERATE is 30% of total, 2/5 complete = 40% of GENERATE = 12% overall
        assert progress.overall_progress.percent == pytest.approx(12.0, rel=0.1)
        assert progress.overall_progress.completed_tasks == 2
        assert progress.overall_progress.current_phase == "GENERATE"

        # Check GENERATE phase
        generate = next(p for p in progress.phases if p.name == "GENERATE")
        assert generate.status == PhaseStatus.RUNNING
        assert generate.tasks_completed == 2
        assert generate.tasks_total == 5  # Expected 5 batches
        assert generate.percent == 40.0  # 2/5 = 40%

    def test_analyze_phase_progress(self, calculator):
        """Test progress during ANALYZE phase with GENERATE complete."""
        tasks = {}

        # All GENERATE tasks complete
        for i in range(1, 6):
            tasks[f"gen{i}"] = NextflowTask(
                task_id=f"g{i:02d}/111111",
                name="GENERATE",
                tag=str(i),
                completed=1,
                total=1,
                status=TaskStatus.COMPLETED,
            )

        # Some ANALYZE tasks
        for i in range(1, 4):
            tasks[f"ana{i}"] = NextflowTask(
                task_id=f"a{i:02d}/222222",
                name="ANALYZE",
                tag=str(i),
                completed=1,
                total=1,
                status=TaskStatus.COMPLETED,
            )

        progress = calculator.calculate_progress(tasks)

        # GENERATE: 30% weight, 100% complete = 30%
        # ANALYZE: 60% weight, 3/5 complete = 36%
        # Total: 30% + 36% = 66%
        assert progress.overall_progress.percent == pytest.approx(66.0, rel=0.1)
        assert progress.overall_progress.current_phase == "ANALYZE"

        # Check phase states
        generate = next(p for p in progress.phases if p.name == "GENERATE")
        assert generate.status == PhaseStatus.COMPLETED
        assert generate.percent == 100.0

        analyze = next(p for p in progress.phases if p.name == "ANALYZE")
        assert analyze.status == PhaseStatus.RUNNING
        assert analyze.tasks_completed == 3
        assert analyze.percent == 60.0  # 3/5 = 60%

    def test_complete_workflow(self, calculator):
        """Test fully completed workflow."""
        tasks = {}

        # All GENERATE tasks
        for i in range(1, 6):
            tasks[f"gen{i}"] = NextflowTask(
                task_id=f"g{i:02d}/111111",
                name="GENERATE",
                tag=str(i),
                completed=1,
                total=1,
                status=TaskStatus.COMPLETED,
            )

        # All ANALYZE tasks
        for i in range(1, 6):
            tasks[f"ana{i}"] = NextflowTask(
                task_id=f"a{i:02d}/222222",
                name="ANALYZE",
                tag=str(i),
                completed=1,
                total=1,
                status=TaskStatus.COMPLETED,
            )

        # REPORT task
        tasks["report"] = NextflowTask(
            task_id="rr/333333",
            name="REPORT",
            completed=1,
            total=1,
            status=TaskStatus.COMPLETED,
        )

        progress = calculator.calculate_progress(tasks)

        # All phases complete = 100%
        assert progress.overall_progress.percent == 100.0
        assert progress.overall_progress.completed_tasks == 11
        assert progress.overall_progress.current_phase is None  # No phase running

        # All phases should be completed
        for phase in progress.phases:
            assert phase.status == PhaseStatus.COMPLETED
            assert phase.percent == 100.0

    def test_workflow_validation_invalid_order(self, calculator):
        """Test workflow validation detects invalid execution order."""
        tasks = {
            # REPORT running but prerequisites not complete
            "report": NextflowTask(
                task_id="rr/333333",
                name="REPORT",
                completed=0,
                total=1,
                status=TaskStatus.RUNNING,
            ),
            # GENERATE only partially complete
            "gen1": NextflowTask(
                task_id="g01/111111",
                name="GENERATE",
                tag="1",
                completed=1,
                total=1,
                status=TaskStatus.COMPLETED,
            ),
        }

        progress = calculator.calculate_progress(tasks)

        # Health should indicate invalid workflow
        assert not progress.health.workflow_valid
        assert not progress.health.tasks_in_order
        assert len(progress.health.warnings) > 0
        assert any("REPORT started before" in w for w in progress.health.warnings)

    def test_workflow_validation_with_failures(self, calculator):
        """Test workflow validation with failed tasks."""
        tasks = {
            "gen1": NextflowTask(
                task_id="g01/111111",
                name="GENERATE",
                tag="1",
                completed=0,
                total=1,
                status=TaskStatus.FAILED,
            ),
        }

        progress = calculator.calculate_progress(tasks)

        # Should have warning about failed task
        assert any("failed" in w.lower() for w in progress.health.warnings)

    def test_resource_metrics(self, calculator):
        """Test resource metrics calculation."""
        tasks = {
            "gen1": NextflowTask(
                task_id="g01/111111",
                name="GENERATE",
                tag="1",
                completed=1,
                total=1,
                status=TaskStatus.COMPLETED,
            ),
        }

        progress = calculator.calculate_progress(
            tasks,
            executor_info="k8s (11)",
            active_pods=8,
        )

        # Check resource metrics
        assert progress.resources.active_pods == 8
        assert progress.resources.executor == "k8s (11)"
        # CPU: 2 (controller) + 8 (workers) = 10 / 14 total = 71.4%
        assert progress.resources.cpu_usage_percent == pytest.approx(71.4, rel=0.1)
        # Memory: 4 (controller) + 32 (8 workers * 4) = 36 / 50 total = 72%
        assert progress.resources.memory_usage_gb == 36.0

    def test_weighted_progress_calculation(self):
        """Test that phase weights are applied correctly."""
        calculator = ProgressCalculator(batch_count=10)

        tasks = {}
        # 10/10 GENERATE complete (30% weight)
        for i in range(1, 11):
            tasks[f"gen{i}"] = NextflowTask(
                task_id=f"g{i:02d}/111111",
                name="GENERATE",
                tag=str(i),
                completed=1,
                total=1,
                status=TaskStatus.COMPLETED,
            )

        # 5/10 ANALYZE complete (60% weight)
        for i in range(1, 6):
            tasks[f"ana{i}"] = NextflowTask(
                task_id=f"a{i:02d}/222222",
                name="ANALYZE",
                tag=str(i),
                completed=1,
                total=1,
                status=TaskStatus.COMPLETED,
            )

        # 0/1 REPORT (10% weight)

        progress = calculator.calculate_progress(tasks)

        # Expected: (1.0 * 0.3) + (0.5 * 0.6) + (0.0 * 0.1) = 0.3 + 0.3 + 0 = 0.6 = 60%
        assert progress.overall_progress.percent == pytest.approx(60.0, rel=0.1)

    def test_task_details_limited(self, calculator):
        """Test that task details are limited to prevent message size issues."""
        tasks = {}

        # Create many tasks
        for i in range(1, 21):
            tasks[f"gen{i}"] = NextflowTask(
                task_id=f"g{i:02d}/111111",
                name="GENERATE",
                tag=str(i),
                completed=1 if i <= 10 else 0,
                total=1,
                status=TaskStatus.COMPLETED if i <= 10 else TaskStatus.RUNNING,
            )

        progress = calculator.calculate_progress(tasks)

        # Should only include limited task details
        assert len(progress.tasks) <= 15  # Max 5 per phase

    def test_phase_timing_tracking(self, calculator):
        """Test that phase start/end times are tracked."""
        # Start GENERATE
        tasks = {
            "gen1": NextflowTask(
                task_id="g01/111111",
                name="GENERATE",
                tag="1",
                completed=0,
                total=1,
                status=TaskStatus.RUNNING,
            ),
        }

        progress1 = calculator.calculate_progress(tasks)
        generate1 = next(p for p in progress1.phases if p.name == "GENERATE")
        assert generate1.started_at is not None
        assert generate1.finished_at is None

        # Complete GENERATE
        tasks["gen1"].status = TaskStatus.COMPLETED
        tasks["gen1"].completed = 1

        # Add remaining GENERATE tasks as complete
        for i in range(2, 6):
            tasks[f"gen{i}"] = NextflowTask(
                task_id=f"g{i:02d}/111111",
                name="GENERATE",
                tag=str(i),
                completed=1,
                total=1,
                status=TaskStatus.COMPLETED,
            )

        progress2 = calculator.calculate_progress(tasks)
        generate2 = next(p for p in progress2.phases if p.name == "GENERATE")
        assert generate2.started_at is not None
        assert generate2.finished_at is not None