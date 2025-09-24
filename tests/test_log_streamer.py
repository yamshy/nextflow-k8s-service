from datetime import datetime, timezone

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
