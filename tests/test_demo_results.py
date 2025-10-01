"""Tests for demo results parser."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.parsers.demo_results import (
    estimate_completion_time,
    extract_batch_metrics_from_logs,
    parse_report_json,
)
from app.models import DemoResultMetrics


class TestParseReportJson:
    """Tests for parse_report_json function."""

    def test_parse_valid_report(self):
        """Test parsing a valid report.json file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            report_data = {
                "total_batches": 5,
                "batches": [
                    {"batch_id": 1, "lines": 1000, "sum": 500000, "average": 500.0},
                    {"batch_id": 2, "lines": 1000, "sum": 501000, "average": 501.0},
                    {"batch_id": 3, "lines": 1000, "sum": 499000, "average": 499.0},
                    {"batch_id": 4, "lines": 1000, "sum": 502000, "average": 502.0},
                    {"batch_id": 5, "lines": 1000, "sum": 498000, "average": 498.0},
                ],
            }
            json.dump(report_data, f)
            f.flush()

            result = parse_report_json(
                report_path=f.name,
                execution_time_seconds=55.5,
                worker_pods_spawned=10,
            )

            assert isinstance(result, DemoResultMetrics)
            assert result.total_batches == 5
            assert result.total_records == 5000
            assert result.total_sum == 2500000
            assert result.average_value == 500.0
            assert result.worker_pods_spawned == 10
            assert result.execution_time_seconds == 55.5
            assert result.report_path == f.name

            # Clean up
            Path(f.name).unlink()

    def test_parse_empty_batches(self):
        """Test parsing report with no batches."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            report_data = {"total_batches": 0, "batches": []}
            json.dump(report_data, f)
            f.flush()

            result = parse_report_json(
                report_path=f.name,
                execution_time_seconds=10.0,
                worker_pods_spawned=0,
            )

            assert result.total_batches == 0
            assert result.total_records == 0
            assert result.total_sum == 0
            assert result.average_value == 0.0

            Path(f.name).unlink()

    def test_parse_missing_file(self):
        """Test parsing when report.json doesn't exist."""
        result = parse_report_json(
            report_path="/nonexistent/report.json",
            execution_time_seconds=0.0,
            worker_pods_spawned=0,
        )

        assert result is None

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ invalid json }")
            f.flush()

            result = parse_report_json(
                report_path=f.name,
                execution_time_seconds=0.0,
                worker_pods_spawned=0,
            )

            assert result is None

            Path(f.name).unlink()

    def test_parse_missing_fields(self):
        """Test parsing report with missing fields."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            # Report missing "batches" field
            report_data = {"total_batches": 3}
            json.dump(report_data, f)
            f.flush()

            result = parse_report_json(
                report_path=f.name,
                execution_time_seconds=30.0,
                worker_pods_spawned=6,
            )

            # Should handle gracefully with defaults
            assert result.total_batches == 3
            assert result.total_records == 0  # No batches to sum
            assert result.total_sum == 0
            assert result.average_value == 0.0

            Path(f.name).unlink()

    def test_parse_partial_batch_data(self):
        """Test parsing batches with incomplete data."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            report_data = {
                "total_batches": 2,
                "batches": [
                    {"batch_id": 1, "lines": 1000, "sum": 500000},  # Missing average
                    {"batch_id": 2},  # Missing all metrics
                ],
            }
            json.dump(report_data, f)
            f.flush()

            result = parse_report_json(
                report_path=f.name,
                execution_time_seconds=20.0,
                worker_pods_spawned=4,
            )

            assert result.total_batches == 2
            assert result.total_records == 1000  # Only first batch has lines
            assert result.total_sum == 500000
            assert result.average_value == 500.0

            Path(f.name).unlink()

    def test_parse_rounding(self):
        """Test that values are properly rounded."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            report_data = {
                "total_batches": 1,
                "batches": [
                    {"batch_id": 1, "lines": 3, "sum": 10},  # Average will be 3.333...
                ],
            }
            json.dump(report_data, f)
            f.flush()

            result = parse_report_json(
                report_path=f.name,
                execution_time_seconds=12.3456789,
                worker_pods_spawned=2,
            )

            assert result.average_value == 3.33  # Should round to 2 decimal places
            assert result.execution_time_seconds == 12.35

            Path(f.name).unlink()

    @patch("app.parsers.demo_results.logger")
    def test_parse_logs_warnings(self, mock_logger):
        """Test that appropriate warnings are logged."""
        parse_report_json(
            report_path="/nonexistent/report.json",
            execution_time_seconds=0.0,
            worker_pods_spawned=0,
        )

        mock_logger.warning.assert_called_once()
        assert "Report file not found" in str(mock_logger.warning.call_args)


class TestExtractBatchMetricsFromLogs:
    """Tests for extract_batch_metrics_from_logs function."""

    def test_extract_completed_processes(self):
        """Test extracting completed GENERATE and ANALYZE processes."""
        log_lines = [
            "[1a/2b3c4d] process > GENERATE (1) [100%] 1 of 1 ✔",
            "[2a/3b4c5d] process > GENERATE (2) [100%] 2 of 2 ✔",
            "[3a/4b5c6d] process > ANALYZE (1) [100%] 1 of 1 ✔",
            "[4a/5b6c7d] process > GENERATE (3) [100%] 3 of 3 ✔",
            "[5a/6b7c8d] process > ANALYZE (2) [100%] 2 of 2 ✔",
        ]

        generated, analyzed = extract_batch_metrics_from_logs(log_lines)

        assert generated == 3
        assert analyzed == 2

    def test_extract_cached_processes(self):
        """Test extracting cached processes."""
        log_lines = [
            "[1a/2b3c4d] process > GENERATE (1) Cached",
            "[2a/3b4c5d] process > GENERATE (2) Cached",
            "[3a/4b5c6d] process > ANALYZE (1) Cached",
            "[4a/5b6c7d] process > ANALYZE (2) Cached",
            "[5a/6b7c8d] process > ANALYZE (3) Cached",
        ]

        generated, analyzed = extract_batch_metrics_from_logs(log_lines)

        assert generated == 2
        assert analyzed == 3

    def test_extract_mixed_states(self):
        """Test extracting mix of completed and cached processes."""
        log_lines = [
            "[1a/2b3c4d] process > GENERATE (1) ✔",
            "[2a/3b4c5d] process > GENERATE (2) Cached",
            "[3a/4b5c6d] process > ANALYZE (1) ✔",
            "[4a/5b6c7d] process > GENERATE (3) ✔",
            "[5a/6b7c8d] process > ANALYZE (2) Cached",
            "[6a/7b8c9d] process > REPORT Running...",  # Should not count
        ]

        generated, analyzed = extract_batch_metrics_from_logs(log_lines)

        assert generated == 3
        assert analyzed == 2

    def test_extract_ignore_incomplete(self):
        """Test that incomplete processes are ignored."""
        log_lines = [
            "[1a/2b3c4d] Submitted process > GENERATE (1)",
            "[2a/3b4c5d] process > GENERATE (2) Running",
            "[3a/4b5c6d] process > GENERATE (3) [50%]",
            "[4a/5b6c7d] process > GENERATE (4) ✔",
            "[5a/6b7c8d] process > ANALYZE (1) [75%]",
            "[6a/7b8c9d] process > ANALYZE (2) Failed",
            "[7a/8b9c0d] process > ANALYZE (3) ✔",
        ]

        generated, analyzed = extract_batch_metrics_from_logs(log_lines)

        assert generated == 1  # Only GENERATE (4) completed
        assert analyzed == 1  # Only ANALYZE (3) completed

    def test_extract_empty_logs(self):
        """Test extracting from empty logs."""
        generated, analyzed = extract_batch_metrics_from_logs([])

        assert generated == 0
        assert analyzed == 0

    def test_extract_no_matching_processes(self):
        """Test logs with no GENERATE or ANALYZE processes."""
        log_lines = [
            "Starting Nextflow pipeline",
            "Loading configuration",
            "[1a/2b3c4d] process > REPORT ✔",
            "Pipeline completed successfully",
        ]

        generated, analyzed = extract_batch_metrics_from_logs(log_lines)

        assert generated == 0
        assert analyzed == 0

    def test_extract_case_sensitive(self):
        """Test that process names are case-sensitive."""
        log_lines = [
            "[1a/2b3c4d] process > generate (1) ✔",  # lowercase
            "[2a/3b4c5d] process > GENERATE (1) ✔",  # uppercase
            "[3a/4b5c6d] process > Analyze (1) ✔",  # mixed case
            "[4a/5b6c7d] process > ANALYZE (1) ✔",  # uppercase
        ]

        generated, analyzed = extract_batch_metrics_from_logs(log_lines)

        assert generated == 1  # Only uppercase GENERATE
        assert analyzed == 1  # Only uppercase ANALYZE


class TestEstimateCompletionTime:
    """Tests for estimate_completion_time function."""

    def test_estimate_no_progress(self):
        """Test estimation when no progress has been made."""
        started_at = datetime.now(timezone.utc)

        result = estimate_completion_time(
            started_at=started_at,
            batches_generated=0,
            batches_analyzed=0,
            total_batches=5,
        )

        # Should estimate 60 seconds from start
        expected = started_at + timedelta(seconds=60)
        assert abs((result - expected).total_seconds()) < 1

    def test_estimate_partial_generation(self):
        """Test estimation with partial generation complete."""
        started_at = datetime.now(timezone.utc) - timedelta(seconds=10)

        # 2 of 5 batches generated (40% of generation phase = 16% total)
        result = estimate_completion_time(
            started_at=started_at,
            batches_generated=2,
            batches_analyzed=0,
            total_batches=5,
        )

        # With 16% done in 10 seconds, total should be ~62.5 seconds
        assert result > started_at
        elapsed = (result - started_at).total_seconds()
        assert 50 < elapsed < 70

    def test_estimate_partial_analysis(self):
        """Test estimation with generation complete and partial analysis."""
        started_at = datetime.now(timezone.utc) - timedelta(seconds=30)

        # All generated, 3 of 5 analyzed
        # 100% of generation (40%) + 60% of analysis (24%) = 64% total
        result = estimate_completion_time(
            started_at=started_at,
            batches_generated=5,
            batches_analyzed=3,
            total_batches=5,
        )

        assert result > started_at
        elapsed = (result - started_at).total_seconds()
        assert 40 < elapsed < 60

    def test_estimate_nearly_complete(self):
        """Test estimation when nearly complete."""
        started_at = datetime.now(timezone.utc) - timedelta(seconds=50)

        # All generated and analyzed (80% complete, missing REPORT phase)
        result = estimate_completion_time(
            started_at=started_at,
            batches_generated=5,
            batches_analyzed=5,
            total_batches=5,
        )

        assert result > started_at
        elapsed = (result - started_at).total_seconds()
        # Should estimate around 62.5 seconds total (50/0.8)
        assert 55 < elapsed < 70

    def test_estimate_zero_batches(self):
        """Test estimation with zero total batches."""
        started_at = datetime.now(timezone.utc)

        result = estimate_completion_time(
            started_at=started_at,
            batches_generated=0,
            batches_analyzed=0,
            total_batches=0,
        )

        assert result is None

    def test_estimate_timezone_validation(self):
        """Test that naive datetime raises ValueError."""
        naive_datetime = datetime.now()  # No timezone

        with pytest.raises(ValueError, match="must be timezone-aware"):
            estimate_completion_time(
                started_at=naive_datetime,
                batches_generated=0,
                batches_analyzed=0,
                total_batches=5,
            )

    def test_estimate_different_timezones(self):
        """Test estimation with different timezone."""
        import pytz

        # Use a different timezone
        eastern = pytz.timezone("US/Eastern")
        started_at = datetime.now(eastern)

        result = estimate_completion_time(
            started_at=started_at,
            batches_generated=1,
            batches_analyzed=0,
            total_batches=5,
        )

        assert result.tzinfo is not None
        assert result > started_at

    @patch("app.parsers.demo_results.datetime")
    def test_estimate_with_mocked_time(self, mock_datetime):
        """Test estimation with controlled time progression."""
        start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        current = datetime(2024, 1, 1, 12, 0, 20, tzinfo=timezone.utc)  # 20 seconds elapsed

        mock_datetime.now.return_value = current

        # 50% progress in 20 seconds
        result = estimate_completion_time(
            started_at=start,
            batches_generated=5,  # 40% weight
            batches_analyzed=2,  # 10% weight (2/5 * 40%)
            total_batches=5,
        )

        # 50% in 20 seconds means 40 seconds total
        expected = start + timedelta(seconds=40)
        assert abs((result - expected).total_seconds()) < 1

    def test_estimate_overflow_protection(self):
        """Test that estimation handles edge cases without overflow."""
        started_at = datetime.now(timezone.utc) - timedelta(days=1)

        # Very slow progress
        result = estimate_completion_time(
            started_at=started_at,
            batches_generated=1,
            batches_analyzed=0,
            total_batches=100,
        )

        # Should still return a reasonable estimate
        assert result > started_at
        elapsed = (result - started_at).total_seconds()
        assert elapsed < 86400 * 10  # Less than 10 days