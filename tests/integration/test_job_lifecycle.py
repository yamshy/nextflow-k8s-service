from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Sequence

import pytest

from app.config import Settings
from app.models import PipelineParameters
from app.kubernetes.jobs import _build_job_manifest

K8S_TEST_NAMESPACE = os.getenv("K8S_TEST_NAMESPACE", "nextflow-test")
K8S_INTEGRATION_ENABLED = bool(os.getenv("K8S_INTEGRATION"))
KUBECTL_BIN = os.getenv("KUBECTL_BIN", "kubectl")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.kubernetes,
    pytest.mark.skipif(not K8S_INTEGRATION_ENABLED, reason="requires a live Kubernetes cluster"),
]


def _kubectl(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [KUBECTL_BIN, *args],
        check=True,
        capture_output=True,
        text=True,
    )


async def test_job_manifest_dry_run() -> None:
    """Render the job manifest and ensure it passes kubectl dry-run apply."""
    settings = Settings(nextflow_namespace=K8S_TEST_NAMESPACE)
    manifest = _build_job_manifest(
        run_id="integration",
        params=PipelineParameters(pipeline="nextflow/hello", parameters={"profile": "test"}),
        settings=settings,
    )

    manifest_dir = Path("build")
    manifest_dir.mkdir(exist_ok=True)
    manifest_path = manifest_dir / "job.yaml"
    manifest_path.write_text(manifest.to_str())  # type: ignore[no-untyped-call]

    _kubectl(["--namespace", K8S_TEST_NAMESPACE, "apply", "--dry-run=server", "-f", str(manifest_path)])
