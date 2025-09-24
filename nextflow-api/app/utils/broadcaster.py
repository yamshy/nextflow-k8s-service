"""Simple broadcaster for fan-out of events to WebSocket clients."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class Broadcaster:
    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.add(websocket)
            logger.debug("WebSocket %s registered", id(websocket))

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)
            logger.debug("WebSocket %s unregistered", id(websocket))

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, default=str)
        async with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                await client.send_text(payload)
            except Exception:  # pragma: no cover - ensure failure does not break others
                logger.exception("Failed to send message to WebSocket %s", id(client))
                await self.unregister(client)
