"""Utilities for creating and managing Kubernetes Jobs for Nextflow runs."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from kubernetes import client as k8s_client
from kubernetes.client import ApiException

from ..config import Settings
from ..models import PipelineParameters, RunStatus
from .client import get_kubernetes_client

logger = logging.getLogger(__name__)


def _job_labels(run_id: str) -> Dict[str, str]:
    return {
        "app": "nextflow-pipeline",
        "run-id": run_id,
    }


def _build_job_manifest(
    *,
    run_id: str,
    params: PipelineParameters,
    settings: Settings,
) -> k8s_client.V1Job:
    metadata = k8s_client.V1ObjectMeta(
        name=f"nextflow-run-{run_id}",
        labels=_job_labels(run_id),
    )

    args = ["run", params.pipeline]
    for key, value in params.parameters.items():
        args.extend([f"--{key}", str(value)])

    container = k8s_client.V1Container(
        name="nextflow",
        image=settings.nextflow_image,
        args=args,
        env=[
            k8s_client.V1EnvVar(name="NXF_WORK", value=params.workdir or "/workspace"),
        ],
    )

    template = k8s_client.V1PodTemplateSpec(
        metadata=k8s_client.V1ObjectMeta(labels=_job_labels(run_id)),
        spec=k8s_client.V1PodSpec(
            restart_policy="Never",
            service_account_name=settings.nextflow_service_account,
            containers=[container],
        ),
    )

    spec = k8s_client.V1JobSpec(
        template=template,
        backoff_limit=settings.job_backoff_limit,
        active_deadline_seconds=settings.job_active_deadline_seconds,
    )

    return k8s_client.V1Job(api_version="batch/v1", kind="Job", metadata=metadata, spec=spec)


async def create_job(run_id: str, params: PipelineParameters, settings: Settings) -> k8s_client.V1Job:
    kube = get_kubernetes_client(settings)
    job_manifest = _build_job_manifest(run_id=run_id, params=params, settings=settings)

    def _create() -> k8s_client.V1Job:
        return kube.batch.create_namespaced_job(namespace=settings.nextflow_namespace, body=job_manifest)

    try:
        job = await asyncio.to_thread(_create)
        logger.info("Created job %s for run %s", job.metadata.name if job else "<unknown>", run_id)
        return job
    except ApiException as exc:
        logger.exception("Failed to create job for run %s: %s", run_id, exc)
        raise


async def delete_job(job_name: str, settings: Settings, grace_period_seconds: Optional[int] = None) -> None:
    kube = get_kubernetes_client(settings)

    body = k8s_client.V1DeleteOptions(grace_period_seconds=grace_period_seconds)

    def _delete() -> None:
        kube.batch.delete_namespaced_job(
            name=job_name,
            namespace=settings.nextflow_namespace,
            body=body,
            propagation_policy="Foreground",
        )

    try:
        await asyncio.to_thread(_delete)
        logger.info("Deleted job %s", job_name)
    except ApiException as exc:
        if exc.status == 404:
            logger.warning("Job %s already gone", job_name)
        else:
            logger.exception("Failed to delete job %s: %s", job_name, exc)
            raise


async def get_job_status(job_name: str, settings: Settings) -> RunStatus:
    kube = get_kubernetes_client(settings)

    def _read() -> k8s_client.V1Job:
        return kube.batch.read_namespaced_job(name=job_name, namespace=settings.nextflow_namespace)

    try:
        job = await asyncio.to_thread(_read)
    except ApiException as exc:
        if exc.status == 404:
            return RunStatus.UNKNOWN
        raise

    status = job.status
    if status is None:
        return RunStatus.UNKNOWN
    if status.active:
        return RunStatus.RUNNING
    if status.succeeded:
        return RunStatus.SUCCEEDED
    if status.failed:
        return RunStatus.FAILED
    return RunStatus.UNKNOWN


async def list_job_pods(job_name: str, settings: Settings) -> list[k8s_client.V1Pod]:
    kube = get_kubernetes_client(settings)

    def _list() -> k8s_client.V1PodList:
        return kube.core.list_namespaced_pod(
            namespace=settings.nextflow_namespace,
            label_selector=f"job-name={job_name}",
        )

    pods = await asyncio.to_thread(_list)
    return pods.items if pods else []


async def get_pod_log_stream(
    *,
    pod_name: str,
    container: str,
    settings: Settings,
    since_time: Optional[datetime] = None,
) -> str:
    kube = get_kubernetes_client(settings)

    def _logs() -> str:
        return kube.core.read_namespaced_pod_log(
            name=pod_name,
            namespace=settings.nextflow_namespace,
            container=container,
            since_time=since_time,
            follow=False,
            timestamps=True,
        )

    return await asyncio.to_thread(_logs)
