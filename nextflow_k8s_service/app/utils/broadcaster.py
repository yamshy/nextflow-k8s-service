"""Simple broadcaster for fan-out of events to WebSocket clients."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Set, TYPE_CHECKING

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from ..services.state_store import StateStore


class ConnectionLimitExceeded(RuntimeError):
    """Raised when the broadcaster has reached its connection limit."""


class Broadcaster:
    def __init__(
        self,
        *,
        state_store: Optional["StateStore"] = None,
        max_clients: int = 100,
    ) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._pending: dict[WebSocket, asyncio.Task[None]] = {}
        self._state_store = state_store
        self._max_clients = max_clients

    async def register(self, websocket: WebSocket) -> None:
        async with self._lock:
            if len(self._clients) >= self._max_clients:
                raise ConnectionLimitExceeded("WebSocket connection limit reached")
            self._clients.add(websocket)
            pending_count = len(self._clients)
            logger.info("WebSocket registered, total connected: %d", pending_count)
        if self._state_store:
            await self._state_store.set_connected_clients(pending_count)

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)
            pending = self._pending.pop(websocket, None)
            pending_count = len(self._clients)
            logger.info("WebSocket unregistered, total connected: %d", pending_count)
        if pending is not None:
            pending.cancel()
        if self._state_store:
            await self._state_store.set_connected_clients(pending_count)

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, default=str)
        timestamp_raw = message.get("timestamp")
        broadcast_time = datetime.now(timezone.utc)
        if isinstance(timestamp_raw, datetime):
            broadcast_time = timestamp_raw.astimezone(timezone.utc)
        elif isinstance(timestamp_raw, str):
            try:
                broadcast_time = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                logger.debug("Unable to parse timestamp '%s' in broadcast", timestamp_raw)
        async with self._lock:
            clients = list(self._clients)
        if self._state_store:
            await self._state_store.update_last_broadcast(broadcast_time)
        if not clients:
            return

        async def _send(client: WebSocket) -> None:
            try:
                await client.send_text(payload)
            except (RuntimeError, WebSocketDisconnect):
                # Expected: connection closed or message sent after close
                logger.info("WebSocket disconnected during send, unregistering")
                await self.unregister(client)
            except Exception:  # pragma: no cover - unexpected errors
                logger.exception("Unexpected error sending to WebSocket")
                await self.unregister(client)

        for client in clients:
            async with self._lock:
                pending = self._pending.get(client)
                if pending and not pending.done():
                    logger.warning("Dropping slow WebSocket %s due to pending send", id(client))
                    drop_client = True
                else:
                    drop_client = False

            if drop_client:
                await self.unregister(client)
                continue

            task = asyncio.create_task(_send(client))
            task.add_done_callback(
                lambda completed, ws=client: asyncio.create_task(self._cleanup_pending(ws, completed))
            )
            async with self._lock:
                self._pending[client] = task

    async def _cleanup_pending(self, client: WebSocket, task: asyncio.Task[None]) -> None:
        with contextlib.suppress(Exception):
            await task
        async with self._lock:
            if self._pending.get(client) is task:
                self._pending.pop(client, None)
