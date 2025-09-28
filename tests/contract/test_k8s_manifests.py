from __future__ import annotations

import pytest

from app.config import Settings
from app.kubernetes.jobs import _build_job_manifest
from app.models import PipelineParameters


@pytest.mark.contract
def test_job_manifest_matches_expected_schema() -> None:
    settings = Settings(
        nextflow_namespace="contracts",
        nextflow_service_account="nextflow-runner",
        nextflow_image="ghcr.io/example/nextflow:latest",
        job_active_deadline_seconds=7200,
        job_backoff_limit=1,
    )
    parameters = PipelineParameters(
        pipeline="main.nf",
        workdir="/workspace",
        parameters={"profile": "test", "resume": True},
    )

    job = _build_job_manifest(run_id="example", params=parameters, settings=settings)

    assert job.metadata.name == "nextflow-run-example"
    assert job.metadata.labels["run-id"] == "example"
    assert job.spec.template.spec.service_account_name == "nextflow-runner"
    assert job.spec.backoff_limit == 1
    assert job.spec.active_deadline_seconds == 7200

    container = job.spec.template.spec.containers[0]
    assert container.image == "ghcr.io/example/nextflow:latest"
    assert container.args[:2] == ["run", "main.nf"]
    assert "--profile" in container.args
    assert container.env[0].name == "NXF_WORK"
    assert container.env[0].value == "/workspace"
