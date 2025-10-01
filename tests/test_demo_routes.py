"""Tests for demo API routes."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from app.main import app
from app.models import (
    RunStatus,
    RunResponse,
    ActiveRunStatus,
    RunInfo,
    CancelResponse,
    RunHistoryEntry,
    DemoResultMetrics,
)


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_pipeline_manager(mocker):
    """Mock the pipeline manager."""
    mock = AsyncMock()
    mocker.patch("app.api.routes.get_pipeline_manager", return_value=mock)
    return mock


class TestDemoRunEndpoint:
    """Tests for POST /demo/run endpoint."""

    def test_start_demo_run_success(self, client, mock_pipeline_manager):
        """Test successful demo run start."""
        mock_pipeline_manager.start_demo_run.return_value = RunResponse(
            run_id="r12345abcdef",
            status=RunStatus.QUEUED,
            attached=False,
            job_name="demo-r12345abcdef",
            websocket_url="ws://localhost/api/v1/pipeline/stream",
        )

        response = client.post("/api/v1/demo/run", json={"batch_count": 5})

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "r12345abcdef"
        assert data["status"] == "queued"
        assert data["attached"] is False
        assert data["job_name"] == "demo-r12345abcdef"

    def test_start_demo_run_with_triggered_by(self, client, mock_pipeline_manager):
        """Test demo run with triggered_by field."""
        mock_pipeline_manager.start_demo_run.return_value = RunResponse(
            run_id="r22345abcdef",
            status=RunStatus.QUEUED,
            attached=False,
            job_name="demo-r22345abcdef",
        )

        response = client.post(
            "/api/v1/demo/run",
            json={"batch_count": 3, "triggered_by": "portfolio-visitor"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "r22345abcdef"

    def test_start_demo_run_attach_to_existing(self, client, mock_pipeline_manager):
        """Test attaching to an existing run."""
        mock_pipeline_manager.start_demo_run.return_value = RunResponse(
            run_id="rexisting123",
            status=RunStatus.RUNNING,
            attached=True,
            job_name="demo-rexisting123",
        )

        response = client.post("/api/v1/demo/run", json={"batch_count": 5})

        assert response.status_code == 200
        data = response.json()
        assert data["attached"] is True
        assert data["status"] == "running"

    def test_start_demo_run_invalid_batch_count_too_low(self, client):
        """Test demo run with batch_count < 1."""
        response = client.post("/api/v1/demo/run", json={"batch_count": 0})

        assert response.status_code == 422
        errors = response.json()["detail"]
        assert any("greater than or equal to 1" in str(error) for error in errors)

    def test_start_demo_run_invalid_batch_count_too_high(self, client):
        """Test demo run with batch_count > 12."""
        response = client.post("/api/v1/demo/run", json={"batch_count": 13})

        assert response.status_code == 422
        errors = response.json()["detail"]
        assert any("less than or equal to 12" in str(error) for error in errors)

    def test_start_demo_run_negative_batch_count(self, client):
        """Test demo run with negative batch_count."""
        response = client.post("/api/v1/demo/run", json={"batch_count": -5})

        assert response.status_code == 422
        errors = response.json()["detail"]
        assert any("greater than or equal to 1" in str(error) for error in errors)

    def test_start_demo_run_default_batch_count(self, client, mock_pipeline_manager):
        """Test demo run with default batch_count when not provided."""
        mock_pipeline_manager.start_demo_run.return_value = RunResponse(
            run_id="rdefault123",
            status=RunStatus.QUEUED,
            attached=False,
            job_name="demo-rdefault123",
        )

        response = client.post("/api/v1/demo/run", json={})

        assert response.status_code == 200
        # Verify the default value is used (5)
        call_args = mock_pipeline_manager.start_demo_run.call_args
        assert call_args[0][0].batch_count == 5

    def test_start_demo_run_server_error(self, client, mock_pipeline_manager):
        """Test demo run when server error occurs."""
        mock_pipeline_manager.start_demo_run.side_effect = Exception("Internal error")

        response = client.post("/api/v1/demo/run", json={"batch_count": 5})

        assert response.status_code == 500


class TestDemoPreviewEndpoint:
    """Tests for GET /demo/preview endpoint."""

    def test_preview_demo_run_default(self, client):
        """Test preview with default batch count."""
        response = client.get("/api/v1/demo/preview")

        assert response.status_code == 200
        data = response.json()
        assert data["workflow"] == "Portfolio Demo Pipeline"
        assert data["batch_count"] == 5
        assert data["expected_pods"] == 10  # 5 * 2
        assert data["resource_usage"]["total_workers"] == 10

    def test_preview_demo_run_custom_batch_count(self, client):
        """Test preview with custom batch count."""
        response = client.get("/api/v1/demo/preview?batch_count=8")

        assert response.status_code == 200
        data = response.json()
        assert data["batch_count"] == 8
        assert data["expected_pods"] == 16  # 8 * 2
        assert data["resource_usage"]["total_workers"] == 16
        assert data["homelab_quota"]["optimized_for"] == "8 batches"

    def test_preview_demo_run_max_batch_count(self, client):
        """Test preview with maximum batch count."""
        response = client.get("/api/v1/demo/preview?batch_count=12")

        assert response.status_code == 200
        data = response.json()
        assert data["batch_count"] == 12
        assert data["expected_pods"] == 24  # 12 * 2

    def test_preview_demo_run_invalid_batch_count(self, client):
        """Test preview with invalid batch count."""
        response = client.get("/api/v1/demo/preview?batch_count=15")

        assert response.status_code == 422

    def test_preview_demo_run_resource_info(self, client):
        """Test preview returns correct resource information."""
        response = client.get("/api/v1/demo/preview?batch_count=5")

        assert response.status_code == 200
        data = response.json()
        assert data["resource_usage"]["worker_cpu_per_pod"] == "1"
        assert data["resource_usage"]["worker_memory_per_pod"] == "4GB"
        assert data["resource_usage"]["controller_cpu"] == "2"
        assert data["resource_usage"]["controller_memory"] == "4Gi"
        assert data["homelab_quota"]["available_memory"] == "50Gi"
        assert data["homelab_quota"]["available_cpu"] == "14"


class TestDemoStatusEndpoint:
    """Tests for GET /demo/status endpoint."""

    def test_get_status_active_run(self, client, mock_pipeline_manager):
        """Test getting status of active run."""
        now = datetime.now(timezone.utc)
        mock_pipeline_manager.current_status.return_value = ActiveRunStatus(
            active=True,
            run=RunInfo(
                run_id="ractive123",
                status=RunStatus.RUNNING,
                started_at=now,
                finished_at=None,
                job_name="demo-ractive123",
                message="Processing batch 3/5",
            ),
            progress_percent=60.0,
            log_preview=["[1/3] process > GENERATE (1) ✔", "[2/3] process > ANALYZE (1) ✔"],
            websocket_url="ws://localhost/api/v1/pipeline/stream",
            connected_clients=3,
            last_update=now,
            batches_generated=3,
            batches_analyzed=2,
        )

        response = client.get("/api/v1/demo/status")

        assert response.status_code == 200
        data = response.json()
        assert data["active"] is True
        assert data["run"]["run_id"] == "ractive123"
        assert data["run"]["status"] == "running"
        assert data["progress_percent"] == 60.0
        assert data["batches_generated"] == 3
        assert data["batches_analyzed"] == 2
        assert len(data["log_preview"]) == 2

    def test_get_status_no_active_run(self, client, mock_pipeline_manager):
        """Test getting status when no active run."""
        mock_pipeline_manager.current_status.return_value = ActiveRunStatus(
            active=False,
            run=None,
            progress_percent=None,
            log_preview=[],
            connected_clients=0,
        )

        response = client.get("/api/v1/demo/status")

        assert response.status_code == 200
        data = response.json()
        assert data["active"] is False
        assert data["run"] is None
        assert data["progress_percent"] is None

    def test_get_status_completed_run_with_metrics(self, client, mock_pipeline_manager):
        """Test getting status of completed run with demo metrics."""
        now = datetime.now(timezone.utc)
        mock_pipeline_manager.current_status.return_value = ActiveRunStatus(
            active=False,
            run=RunInfo(
                run_id="rcompleted123",
                status=RunStatus.SUCCEEDED,
                started_at=now,
                finished_at=now,
                job_name="demo-rcompleted123",
                message="Pipeline completed successfully",
            ),
            progress_percent=100.0,
            demo_metrics=DemoResultMetrics(
                total_batches=5,
                total_records=5000,
                total_sum=2500000,
                average_value=500.0,
                worker_pods_spawned=10,
                execution_time_seconds=55.5,
                report_path="/workspace/results/rcompleted123/report.json",
            ),
        )

        response = client.get("/api/v1/demo/status")

        assert response.status_code == 200
        data = response.json()
        assert data["progress_percent"] == 100.0
        assert data["demo_metrics"]["total_batches"] == 5
        assert data["demo_metrics"]["execution_time_seconds"] == 55.5


class TestDemoActiveEndpoint:
    """Tests for GET /demo/active endpoint."""

    def test_active_run_exists(self, client, mock_pipeline_manager):
        """Test when active run exists."""
        now = datetime.now(timezone.utc)
        mock_pipeline_manager.is_active.return_value = ActiveRunStatus(
            active=True,
            run=RunInfo(
                run_id="ractive456",
                status=RunStatus.RUNNING,
                started_at=now,
                finished_at=None,
                job_name="demo-ractive456",
            ),
        )

        response = client.get("/api/v1/demo/active")

        assert response.status_code == 200
        data = response.json()
        assert data["active"] is True
        assert data["run"]["run_id"] == "ractive456"

    def test_no_active_run(self, client, mock_pipeline_manager):
        """Test when no active run."""
        mock_pipeline_manager.is_active.return_value = ActiveRunStatus(
            active=False,
            run=None,
        )

        response = client.get("/api/v1/demo/active")

        assert response.status_code == 200
        data = response.json()
        assert data["active"] is False
        assert data["run"] is None


class TestDemoCancelEndpoint:
    """Tests for DELETE /demo/cancel endpoint."""

    def test_cancel_active_run_success(self, client, mock_pipeline_manager):
        """Test successful cancellation of active run."""
        mock_pipeline_manager.cancel_active_run.return_value = CancelResponse(
            cancelled=True,
            run_id="rcancelled123",
            message="Run cancelled successfully",
        )

        response = client.delete("/api/v1/demo/cancel")

        assert response.status_code == 200
        data = response.json()
        assert data["cancelled"] is True
        assert data["run_id"] == "rcancelled123"
        assert data["message"] == "Run cancelled successfully"

    def test_cancel_no_active_run(self, client, mock_pipeline_manager):
        """Test cancellation when no active run."""
        mock_pipeline_manager.cancel_active_run.return_value = CancelResponse(
            cancelled=False,
            run_id=None,
            message="No active run to cancel",
        )

        response = client.delete("/api/v1/demo/cancel")

        assert response.status_code == 200
        data = response.json()
        assert data["cancelled"] is False
        assert data["run_id"] is None
        assert "No active run" in data["message"]

    def test_cancel_run_failure(self, client, mock_pipeline_manager):
        """Test cancellation failure."""
        mock_pipeline_manager.cancel_active_run.side_effect = Exception("Failed to delete job")

        response = client.delete("/api/v1/demo/cancel")

        assert response.status_code == 500


class TestDemoHistoryEndpoint:
    """Tests for GET /demo/history endpoint."""

    def test_get_history_default_limit(self, client, mock_pipeline_manager):
        """Test getting history with default limit."""
        now = datetime.now(timezone.utc)
        mock_pipeline_manager.get_history.return_value = [
            RunHistoryEntry(
                run_id="rhistory1",
                status=RunStatus.SUCCEEDED,
                started_at=now,
                finished_at=now,
                triggered_by="admin",
                duration_seconds=55,
                batch_count=5,
            ),
            RunHistoryEntry(
                run_id="rhistory2",
                status=RunStatus.FAILED,
                started_at=now,
                finished_at=now,
                triggered_by="portfolio-visitor",
                duration_seconds=30,
                batch_count=3,
            ),
        ]

        response = client.get("/api/v1/demo/history")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["run_id"] == "rhistory1"
        assert data[0]["status"] == "succeeded"
        assert data[1]["run_id"] == "rhistory2"
        assert data[1]["status"] == "failed"

    def test_get_history_custom_limit(self, client, mock_pipeline_manager):
        """Test getting history with custom limit."""
        mock_pipeline_manager.get_history.return_value = []

        response = client.get("/api/v1/demo/history?limit=20")

        assert response.status_code == 200
        mock_pipeline_manager.get_history.assert_called_once_with(limit=20)

    def test_get_history_max_limit(self, client, mock_pipeline_manager):
        """Test getting history with maximum limit."""
        mock_pipeline_manager.get_history.return_value = []

        response = client.get("/api/v1/demo/history?limit=100")

        assert response.status_code == 200
        mock_pipeline_manager.get_history.assert_called_once_with(limit=100)

    def test_get_history_invalid_limit_too_high(self, client):
        """Test getting history with limit > 100."""
        response = client.get("/api/v1/demo/history?limit=101")

        assert response.status_code == 422

    def test_get_history_invalid_limit_too_low(self, client):
        """Test getting history with limit < 1."""
        response = client.get("/api/v1/demo/history?limit=0")

        assert response.status_code == 422

    def test_get_history_empty(self, client, mock_pipeline_manager):
        """Test getting empty history."""
        mock_pipeline_manager.get_history.return_value = []

        response = client.get("/api/v1/demo/history")

        assert response.status_code == 200
        data = response.json()
        assert data == []