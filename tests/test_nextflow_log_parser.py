"""Tests for Nextflow log parser."""

import pytest

from app.models import TaskStatus
from app.parsers.nextflow_log_parser import (
    parse_task_progress,
    parse_executor_info,
)


class TestNextflowLogParser:
    """Test Nextflow log parsing functionality."""

    def test_parse_submitted_task(self):
        """Test that submitted tasks are marked as PENDING, not RUNNING."""
        line = "[db/820120] Submitted process > GENERATE (1)"
        task = parse_task_progress(line)

        assert task is not None
        assert task.task_id == "db/820120"
        assert task.name == "GENERATE"
        assert task.tag == "1"
        assert task.status == TaskStatus.PENDING  # Should be PENDING when submitted
        assert task.completed == 0
        assert task.total == 1

    def test_parse_cached_task(self):
        """Test that cached tasks are marked as COMPLETED."""
        line = "[db/820120] Cached process > GENERATE (1)"
        task = parse_task_progress(line)

        assert task is not None
        assert task.task_id == "db/820120"
        assert task.name == "GENERATE"
        assert task.tag == "1"
        assert task.status == TaskStatus.COMPLETED
        assert task.completed == 1
        assert task.total == 1

    def test_parse_running_task(self):
        """Test parsing of actively running tasks."""
        line = "[93/a11859] process > ANALYZE (5) | 3 of 5"
        task = parse_task_progress(line)

        assert task is not None
        assert task.task_id == "93/a11859"
        assert task.name == "ANALYZE"
        assert task.tag == "5"
        assert task.status == TaskStatus.RUNNING
        assert task.completed == 3
        assert task.total == 5

    def test_parse_completed_task(self):
        """Test parsing of completed tasks."""
        line = "[db/820120] GENERATE (2) | 5 of 5 ✔"
        task = parse_task_progress(line)

        assert task is not None
        assert task.task_id == "db/820120"
        assert task.name == "GENERATE"
        assert task.tag == "2"
        assert task.status == TaskStatus.COMPLETED
        assert task.completed == 5
        assert task.total == 5

    def test_parse_failed_task(self):
        """Test parsing of failed tasks."""
        line = "[db/820120] GENERATE (2) | 3 of 5 ❌"
        task = parse_task_progress(line)

        assert task is not None
        assert task.task_id == "db/820120"
        assert task.name == "GENERATE"
        assert task.tag == "2"
        assert task.status == TaskStatus.FAILED
        assert task.completed == 3
        assert task.total == 5

    def test_parse_executor_info(self):
        """Test parsing of executor information."""
        line = "executor > k8s (11)"
        result = parse_executor_info(line)

        assert result is not None
        assert result == ("k8s", 11)

    def test_parse_non_task_line(self):
        """Test that non-task lines return None."""
        lines = [
            "Random log message",
            "Pipeline started",
            "[2024-01-01] Some timestamp",
        ]

        for line in lines:
            assert parse_task_progress(line) is None

    def test_submitted_report_task(self):
        """Test that submitted REPORT task is PENDING until it actually runs."""
        # This is the key test - REPORT should be PENDING when submitted
        # It should only become RUNNING when it actually starts executing
        line = "[ab/123456] Submitted process > REPORT"
        task = parse_task_progress(line)

        assert task is not None
        assert task.task_id == "ab/123456"
        assert task.name == "REPORT"
        assert task.tag is None  # REPORT typically has no tag
        assert task.status == TaskStatus.PENDING
        assert task.completed == 0
        assert task.total == 1