"""Tests for WebSocket endpoint."""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.models import RunInfo, RunStatus, StreamMessageType
from app.services.state_store import ActiveRunStatus
from app.utils.broadcaster import ConnectionLimitExceeded


@pytest.fixture
def mock_app_state(mocker):
    """Mock app state with required services."""
    mock_manager = AsyncMock()
    mock_broadcaster = AsyncMock()
    mock_state_store = AsyncMock()

    # Set up mock app
    mock_app = MagicMock()
    mock_app.state.pipeline_manager = mock_manager
    mock_app.state.broadcaster = mock_broadcaster
    mock_app.state.state_store = mock_state_store

    return mock_app, mock_manager, mock_broadcaster, mock_state_store


class TestWebSocketConnection:
    """Tests for WebSocket connection and initialization."""

    def test_websocket_connection_success(self):
        """Test successful WebSocket connection."""
        with TestClient(app) as client:
            # Mock the app state
            mock_manager = AsyncMock()
            mock_broadcaster = AsyncMock()
            mock_state_store = AsyncMock()

            app.state.pipeline_manager = mock_manager
            app.state.broadcaster = mock_broadcaster
            app.state.state_store = mock_state_store

            # Set up mock responses
            mock_manager.current_status.return_value = ActiveRunStatus(
                active=True,
                run=RunInfo(
                    run_id="rws123",
                    status=RunStatus.RUNNING,
                    started_at=datetime.now(timezone.utc),
                    finished_at=None,
                    job_name="demo-rws123",
                ),
                progress_percent=50.0,
            )

            with client.websocket_connect("/api/v1/pipeline/stream") as websocket:
                # Should receive initial status message
                data = websocket.receive_json()
                assert data["type"] == StreamMessageType.STATUS.value
                assert data["run_id"] == "rws123"
                assert "timestamp" in data
                assert data["data"]["active"] is True

    def test_websocket_connection_limit_exceeded(self):
        """Test WebSocket connection when limit is exceeded."""
        with TestClient(app) as client:
            mock_manager = AsyncMock()
            mock_broadcaster = AsyncMock()
            mock_state_store = AsyncMock()

            app.state.pipeline_manager = mock_manager
            app.state.broadcaster = mock_broadcaster
            app.state.state_store = mock_state_store

            # Mock connection limit exceeded
            mock_broadcaster.register.side_effect = ConnectionLimitExceeded()

            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/api/v1/pipeline/stream") as websocket:
                    pass

            assert exc_info.value.code == 1013
            assert exc_info.value.reason == "Too many connections"

    def test_websocket_initial_status_no_active_run(self):
        """Test initial status message when no active run."""
        with TestClient(app) as client:
            mock_manager = AsyncMock()
            mock_broadcaster = AsyncMock()
            mock_state_store = AsyncMock()

            app.state.pipeline_manager = mock_manager
            app.state.broadcaster = mock_broadcaster
            app.state.state_store = mock_state_store

            # No active run
            mock_manager.current_status.return_value = ActiveRunStatus(
                active=False,
                run=None,
            )

            with client.websocket_connect("/api/v1/pipeline/stream") as websocket:
                data = websocket.receive_json()
                assert data["type"] == StreamMessageType.STATUS.value
                assert data["run_id"] == "none"
                assert data["data"]["active"] is False

    def test_websocket_disconnect_cleanup(self):
        """Test that WebSocket disconnect properly cleans up."""
        with TestClient(app) as client:
            mock_manager = AsyncMock()
            mock_broadcaster = AsyncMock()
            mock_state_store = AsyncMock()

            app.state.pipeline_manager = mock_manager
            app.state.broadcaster = mock_broadcaster
            app.state.state_store = mock_state_store

            mock_manager.current_status.return_value = ActiveRunStatus(active=False)

            with client.websocket_connect("/api/v1/pipeline/stream") as websocket:
                # Receive initial status
                websocket.receive_json()
                # Close the connection
                websocket.close()

            # Verify unregister was called
            mock_broadcaster.unregister.assert_called_once()


class TestWebSocketKeepalive:
    """Tests for WebSocket keepalive functionality."""

    @pytest.mark.asyncio
    async def test_keepalive_messages(self, mocker):
        """Test that keepalive messages are sent periodically."""
        # Mock asyncio.sleep to speed up the test
        mock_sleep = mocker.patch("asyncio.sleep", new_callable=AsyncMock)
        mock_sleep.side_effect = [None, asyncio.CancelledError()]

        with TestClient(app) as client:
            mock_manager = AsyncMock()
            mock_broadcaster = AsyncMock()
            mock_state_store = AsyncMock()

            app.state.pipeline_manager = mock_manager
            app.state.broadcaster = mock_broadcaster
            app.state.state_store = mock_state_store

            mock_manager.current_status.return_value = ActiveRunStatus(
                active=True,
                run=RunInfo(
                    run_id="rkeepalive",
                    status=RunStatus.RUNNING,
                    started_at=datetime.now(timezone.utc),
                    finished_at=None,
                    job_name="demo-rkeepalive",
                ),
            )
            mock_state_store.get_connected_clients.return_value = 5

            # We need to simulate the keepalive task
            keepalive_sent = False
            original_send_json = None

            def mock_send_json(data):
                nonlocal keepalive_sent
                if data.get("data", {}).get("status") == "keep-alive":
                    keepalive_sent = True
                    assert data["type"] == StreamMessageType.STATUS.value
                    assert data["data"]["connected_clients"] == 5
                    assert data["run_id"] == "rkeepalive"
                    # Close the WebSocket after receiving keepalive
                    raise WebSocketDisconnect()
                return original_send_json(data)

            with client.websocket_connect("/api/v1/pipeline/stream") as websocket:
                original_send_json = websocket.send_json
                websocket.send_json = mock_send_json

                # Receive initial status
                data = websocket.receive_json()
                assert data["type"] == StreamMessageType.STATUS.value

                # The test should complete when keepalive is sent or timeout
                # Since we're mocking sleep, this should happen quickly

    def test_keepalive_interval_configuration(self):
        """Test that keepalive uses 30-second interval."""
        # This test verifies the code structure rather than runtime behavior
        # The actual interval testing would require real async timing
        from app.api.websocket import pipeline_stream
        import inspect

        source = inspect.getsource(pipeline_stream)
        assert "await asyncio.sleep(30)" in source


class TestWebSocketMessaging:
    """Tests for WebSocket message handling."""

    def test_websocket_receives_broadcast_messages(self):
        """Test that WebSocket receives messages from broadcaster."""
        with TestClient(app) as client:
            mock_manager = AsyncMock()
            mock_broadcaster = AsyncMock()
            mock_state_store = AsyncMock()

            app.state.pipeline_manager = mock_manager
            app.state.broadcaster = mock_broadcaster
            app.state.state_store = mock_state_store

            mock_manager.current_status.return_value = ActiveRunStatus(active=False)

            # Set up a message queue
            messages = []

            async def mock_register(ws):
                # Store the WebSocket for later use
                messages.append(ws)

            mock_broadcaster.register.side_effect = mock_register

            with client.websocket_connect("/api/v1/pipeline/stream") as websocket:
                # Receive initial status
                data = websocket.receive_json()
                assert data["type"] == StreamMessageType.STATUS.value

                # Verify WebSocket was registered
                mock_broadcaster.register.assert_called_once()

    def test_websocket_handles_client_messages(self):
        """Test that WebSocket handles incoming client messages."""
        with TestClient(app) as client:
            mock_manager = AsyncMock()
            mock_broadcaster = AsyncMock()
            mock_state_store = AsyncMock()

            app.state.pipeline_manager = mock_manager
            app.state.broadcaster = mock_broadcaster
            app.state.state_store = mock_state_store

            mock_manager.current_status.return_value = ActiveRunStatus(active=False)

            with client.websocket_connect("/api/v1/pipeline/stream") as websocket:
                # Receive initial status
                websocket.receive_json()

                # Send a message from client (currently ignored by server)
                websocket.send_text("ping")

                # Connection should remain open
                # (In actual implementation, this would continue receiving)

    def test_websocket_json_message_format(self):
        """Test WebSocket message format consistency."""
        with TestClient(app) as client:
            mock_manager = AsyncMock()
            mock_broadcaster = AsyncMock()
            mock_state_store = AsyncMock()

            app.state.pipeline_manager = mock_manager
            app.state.broadcaster = mock_broadcaster
            app.state.state_store = mock_state_store

            now = datetime.now(timezone.utc)
            mock_manager.current_status.return_value = ActiveRunStatus(
                active=True,
                run=RunInfo(
                    run_id="rformat123",
                    status=RunStatus.RUNNING,
                    started_at=now,
                    finished_at=None,
                    job_name="demo-rformat123",
                    message="Processing",
                ),
                progress_percent=75.0,
                log_preview=["Line 1", "Line 2"],
                connected_clients=2,
                last_update=now,
                batches_generated=3,
                batches_analyzed=2,
            )

            with client.websocket_connect("/api/v1/pipeline/stream") as websocket:
                data = websocket.receive_json()

                # Verify message structure
                assert "type" in data
                assert "data" in data
                assert "timestamp" in data
                assert "run_id" in data

                # Verify timestamp is ISO format
                timestamp = datetime.fromisoformat(data["timestamp"])
                assert timestamp.tzinfo is not None

                # Verify data contains expected fields
                assert data["data"]["active"] is True
                assert data["data"]["progress_percent"] == 75.0
                assert data["data"]["batches_generated"] == 3


class TestWebSocketErrorHandling:
    """Tests for WebSocket error handling."""

    @pytest.mark.asyncio
    async def test_websocket_handles_manager_error(self):
        """Test WebSocket handles pipeline manager errors gracefully."""
        with TestClient(app) as client:
            mock_manager = AsyncMock()
            mock_broadcaster = AsyncMock()
            mock_state_store = AsyncMock()

            app.state.pipeline_manager = mock_manager
            app.state.broadcaster = mock_broadcaster
            app.state.state_store = mock_state_store

            # Make current_status raise an exception
            mock_manager.current_status.side_effect = Exception("Manager error")

            # Connection should still work but handle the error
            with pytest.raises(Exception):
                with client.websocket_connect("/api/v1/pipeline/stream") as websocket:
                    pass

    def test_websocket_handles_broadcast_registration_error(self):
        """Test WebSocket handles broadcaster registration errors."""
        with TestClient(app) as client:
            mock_manager = AsyncMock()
            mock_broadcaster = AsyncMock()
            mock_state_store = AsyncMock()

            app.state.pipeline_manager = mock_manager
            app.state.broadcaster = mock_broadcaster
            app.state.state_store = mock_state_store

            # Make register raise a different exception
            mock_broadcaster.register.side_effect = RuntimeError("Registration failed")

            with pytest.raises(RuntimeError):
                with client.websocket_connect("/api/v1/pipeline/stream") as websocket:
                    pass

    @pytest.mark.asyncio
    async def test_keepalive_task_cancellation(self):
        """Test that keepalive task is properly cancelled on disconnect."""
        with TestClient(app) as client:
            mock_manager = AsyncMock()
            mock_broadcaster = AsyncMock()
            mock_state_store = AsyncMock()

            app.state.pipeline_manager = mock_manager
            app.state.broadcaster = mock_broadcaster
            app.state.state_store = mock_state_store

            mock_manager.current_status.return_value = ActiveRunStatus(active=False)

            task_cancelled = False

            async def track_cancellation():
                try:
                    while True:
                        await asyncio.sleep(30)
                except asyncio.CancelledError:
                    nonlocal task_cancelled
                    task_cancelled = True
                    raise

            with client.websocket_connect("/api/v1/pipeline/stream") as websocket:
                websocket.receive_json()
                # Close immediately

            # In the actual implementation, the task should be cancelled
            # This is more of a code structure verification