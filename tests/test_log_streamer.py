import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from app.config import Settings
from app.models import StreamMessageType
from app.services.log_streamer import LogStreamer


def test_split_timestamp_parses_isoformat() -> None:
    timestamp = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    line = f"{timestamp.isoformat()} An example log line"

    parsed_timestamp, message = LogStreamer._split_timestamp(line)

    assert parsed_timestamp == timestamp
    assert message == "An example log line"


def test_split_timestamp_handles_missing_timestamp() -> None:
    parsed_timestamp, message = LogStreamer._split_timestamp("no timestamp here")

    assert parsed_timestamp is None
    assert message == "no timestamp here"


class _RecordingBroadcaster:
    def __init__(self, expected_messages: int) -> None:
        self.messages: list[dict[str, object]] = []
        self._expected = expected_messages
        self.emitted = asyncio.Event()

    async def broadcast(self, message: dict[str, object]) -> None:
        self.messages.append(message)
        if len(self.messages) >= self._expected:
            self.emitted.set()


class _RecordingStateStore:
    def __init__(self) -> None:
        self.log_batches: list[list[str]] = []
        self.progress_updates: list[tuple[object, object, object]] = []

    async def append_log_lines(self, lines: list[str]) -> None:
        self.log_batches.append(list(lines))

    async def update_progress(
        self,
        *,
        completed=None,
        total=None,
        percent=None,
    ) -> tuple[float, object, object]:
        self.progress_updates.append((completed, total, percent))
        return 0.0, completed, total


@pytest.mark.asyncio
async def test_stream_loop_emits_logs_from_all_containers(mocker: MockerFixture) -> None:
    pods = [
        SimpleNamespace(
            metadata=SimpleNamespace(name="pod-1"),
            spec=SimpleNamespace(
                containers=[SimpleNamespace(name="main")],
                init_containers=[SimpleNamespace(name="init-setup")],
                ephemeral_containers=[SimpleNamespace(name="debugger")],
            ),
        )
    ]

    mocker.patch("app.services.log_streamer.list_job_pods", mocker.AsyncMock(return_value=pods))

    timestamps = {
        "main": datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "init-setup": datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc),
        "debugger": datetime(2024, 1, 1, 12, 2, 0, tzinfo=timezone.utc),
    }

    async def fake_get_pod_log_stream(
        *,
        pod_name: str,
        container: str,
        settings: Settings,
        since_time: datetime | None,
    ) -> str:
        del pod_name, settings, since_time
        ts = timestamps[container]
        return f"{ts.isoformat()} {container} log"

    get_log_mock = mocker.AsyncMock(side_effect=fake_get_pod_log_stream)
    mocker.patch("app.services.log_streamer.get_pod_log_stream", get_log_mock)

    settings = Settings(log_fetch_interval_seconds=0.01)
    broadcaster = _RecordingBroadcaster(expected_messages=1)
    state_store = _RecordingStateStore()
    streamer = LogStreamer(settings=settings, broadcaster=broadcaster, state_store=state_store)
    stop_event = asyncio.Event()

    stream_task = asyncio.create_task(
        streamer._stream_loop(run_id="run-1", job_name="job-123", stop_event=stop_event)
    )

    await asyncio.wait_for(broadcaster.emitted.wait(), timeout=1)
    stop_event.set()
    await stream_task

    assert len(broadcaster.messages) == 1
    log_message = broadcaster.messages[0]
    assert log_message["type"] == StreamMessageType.LOG.value
    log_lines = [entry["message"] for entry in log_message["data"]["lines"]]
    assert log_lines == ["main log", "init-setup log", "debugger log"]

    containers_seen = {
        (call.kwargs["pod_name"], call.kwargs["container"]) for call in get_log_mock.await_args_list
    }
    assert containers_seen == {
        ("pod-1", "main"),
        ("pod-1", "init-setup"),
        ("pod-1", "debugger"),
    }

    assert any("[pod-1/main]" in batch_line for batch in state_store.log_batches for batch_line in batch)
