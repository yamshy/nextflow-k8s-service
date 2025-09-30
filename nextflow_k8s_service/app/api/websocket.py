"""WebSocket endpoints for real-time pipeline updates."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..models import StreamMessageType
from ..services.pipeline_manager import PipelineManager
from ..services.state_store import StateStore
from ..utils.broadcaster import Broadcaster, ConnectionLimitExceeded

router = APIRouter(prefix="/pipeline", tags=["pipeline-stream"])


@router.websocket("/stream")
async def pipeline_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    manager: PipelineManager = websocket.app.state.pipeline_manager  # type: ignore[attr-defined]
    broadcaster: Broadcaster = websocket.app.state.broadcaster  # type: ignore[attr-defined]
    state_store: StateStore = websocket.app.state.state_store  # type: ignore[attr-defined]

    try:
        await broadcaster.register(websocket)
    except ConnectionLimitExceeded:
        await websocket.close(code=1013, reason="Too many connections")
        return

    async def _keepalive() -> None:
        try:
            while True:
                await asyncio.sleep(30)
                status = await manager.current_status()
                run_id = status.run.run_id if status.run else "none"
                await websocket.send_json(
                    {
                        "type": StreamMessageType.STATUS.value,
                        "data": {
                            "status": "keep-alive",
                            "connected_clients": await state_store.get_connected_clients(),
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "run_id": run_id,
                    }
                )
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
            raise
        except Exception:  # pragma: no cover - defensive logging
            await broadcaster.unregister(websocket)
            raise

    keepalive_task = asyncio.create_task(_keepalive())

    status = await manager.current_status()
    run_id = status.run.run_id if status.run else "none"
    await websocket.send_json(
        {
            "type": StreamMessageType.STATUS.value,
            "data": status.model_dump(mode="json"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
        }
    )

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass  # Expected when keepalive task is cancelled
        await broadcaster.unregister(websocket)
