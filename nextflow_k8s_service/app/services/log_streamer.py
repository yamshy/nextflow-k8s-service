"""Service that streams Kubernetes pod logs to connected WebSocket clients."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..config import Settings
from ..kubernetes.jobs import get_pod_log_stream, list_job_pods
from ..models import NextflowTask, StreamMessageType, TaskStatus
from ..parsers.nextflow_log_parser import (
    parse_executor_info,
    parse_task_progress,
)
from ..utils.broadcaster import Broadcaster
from .progress_calculator import ProgressCalculator
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
        self._last_task_progress_broadcast: dict[str, datetime] = {}
        self._last_resource_broadcast: dict[str, datetime] = {}
        # Track task state per run: run_id -> task_key -> NextflowTask
        self._task_states: dict[str, dict[str, NextflowTask]] = {}
        # Track resource metrics per run: run_id -> metrics
        self._resource_metrics: dict[str, dict[str, Any]] = {}
        # Track progress calculators per run
        self._progress_calculators: dict[str, ProgressCalculator] = {}
        # Track last workflow progress broadcast
        self._last_workflow_progress_broadcast: dict[str, datetime] = {}

    async def start(self, run_id: str, job_name: str, batch_count: int = 5) -> None:
        async with self._lock:
            if run_id in self._tasks:
                return
            # Initialize progress calculator for this run
            self._progress_calculators[run_id] = ProgressCalculator(batch_count=batch_count)
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
            self._last_task_progress_broadcast.pop(run_id, None)
            self._last_resource_broadcast.pop(run_id, None)
            self._last_workflow_progress_broadcast.pop(run_id, None)
            self._task_states.pop(run_id, None)
            self._resource_metrics.pop(run_id, None)
            self._progress_calculators.pop(run_id, None)
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

                            # Parse and track task progress
                            task = parse_task_progress(message)
                            if task:
                                logger.debug(
                                    "Parsed task for run %s: %s %d/%d [%s]",
                                    run_id,
                                    task.name,
                                    task.completed,
                                    task.total,
                                    task.status,
                                )
                                await self._update_task_state(run_id, task)

                            # Parse and track executor/resource info
                            executor_info = parse_executor_info(message)
                            if executor_info:
                                executor_type, active_pods = executor_info
                                await self._update_resource_metrics(
                                    run_id, active_pods=active_pods, executor_info=f"{executor_type} ({active_pods})"
                                )

                            # Keep legacy progress parsing for backward compatibility
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

                # Broadcast task progress if state changed (legacy)
                await self._broadcast_task_progress(run_id)

                # Broadcast resource usage periodically (legacy)
                await self._broadcast_resource_usage(run_id, len(pods))

                # Broadcast unified workflow progress (new)
                await self._broadcast_workflow_progress(run_id)

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
        message = {
            "type": message_type.value,
            "data": data,
            "timestamp": ts.isoformat(),
            "run_id": run_id,
        }
        try:
            logger.info("📤 Calling broadcaster.broadcast(%s) for run %s", message_type.value, run_id)
            await self._broadcaster.broadcast(message)
            logger.info("📬 broadcaster.broadcast(%s) returned for run %s", message_type.value, run_id)
        except Exception as exc:
            logger.exception("❌ Failed to broadcast %s message for run %s: %s", message_type.value, run_id, exc)

    async def _update_task_state(self, run_id: str, task: NextflowTask) -> None:
        """Update the internal task state tracker.

        Merges new task information with existing state, preserving progress.
        """
        if run_id not in self._task_states:
            self._task_states[run_id] = {}

        # Use (name, tag) as the key to track tasks across updates
        # If no tag, use just the name
        task_key = f"{task.name}:{task.tag}" if task.tag else task.name

        existing = self._task_states[run_id].get(task_key)
        if existing:
            # Merge with existing task state
            # Update completed count if it increased
            if task.completed > existing.completed:
                existing.completed = task.completed
            # Update total if it changed
            if task.total > existing.total:
                existing.total = task.total
            # Update status if it progressed
            if task.status != existing.status:
                # Only update if status is "progressing" (pending -> running -> completed)
                status_order = {
                    TaskStatus.PENDING: 0,
                    TaskStatus.RUNNING: 1,
                    TaskStatus.COMPLETED: 2,
                    TaskStatus.FAILED: 3,
                }
                if status_order.get(task.status, 0) > status_order.get(existing.status, 0):
                    existing.status = task.status
            # Update task_id if we didn't have one before
            if task.task_id != "unknown" and existing.task_id == "unknown":
                existing.task_id = task.task_id
        else:
            # New task - add to state
            self._task_states[run_id][task_key] = task

    async def _update_resource_metrics(
        self, run_id: str, active_pods: Optional[int] = None, executor_info: Optional[str] = None
    ) -> None:
        """Update resource metrics for a run."""
        if run_id not in self._resource_metrics:
            self._resource_metrics[run_id] = {"active_pods": 0, "total_pods_spawned": 0, "executor_info": None}

        metrics = self._resource_metrics[run_id]
        if active_pods is not None:
            metrics["active_pods"] = active_pods
            # Track max pods spawned
            if active_pods > metrics["total_pods_spawned"]:
                metrics["total_pods_spawned"] = active_pods
        if executor_info is not None:
            metrics["executor_info"] = executor_info

    async def _broadcast_task_progress(self, run_id: str) -> None:
        """Broadcast task progress if state has changed since last broadcast."""
        now = datetime.now(timezone.utc)
        last_sent = self._last_task_progress_broadcast.get(run_id)

        # Broadcast at most once per second
        if last_sent and (now - last_sent) < timedelta(seconds=1):
            return

        tasks = self._task_states.get(run_id, {})
        if not tasks:
            logger.debug("No tasks tracked yet for run %s", run_id)
            return

        logger.info("Broadcasting task_progress for run %s with %d tasks", run_id, len(tasks))

        # Get executor info from resource metrics
        metrics = self._resource_metrics.get(run_id, {})
        executor_info = metrics.get("executor_info")

        # Convert task dict to sorted list for consistent ordering
        task_list = sorted(tasks.values(), key=lambda t: (t.name, t.tag or ""))

        try:
            # Serialize tasks to JSON-compatible dicts
            serialized_tasks = [t.model_dump(mode="json") for t in task_list]
            logger.info("✅ Serialized %d tasks, calling _broadcast for run %s", len(serialized_tasks), run_id)

            await self._broadcast(
                run_id,
                StreamMessageType.TASK_PROGRESS,
                {
                    "tasks": serialized_tasks,
                    "executor_info": executor_info,
                },
            )
            self._last_task_progress_broadcast[run_id] = now
            logger.info("✅ _broadcast() call completed for task_progress run %s", run_id)
        except Exception as exc:
            logger.exception("❌ Failed to broadcast task_progress for run %s: %s", run_id, exc)

    async def _broadcast_resource_usage(self, run_id: str, current_pod_count: int) -> None:
        """Broadcast resource usage metrics periodically."""
        now = datetime.now(timezone.utc)
        last_sent = self._last_resource_broadcast.get(run_id)

        # Broadcast at most once every 3 seconds
        if last_sent and (now - last_sent) < timedelta(seconds=3):
            return

        metrics = self._resource_metrics.get(run_id, {})
        if not metrics:
            # Initialize with current pod count
            metrics = {
                "active_pods": current_pod_count,
                "total_pods_spawned": current_pod_count,
            }
            self._resource_metrics[run_id] = metrics

        await self._broadcast(
            run_id,
            StreamMessageType.RESOURCE_USAGE,
            {
                "active_pods": metrics.get("active_pods", current_pod_count),
                "total_pods_spawned": metrics.get("total_pods_spawned", current_pod_count),
            },
        )
        self._last_resource_broadcast[run_id] = now

    async def _broadcast_workflow_progress(self, run_id: str) -> None:
        """Broadcast unified workflow progress with all calculated metrics."""
        now = datetime.now(timezone.utc)
        last_sent = self._last_workflow_progress_broadcast.get(run_id)

        # Broadcast at most once per second
        if last_sent and (now - last_sent) < timedelta(seconds=1):
            return

        # Get progress calculator for this run
        calculator = self._progress_calculators.get(run_id)
        if not calculator:
            logger.debug("No progress calculator for run %s", run_id)
            return

        # Get current task states and resource metrics
        task_states = self._task_states.get(run_id, {})
        metrics = self._resource_metrics.get(run_id, {})

        if not task_states:
            logger.debug("No tasks tracked yet for run %s", run_id)
            return

        try:
            # Calculate comprehensive progress
            workflow_progress = calculator.calculate_progress(
                task_states=task_states,
                executor_info=metrics.get("executor_info"),
                active_pods=metrics.get("active_pods", 0),
            )

            # Convert to JSON-serializable format
            progress_data = workflow_progress.model_dump(mode="json")

            logger.info("Broadcasting workflow_progress for run %s", run_id)
            await self._broadcast(
                run_id,
                StreamMessageType.WORKFLOW_PROGRESS,
                progress_data,
            )
            self._last_workflow_progress_broadcast[run_id] = now

        except Exception as exc:
            logger.exception("Failed to broadcast workflow_progress for run %s: %s", run_id, exc)

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
