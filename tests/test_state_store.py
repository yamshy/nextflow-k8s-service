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
