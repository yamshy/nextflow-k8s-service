"""Utilities for creating and managing Kubernetes Jobs for Nextflow runs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from kubernetes import client as k8s_client
from kubernetes.client import ApiException

from ..config import Settings
from ..models import PipelineParameters, RunStatus
from .client import get_kubernetes_client

logger = logging.getLogger(__name__)


def _job_labels(run_id: str) -> dict[str, str]:
    return {
        "app": "nextflow-pipeline",
        "run-id": run_id,
    }


def _pod_sort_key(pod: k8s_client.V1Pod) -> datetime:
    if pod.status and pod.status.start_time:
        return pod.status.start_time
    if pod.metadata and pod.metadata.creation_timestamp:
        return pod.metadata.creation_timestamp
    return datetime.min.replace(tzinfo=timezone.utc)


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

    # Nextflow core options use single dash, pipeline parameters use double dash
    nextflow_core_options = {"profile", "revision", "resume", "with-docker", "with-singularity", "with-conda"}

    # Set default outdir if not provided (required by most nf-core pipelines)
    if "outdir" not in params.parameters:
        params.parameters["outdir"] = "/workspace/results"

    args = ["run", params.pipeline]
    for key, value in params.parameters.items():
        if key == "revision":
            # Special handling for revision shorthand
            args.extend(["-r", str(value)])
        elif key in nextflow_core_options:
            # Core Nextflow options use single dash
            args.extend([f"-{key}", str(value)])
        else:
            # Pipeline parameters use double dash
            args.extend([f"--{key}", str(value)])

    container = k8s_client.V1Container(
        name="nextflow",
        image=settings.nextflow_image,
        command=["nextflow"],
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
        ttl_seconds_after_finished=settings.job_ttl_seconds_after_finished,
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


async def get_job_status(job_name: str, settings: Settings, *, max_retries: int = 3) -> RunStatus:
    """Get job status with retry logic to handle Kubernetes update race conditions.

    Args:
        job_name: Name of the Kubernetes job
        settings: Application settings
        max_retries: Number of times to retry if status is UNKNOWN (default: 3)
    """
    kube = get_kubernetes_client(settings)

    def _read() -> k8s_client.V1Job:
        return kube.batch.read_namespaced_job(name=job_name, namespace=settings.nextflow_namespace)

    for attempt in range(max_retries):
        try:
            job = await asyncio.to_thread(_read)
        except ApiException as exc:
            if exc.status == 404:
                return RunStatus.UNKNOWN
            raise

        status = job.status
        if status is None:
            if attempt < max_retries - 1:
                await asyncio.sleep(1.0)
                continue
            return RunStatus.UNKNOWN

        # Check conditions first (most authoritative when present)
        if status.conditions:
            for condition in status.conditions:
                if condition.type == "Complete" and condition.status == "True":
                    return RunStatus.SUCCEEDED
                if condition.type == "Failed" and condition.status == "True":
                    return RunStatus.FAILED

        # Check active before succeeded/failed to handle retrying jobs correctly
        # A job with active > 0 is still running/retrying, even if failed > 0
        if status.active and status.active > 0:
            return RunStatus.RUNNING

        # Only check terminal counters once job is no longer active
        if status.succeeded and status.succeeded > 0:
            return RunStatus.SUCCEEDED
        if status.failed and status.failed > 0:
            return RunStatus.FAILED

        # Fallback: check pod status if job status fields not yet updated (race condition)
        if status.active is None or status.active == 0:
            pods = await list_job_pods(job_name=job_name, settings=settings)
            if pods:
                terminal_pods = [pod for pod in pods if pod.status and pod.status.phase in {"Succeeded", "Failed"}]
                latest_pod = max(terminal_pods or pods, key=_pod_sort_key)
                pod = latest_pod
                if pod.status and pod.status.phase == "Succeeded":
                    return RunStatus.SUCCEEDED
                if pod.status and pod.status.phase == "Failed":
                    return RunStatus.FAILED

        # If we got UNKNOWN and this isn't the last attempt, wait and retry
        if attempt < max_retries - 1:
            await asyncio.sleep(1.0)
            continue

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
