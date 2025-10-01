"""Tests for Kubernetes job monitoring."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from kubernetes.client import V1Job, V1JobCondition, V1JobStatus, V1ObjectMeta

from app.kubernetes.monitor import wait_for_completion
from app.models import RunStatus


@pytest.fixture
def mock_k8s_client():
    """Mock Kubernetes client."""
    client = MagicMock()
    client.batch_v1 = MagicMock()
    return client


class TestWaitForCompletion:
    """Tests for wait_for_completion function."""

    @pytest.mark.asyncio
    async def test_wait_for_completion_success(self, mock_k8s_client):
        """Test successful job completion monitoring."""
        # Set up job status progression
        job_statuses = [
            # Initial state - pending
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(active=1, succeeded=None, failed=None),
            ),
            # Running state
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(active=1, succeeded=None, failed=None),
            ),
            # Completed state
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(
                    active=None,
                    succeeded=1,
                    failed=None,
                    conditions=[
                        V1JobCondition(type="Complete", status="True", reason="Completed")
                    ],
                ),
            ),
        ]

        mock_k8s_client.batch_v1.read_namespaced_job_status.side_effect = job_statuses

        # Track status callbacks
        status_updates = []

        async def on_status(status):
            status_updates.append(status)

        final_status = await wait_for_completion(
            client=mock_k8s_client,
            job_name="test-job",
            namespace="test-namespace",
            on_status=on_status,
        )

        assert final_status == RunStatus.SUCCEEDED
        assert RunStatus.RUNNING in status_updates
        assert RunStatus.SUCCEEDED in status_updates

    @pytest.mark.asyncio
    async def test_wait_for_completion_failure(self, mock_k8s_client):
        """Test job failure monitoring."""
        job_statuses = [
            # Running state
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(active=1, succeeded=None, failed=None),
            ),
            # Failed state
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(
                    active=None,
                    succeeded=None,
                    failed=1,
                    conditions=[
                        V1JobCondition(type="Failed", status="True", reason="BackoffLimitExceeded")
                    ],
                ),
            ),
        ]

        mock_k8s_client.batch_v1.read_namespaced_job_status.side_effect = job_statuses

        status_updates = []

        async def on_status(status):
            status_updates.append(status)

        final_status = await wait_for_completion(
            client=mock_k8s_client,
            job_name="test-job",
            namespace="test-namespace",
            on_status=on_status,
        )

        assert final_status == RunStatus.FAILED
        assert RunStatus.FAILED in status_updates

    @pytest.mark.asyncio
    async def test_wait_for_completion_with_retries(self, mock_k8s_client):
        """Test job monitoring handles transient failures."""
        # Simulate transient API errors followed by success
        mock_k8s_client.batch_v1.read_namespaced_job_status.side_effect = [
            Exception("API temporarily unavailable"),
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(active=1, succeeded=None, failed=None),
            ),
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(
                    active=None,
                    succeeded=1,
                    failed=None,
                    conditions=[
                        V1JobCondition(type="Complete", status="True", reason="Completed")
                    ],
                ),
            ),
        ]

        final_status = await wait_for_completion(
            client=mock_k8s_client,
            job_name="test-job",
            namespace="test-namespace",
        )

        assert final_status == RunStatus.SUCCEEDED
        # Verify it retried after error
        assert mock_k8s_client.batch_v1.read_namespaced_job_status.call_count >= 3

    @pytest.mark.asyncio
    async def test_wait_for_completion_job_deleted(self, mock_k8s_client):
        """Test handling when job is deleted during monitoring."""
        from kubernetes.client.exceptions import ApiException

        mock_k8s_client.batch_v1.read_namespaced_job_status.side_effect = [
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(active=1, succeeded=None, failed=None),
            ),
            ApiException(status=404, reason="Not Found"),
        ]

        final_status = await wait_for_completion(
            client=mock_k8s_client,
            job_name="test-job",
            namespace="test-namespace",
        )

        assert final_status == RunStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_wait_for_completion_timeout(self, mock_k8s_client, mocker):
        """Test job monitoring timeout behavior."""
        # Mock sleep to speed up test
        mock_sleep = mocker.patch("asyncio.sleep", new_callable=AsyncMock)
        mock_sleep.return_value = None

        # Job stays in running state
        mock_k8s_client.batch_v1.read_namespaced_job_status.return_value = V1Job(
            metadata=V1ObjectMeta(name="test-job"),
            status=V1JobStatus(active=1, succeeded=None, failed=None),
        )

        # Use a very short timeout
        final_status = await wait_for_completion(
            client=mock_k8s_client,
            job_name="test-job",
            namespace="test-namespace",
            timeout_seconds=0.1,
        )

        # Should return UNKNOWN status on timeout
        assert final_status == RunStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_wait_for_completion_poll_interval(self, mock_k8s_client, mocker):
        """Test that polling uses correct interval."""
        mock_sleep = mocker.patch("asyncio.sleep", new_callable=AsyncMock)
        mock_sleep.side_effect = [None, None, asyncio.CancelledError()]

        mock_k8s_client.batch_v1.read_namespaced_job_status.return_value = V1Job(
            metadata=V1ObjectMeta(name="test-job"),
            status=V1JobStatus(
                active=None,
                succeeded=1,
                failed=None,
                conditions=[
                    V1JobCondition(type="Complete", status="True", reason="Completed")
                ],
            ),
        )

        await wait_for_completion(
            client=mock_k8s_client,
            job_name="test-job",
            namespace="test-namespace",
            poll_interval_seconds=5.0,
        )

        # Verify sleep was called with correct interval
        mock_sleep.assert_called_with(5.0)

    @pytest.mark.asyncio
    async def test_wait_for_completion_status_callbacks(self, mock_k8s_client):
        """Test that status callbacks are invoked correctly."""
        job_statuses = [
            # Queued
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(active=None, succeeded=None, failed=None),
            ),
            # Starting
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(active=1, succeeded=None, failed=None, ready=0),
            ),
            # Running
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(active=1, succeeded=None, failed=None, ready=1),
            ),
            # Succeeded
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(
                    active=None,
                    succeeded=1,
                    failed=None,
                    conditions=[
                        V1JobCondition(type="Complete", status="True", reason="Completed")
                    ],
                ),
            ),
        ]

        mock_k8s_client.batch_v1.read_namespaced_job_status.side_effect = job_statuses

        callback_mock = AsyncMock()

        final_status = await wait_for_completion(
            client=mock_k8s_client,
            job_name="test-job",
            namespace="test-namespace",
            on_status=callback_mock,
        )

        # Verify all status transitions were reported
        callback_mock.assert_has_calls(
            [
                call(RunStatus.QUEUED),
                call(RunStatus.STARTING),
                call(RunStatus.RUNNING),
                call(RunStatus.SUCCEEDED),
            ]
        )
        assert final_status == RunStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_wait_for_completion_no_conditions(self, mock_k8s_client):
        """Test job status without conditions."""
        job_statuses = [
            # Job with succeeded count but no conditions
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(
                    active=None, succeeded=1, failed=None, conditions=None
                ),
            ),
        ]

        mock_k8s_client.batch_v1.read_namespaced_job_status.side_effect = job_statuses

        final_status = await wait_for_completion(
            client=mock_k8s_client,
            job_name="test-job",
            namespace="test-namespace",
        )

        # Should still detect success from succeeded count
        assert final_status == RunStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_wait_for_completion_mixed_conditions(self, mock_k8s_client):
        """Test job with multiple conditions."""
        job_statuses = [
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(
                    active=None,
                    succeeded=None,
                    failed=1,
                    conditions=[
                        V1JobCondition(type="Progressing", status="False"),
                        V1JobCondition(type="Failed", status="True", reason="DeadlineExceeded"),
                    ],
                ),
            ),
        ]

        mock_k8s_client.batch_v1.read_namespaced_job_status.side_effect = job_statuses

        final_status = await wait_for_completion(
            client=mock_k8s_client,
            job_name="test-job",
            namespace="test-namespace",
        )

        assert final_status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_wait_for_completion_unknown_state(self, mock_k8s_client):
        """Test job in unknown state."""
        job_statuses = [
            # Job with no clear state
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(active=None, succeeded=None, failed=None),
            ),
            # Still no clear state after retry
            V1Job(
                metadata=V1ObjectMeta(name="test-job"),
                status=V1JobStatus(active=None, succeeded=None, failed=None),
            ),
        ]

        mock_k8s_client.batch_v1.read_namespaced_job_status.side_effect = (
            job_statuses + [job_statuses[-1]] * 10
        )  # Keep returning same status

        final_status = await wait_for_completion(
            client=mock_k8s_client,
            job_name="test-job",
            namespace="test-namespace",
            timeout_seconds=0.5,
        )

        assert final_status == RunStatus.UNKNOWN