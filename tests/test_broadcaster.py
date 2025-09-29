from __future__ import annotations

import asyncio

import pytest

from app.utils.broadcaster import Broadcaster


@pytest.mark.asyncio
async def test_broadcast_does_not_block_on_slow_client(mocker):
    broadcaster = Broadcaster()

    slow_started = asyncio.Event()
    slow_release = asyncio.Event()
    fast_called = asyncio.Event()

    slow_ws = mocker.Mock()
    fast_ws = mocker.Mock()

    async def slow_send(payload: str) -> None:
        slow_started.set()
        await slow_release.wait()

    async def fast_send(payload: str) -> None:
        fast_called.set()

    slow_ws.send_text = mocker.AsyncMock(side_effect=slow_send)
    fast_ws.send_text = mocker.AsyncMock(side_effect=fast_send)

    await broadcaster.register(slow_ws)
    await broadcaster.register(fast_ws)

    broadcast_task = asyncio.create_task(broadcaster.broadcast({"message": "hello"}))

    await asyncio.wait_for(slow_started.wait(), timeout=0.1)
    await asyncio.sleep(0)  # allow fast client task to run

    assert fast_ws.send_text.await_count == 1
    assert fast_called.is_set()
    assert broadcast_task.done()
    await broadcast_task

    slow_release.set()
    await asyncio.sleep(0)
    assert slow_ws.send_text.await_count == 1
