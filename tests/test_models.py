"""Tests for Pydantic models."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    ActiveRunStatus,
    CancelResponse,
    DemoResultMetrics,
    DemoRunRequest,
    DemoWorkflowParameters,
    PipelineParameters,
    RunHistoryEntry,
    RunInfo,
    RunRequest,
    RunResponse,
    RunStatus,
    StreamMessageType,
)


class TestRunStatus:
    """Tests for RunStatus enum."""

    def test_all_status_values(self):
        """Test that all expected status values exist."""
        assert RunStatus.QUEUED.value == "queued"
        assert RunStatus.STARTING.value == "starting"
        assert RunStatus.RUNNING.value == "running"
        assert RunStatus.SUCCEEDED.value == "succeeded"
        assert RunStatus.FAILED.value == "failed"
        assert RunStatus.CANCELLED.value == "cancelled"
        assert RunStatus.UNKNOWN.value == "unknown"

    def test_status_from_value(self):
        """Test creating status from string value."""
        assert RunStatus("running") == RunStatus.RUNNING
        assert RunStatus("succeeded") == RunStatus.SUCCEEDED

    def test_invalid_status(self):
        """Test that invalid status raises error."""
        with pytest.raises(ValueError):
            RunStatus("invalid_status")


class TestStreamMessageType:
    """Tests for StreamMessageType enum."""

    def test_message_types(self):
        """Test all stream message types."""
        assert StreamMessageType.STATUS.value == "status"
        assert StreamMessageType.PROGRESS.value == "progress"
        assert StreamMessageType.LOG.value == "log"
        assert StreamMessageType.COMPLETE.value == "complete"
        assert StreamMessageType.ERROR.value == "error"


class TestDemoWorkflowParameters:
    """Tests for DemoWorkflowParameters model."""

    def test_valid_batch_count(self):
        """Test valid batch count values."""
        params = DemoWorkflowParameters(batch_count=5)
        assert params.batch_count == 5

        params = DemoWorkflowParameters(batch_count=1)
        assert params.batch_count == 1

        params = DemoWorkflowParameters(batch_count=12)
        assert params.batch_count == 12

    def test_default_batch_count(self):
        """Test default batch count."""
        params = DemoWorkflowParameters()
        assert params.batch_count == 5

    def test_batch_count_too_low(self):
        """Test batch count validation (minimum 1)."""
        with pytest.raises(ValidationError) as exc_info:
            DemoWorkflowParameters(batch_count=0)

        errors = exc_info.value.errors()
        assert any("greater than or equal to 1" in str(error) for error in errors)

    def test_batch_count_too_high(self):
        """Test batch count validation (maximum 12)."""
        with pytest.raises(ValidationError) as exc_info:
            DemoWorkflowParameters(batch_count=13)

        errors = exc_info.value.errors()
        assert any("less than or equal to 12" in str(error) for error in errors)

    def test_negative_batch_count(self):
        """Test negative batch count."""
        with pytest.raises(ValidationError):
            DemoWorkflowParameters(batch_count=-1)


class TestDemoRunRequest:
    """Tests for DemoRunRequest model."""

    def test_valid_request(self):
        """Test valid demo run request."""
        request = DemoRunRequest(
            batch_count=7,
            triggered_by="portfolio-visitor"
        )
        assert request.batch_count == 7
        assert request.triggered_by == "portfolio-visitor"

    def test_default_values(self):
        """Test default values."""
        request = DemoRunRequest()
        assert request.batch_count == 5
        assert request.triggered_by is None

    def test_batch_count_validation(self):
        """Test batch count validation is inherited."""
        with pytest.raises(ValidationError):
            DemoRunRequest(batch_count=20)

    def test_json_serialization(self):
        """Test JSON serialization."""
        request = DemoRunRequest(batch_count=3, triggered_by="test")
        json_str = request.model_dump_json()
        data = json.loads(json_str)

        assert data["batch_count"] == 3
        assert data["triggered_by"] == "test"


class TestRunInfo:
    """Tests for RunInfo model."""

    def test_complete_run_info(self):
        """Test RunInfo with all fields."""
        now = datetime.now(timezone.utc)
        later = datetime.now(timezone.utc)

        info = RunInfo(
            run_id="r123abc",
            status=RunStatus.SUCCEEDED,
            started_at=now,
            finished_at=later,
            job_name="demo-r123abc",
            message="Pipeline completed successfully"
        )

        assert info.run_id == "r123abc"
        assert info.status == RunStatus.SUCCEEDED
        assert info.started_at == now
        assert info.finished_at == later
        assert info.job_name == "demo-r123abc"
        assert info.message == "Pipeline completed successfully"

    def test_minimal_run_info(self):
        """Test RunInfo with minimal fields."""
        now = datetime.now(timezone.utc)

        info = RunInfo(
            run_id="r456def",
            status=RunStatus.RUNNING,
            started_at=now,
            finished_at=None,
            job_name=None,
            message=None
        )

        assert info.run_id == "r456def"
        assert info.status == RunStatus.RUNNING
        assert info.finished_at is None
        assert info.job_name is None
        assert info.message is None

    def test_datetime_serialization(self):
        """Test that datetime fields serialize correctly."""
        now = datetime.now(timezone.utc)

        info = RunInfo(
            run_id="r789ghi",
            status=RunStatus.QUEUED,
            started_at=now,
            finished_at=None,
            job_name=None,
            message=None
        )

        json_data = info.model_dump(mode="json")
        assert isinstance(json_data["started_at"], str)
        # Should be ISO format
        assert "T" in json_data["started_at"]


class TestRunResponse:
    """Tests for RunResponse model."""

    def test_run_response_full(self):
        """Test RunResponse with all fields."""
        response = RunResponse(
            run_id="r111aaa",
            status=RunStatus.RUNNING,
            attached=False,
            job_name="demo-r111aaa",
            websocket_url="ws://localhost/api/v1/pipeline/stream"
        )

        assert response.run_id == "r111aaa"
        assert response.status == RunStatus.RUNNING
        assert response.attached is False
        assert response.job_name == "demo-r111aaa"
        assert response.websocket_url == "ws://localhost/api/v1/pipeline/stream"

    def test_run_response_attached(self):
        """Test RunResponse for attached run."""
        response = RunResponse(
            run_id="r222bbb",
            status=RunStatus.RUNNING,
            attached=True
        )

        assert response.attached is True
        assert response.job_name is None


class TestDemoResultMetrics:
    """Tests for DemoResultMetrics model."""

    def test_demo_metrics_complete(self):
        """Test complete demo metrics."""
        metrics = DemoResultMetrics(
            total_batches=5,
            total_records=5000,
            total_sum=2500000,
            average_value=500.0,
            worker_pods_spawned=10,
            execution_time_seconds=55.5,
            report_path="/workspace/results/r333ccc/report.json"
        )

        assert metrics.total_batches == 5
        assert metrics.total_records == 5000
        assert metrics.total_sum == 2500000
        assert metrics.average_value == 500.0
        assert metrics.worker_pods_spawned == 10
        assert metrics.execution_time_seconds == 55.5
        assert metrics.report_path == "/workspace/results/r333ccc/report.json"

    def test_demo_metrics_validation(self):
        """Test that metrics accept various numeric types."""
        metrics = DemoResultMetrics(
            total_batches=1,
            total_records=100,
            total_sum=5000,
            average_value=50.0,
            worker_pods_spawned=2,
            execution_time_seconds=10.0,
            report_path="/test/path"
        )

        assert metrics.total_batches == 1
        assert metrics.average_value == 50.0


class TestActiveRunStatus:
    """Tests for ActiveRunStatus model."""

    def test_active_run_status_complete(self):
        """Test ActiveRunStatus with all fields."""
        now = datetime.now(timezone.utc)

        status = ActiveRunStatus(
            active=True,
            run=RunInfo(
                run_id="r444ddd",
                status=RunStatus.RUNNING,
                started_at=now
            ),
            progress_percent=75.0,
            log_preview=["Line 1", "Line 2"],
            websocket_url="ws://localhost/stream",
            connected_clients=5,
            last_update=now,
            batches_generated=3,
            batches_analyzed=2,
            estimated_completion=now,
            parallel_workers_active=6,
            demo_metrics=None
        )

        assert status.active is True
        assert status.run.run_id == "r444ddd"
        assert status.progress_percent == 75.0
        assert len(status.log_preview) == 2
        assert status.connected_clients == 5
        assert status.batches_generated == 3
        assert status.batches_analyzed == 2
        assert status.parallel_workers_active == 6

    def test_active_run_status_inactive(self):
        """Test ActiveRunStatus for inactive state."""
        status = ActiveRunStatus(
            active=False,
            run=None
        )

        assert status.active is False
        assert status.run is None
        assert status.progress_percent is None
        assert status.log_preview == []
        assert status.connected_clients == 0

    def test_active_run_status_with_metrics(self):
        """Test ActiveRunStatus with completed metrics."""
        metrics = DemoResultMetrics(
            total_batches=5,
            total_records=5000,
            total_sum=2500000,
            average_value=500.0,
            worker_pods_spawned=10,
            execution_time_seconds=60.0,
            report_path="/workspace/results/r555eee/report.json"
        )

        status = ActiveRunStatus(
            active=False,
            demo_metrics=metrics
        )

        assert status.demo_metrics is not None
        assert status.demo_metrics.total_batches == 5
        assert status.demo_metrics.execution_time_seconds == 60.0


class TestRunHistoryEntry:
    """Tests for RunHistoryEntry model."""

    def test_history_entry_complete(self):
        """Test complete history entry."""
        now = datetime.now(timezone.utc)
        later = datetime.now(timezone.utc)

        entry = RunHistoryEntry(
            run_id="r666fff",
            status=RunStatus.SUCCEEDED,
            started_at=now,
            finished_at=later,
            triggered_by="admin",
            duration_seconds=60.0,
            batch_count=5
        )

        assert entry.run_id == "r666fff"
        assert entry.status == RunStatus.SUCCEEDED
        assert entry.triggered_by == "admin"
        assert entry.duration_seconds == 60.0
        assert entry.batch_count == 5

    def test_history_entry_minimal(self):
        """Test minimal history entry."""
        now = datetime.now(timezone.utc)

        entry = RunHistoryEntry(
            run_id="r777ggg",
            status=RunStatus.FAILED,
            started_at=now
        )

        assert entry.run_id == "r777ggg"
        assert entry.status == RunStatus.FAILED
        assert entry.finished_at is None
        assert entry.triggered_by is None
        assert entry.duration_seconds is None
        assert entry.batch_count is None


class TestCancelResponse:
    """Tests for CancelResponse model."""

    def test_cancel_response_success(self):
        """Test successful cancellation response."""
        response = CancelResponse(
            cancelled=True,
            run_id="r888hhh",
            message="Run cancelled successfully"
        )

        assert response.cancelled is True
        assert response.run_id == "r888hhh"
        assert response.message == "Run cancelled successfully"

    def test_cancel_response_no_active(self):
        """Test cancellation response when no active run."""
        response = CancelResponse(
            cancelled=False,
            run_id=None,
            message="No active run to cancel"
        )

        assert response.cancelled is False
        assert response.run_id is None
        assert "No active run" in response.message


class TestLegacyModels:
    """Tests for legacy models kept for backward compatibility."""

    def test_pipeline_parameters(self):
        """Test PipelineParameters model."""
        params = PipelineParameters(
            pipeline="nf-core/rnaseq",
            workdir="/workspace",
            parameters={"genome": "GRCh38", "read_length": 100}
        )

        assert params.pipeline == "nf-core/rnaseq"
        assert params.workdir == "/workspace"
        assert params.parameters["genome"] == "GRCh38"
        assert params.parameters["read_length"] == 100

    def test_pipeline_parameters_required_field(self):
        """Test that pipeline is required."""
        with pytest.raises(ValidationError):
            PipelineParameters()

    def test_run_request(self):
        """Test RunRequest model."""
        request = RunRequest(
            pipeline="nextflow-io/hello",
            triggered_by="test-user"
        )

        assert request.pipeline == "nextflow-io/hello"
        assert request.triggered_by == "test-user"
        assert request.workdir is None
        assert request.parameters == {}

    def test_run_request_with_parameters(self):
        """Test RunRequest with custom parameters."""
        request = RunRequest(
            pipeline="custom/pipeline",
            workdir="/custom/workdir",
            parameters={"cpu": 4, "memory": "8GB"},
            triggered_by="admin"
        )

        assert request.pipeline == "custom/pipeline"
        assert request.workdir == "/custom/workdir"
        assert request.parameters["cpu"] == 4
        assert request.parameters["memory"] == "8GB"


class TestModelValidationEdgeCases:
    """Tests for edge cases in model validation."""

    def test_batch_count_boundary_values(self):
        """Test batch count at boundaries."""
        # Minimum boundary
        request1 = DemoRunRequest(batch_count=1)
        assert request1.batch_count == 1

        # Maximum boundary
        request2 = DemoRunRequest(batch_count=12)
        assert request2.batch_count == 12

    def test_empty_string_handling(self):
        """Test how models handle empty strings."""
        request = DemoRunRequest(triggered_by="")
        assert request.triggered_by == ""  # Empty string is valid

        info = RunInfo(
            run_id="",  # Empty run_id should be allowed
            status=RunStatus.UNKNOWN,
            started_at=datetime.now(timezone.utc),
            message=""
        )
        assert info.run_id == ""
        assert info.message == ""

    def test_large_numbers(self):
        """Test models with very large numbers."""
        metrics = DemoResultMetrics(
            total_batches=1000000,
            total_records=999999999,
            total_sum=99999999999999,
            average_value=123456789.123456,
            worker_pods_spawned=10000,
            execution_time_seconds=86400.0,  # 24 hours
            report_path="/path"
        )

        assert metrics.total_records == 999999999
        assert metrics.total_sum == 99999999999999

    def test_unicode_in_strings(self):
        """Test Unicode characters in string fields."""
        request = DemoRunRequest(triggered_by="测试用户 🚀")
        assert request.triggered_by == "测试用户 🚀"

        info = RunInfo(
            run_id="r_unicode_✓",
            status=RunStatus.SUCCEEDED,
            started_at=datetime.now(timezone.utc),
            message="Pipeline completed ✅"
        )
        assert "✓" in info.run_id
        assert "✅" in info.message

    def test_model_copy_and_update(self):
        """Test Pydantic model copy with update."""
        original = DemoRunRequest(batch_count=5, triggered_by="user1")

        # Create a copy with updates
        updated = original.model_copy(update={"batch_count": 10})

        assert updated.batch_count == 10
        assert updated.triggered_by == "user1"
        assert original.batch_count == 5  # Original unchanged

    def test_model_dict_export_modes(self):
        """Test different export modes for models."""
        now = datetime.now(timezone.utc)
        info = RunInfo(
            run_id="r999iii",
            status=RunStatus.RUNNING,
            started_at=now,
            job_name=None,
            message=None
        )

        # Default mode includes None values
        dict_with_none = info.model_dump()
        assert "job_name" in dict_with_none
        assert dict_with_none["job_name"] is None

        # Exclude None values
        dict_without_none = info.model_dump(exclude_none=True)
        assert "job_name" not in dict_without_none
        assert "message" not in dict_without_none

        # JSON mode converts datetime to string
        json_dict = info.model_dump(mode="json")
        assert isinstance(json_dict["started_at"], str)