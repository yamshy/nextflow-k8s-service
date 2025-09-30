"""Tests for Kubernetes job manifest helpers."""

from __future__ import annotations

from app.config import Settings
from app.kubernetes.jobs import _build_job_manifest
from app.models import PipelineParameters


def _build_params(pipeline: str, **parameters) -> PipelineParameters:
    return PipelineParameters(pipeline=pipeline, parameters=parameters)


def test_nf_core_pipeline_gets_default_outdir() -> None:
    params = _build_params("nf-core/fetchngs")
    settings = Settings()

    job = _build_job_manifest(run_id="run-123", params=params, settings=settings)

    container = job.spec.template.spec.containers[0]

    assert "--outdir" in container.args
    outdir_index = container.args.index("--outdir")
    assert container.args[outdir_index + 1] == "/workspace/results"


def test_non_nf_core_pipeline_does_not_receive_default_outdir() -> None:
    params = _build_params("my-org/custom-pipeline")
    settings = Settings()

    job = _build_job_manifest(run_id="run-123", params=params, settings=settings)

    container = job.spec.template.spec.containers[0]

    assert "--outdir" not in container.args


def test_nf_core_pipeline_respects_user_outdir() -> None:
    params = _build_params("https://github.com/nf-core/rnaseq", outdir="/custom/path")
    settings = Settings()

    job = _build_job_manifest(run_id="run-123", params=params, settings=settings)

    container = job.spec.template.spec.containers[0]

    outdir_index = container.args.index("--outdir")
    assert container.args[outdir_index + 1] == "/custom/path"
