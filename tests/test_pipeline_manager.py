from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models import ActiveRunStatus, PipelineParameters, RunHistoryEntry, RunInfo, RunRequest, RunStatus
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
    manager._broadcast = mocker.AsyncMock()  # replace internal fan-out with stub

    run_request = RunRequest(
        parameters=PipelineParameters(pipeline="hello", parameters={}, workdir=None),
        triggered_by="tester",
    )

    result = await manager.start_or_attach_run(run_request)

    assert result.run_id == "existing"
    assert result.attached is True
    assert result.status == RunStatus.RUNNING
    assert result.job_name == "nextflow-run-existing"
    assert manager._broadcast.await_count == 0


@pytest.mark.asyncio
async def test_start_or_attach_run_creates_new_job(mocker) -> None:
    settings = Settings()
    state_store: StateStore = mocker.AsyncMock(spec=StateStore)
    state_store.get_active_run.return_value = ActiveRunStatus(active=False, run=None)
    state_store.acquire_active_run.return_value = True

    log_streamer: LogStreamer = mocker.AsyncMock(spec=LogStreamer)
    broadcaster: Broadcaster = mocker.AsyncMock(spec=Broadcaster)

    manager = PipelineManager(
        settings=settings,
        state_store=state_store,
        log_streamer=log_streamer,
        broadcaster=broadcaster,
    )

    manager._broadcast = mocker.AsyncMock()
    mock_schedule = mocker.AsyncMock()
    mocker.patch.object(manager, "_schedule_monitor", mock_schedule)

    fake_uuid = mocker.Mock(hex="1234567890abcdef")
    mocker.patch("app.services.pipeline_manager.uuid.uuid4", return_value=fake_uuid)

    fake_job = SimpleNamespace(metadata=SimpleNamespace(name="nextflow-run-1234567890ab"))
    mock_create = mocker.AsyncMock(return_value=fake_job)
    mocker.patch("app.services.pipeline_manager.jobs.create_job", mock_create)

    run_request = RunRequest(
        parameters=PipelineParameters(pipeline="nf", parameters={"profile": "test"}, workdir="/data"),
        triggered_by="ci",
    )

    result = await manager.start_or_attach_run(run_request)

    assert result.attached is False
    assert result.status == RunStatus.RUNNING
    assert result.job_name == "nextflow-run-1234567890ab"
    assert result.run_id == "1234567890ab"

    mock_create.assert_awaited_once()
    state_store.acquire_active_run.assert_awaited_once()
    state_store.update_active_status.assert_awaited_once_with(RunStatus.RUNNING)
    log_streamer.start.assert_awaited_once_with(run_id="1234567890ab", job_name="nextflow-run-1234567890ab")
    mock_schedule.assert_awaited_once_with(run_id="1234567890ab", job_name="nextflow-run-1234567890ab")
    assert manager._broadcast.await_count >= 1
    first_message = manager._broadcast.await_args_list[0].args[0]
    assert first_message["payload"]["status"] == RunStatus.STARTING


@pytest.mark.asyncio
async def test_start_or_attach_run_cleans_up_on_monitor_failure(mocker) -> None:
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

    manager._broadcast = mocker.AsyncMock()

    fake_uuid = mocker.Mock(hex="1234567890abcdef")
    mocker.patch("app.services.pipeline_manager.uuid.uuid4", return_value=fake_uuid)

    fake_job = SimpleNamespace(metadata=SimpleNamespace(name="nextflow-run-1234567890ab"))
    mock_create = mocker.AsyncMock(return_value=fake_job)
    mocker.patch("app.services.pipeline_manager.jobs.create_job", mock_create)

    mock_delete = mocker.AsyncMock()
    mocker.patch("app.services.pipeline_manager.jobs.delete_job", mock_delete)

    schedule_error = RuntimeError("monitor boom")
    mock_schedule = mocker.AsyncMock(side_effect=schedule_error)
    mocker.patch.object(manager, "_schedule_monitor", mock_schedule)

    run_request = RunRequest(
        parameters=PipelineParameters(pipeline="nf", parameters={}, workdir=None),
        triggered_by="tester",
    )

    with pytest.raises(RuntimeError) as excinfo:
        await manager.start_or_attach_run(run_request)

    assert str(excinfo.value) == "monitor boom"

    log_streamer.start.assert_awaited_once_with(run_id="1234567890ab", job_name="nextflow-run-1234567890ab")
    log_streamer.stop.assert_awaited_once_with("1234567890ab")

    mock_delete.assert_awaited_once_with(
        "nextflow-run-1234567890ab", settings=settings, grace_period_seconds=0
    )

    state_store.finish_active_run.assert_awaited_once_with(RunStatus.FAILED, message="monitor boom")

    assert manager._broadcast.await_count == 2
    broadcast_payload = manager._broadcast.await_args_list[-1].args[0]
    assert broadcast_payload["type"] == "run_completed"
    assert broadcast_payload["payload"]["status"] == RunStatus.FAILED
    assert broadcast_payload["payload"]["run_id"] == "1234567890ab"


@pytest.mark.asyncio
async def test_current_status_falls_back_to_history(mocker) -> None:
    settings = Settings()
    state_store: StateStore = mocker.AsyncMock(spec=StateStore)
    state_store.get_active_run.return_value = ActiveRunStatus(active=False, run=None)

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
    manager._broadcast = mocker.AsyncMock()

    status = await manager.current_status()

    assert status.active is False
    assert status.run is not None
    assert status.run.run_id == "history-run"
    assert status.run.status == RunStatus.SUCCEEDED
