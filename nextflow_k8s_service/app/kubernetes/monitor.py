"""Monitoring helpers for Kubernetes Jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ..config import Settings
from ..models import RunStatus
from .jobs import get_job_status

logger = logging.getLogger(__name__)


async def wait_for_completion(
    *,
    run_id: str,
    job_name: str,
    settings: Settings,
    poll_interval: float = 5.0,
    on_status: Callable[[RunStatus], Awaitable[None]] | None = None,
) -> RunStatus:
    """Poll the job status until it reaches a terminal state."""
    terminal_states = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.UNKNOWN}

    while True:
        status = await get_job_status(job_name=job_name, settings=settings)
        if on_status:
            await on_status(status)
        if status in terminal_states:
            logger.info("Job %s for run %s reached terminal status %s", job_name, run_id, status)
            return status
        logger.debug("Job %s for run %s still %s", job_name, run_id, status)
        await asyncio.sleep(poll_interval)
