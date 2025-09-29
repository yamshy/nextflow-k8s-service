"""WebSocket endpoints for real-time pipeline updates."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..services.pipeline_manager import PipelineManager
from ..utils.broadcaster import Broadcaster

router = APIRouter(prefix="/pipeline", tags=["pipeline-stream"])


@router.websocket("/stream")
async def pipeline_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    manager: PipelineManager = websocket.app.state.pipeline_manager  # type: ignore[attr-defined]
    broadcaster: Broadcaster = websocket.app.state.broadcaster  # type: ignore[attr-defined]

    await broadcaster.register(websocket)

    status = await manager.current_status()
    await websocket.send_json({
        "type": "initial_status",
        "payload": status.model_dump(),
    })

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unregister(websocket)
