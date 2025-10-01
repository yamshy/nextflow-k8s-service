"""Tests for metrics API endpoints."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.models import RunStatus, RunHistoryEntry, DemoResultMetrics


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_state_store(mocker):
    """Mock the state store."""
    mock = AsyncMock()
    mocker.patch("app.api.metrics.get_state_store", return_value=mock)
    return mock


class TestAggregateMetrics:
    """Tests for GET /demo/metrics endpoint."""

    def test_aggregate_metrics_with_history(self, client, mock_state_store):
        """Test aggregate metrics with successful and failed runs."""
        now = datetime.now(timezone.utc)
        history = [
            RunHistoryEntry(
                run_id="r001",
                status=RunStatus.SUCCEEDED,
                started_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=9),
                duration_seconds=60.0,
                triggered_by="user1",
                batch_count=5,
            ),
            RunHistoryEntry(
                run_id="r002",
                status=RunStatus.SUCCEEDED,
                started_at=now - timedelta(minutes=8),
                finished_at=now - timedelta(minutes=7, seconds=15),
                duration_seconds=45.0,
                triggered_by="user2",
                batch_count=3,
            ),
            RunHistoryEntry(
                run_id="r003",
                status=RunStatus.FAILED,
                started_at=now - timedelta(minutes=6),
                finished_at=now - timedelta(minutes=5, seconds=30),
                duration_seconds=30.0,
                triggered_by="user1",
                batch_count=5,
            ),
            RunHistoryEntry(
                run_id="r004",
                status=RunStatus.SUCCEEDED,
                started_at=now - timedelta(minutes=4),
                finished_at=now - timedelta(minutes=3),
                duration_seconds=55.0,
                triggered_by="user3",
                batch_count=4,
            ),
        ]
        mock_state_store.get_history.return_value = history

        response = client.get("/api/v1/demo/metrics")

        assert response.status_code == 200
        data = response.json()

        assert data["total_runs"] == 4
        assert data["successful_runs"] == 3
        assert data["failed_runs"] == 1
        assert data["success_rate"] == 75.0
        assert data["average_execution_time_seconds"] == 53.33  # (60+45+55)/3
        assert data["min_execution_time_seconds"] == 45.0
        assert data["max_execution_time_seconds"] == 60.0
        assert len(data["recent_runs"]) == 4

        # Check recent runs structure
        first_run = data["recent_runs"][0]
        assert first_run["run_id"] == "r001"
        assert first_run["status"] == "succeeded"
        assert first_run["duration_seconds"] == 60.0

    def test_aggregate_metrics_empty_history(self, client, mock_state_store):
        """Test aggregate metrics with no runs."""
        mock_state_store.get_history.return_value = []

        response = client.get("/api/v1/demo/metrics")

        assert response.status_code == 200
        data = response.json()

        assert data["total_runs"] == 0
        assert data["successful_runs"] == 0
        assert data["failed_runs"] == 0
        assert data["success_rate"] == 0.0
        assert data["average_execution_time_seconds"] == 0.0
        assert data["total_batches_processed"] == 0
        assert data["total_records_processed"] == 0

    def test_aggregate_metrics_only_failed_runs(self, client, mock_state_store):
        """Test aggregate metrics with only failed runs."""
        now = datetime.now(timezone.utc)
        history = [
            RunHistoryEntry(
                run_id="r001",
                status=RunStatus.FAILED,
                started_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=9),
                duration_seconds=60.0,
                triggered_by="user1",
            ),
            RunHistoryEntry(
                run_id="r002",
                status=RunStatus.FAILED,
                started_at=now - timedelta(minutes=8),
                finished_at=now - timedelta(minutes=7),
                duration_seconds=None,  # No duration for this failed run
                triggered_by="user2",
            ),
        ]
        mock_state_store.get_history.return_value = history

        response = client.get("/api/v1/demo/metrics")

        assert response.status_code == 200
        data = response.json()

        assert data["total_runs"] == 2
        assert data["successful_runs"] == 0
        assert data["failed_runs"] == 2
        assert data["success_rate"] == 0.0
        assert data["average_execution_time_seconds"] == 0.0  # No successful runs

    def test_aggregate_metrics_recent_runs_limited(self, client, mock_state_store):
        """Test that recent_runs is limited to 10 entries."""
        now = datetime.now(timezone.utc)
        # Create 15 runs
        history = [
            RunHistoryEntry(
                run_id=f"r{i:03d}",
                status=RunStatus.SUCCEEDED,
                started_at=now - timedelta(minutes=i * 2),
                finished_at=now - timedelta(minutes=i * 2 - 1),
                duration_seconds=60.0,
                triggered_by=f"user{i % 3}",
            )
            for i in range(15)
        ]
        mock_state_store.get_history.return_value = history

        response = client.get("/api/v1/demo/metrics")

        assert response.status_code == 200
        data = response.json()

        assert data["total_runs"] == 15
        assert len(data["recent_runs"]) == 10  # Limited to 10

    def test_aggregate_metrics_with_cancelled_runs(self, client, mock_state_store):
        """Test aggregate metrics including cancelled runs."""
        now = datetime.now(timezone.utc)
        history = [
            RunHistoryEntry(
                run_id="r001",
                status=RunStatus.SUCCEEDED,
                started_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=9),
                duration_seconds=60.0,
            ),
            RunHistoryEntry(
                run_id="r002",
                status=RunStatus.CANCELLED,
                started_at=now - timedelta(minutes=8),
                finished_at=now - timedelta(minutes=7, seconds=30),
                duration_seconds=30.0,
            ),
        ]
        mock_state_store.get_history.return_value = history

        response = client.get("/api/v1/demo/metrics")

        assert response.status_code == 200
        data = response.json()

        assert data["total_runs"] == 2
        assert data["successful_runs"] == 1
        assert data["failed_runs"] == 0  # Cancelled is not counted as failed


class TestGetDemoResults:
    """Tests for GET /demo/results/{run_id} endpoint."""

    def test_get_results_success(self, client, mock_state_store, mocker):
        """Test getting results for a successful run."""
        now = datetime.now(timezone.utc)
        history = [
            RunHistoryEntry(
                run_id="r123",
                status=RunStatus.SUCCEEDED,
                started_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
                duration_seconds=60.0,
                triggered_by="user1",
                batch_count=5,
            )
        ]
        mock_state_store.get_history.return_value = history

        # Mock parse_report_json
        mock_parse = mocker.patch("app.api.metrics.parse_report_json")
        mock_parse.return_value = DemoResultMetrics(
            total_batches=5,
            total_records=5000,
            total_sum=2500000,
            average_value=500.0,
            worker_pods_spawned=10,
            execution_time_seconds=60.0,
            report_path="/workspace/results/r123/report.json",
        )

        response = client.get("/api/v1/demo/results/r123")

        assert response.status_code == 200
        data = response.json()

        assert data["total_batches"] == 5
        assert data["total_records"] == 5000
        assert data["average_value"] == 500.0
        assert data["execution_time_seconds"] == 60.0
        assert data["report_path"] == "/workspace/results/r123/report.json"

        # Verify parse_report_json was called correctly
        mock_parse.assert_called_once_with(
            report_path="/workspace/results/r123/report.json",
            execution_time_seconds=60.0,
            worker_pods_spawned=10,
        )

    def test_get_results_run_not_found(self, client, mock_state_store):
        """Test getting results for non-existent run."""
        mock_state_store.get_history.return_value = []

        response = client.get("/api/v1/demo/results/r999")

        assert response.status_code == 404
        assert "Run r999 not found" in response.json()["detail"]

    def test_get_results_run_not_succeeded(self, client, mock_state_store):
        """Test getting results for failed run."""
        now = datetime.now(timezone.utc)
        history = [
            RunHistoryEntry(
                run_id="r123",
                status=RunStatus.FAILED,
                started_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
                duration_seconds=60.0,
            )
        ]
        mock_state_store.get_history.return_value = history

        response = client.get("/api/v1/demo/results/r123")

        assert response.status_code == 400
        assert "did not complete successfully" in response.json()["detail"]

    def test_get_results_no_duration(self, client, mock_state_store):
        """Test getting results when duration is not available."""
        now = datetime.now(timezone.utc)
        history = [
            RunHistoryEntry(
                run_id="r123",
                status=RunStatus.SUCCEEDED,
                started_at=now - timedelta(minutes=5),
                finished_at=None,  # Not finished
                duration_seconds=None,
            )
        ]
        mock_state_store.get_history.return_value = history

        response = client.get("/api/v1/demo/results/r123")

        assert response.status_code == 400
        assert "duration not available" in response.json()["detail"]

    def test_get_results_parse_failure(self, client, mock_state_store, mocker):
        """Test when parse_report_json returns None."""
        now = datetime.now(timezone.utc)
        history = [
            RunHistoryEntry(
                run_id="r123",
                status=RunStatus.SUCCEEDED,
                started_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
                duration_seconds=60.0,
            )
        ]
        mock_state_store.get_history.return_value = history

        # Mock parse_report_json to return None (file not found)
        mock_parse = mocker.patch("app.api.metrics.parse_report_json")
        mock_parse.return_value = None

        response = client.get("/api/v1/demo/results/r123")

        assert response.status_code == 404
        assert "Results not found" in response.json()["detail"]


class TestShowcaseSummary:
    """Tests for GET /demo/showcase endpoint."""

    def test_showcase_with_runs(self, client, mock_state_store):
        """Test showcase summary with run history."""
        now = datetime.now(timezone.utc)
        history = [
            RunHistoryEntry(
                run_id="r001",
                status=RunStatus.SUCCEEDED,
                started_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=9),
                duration_seconds=60.0,
            ),
            RunHistoryEntry(
                run_id="r002",
                status=RunStatus.SUCCEEDED,
                started_at=now - timedelta(minutes=8),
                finished_at=now - timedelta(minutes=7),
                duration_seconds=50.0,
            ),
            RunHistoryEntry(
                run_id="r003",
                status=RunStatus.FAILED,
                started_at=now - timedelta(minutes=6),
                finished_at=now - timedelta(minutes=5),
                duration_seconds=30.0,
            ),
        ]
        mock_state_store.get_history.return_value = history

        response = client.get("/api/v1/demo/showcase")

        assert response.status_code == 200
        data = response.json()

        # Check structure
        assert "demo_info" in data
        assert "infrastructure" in data
        assert "performance" in data
        assert "capabilities" in data
        assert "tech_stack" in data
        assert "demo_narrative" in data

        # Check demo info
        assert data["demo_info"]["name"] == "Parallel Data Pipeline Orchestrator"
        assert "github_url" in data["demo_info"]

        # Check infrastructure
        assert data["infrastructure"]["platform"] == "Kubernetes"
        assert data["infrastructure"]["homelab_specs"]["total_memory"] == "50Gi"
        assert data["infrastructure"]["homelab_specs"]["total_cpu"] == 14

        # Check performance metrics
        assert data["performance"]["total_runs"] == 3
        assert data["performance"]["successful_runs"] == 2
        assert data["performance"]["average_runtime_seconds"] == 55.0  # (60+50)/2
        assert data["performance"]["min_runtime_seconds"] == 50.0
        assert data["performance"]["max_runtime_seconds"] == 60.0

        # Check capabilities
        assert "real_time_streaming" in data["capabilities"]
        assert "parallel_processing" in data["capabilities"]

        # Check tech stack
        assert data["tech_stack"]["backend"] == "FastAPI (Python 3.12+)"
        assert data["tech_stack"]["orchestration"] == "Nextflow + Kubernetes"

        # Check narrative exists
        assert len(data["demo_narrative"]) > 100

    def test_showcase_empty_history(self, client, mock_state_store):
        """Test showcase summary with no runs."""
        mock_state_store.get_history.return_value = []

        response = client.get("/api/v1/demo/showcase")

        assert response.status_code == 200
        data = response.json()

        # Performance should handle empty history
        assert data["performance"]["total_runs"] == 0
        assert data["performance"]["successful_runs"] == 0
        assert data["performance"]["average_runtime_seconds"] == 0.0

        # Other sections should still be populated
        assert data["infrastructure"]["platform"] == "Kubernetes"
        assert "demo_narrative" in data

    def test_showcase_only_failed_runs(self, client, mock_state_store):
        """Test showcase with only failed runs."""
        now = datetime.now(timezone.utc)
        history = [
            RunHistoryEntry(
                run_id="r001",
                status=RunStatus.FAILED,
                started_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=9),
                duration_seconds=None,
            ),
        ]
        mock_state_store.get_history.return_value = history

        response = client.get("/api/v1/demo/showcase")

        assert response.status_code == 200
        data = response.json()

        assert data["performance"]["total_runs"] == 1
        assert data["performance"]["successful_runs"] == 0
        assert data["performance"]["average_runtime_seconds"] == 0.0

    def test_showcase_resource_calculations(self, client, mock_state_store):
        """Test that resource calculations are correct."""
        mock_state_store.get_history.return_value = []

        response = client.get("/api/v1/demo/showcase")

        assert response.status_code == 200
        data = response.json()

        # Check resource math
        infra = data["infrastructure"]
        assert infra["homelab_specs"]["total_memory"] == "50Gi"
        assert infra["homelab_specs"]["total_cpu"] == 14
        assert infra["homelab_specs"]["available_for_workers"]["memory"] == "46Gi"
        assert infra["homelab_specs"]["available_for_workers"]["cpu"] == 12

        # Controller uses 2 CPU + 4Gi
        assert infra["controller_resources"]["cpu"] == "2"
        assert infra["controller_resources"]["memory"] == "4Gi"

        # Workers use 1 CPU + 4GB each
        assert infra["worker_resources"]["cpu_per_worker"] == "1"
        assert infra["worker_resources"]["memory_per_worker"] == "4GB"
        assert infra["worker_resources"]["max_parallel_workers"] == 12