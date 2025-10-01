"""Parser for Nextflow log output to extract task progress and resource information."""

from __future__ import annotations

import re
from typing import Optional

from ..models import NextflowTask, TaskStatus

# Regex patterns for parsing Nextflow log output
# Example: executor > k8s (11)
EXECUTOR_PATTERN = re.compile(r"executor\s+>\s+(?P<type>\w+)\s+\((?P<count>\d+)\)")

# Example: [db/820120] GENERATE (2) | 5 of 5 ✔
# Example: [93/a11859] process > ANALYZE (5) | 3 of 5
TASK_PROGRESS_PATTERN = re.compile(
    r"\[(?P<task_id>[a-f0-9]{2}/[a-f0-9]{6})\]\s+"  # Task ID
    r"(?:process\s+>\s+)?(?P<name>[A-Z_]+)\s*"  # Task name (with optional "process > ")
    r"(?:\((?P<tag>[^)]+)\))?\s*"  # Optional tag in parentheses
    r"(?:\|\s+(?P<completed>\d+)\s+of\s+(?P<total>\d+))?"  # Optional progress
    r"\s*(?P<status_marker>[✔❌⚠])?",  # Optional status marker
)

# Example: [db/820120] Submitted process > GENERATE (1)
TASK_SUBMITTED_PATTERN = re.compile(
    r"\[(?P<task_id>[a-f0-9]{2}/[a-f0-9]{6})\]\s+"  # Task ID
    r"Submitted\s+process\s+>\s+(?P<name>[A-Z_]+)\s*"  # Task name
    r"(?:\((?P<tag>[^)]+)\))?",  # Optional tag
)

# Example: [db/820120] Cached process > GENERATE (1)
TASK_CACHED_PATTERN = re.compile(
    r"\[(?P<task_id>[a-f0-9]{2}/[a-f0-9]{6})\]\s+"  # Task ID
    r"Cached\s+process\s+>\s+(?P<name>[A-Z_]+)\s*"  # Task name
    r"(?:\((?P<tag>[^)]+)\))?",  # Optional tag
)

# Example: ERROR ~ Process `GENERATE (1)` terminated with an error exit status (1)
TASK_ERROR_PATTERN = re.compile(r"ERROR.*Process\s+`(?P<name>[A-Z_]+)\s*(?:\((?P<tag>[^)]+)\))?`")


def parse_executor_info(line: str) -> Optional[tuple[str, int]]:
    """Parse executor information from log line.

    Args:
        line: Log line to parse

    Returns:
        Tuple of (executor_type, active_count) or None if not an executor line

    Example:
        "executor > k8s (11)" -> ("k8s", 11)
    """
    match = EXECUTOR_PATTERN.search(line)
    if match:
        return match.group("type"), int(match.group("count"))
    return None


def parse_task_progress(line: str) -> Optional[NextflowTask]:
    """Parse task progress information from log line.

    Args:
        line: Log line to parse

    Returns:
        NextflowTask object or None if not a task progress line

    Examples:
        "[db/820120] GENERATE (2) | 5 of 5 ✔" -> NextflowTask(...)
        "[93/a11859] process > ANALYZE (5) | 3 of 5" -> NextflowTask(...)
    """
    # Try submitted pattern first
    match = TASK_SUBMITTED_PATTERN.search(line)
    if match:
        return NextflowTask(
            task_id=match.group("task_id"),
            name=match.group("name"),
            tag=match.group("tag"),
            completed=0,
            total=1,
            status=TaskStatus.PENDING,  # Submitted tasks are pending, not yet running
        )

    # Try cached pattern
    match = TASK_CACHED_PATTERN.search(line)
    if match:
        return NextflowTask(
            task_id=match.group("task_id"),
            name=match.group("name"),
            tag=match.group("tag"),
            completed=1,
            total=1,
            status=TaskStatus.COMPLETED,
        )

    # Try progress pattern
    match = TASK_PROGRESS_PATTERN.search(line)
    if match:
        task_id = match.group("task_id")
        name = match.group("name")
        tag = match.group("tag")
        completed_str = match.group("completed")
        total_str = match.group("total")
        status_marker = match.group("status_marker")

        completed = int(completed_str) if completed_str else 0
        total = int(total_str) if total_str else 1

        # Determine status based on marker and progress
        if status_marker == "✔" or (completed > 0 and completed == total):
            status = TaskStatus.COMPLETED
        elif status_marker == "❌":
            status = TaskStatus.FAILED
        elif status_marker == "⚠":
            status = TaskStatus.FAILED
        elif completed > 0:
            status = TaskStatus.RUNNING
        else:
            status = TaskStatus.PENDING

        return NextflowTask(
            task_id=task_id,
            name=name,
            tag=tag,
            completed=completed,
            total=total,
            status=status,
        )

    # Check for error pattern
    match = TASK_ERROR_PATTERN.search(line)
    if match:
        # Return a partial task with error status
        # We don't have task_id in error messages, so we'll need to match by name/tag
        return NextflowTask(
            task_id="unknown",
            name=match.group("name"),
            tag=match.group("tag"),
            completed=0,
            total=1,
            status=TaskStatus.FAILED,
        )

    return None


def extract_pod_count(line: str) -> Optional[int]:
    """Extract active pod count from executor line.

    Args:
        line: Log line to parse

    Returns:
        Number of active pods or None

    Example:
        "executor > k8s (11)" -> 11
    """
    result = parse_executor_info(line)
    if result:
        return result[1]
    return None


def is_pipeline_startup(line: str) -> bool:
    """Check if line indicates pipeline startup/initialization.

    Args:
        line: Log line to check

    Returns:
        True if line indicates startup activity
    """
    startup_indicators = [
        "Launching",
        "Pulling",
        "executor >",
        "Staging foreign file",
        "Nextflow",
        "version",
    ]
    return any(indicator in line for indicator in startup_indicators)


def is_pipeline_complete(line: str) -> Optional[str]:
    """Check if line indicates pipeline completion.

    Args:
        line: Log line to check

    Returns:
        "success" or "failed" if pipeline completed, None otherwise
    """
    if "Pipeline completed successfully" in line or "Completed at:" in line:
        return "success"
    if "Pipeline completed with errors" in line or "ERROR" in line:
        return "failed"
    return None
