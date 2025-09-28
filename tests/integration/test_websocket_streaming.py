from __future__ import annotations

import pytest


@pytest.mark.integration
def test_multiple_websocket_clients(sync_client, pipeline_manager_stub) -> None:
    with sync_client.websocket_connect("/api/v1/pipeline/stream") as ws1, sync_client.websocket_connect(
        "/api/v1/pipeline/stream"
    ) as ws2:
        initial_1 = ws1.receive_json()
        initial_2 = ws2.receive_json()
        assert initial_1["type"] == "initial_status"
        assert initial_2["type"] == "initial_status"

        sync_client.portal.call(
            sync_client.app.state.broadcaster.broadcast,
            {"type": "log", "payload": "hello"},
        )

        msg1 = ws1.receive_json()
        msg2 = ws2.receive_json()

        assert msg1 == msg2
        assert msg1["type"] == "log"
        assert msg1["payload"] == "hello"
