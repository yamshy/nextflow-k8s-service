"""High-level orchestration for Nextflow pipeline execution."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

from ..config import Settings
from ..kubernetes import jobs
from ..kubernetes.monitor import wait_for_completion
from ..models import (
    ActiveRunStatus,
    CancelResponse,
    RunHistoryEntry,
    RunInfo,
    RunRequest,
    RunResponse,
    RunStatus,
)
from ..utils.broadcaster import Broadcaster
from .log_streamer import LogStreamer
from .state_store import StateStore

logger = logging.getLogger(__name__)


class PipelineManager:
    def __init__(
        self,
        *,
        settings: Settings,
        state_store: StateStore,
        log_streamer: LogStreamer,
        broadcaster: Broadcaster,
    ) -> None:
        self._settings = settings
        self._state_store = state_store
        self._log_streamer = log_streamer
        self._broadcaster = broadcaster
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._task_lock = asyncio.Lock()

    async def start_or_attach_run(self, request: RunRequest) -> RunResponse:
        active = await self._state_store.get_active_run()
        if active.active and active.run:
            return RunResponse(
                run_id=active.run.run_id,
                status=active.run.status,
                attached=True,
                job_name=active.run.job_name,
            )

        run_id = uuid.uuid4().hex[:12]
        job_name = f"nextflow-run-{run_id}"

        acquired = await self._state_store.acquire_active_run(
            run_id=run_id,
            job_name=job_name,
            triggered_by=request.triggered_by,
        )
        if not acquired:
            active = await self._state_store.get_active_run()
            if active.run:
                return RunResponse(
                    run_id=active.run.run_id,
                    status=active.run.status,
                    attached=True,
                    job_name=active.run.job_name,
                )
            raise RuntimeError("Unable to acquire pipeline lock")

        await self._broadcast({
            "type": "run_status",
            "payload": {
                "run_id": run_id,
                "status": RunStatus.STARTING,
            },
        })

        try:
            job = await jobs.create_job(run_id=run_id, params=request.parameters, settings=self._settings)
        except Exception as exc:
            await self._state_store.finish_active_run(RunStatus.FAILED, message=str(exc))
            raise

        await self._state_store.update_active_status(RunStatus.RUNNING)
        await self._log_streamer.start(run_id=run_id, job_name=job.metadata.name)
        await self._schedule_monitor(run_id=run_id, job_name=job.metadata.name)
        logger.info("Started Nextflow run %s with job %s", run_id, job.metadata.name)

        return RunResponse(run_id=run_id, status=RunStatus.RUNNING, attached=False, job_name=job.metadata.name)

    async def _schedule_monitor(self, *, run_id: str, job_name: str) -> None:
        async with self._task_lock:
            if run_id in self._tasks:
                return
            task = asyncio.create_task(self._monitor_run(run_id=run_id, job_name=job_name))
            self._tasks[run_id] = task

    async def _monitor_run(self, *, run_id: str, job_name: str) -> None:
        async def _on_status(status: RunStatus) -> None:
            await self._state_store.update_active_status(status)
            await self._broadcast({
                "type": "run_status",
                "payload": {
                    "run_id": run_id,
                    "status": status,
                },
            })

        try:
            terminal_status = await wait_for_completion(
                run_id=run_id,
                job_name=job_name,
                settings=self._settings,
                on_status=_on_status,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Run %s monitor failed: %s", run_id, exc)
            terminal_status = RunStatus.UNKNOWN

        await self._log_streamer.stop(run_id)
        run_info = await self._state_store.finish_active_run(terminal_status)
        if terminal_status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.UNKNOWN}:
            await jobs.delete_job(
                job_name,
                settings=self._settings,
                grace_period_seconds=self._settings.cleanup_grace_period_seconds,
            )

        await self._broadcast({
            "type": "run_completed",
            "payload": {
                "run_id": run_id,
                "status": terminal_status,
                "run": run_info.dict() if run_info else None,
            },
        })

        async with self._task_lock:
            self._tasks.pop(run_id, None)

    async def cancel_active_run(self) -> CancelResponse:
        active = await self._state_store.get_active_run()
        if not active.active or not active.run or not active.run.job_name:
            return CancelResponse(run_id=None, status=RunStatus.UNKNOWN, cancelled=False, detail="No active run")

        await jobs.delete_job(active.run.job_name, settings=self._settings, grace_period_seconds=0)
        await self._log_streamer.stop(active.run.run_id)
        info = await self._state_store.cancel_active_run("Cancelled by user")
        await self._broadcast({
            "type": "run_cancelled",
            "payload": {
                "run_id": active.run.run_id,
            },
        })

        return CancelResponse(
            run_id=active.run.run_id,
            status=info.status if info else RunStatus.CANCELLED,
            cancelled=True,
            detail=None,
        )

    async def is_active(self) -> ActiveRunStatus:
        return await self._state_store.get_active_run()

    async def get_history(self, limit: Optional[int] = None) -> list[RunHistoryEntry]:
        return await self._state_store.get_history(limit=limit)

    async def current_status(self) -> ActiveRunStatus:
        active = await self._state_store.get_active_run()
        if active.active:
            return active

        history = await self._state_store.get_history(limit=1)
        if history:
            last = history[0]
            run = RunInfo(
                run_id=last.run_id,
                status=last.status,
                started_at=last.started_at,
                finished_at=last.finished_at,
                job_name=last.job_name,
                message=None,
            )
            return ActiveRunStatus(active=False, run=run)

        return ActiveRunStatus(active=False, run=None)

    async def shutdown(self) -> None:
        async with self._task_lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                continue

    async def _broadcast(self, message: Dict[str, Any]) -> None:
        await self._broadcaster.broadcast(message)
