"""State management for tracking active and historical pipeline runs."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Deque, Optional

from redis.asyncio import Redis

from ..config import Settings
from ..models import ActiveRunStatus, RunHistoryEntry, RunInfo, RunStatus

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _RunRecord:
    info: RunInfo
    triggered_by: Optional[str]
    expires_at: datetime


class StateStore:
    """Stores pipeline run state in-memory with optional Redis persistence."""

    def __init__(self, *, settings: Settings, redis: Optional[Redis]) -> None:
        self._settings = settings
        self._redis = redis
        self._lock = asyncio.Lock()
        self._active_run: Optional[_RunRecord] = None
        self._history: Deque[_RunRecord] = deque(maxlen=settings.run_history_limit)
        self._redis_lock_key = "nextflow:pipeline:active-run"

    @classmethod
    async def create(cls, settings: Settings) -> "StateStore":
        redis: Optional[Redis] = None
        if settings.redis_url:
            try:
                redis = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
                await redis.ping()
                logger.info("Connected to Redis at %s", settings.redis_url)
            except Exception:
                logger.exception("Failed to connect to Redis at %s, falling back to memory store", settings.redis_url)
                redis = None
        return cls(settings=settings, redis=redis)

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()

    async def ping(self) -> None:
        if self._redis:
            await self._redis.ping()

    async def _set_active_run_locked(self, record: _RunRecord) -> bool:
        if self._active_run:
            return False
        if self._redis:
            ttl_seconds = self._settings.run_ttl_minutes * 60
            success = await self._redis.set(self._redis_lock_key, record.info.run_id, nx=True, ex=ttl_seconds)
            if not success:
                return False
        self._active_run = record
        return True

    async def acquire_active_run(
        self,
        *,
        run_id: str,
        job_name: str,
        triggered_by: Optional[str],
    ) -> bool:
        """Attempt to mark a run as active. Returns True if acquired."""
        record = _RunRecord(
            info=RunInfo(
                run_id=run_id,
                status=RunStatus.STARTING,
                started_at=_utcnow(),
                finished_at=None,
                job_name=job_name,
                message=None,
            ),
            triggered_by=triggered_by,
            expires_at=_utcnow() + timedelta(minutes=self._settings.run_ttl_minutes),
        )

        async with self._lock:
            acquired = await self._set_active_run_locked(record)
            if not acquired:
                return False
            await self._write_active_state(record)
            return True

    async def _write_active_state(self, record: _RunRecord) -> None:
        if self._redis:
            payload = {
                "run_id": record.info.run_id,
                "status": record.info.status.value,
                "started_at": record.info.started_at.isoformat(),
                "job_name": record.info.job_name or "",
                "triggered_by": record.triggered_by or "",
            }
            await self._redis.hset("nextflow:pipeline:state", mapping=payload)

    async def get_active_run(self) -> ActiveRunStatus:
        async with self._lock:
            record = self._active_run
            if not record and self._redis:
                raw = await self._redis.hgetall("nextflow:pipeline:state")
                if raw and raw.get("run_id"):
                    started_at_raw = raw.get("started_at") or _utcnow().isoformat()
                    try:
                        started_at = datetime.fromisoformat(started_at_raw)
                    except ValueError:
                        started_at = _utcnow()
                    status_raw = raw.get("status") or RunStatus.UNKNOWN.value
                    record = _RunRecord(
                        info=RunInfo(
                            run_id=raw["run_id"],
                            status=RunStatus(status_raw),
                            started_at=started_at,
                            finished_at=None,
                            job_name=raw.get("job_name") or None,
                            message=None,
                        ),
                        triggered_by=raw.get("triggered_by") or None,
                        expires_at=_utcnow() + timedelta(minutes=self._settings.run_ttl_minutes),
                    )
                    self._active_run = record
            return ActiveRunStatus(active=record is not None, run=record.info if record else None)

    async def update_active_status(self, status: RunStatus, message: Optional[str] = None) -> None:
        async with self._lock:
            if not self._active_run:
                return
            self._active_run.info.status = status
            if message:
                self._active_run.info.message = message
            await self._write_active_state(self._active_run)

    async def finish_active_run(self, status: RunStatus, message: Optional[str] = None) -> Optional[RunInfo]:
        async with self._lock:
            if not self._active_run:
                return None
            record = self._active_run
            record.info.status = status
            record.info.finished_at = _utcnow()
            if message:
                record.info.message = message
            self._history.appendleft(record)
            self._active_run = None
            await self._purge_expired_history()
            await self._clear_active_state()
            return record.info

    async def _clear_active_state(self) -> None:
        if self._redis:
            await self._redis.delete(self._redis_lock_key)
            await self._redis.delete("nextflow:pipeline:state")

    async def _purge_expired_history(self) -> None:
        cutoff = _utcnow()
        while self._history and self._history[-1].expires_at < cutoff:
            removed = self._history.pop()
            logger.debug("Purged expired run %s", removed.info.run_id)

    async def get_history(self, limit: Optional[int] = None) -> list[RunHistoryEntry]:
        async with self._lock:
            await self._purge_expired_history()
            items = list(self._history)
        limit = limit or self._settings.run_history_limit
        history = []
        for record in items[:limit]:
            finished_at = record.info.finished_at
            duration = None
            if finished_at:
                duration = (finished_at - record.info.started_at).total_seconds()
            history.append(
                RunHistoryEntry(
                    run_id=record.info.run_id,
                    status=record.info.status,
                    started_at=record.info.started_at,
                    finished_at=finished_at,
                    duration_seconds=duration,
                    triggered_by=record.triggered_by,
                    job_name=record.info.job_name,
                )
            )
        return history

    async def cancel_active_run(self, message: Optional[str] = None) -> Optional[RunInfo]:
        return await self.finish_active_run(RunStatus.CANCELLED, message)
