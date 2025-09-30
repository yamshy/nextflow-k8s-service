"""Service that streams Kubernetes pod logs to connected WebSocket clients."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..config import Settings
from ..kubernetes.jobs import get_pod_log_stream, list_job_pods
from ..models import StreamMessageType
from ..utils.broadcaster import Broadcaster
from .state_store import StateStore

logger = logging.getLogger(__name__)

_PROCESS_COMPLETE_RE = re.compile(r"\[(?P<done>\d+)/(?P<total>\d+)\]\s+process\s+>.*✔")
_PROCESS_RUNNING_RE = re.compile(r"\[(?P<done>\d+)/(?P<total>\d+)\]\s+process\s+>.*\[(?P<pct>\d+)%\]")


class LogStreamer:
    """Polls Kubernetes pods for logs and broadcasts structured updates."""

    def __init__(
        self,
        *,
        settings: Settings,
        broadcaster: Broadcaster,
        state_store: StateStore,
    ) -> None:
        self._settings = settings
        self._broadcaster = broadcaster
        self._state_store = state_store
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stoppers: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()
        self._log_cursors: dict[tuple[str, str], datetime] = {}
        self._last_log_broadcast: dict[str, datetime] = {}
        self._last_progress_broadcast: dict[str, datetime] = {}
        self._last_status_broadcast: dict[str, datetime] = {}

    async def start(self, run_id: str, job_name: str) -> None:
        async with self._lock:
            if run_id in self._tasks:
                return
            stop_event = asyncio.Event()
            task = asyncio.create_task(
                self._stream_loop(
                    run_id=run_id,
                    job_name=job_name,
                    stop_event=stop_event,
                )
            )
            self._stoppers[run_id] = stop_event
            self._tasks[run_id] = task

    async def stop(self, run_id: str) -> None:
        async with self._lock:
            stop_event = self._stoppers.pop(run_id, None)
            task = self._tasks.pop(run_id, None)
            self._last_log_broadcast.pop(run_id, None)
            self._last_progress_broadcast.pop(run_id, None)
            self._last_status_broadcast.pop(run_id, None)
            self._log_cursors.clear()
        if stop_event:
            stop_event.set()
        if task:
            await task

    async def close(self) -> None:
        for run_id in list(self._tasks.keys()):
            await self.stop(run_id)

    async def _stream_loop(
        self,
        *,
        run_id: str,
        job_name: str,
        stop_event: asyncio.Event,
    ) -> None:
        logger.info("Starting log stream for run %s", run_id)
        try:
            pending_lines: list[dict[str, Any]] = []
            while not stop_event.is_set():
                pods = await list_job_pods(job_name=job_name, settings=self._settings)
                if not pods:
                    await self._broadcast_waiting(run_id, job_name)
                    await self._sleep(stop_event)
                    continue

                logger.info("Fetching logs from %d pod(s) for run %s", len(pods), run_id)
                preview_lines: list[str] = []
                for pod in pods:
                    metadata = getattr(pod, "metadata", None)
                    pod_name = getattr(metadata, "name", None)
                    if not pod_name:
                        continue

                    for container_name in self._container_names_for_pod(pod):
                        cursor_key = (pod_name, container_name)
                        since_time = self._log_cursors.get(cursor_key)
                        try:
                            logs = await get_pod_log_stream(
                                pod_name=pod_name,
                                container=container_name,
                                settings=self._settings,
                                since_time=since_time,
                            )
                        except Exception as exc:  # pragma: no cover - defensive
                            logger.warning(
                                "Unable to fetch logs for %s/%s: %s",
                                pod_name,
                                container_name,
                                exc,
                            )
                            continue

                        if not logs:
                            continue

                        log_lines = logs.splitlines()
                        logger.info("Fetched %d log lines from %s/%s", len(log_lines), pod_name, container_name)
                        for line in log_lines:
                            timestamp, message = self._split_timestamp(line)
                            effective_timestamp = timestamp or datetime.now(timezone.utc)
                            self._log_cursors[cursor_key] = effective_timestamp

                            # Skip empty messages (blank lines) to reduce noise
                            if not message or not message.strip():
                                continue

                            pending_lines.append(
                                {
                                    "timestamp": effective_timestamp,
                                    "message": message,
                                    "pod": pod_name,
                                    "container": container_name,
                                }
                            )
                            preview_lines.append(f"[{pod_name}/{container_name}] {message}")
                            update = self._parse_progress_line(message)
                            if update:
                                completed_processes, total_processes, status = update
                                await self._broadcast_progress(
                                    run_id,
                                    completed_processes,
                                    total_processes,
                                )
                                if status == "error":
                                    await self._broadcast(
                                        run_id,
                                        StreamMessageType.ERROR,
                                        {
                                            "level": "error",
                                            "message": message,
                                            "pod": pod_name,
                                        },
                                        timestamp=effective_timestamp,
                                    )
                            if message.startswith("ERROR") or message.startswith("WARN"):
                                await self._broadcast(
                                    run_id,
                                    StreamMessageType.ERROR,
                                    {
                                        "level": "error" if message.startswith("ERROR") else "warning",
                                        "message": message,
                                        "pod": pod_name,
                                    },
                                    timestamp=effective_timestamp,
                                )

                if preview_lines:
                    await self._state_store.append_log_lines(preview_lines)

                if pending_lines:
                    pending_lines = await self._flush_logs(run_id, pending_lines)

                await self._sleep(stop_event)
        except Exception:  # pragma: no cover - ensure we log unexpected failures
            logger.exception("Log streaming failed for run %s", run_id)
        finally:
            logger.info("Stopped log stream for run %s", run_id)

    async def _flush_logs(self, run_id: str, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        last_sent = self._last_log_broadcast.get(run_id)
        if last_sent and (now - last_sent) < timedelta(seconds=self._settings.log_batch_interval_seconds):
            return lines

        tail = min(len(lines), self._settings.log_tail_lines)
        batch = lines[:tail]
        payload_lines = [
            {
                "timestamp": entry["timestamp"].isoformat(),
                "message": entry["message"],
                "pod": entry["pod"],
                "container": entry["container"],
            }
            for entry in batch[:50]
        ]
        if not payload_lines:
            return lines

        await self._broadcast(
            run_id,
            StreamMessageType.LOG,
            {
                "lines": payload_lines,
                "count": len(payload_lines),
            },
            timestamp=batch[-1]["timestamp"],
        )
        self._last_log_broadcast[run_id] = now
        remaining = lines[len(payload_lines) :]
        return remaining

    async def _broadcast_progress(
        self,
        run_id: str,
        completed: Optional[int],
        total: Optional[int],
    ) -> None:
        percent, completed_val, total_val = await self._state_store.update_progress(
            completed=completed,
            total=total,
        )
        now = datetime.now(timezone.utc)
        last_sent = self._last_progress_broadcast.get(run_id)
        if last_sent and (now - last_sent) < timedelta(seconds=self._settings.progress_broadcast_interval_seconds):
            return

        await self._broadcast(
            run_id,
            StreamMessageType.PROGRESS,
            {
                "percent": percent,
                "completed": completed_val,
                "total": total_val,
            },
        )
        self._last_progress_broadcast[run_id] = now

    async def _broadcast_waiting(self, run_id: str, job_name: str) -> None:
        now = datetime.now(timezone.utc)
        last_sent = self._last_status_broadcast.get(run_id)
        if last_sent and (now - last_sent) < timedelta(seconds=self._settings.log_fetch_interval_seconds):
            return
        await self._broadcast(
            run_id,
            StreamMessageType.STATUS,
            {"message": "Waiting for pods", "job_name": job_name},
        )
        self._last_status_broadcast[run_id] = now

    async def _broadcast(
        self,
        run_id: str,
        message_type: StreamMessageType,
        data: dict[str, Any],
        *,
        timestamp: Optional[datetime] = None,
    ) -> None:
        ts = timestamp or datetime.now(timezone.utc)
        await self._broadcaster.broadcast(
            {
                "type": message_type.value,
                "data": data,
                "timestamp": ts.isoformat(),
                "run_id": run_id,
            }
        )

    async def _sleep(self, stop_event: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=self._settings.log_fetch_interval_seconds)
        except asyncio.TimeoutError:
            return

    @staticmethod
    def _split_timestamp(line: str) -> tuple[Optional[datetime], str]:
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

    def _parse_progress_line(self, message: str) -> Optional[tuple[int, int, Optional[str]]]:
        match = _PROCESS_COMPLETE_RE.search(message)
        if match:
            done = int(match.group("done"))
            total = int(match.group("total"))
            return done, total, None
        match = _PROCESS_RUNNING_RE.search(message)
        if match:
            done = int(match.group("done"))
            total = int(match.group("total"))
            return done, total, None
        # Detect pipeline startup/initialization messages
        startup_indicators = [
            "Launching",
            "Pulling",
            "executor >",
            "Staging foreign file",
        ]
        if any(indicator in message for indicator in startup_indicators):
            return 0, 100, None  # Show 0% but indicate activity
        if "Pipeline completed successfully" in message:
            return 1, 1, "success"
        if "Pipeline completed with errors" in message:
            return 1, 1, "error"
        return None
