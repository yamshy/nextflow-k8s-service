import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.models import RunStatus
from app.services.state_store import StateStore


@pytest.mark.asyncio
async def test_acquire_and_finish_run_cycle() -> None:
    settings = Settings()
    store = StateStore(settings=settings, redis=None)

    acquired = await store.acquire_active_run(run_id="run123", job_name="job-run123", triggered_by="tester")
    assert acquired

    active = await store.get_active_run()
    assert active.active is True
    assert active.run is not None
    assert active.run.run_id == "run123"

    await store.update_active_status(RunStatus.RUNNING)
    active = await store.get_active_run()
    assert active.run is not None
    assert active.run.status == RunStatus.RUNNING

    info = await store.finish_active_run(RunStatus.SUCCEEDED, message="completed")
    assert info is not None
    assert info.status == RunStatus.SUCCEEDED
    assert info.message == "completed"

    active_after = await store.get_active_run()
    assert active_after.active is False

    history = await store.get_history()
    assert len(history) == 1
    assert history[0].run_id == "run123"
    assert history[0].status == RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_history_respects_limit() -> None:
    settings = Settings(run_history_limit=2)
    store = StateStore(settings=settings, redis=None)

    for idx in range(3):
        run_id = f"run-{idx}"
        acquired = await store.acquire_active_run(run_id=run_id, job_name=f"job-{idx}", triggered_by=None)
        assert acquired
        await store.finish_active_run(RunStatus.SUCCEEDED)

    history = await store.get_history()
    assert len(history) == 2
    assert history[0].run_id == "run-2"
    assert history[1].run_id == "run-1"


@pytest.mark.asyncio
async def test_cancel_active_run_marks_cancelled() -> None:
    settings = Settings()
    store = StateStore(settings=settings, redis=None)

    await store.acquire_active_run(run_id="cancel-me", job_name="job-cancel", triggered_by="tester")
    response = await store.cancel_active_run("stop")

    assert response is not None
    assert response.status == RunStatus.CANCELLED
    assert response.run_id == "cancel-me"

    active_after = await store.get_active_run()
    assert active_after.active is False


# ========== NEW TESTS FOR COMPREHENSIVE COVERAGE ==========


class TestProgressTracking:
    """Tests for progress tracking functionality."""

    @pytest.mark.asyncio
    async def test_update_progress_with_completed_total(self):
        """Test updating progress with completed/total counts."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        await store.acquire_active_run(run_id="prog1", job_name="job-prog1")

        # Update progress with counts
        await store.update_progress(completed=3, total=10)

        active = await store.get_active_run()
        assert active.progress_percent == 30.0

    @pytest.mark.asyncio
    async def test_update_progress_with_percentage(self):
        """Test updating progress with direct percentage."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        await store.acquire_active_run(run_id="prog2", job_name="job-prog2")

        # Update progress with percentage
        await store.update_progress(percent=75.5)

        active = await store.get_active_run()
        assert active.progress_percent == 75.5

    @pytest.mark.asyncio
    async def test_update_progress_zero_total(self):
        """Test updating progress when total is zero."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        await store.acquire_active_run(run_id="prog3", job_name="job-prog3")

        # Update progress with zero total
        await store.update_progress(completed=0, total=0)

        active = await store.get_active_run()
        assert active.progress_percent == 0.0

    @pytest.mark.asyncio
    async def test_update_progress_completed_exceeds_total(self):
        """Test that progress is capped at 100% when completed > total."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        await store.acquire_active_run(run_id="prog4", job_name="job-prog4")

        # Update with completed > total
        await store.update_progress(completed=15, total=10)

        active = await store.get_active_run()
        assert active.progress_percent == 100.0  # Should be capped

    @pytest.mark.asyncio
    async def test_get_progress_no_active_run(self):
        """Test getting progress when no active run."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        progress = await store.get_progress()
        assert progress is None


class TestLogManagement:
    """Tests for log management functionality."""

    @pytest.mark.asyncio
    async def test_append_log_lines(self):
        """Test appending log lines."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        await store.acquire_active_run(run_id="log1", job_name="job-log1")

        # Append log lines
        await store.append_log_lines(["Line 1", "Line 2", "Line 3"])

        logs = await store.get_recent_logs()
        assert len(logs) == 3
        assert logs == ["Line 1", "Line 2", "Line 3"]

    @pytest.mark.asyncio
    async def test_append_log_lines_respects_max_size(self):
        """Test that log deque respects maximum size."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        await store.acquire_active_run(run_id="log2", job_name="job-log2")

        # Append more than 200 lines (the default max)
        lines = [f"Line {i}" for i in range(250)]
        await store.append_log_lines(lines)

        logs = await store.get_recent_logs()
        assert len(logs) == 200  # Should be capped at 200
        assert logs[0] == "Line 50"  # First 50 should be dropped
        assert logs[-1] == "Line 249"

    @pytest.mark.asyncio
    async def test_get_recent_logs_with_limit(self):
        """Test getting recent logs with a limit."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        await store.acquire_active_run(run_id="log3", job_name="job-log3")

        lines = [f"Line {i}" for i in range(20)]
        await store.append_log_lines(lines)

        # Get only last 5 lines
        logs = await store.get_recent_logs(limit=5)
        assert len(logs) == 5
        assert logs == ["Line 15", "Line 16", "Line 17", "Line 18", "Line 19"]

    @pytest.mark.asyncio
    async def test_get_recent_logs_empty(self):
        """Test getting logs when none exist."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        await store.acquire_active_run(run_id="log4", job_name="job-log4")

        logs = await store.get_recent_logs()
        assert logs == []

    @pytest.mark.asyncio
    async def test_logs_cleared_on_finish(self):
        """Test that logs are cleared when run finishes."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        await store.acquire_active_run(run_id="log5", job_name="job-log5")
        await store.append_log_lines(["Test log"])

        # Finish the run
        await store.finish_active_run(RunStatus.SUCCEEDED)

        # Logs should be empty after finish
        logs = await store.get_recent_logs()
        assert logs == []


class TestConnectionTracking:
    """Tests for WebSocket connection tracking."""

    @pytest.mark.asyncio
    async def test_set_connected_clients(self):
        """Test setting connected client count."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        await store.set_connected_clients(5)
        count = await store.get_connected_clients()
        assert count == 5

    @pytest.mark.asyncio
    async def test_set_connected_clients_negative(self):
        """Test that negative client count is handled."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        # Should use max(0, count)
        await store.set_connected_clients(-3)
        count = await store.get_connected_clients()
        assert count == 0

    @pytest.mark.asyncio
    async def test_increment_decrement_clients(self):
        """Test incrementing and decrementing client count."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        # Start at 0
        assert await store.get_connected_clients() == 0

        # Increment
        await store.set_connected_clients(1)
        await store.set_connected_clients(2)
        await store.set_connected_clients(3)
        assert await store.get_connected_clients() == 3

        # Decrement
        await store.set_connected_clients(2)
        await store.set_connected_clients(1)
        assert await store.get_connected_clients() == 1


class TestBroadcastTracking:
    """Tests for broadcast timestamp tracking."""

    @pytest.mark.asyncio
    async def test_update_last_broadcast(self):
        """Test updating last broadcast timestamp."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        now = datetime.now(timezone.utc)
        await store.update_last_broadcast(now)

        last_broadcast = await store.get_last_broadcast()
        assert last_broadcast == now

    @pytest.mark.asyncio
    async def test_get_last_broadcast_none(self):
        """Test getting last broadcast when never set."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        last_broadcast = await store.get_last_broadcast()
        assert last_broadcast is None


class TestRedisIntegration:
    """Tests for Redis integration when available."""

    @pytest.mark.asyncio
    async def test_create_with_redis_url(self, mocker):
        """Test StateStore creation with Redis URL."""
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)

        mock_redis_from_url = mocker.patch("redis.asyncio.from_url")
        mock_redis_from_url.return_value = mock_redis

        settings = Settings(redis_url="redis://localhost:6379")
        store = await StateStore.create(settings)

        assert store._redis == mock_redis
        mock_redis_from_url.assert_called_once_with("redis://localhost:6379")
        mock_redis.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_fallback_on_connection_error(self, mocker):
        """Test fallback to in-memory when Redis connection fails."""
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("Connection failed"))

        mock_redis_from_url = mocker.patch("redis.asyncio.from_url")
        mock_redis_from_url.return_value = mock_redis

        settings = Settings(redis_url="redis://localhost:6379")
        store = await StateStore.create(settings)

        # Should fall back to in-memory
        assert store._redis is None

    @pytest.mark.asyncio
    async def test_acquire_run_with_redis(self, mocker):
        """Test acquiring run with Redis locking."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)  # Lock acquired
        mock_redis.hset = AsyncMock()

        settings = Settings()
        store = StateStore(settings=settings, redis=mock_redis)

        acquired = await store.acquire_active_run(run_id="redis1", job_name="job-redis1")

        assert acquired
        # Verify Redis operations
        mock_redis.set.assert_called_once()  # Lock acquisition
        mock_redis.hset.assert_called()  # State storage

    @pytest.mark.asyncio
    async def test_acquire_run_redis_lock_failed(self, mocker):
        """Test failing to acquire lock in Redis."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=False)  # Lock not acquired

        settings = Settings()
        store = StateStore(settings=settings, redis=mock_redis)

        acquired = await store.acquire_active_run(run_id="redis2", job_name="job-redis2")

        assert not acquired


class TestTTLAndExpiration:
    """Tests for TTL-based history expiration."""

    @pytest.mark.asyncio
    async def test_history_expiration(self, mocker):
        """Test that expired runs are purged from history."""
        # Mock datetime to control time
        mock_now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mocker.patch("app.services.state_store.datetime").now.return_value = mock_now

        settings = Settings(run_ttl_minutes=30)
        store = StateStore(settings=settings, redis=None)

        # Add runs at different times
        for i in range(3):
            # Mock time for each run
            run_time = mock_now - timedelta(minutes=i * 20)
            with patch("app.services.state_store.datetime") as mock_dt:
                mock_dt.now.return_value = run_time
                mock_dt.timezone = timezone

                await store.acquire_active_run(run_id=f"ttl{i}", job_name=f"job-ttl{i}")
                await store.finish_active_run(RunStatus.SUCCEEDED)

        # Now purge expired (older than 30 minutes)
        with patch("app.services.state_store.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.timezone = timezone

            history = await store.get_history()

        # Only runs 0 and 1 should remain (less than 30 minutes old)
        assert len(history) == 2
        assert history[0].run_id == "ttl0"
        assert history[1].run_id == "ttl1"

    @pytest.mark.asyncio
    async def test_purge_expired_history_called_on_get(self):
        """Test that expired history is purged on get_history call."""
        settings = Settings(run_ttl_minutes=1)  # Very short TTL
        store = StateStore(settings=settings, redis=None)

        # Add a run
        await store.acquire_active_run(run_id="expire1", job_name="job-expire1")
        await store.finish_active_run(RunStatus.SUCCEEDED)

        # Verify it's in history
        history = await store.get_history()
        assert len(history) == 1

        # Wait for expiration
        await asyncio.sleep(61)  # Wait more than 1 minute

        # Should be purged now
        history = await store.get_history()
        assert len(history) == 0


class TestRuntimeStateManagement:
    """Tests for runtime state reset functionality."""

    @pytest.mark.asyncio
    async def test_clear_runtime_state(self):
        """Test clearing runtime state."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        # Set up some state
        await store.acquire_active_run(run_id="clear1", job_name="job-clear1")
        await store.update_progress(percent=50.0)
        await store.append_log_lines(["Test log"])
        await store.set_connected_clients(3)

        # Clear runtime state
        await store.clear_runtime_state()

        # Everything should be reset
        active = await store.get_active_run()
        assert active.active is False
        assert await store.get_progress() is None
        assert await store.get_recent_logs() == []
        # Note: connected_clients is not reset by clear_runtime_state

    @pytest.mark.asyncio
    async def test_monitor_task_cleanup(self):
        """Test that monitor task is cleaned up properly."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        # Create a mock monitor task
        mock_task = AsyncMock()
        mock_task.cancel = MagicMock()
        mock_task.cancelled = MagicMock(return_value=True)

        await store.acquire_active_run(run_id="monitor1", job_name="job-monitor1")
        store._monitor_task = mock_task

        # Clear runtime state should cancel the task
        await store.clear_runtime_state()

        mock_task.cancel.assert_called_once()


class TestConcurrentAccess:
    """Tests for concurrent access scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_acquire_attempts(self):
        """Test that only one acquire succeeds with concurrent attempts."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        # First acquire should succeed
        acquired1 = await store.acquire_active_run(run_id="conc1", job_name="job-conc1")
        assert acquired1

        # Second acquire should fail
        acquired2 = await store.acquire_active_run(run_id="conc2", job_name="job-conc2")
        assert not acquired2

        # Active run should be the first one
        active = await store.get_active_run()
        assert active.run.run_id == "conc1"

    @pytest.mark.asyncio
    async def test_acquire_after_finish(self):
        """Test acquiring new run after finishing previous."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        # First run
        await store.acquire_active_run(run_id="seq1", job_name="job-seq1")
        await store.finish_active_run(RunStatus.SUCCEEDED)

        # Should be able to acquire new run
        acquired = await store.acquire_active_run(run_id="seq2", job_name="job-seq2")
        assert acquired

        active = await store.get_active_run()
        assert active.run.run_id == "seq2"


class TestEdgeCases:
    """Tests for edge cases and error scenarios."""

    @pytest.mark.asyncio
    async def test_finish_without_active_run(self):
        """Test finishing when no active run exists."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        result = await store.finish_active_run(RunStatus.SUCCEEDED)
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_without_active_run(self):
        """Test cancelling when no active run exists."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        result = await store.cancel_active_run("No run to cancel")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_status_without_active_run(self):
        """Test updating status when no active run exists."""
        settings = Settings()
        store = StateStore(settings=settings, redis=None)

        # Should not raise exception
        await store.update_active_status(RunStatus.RUNNING)

        # Status should remain inactive
        active = await store.get_active_run()
        assert active.active is False
