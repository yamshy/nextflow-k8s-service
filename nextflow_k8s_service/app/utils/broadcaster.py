"""Simple broadcaster for fan-out of events to WebSocket clients."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class Broadcaster:
    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._pending: dict[WebSocket, asyncio.Task[None]] = {}

    async def register(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.add(websocket)
            logger.debug("WebSocket %s registered", id(websocket))

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)
            pending = self._pending.pop(websocket, None)
            logger.debug("WebSocket %s unregistered", id(websocket))
        if pending is not None:
            pending.cancel()

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, default=str)
        async with self._lock:
            clients = list(self._clients)
        if not clients:
            return

        async def _send(client: WebSocket) -> None:
            try:
                await client.send_text(payload)
            except Exception:  # pragma: no cover - ensure failure does not break others
                logger.exception("Failed to send message to WebSocket %s", id(client))
                await self.unregister(client)

        for client in clients:
            async with self._lock:
                pending = self._pending.get(client)
                if pending and not pending.done():
                    logger.warning(
                        "Dropping slow WebSocket %s due to pending send", id(client)
                    )
                    drop_client = True
                else:
                    drop_client = False

            if drop_client:
                await self.unregister(client)
                continue

            task = asyncio.create_task(_send(client))
            task.add_done_callback(
                lambda completed, ws=client: asyncio.create_task(
                    self._cleanup_pending(ws, completed)
                )
            )
            async with self._lock:
                self._pending[client] = task

    async def _cleanup_pending(
        self, client: WebSocket, task: asyncio.Task[None]
    ) -> None:
        with contextlib.suppress(Exception):
            await task
        async with self._lock:
            if self._pending.get(client) is task:
                self._pending.pop(client, None)
