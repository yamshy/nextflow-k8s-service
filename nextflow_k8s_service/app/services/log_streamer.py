"""Service that streams Kubernetes pod logs to connected WebSocket clients."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from ..config import Settings
from ..kubernetes.jobs import get_pod_log_stream, list_job_pods
from ..models import LogChunk
from ..utils.broadcaster import Broadcaster

logger = logging.getLogger(__name__)


class LogStreamer:
    def __init__(self, *, settings: Settings, broadcaster: Broadcaster) -> None:
        self._settings = settings
        self._broadcaster = broadcaster
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stoppers: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def start(self, run_id: str, job_name: str) -> None:
        async with self._lock:
            if run_id in self._tasks:
                return
            stop_event = asyncio.Event()
            task = asyncio.create_task(self._stream_loop(run_id=run_id, job_name=job_name, stop_event=stop_event))
            self._stoppers[run_id] = stop_event
            self._tasks[run_id] = task

    async def stop(self, run_id: str) -> None:
        async with self._lock:
            stop_event = self._stoppers.pop(run_id, None)
            task = self._tasks.pop(run_id, None)
        if stop_event:
            stop_event.set()
        if task:
            await task

    async def close(self) -> None:
        for run_id in list(self._tasks.keys()):
            await self.stop(run_id)

    async def _stream_loop(self, *, run_id: str, job_name: str, stop_event: asyncio.Event) -> None:
        logger.info("Starting log stream for run %s", run_id)
        cursors: dict[tuple[str, str], datetime] = {}
        try:
            while not stop_event.is_set():
                pods = await list_job_pods(job_name=job_name, settings=self._settings)
                for pod in pods:
                    metadata = getattr(pod, "metadata", None)
                    pod_name = getattr(metadata, "name", None)
                    if not pod_name:
                        continue

                    for container_name in self._container_names_for_pod(pod):
                        cursor_key = (pod_name, container_name)
                        since_time = cursors.get(cursor_key)
                        cursor_label = f"{pod_name}:{container_name}"
                        try:
                            logs = await get_pod_log_stream(
                                pod_name=pod_name,
                                container=container_name,
                                settings=self._settings,
                                since_time=since_time,
                            )
                        except Exception as exc:  # pragma: no cover - defensive
                            logger.debug("Unable to fetch logs for %s: %s", cursor_label, exc)
                            continue

                        if not logs:
                            continue

                        for line in logs.splitlines():
                            timestamp, message = self._split_timestamp(line)
                            effective_timestamp = timestamp or datetime.now(timezone.utc)
                            cursors[cursor_key] = effective_timestamp
                            chunk = LogChunk(
                                run_id=run_id,
                                timestamp=effective_timestamp,
                                message=message,
                            )
                            await self._broadcaster.broadcast({"type": "log", "payload": chunk.model_dump()})

                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self._settings.log_fetch_interval_seconds)
                except asyncio.TimeoutError:
                    continue
        except Exception:  # pragma: no cover - ensure we log unexpected failures
            logger.exception("Log streaming failed for run %s", run_id)
        finally:
            logger.info("Stopped log stream for run %s", run_id)

    @staticmethod
    def _split_timestamp(line: str) -> tuple[datetime | None, str]:
        if not line:
            return None, ""
        try:
            ts, message = line.split(" ", 1)
            return datetime.fromisoformat(ts.replace("Z", "+00:00")), message
        except ValueError:
            return None, line

    @staticmethod
    def _container_names_for_pod(pod: object) -> list[str]:
        spec = getattr(pod, "spec", None)
        if spec is None:
            return ["nextflow"]

        container_names: list[str] = []
        for attr in ("containers", "init_containers"):
            containers = getattr(spec, attr, None) or []
            for container in containers:
                name = getattr(container, "name", None)
                if name:
                    container_names.append(name)

        ephemeral_containers = getattr(spec, "ephemeral_containers", None) or []
        for container in ephemeral_containers:
            name = getattr(container, "name", None)
            if name:
                container_names.append(name)

        if not container_names:
            container_names.append("nextflow")

        return container_names
