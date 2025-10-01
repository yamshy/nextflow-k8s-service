from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models import (
    ActiveRunStatus,
    DemoRunRequest,
    RunHistoryEntry,
    RunInfo,
    RunStatus,
    StreamMessageType,
)
from app.services.log_streamer import LogStreamer
from app.services.pipeline_manager import PipelineManager
from app.services.state_store import StateStore
from app.utils.broadcaster import Broadcaster


@pytest.mark.asyncio
async def test_start_or_attach_run_returns_existing(mocker) -> None:
    settings = Settings()
    run_info = RunInfo(
        run_id="existing",
        status=RunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        finished_at=None,
        job_name="nextflow-run-existing",
        message=None,
    )
    state_store: StateStore = mocker.AsyncMock(spec=StateStore)
    state_store.get_active_run.return_value = ActiveRunStatus(active=True, run=run_info)

    log_streamer: LogStreamer = mocker.AsyncMock(spec=LogStreamer)
    broadcaster: Broadcaster = mocker.AsyncMock(spec=Broadcaster)

    manager = PipelineManager(
        settings=settings,
        state_store=state_store,
        log_streamer=log_streamer,
        broadcaster=broadcaster,
    )

    run_request = DemoRunRequest(batch_count=5, triggered_by="tester")

    result = await manager.start_demo_run(run_request)

    assert result.run_id == "existing"
    assert result.attached is True
    assert result.status == RunStatus.RUNNING
    assert result.job_name == "nextflow-run-existing"
    assert broadcaster.broadcast.await_count == 0
    assert result.websocket_url == "/api/v1/pipeline/stream"


@pytest.mark.asyncio
async def test_start_demo_run_creates_new_job(mocker) -> None:
    settings = Settings()
    state_store: StateStore = mocker.AsyncMock(spec=StateStore)
    state_store.get_active_run.return_value = ActiveRunStatus(active=False, run=None)
    state_store.get_progress.return_value = (0.0, None, None)
    state_store.get_recent_logs.return_value = []
    state_store.get_connected_clients.return_value = 0
    state_store.get_last_broadcast.return_value = None
    state_store.acquire_active_run.return_value = True
    state_store.update_progress.return_value = (0.0, None, None)
    state_store.update_progress.return_value = (0.0, None, None)

    log_streamer: LogStreamer = mocker.AsyncMock(spec=LogStreamer)
    broadcaster: Broadcaster = mocker.AsyncMock(spec=Broadcaster)

    manager = PipelineManager(
        settings=settings,
        state_store=state_store,
        log_streamer=log_streamer,
        broadcaster=broadcaster,
    )
    mock_schedule = mocker.AsyncMock()
    mocker.patch.object(manager, "_schedule_monitor", mock_schedule)

    fake_uuid = mocker.Mock(hex="1234567890abcdef")
    mocker.patch("app.services.pipeline_manager.uuid.uuid4", return_value=fake_uuid)

    fake_job = SimpleNamespace(metadata=SimpleNamespace(name="nextflow-run-r1234567890a"))
    mock_create = mocker.AsyncMock(return_value=fake_job)
    mocker.patch("app.services.pipeline_manager.jobs.create_demo_job", mock_create)

    run_request = DemoRunRequest(
        batch_count=3,
        triggered_by="ci",
    )

    result = await manager.start_demo_run(run_request)

    assert result.attached is False
    assert result.status == RunStatus.RUNNING
    assert result.job_name == "nextflow-run-r1234567890a"
    assert result.run_id == "r1234567890a"
    assert result.websocket_url == "/api/v1/pipeline/stream"

    mock_create.assert_awaited_once()
    state_store.acquire_active_run.assert_awaited_once()
    state_store.update_active_status.assert_awaited_once_with(RunStatus.RUNNING)
    log_streamer.start.assert_awaited_once_with(run_id="r1234567890a", job_name="nextflow-run-r1234567890a", batch_count=3)
    mock_schedule.assert_awaited_once_with(run_id="r1234567890a", job_name="nextflow-run-r1234567890a")
    assert broadcaster.broadcast.await_count >= 2
    first_message = broadcaster.broadcast.await_args_list[0].args[0]
    assert first_message["type"] == StreamMessageType.STATUS.value
    assert first_message["data"]["status"] == RunStatus.STARTING.value
    assert mock_create.await_args.kwargs["batch_count"] == 3


@pytest.mark.asyncio
async def test_start_demo_run_broadcasts_complete_on_job_creation_failure(mocker) -> None:
    settings = Settings()
    state_store: StateStore = mocker.AsyncMock(spec=StateStore)
    state_store.get_active_run.return_value = ActiveRunStatus(active=False, run=None)
    state_store.acquire_active_run.return_value = True
    state_store.update_progress.return_value = (0.0, None, None)

    run_info = RunInfo(
        run_id="rfeedbead123",
        status=RunStatus.FAILED,
        started_at=datetime.now(timezone.utc),
        finished_at=None,
        job_name="nextflow-run-rfeedbead123",
        message="boom",
    )
    state_store.finish_active_run.return_value = run_info

    log_streamer: LogStreamer = mocker.AsyncMock(spec=LogStreamer)
    broadcaster: Broadcaster = mocker.AsyncMock(spec=Broadcaster)

    manager = PipelineManager(
        settings=settings,
        state_store=state_store,
        log_streamer=log_streamer,
        broadcaster=broadcaster,
    )

    fake_uuid = mocker.Mock(hex="feedbead12345678deadbeef")
    mocker.patch("app.services.pipeline_manager.uuid.uuid4", return_value=fake_uuid)

    creation_error = RuntimeError("boom")
    mocker.patch("app.services.pipeline_manager.jobs.create_demo_job", side_effect=creation_error)

    run_request = DemoRunRequest(batch_count=5, triggered_by="tester")

    with pytest.raises(RuntimeError) as excinfo:
        await manager.start_demo_run(run_request)

    assert str(excinfo.value) == "boom"

    state_store.finish_active_run.assert_awaited_once_with(RunStatus.FAILED, message="boom")

    broadcast_calls = broadcaster.broadcast.await_args_list
    assert len(broadcast_calls) >= 3
    error_payload = broadcast_calls[1].args[0]
    assert error_payload["type"] == StreamMessageType.ERROR.value
    assert error_payload["data"]["stage"] == "job_creation"
    assert error_payload["data"]["message"] == "boom"

    complete_payload = broadcast_calls[2].args[0]
    assert complete_payload["type"] == StreamMessageType.COMPLETE.value
    assert complete_payload["data"]["status"] == RunStatus.FAILED.value
    assert complete_payload["data"]["run"]["run_id"] == "rfeedbead123"
    assert complete_payload["run_id"] == "rfeedbead123"


@pytest.mark.asyncio
async def test_start_demo_run_cleans_up_on_monitor_failure(mocker) -> None:
    settings = Settings()
    state_store: StateStore = mocker.AsyncMock(spec=StateStore)
    state_store.get_active_run.return_value = ActiveRunStatus(active=False, run=None)
    state_store.acquire_active_run.return_value = True

    run_info = RunInfo(
        run_id="cleanup",
        status=RunStatus.FAILED,
        started_at=datetime.now(timezone.utc),
        finished_at=None,
        job_name="nextflow-run-cleanup",
        message=None,
    )
    state_store.finish_active_run.return_value = run_info

    log_streamer: LogStreamer = mocker.AsyncMock(spec=LogStreamer)
    broadcaster: Broadcaster = mocker.AsyncMock(spec=Broadcaster)

    manager = PipelineManager(
        settings=settings,
        state_store=state_store,
        log_streamer=log_streamer,
        broadcaster=broadcaster,
    )

    fake_uuid = mocker.Mock(hex="1234567890abcdef")
    mocker.patch("app.services.pipeline_manager.uuid.uuid4", return_value=fake_uuid)

    fake_job = SimpleNamespace(metadata=SimpleNamespace(name="nextflow-run-r1234567890a"))
    mock_create = mocker.AsyncMock(return_value=fake_job)
    mocker.patch("app.services.pipeline_manager.jobs.create_demo_job", mock_create)

    mock_delete = mocker.AsyncMock()
    mocker.patch("app.services.pipeline_manager.jobs.delete_job", mock_delete)

    schedule_error = RuntimeError("monitor boom")
    mock_schedule = mocker.AsyncMock(side_effect=schedule_error)
    mocker.patch.object(manager, "_schedule_monitor", mock_schedule)

    run_request = DemoRunRequest(batch_count=5, triggered_by="tester")

    with pytest.raises(RuntimeError) as excinfo:
        await manager.start_demo_run(run_request)

    assert str(excinfo.value) == "monitor boom"

    log_streamer.start.assert_awaited_once_with(run_id="r1234567890a", job_name="nextflow-run-r1234567890a", batch_count=5)
    log_streamer.stop.assert_awaited_once_with("r1234567890a")

    mock_delete.assert_awaited_once_with("nextflow-run-r1234567890a", settings=settings, grace_period_seconds=0)

    state_store.finish_active_run.assert_awaited_once_with(RunStatus.FAILED, message="monitor boom")
    state_store.set_monitor_task.assert_awaited()

    assert broadcaster.broadcast.await_count >= 2
    broadcast_payload = broadcaster.broadcast.await_args_list[-1].args[0]
    assert broadcast_payload["type"] == StreamMessageType.COMPLETE.value
    assert broadcast_payload["data"]["status"] == RunStatus.FAILED.value
    assert broadcast_payload["run_id"] == "r1234567890a"


@pytest.mark.asyncio
async def test_current_status_falls_back_to_history(mocker) -> None:
    settings = Settings()
    state_store: StateStore = mocker.AsyncMock(spec=StateStore)
    state_store.get_active_run.return_value = ActiveRunStatus(active=False, run=None)
    state_store.get_progress.return_value = (0.0, None, None)
    state_store.get_recent_logs.return_value = []
    state_store.get_connected_clients.return_value = 0
    state_store.get_last_broadcast.return_value = None

    last_run = RunHistoryEntry(
        run_id="history-run",
        status=RunStatus.SUCCEEDED,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        duration_seconds=5.0,
        triggered_by="ops",
        job_name="nextflow-run-history",
    )
    state_store.get_history.return_value = [last_run]

    log_streamer: LogStreamer = mocker.AsyncMock(spec=LogStreamer)
    broadcaster: Broadcaster = mocker.AsyncMock(spec=Broadcaster)

    manager = PipelineManager(
        settings=settings,
        state_store=state_store,
        log_streamer=log_streamer,
        broadcaster=broadcaster,
    )

    status = await manager.current_status()

    assert status.active is False
    assert status.run is not None
    assert status.run.run_id == "history-run"
    assert status.run.status == RunStatus.SUCCEEDED
    assert status.websocket_url == "/api/v1/pipeline/stream"


@pytest.mark.asyncio
async def test_cancel_active_run_handles_job_delete_failure(mocker) -> None:
    settings = Settings()
    now = datetime.now(timezone.utc)
    active_run = RunInfo(
        run_id="run-123",
        status=RunStatus.RUNNING,
        started_at=now,
        finished_at=None,
        job_name="nextflow-run-123",
        message=None,
    )
    cancelled_info = RunInfo(
        run_id="run-123",
        status=RunStatus.CANCELLED,
        started_at=now,
        finished_at=now,
        job_name="nextflow-run-123",
        message="Cancelled by user",
    )

    state_store: StateStore = mocker.AsyncMock(spec=StateStore)
    state_store.get_active_run.return_value = ActiveRunStatus(active=True, run=active_run)
    state_store.cancel_active_run.return_value = cancelled_info

    log_streamer: LogStreamer = mocker.AsyncMock(spec=LogStreamer)
    broadcaster: Broadcaster = mocker.AsyncMock(spec=Broadcaster)

    manager = PipelineManager(
        settings=settings,
        state_store=state_store,
        log_streamer=log_streamer,
        broadcaster=broadcaster,
    )

    mock_delete = mocker.AsyncMock(side_effect=RuntimeError("delete failed"))
    mocker.patch("app.services.pipeline_manager.jobs.delete_job", mock_delete)

    response = await manager.cancel_active_run()

    mock_delete.assert_awaited_once_with("nextflow-run-123", settings=settings, grace_period_seconds=0)
    log_streamer.stop.assert_awaited_once_with("run-123")
    state_store.cancel_active_run.assert_awaited_once_with("Cancelled by user")
    state_store.set_monitor_task.assert_awaited()

    assert broadcaster.broadcast.await_count == 2
    status_msg = broadcaster.broadcast.await_args_list[0].args[0]
    assert status_msg["data"]["status"] == RunStatus.CANCELLED.value
    complete_msg = broadcaster.broadcast.await_args_list[1].args[0]
    assert complete_msg["type"] == StreamMessageType.COMPLETE.value

    assert response.run_id == "run-123"
    assert response.status == RunStatus.CANCELLED
    assert response.cancelled is False
    assert response.detail is not None and "delete failed" in response.detail
