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


def _is_nf_core_pipeline(pipeline: str) -> bool:
    """Return True if the given pipeline refers to an nf-core workflow."""

    normalized = pipeline.strip().lower()
    nf_core_prefixes = (
        "nf-core/",
        "https://github.com/nf-core/",
        "http://github.com/nf-core/",
        "git@github.com:nf-core/",
        "github.com/nf-core/",
        "gh:nf-core/",
        "https://nf-co.re/",
        "http://nf-co.re/",
        "nf-co.re/",
    )

    return any(normalized.startswith(prefix) for prefix in nf_core_prefixes)


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
    boolean_core_options = {"resume", "with-docker", "with-singularity", "with-conda"}

    # Set default outdir only for nf-core pipelines (required by most of them)
    pipeline_parameters = dict(params.parameters)
    if _is_nf_core_pipeline(params.pipeline) and "outdir" not in pipeline_parameters:
        pipeline_parameters["outdir"] = "/workspace/results"

    args = [
        "run",
        params.pipeline,
        "-c",
        "/etc/nextflow/nextflow.config",
    ]
    for key, value in pipeline_parameters.items():
        if key == "revision":
            # Special handling for revision shorthand
            args.extend(["-r", str(value)])
        elif key in nextflow_core_options:
            flag = f"-{key}"
            if isinstance(value, bool):
                if value:
                    # Boolean core options should be emitted as flags without a value
                    args.append(flag)
                continue

            if key in boolean_core_options and not value:
                # Skip falsy values for boolean-style core options
                continue

            # Core Nextflow options use single dash and accept values when provided
            args.extend([flag, str(value)])
        else:
            # Pipeline parameters use double dash
            args.extend([f"--{key}", str(value)])

    # Configure volume mounts
    pvc_volume_mount = k8s_client.V1VolumeMount(
        name="nextflow-work",
        mount_path="/workspace",
    )

    config_volume_mount = k8s_client.V1VolumeMount(
        name="nextflow-config",
        mount_path="/etc/nextflow",
        read_only=True,
    )

    container = k8s_client.V1Container(
        name="nextflow",
        image=settings.nextflow_image,
        command=["nextflow"],
        args=args,
        env=[
            k8s_client.V1EnvVar(name="NXF_WORK", value=params.workdir or "/workspace"),
        ],
        volume_mounts=[pvc_volume_mount, config_volume_mount],
        resources=k8s_client.V1ResourceRequirements(
            requests={
                "cpu": settings.controller_cpu_request,
                "memory": settings.controller_memory_request,
            },
            limits={
                "cpu": settings.controller_cpu_limit,
                "memory": settings.controller_memory_limit,
            },
        ),
    )

    # Define volumes
    pvc_volume = k8s_client.V1Volume(
        name="nextflow-work",
        persistent_volume_claim=k8s_client.V1PersistentVolumeClaimVolumeSource(
            claim_name="nextflow-work-pvc",
        ),
    )

    config_volume = k8s_client.V1Volume(
        name="nextflow-config",
        config_map=k8s_client.V1ConfigMapVolumeSource(
            name=f"nextflow-config-{run_id}",
        ),
    )

    template = k8s_client.V1PodTemplateSpec(
        metadata=k8s_client.V1ObjectMeta(labels=_job_labels(run_id)),
        spec=k8s_client.V1PodSpec(
            restart_policy="Never",
            service_account_name=settings.nextflow_service_account,
            containers=[container],
            volumes=[pvc_volume, config_volume],
        ),
    )

    spec = k8s_client.V1JobSpec(
        template=template,
        backoff_limit=settings.job_backoff_limit,
        active_deadline_seconds=settings.job_active_deadline_seconds,
        ttl_seconds_after_finished=settings.job_ttl_seconds_after_finished,
    )

    return k8s_client.V1Job(api_version="batch/v1", kind="Job", metadata=metadata, spec=spec)


async def _create_config_map(run_id: str, config_content: str, settings: Settings) -> None:
    """Create a ConfigMap containing the Nextflow configuration."""
    kube = get_kubernetes_client(settings)

    config_map = k8s_client.V1ConfigMap(
        metadata=k8s_client.V1ObjectMeta(
            name=f"nextflow-config-{run_id}",
            labels=_job_labels(run_id),
        ),
        data={"nextflow.config": config_content},
    )

    def _create() -> k8s_client.V1ConfigMap:
        return kube.core.create_namespaced_config_map(namespace=settings.nextflow_namespace, body=config_map)

    try:
        await asyncio.to_thread(_create)
        logger.info("Created ConfigMap nextflow-config-%s", run_id)
    except ApiException as exc:
        logger.exception("Failed to create ConfigMap for run %s: %s", run_id, exc)
        raise


async def create_job(run_id: str, params: PipelineParameters, settings: Settings) -> k8s_client.V1Job:
    kube = get_kubernetes_client(settings)

    # Build the Nextflow config with pod-level resource limits
    # The k8s.pod directive allows explicit resource requests/limits configuration
    # which is required when namespace has ResourceQuota policies

    # Parse CPU limit for Nextflow process directives
    cpu_limit = settings.worker_cpu_limit
    if cpu_limit.endswith("m"):
        cpu_numeric = float(cpu_limit.rstrip("m")) / 1000
    else:
        cpu_numeric = float(cpu_limit)

    # Convert memory format for k8s directives (Nextflow uses GB/MB, k8s uses Gi/Mi)
    # For process directive, use Nextflow format (e.g., "1 GB")
    # For k8s.memoryLimits, convert to k8s format (e.g., "1Gi")
    memory_limit_k8s = settings.worker_memory_limit.replace(" GB", "Gi").replace(" MB", "Mi")

    # For nf-core pipelines: override process-specific resource requirements
    # Use withName: '.*' (regex for all) to apply limits to ALL processes, preventing quota violations
    # This overrides nf-core's default resource requests which can be 12Gi+ per process
    nextflow_config = f"""
process {{
    executor = 'k8s'
    cpus = {cpu_numeric}
    memory = '{settings.worker_memory_limit}'
    
    // Override resource requests for all processes (including nf-core)
    withName: '.*' {{
        cpus = {cpu_numeric}
        memory = '{settings.worker_memory_limit}'
    }}
}}

// Set maximum resource limits to prevent any process from exceeding quota
params {{
    max_cpus = {cpu_numeric}
    max_memory = '{settings.worker_memory_limit}'
    max_time = '4.h'
}}

k8s {{
    storageClaimName = 'nextflow-work-pvc'
    storageMountPath = '/workspace'
    namespace = '{settings.nextflow_namespace}'
    serviceAccount = '{settings.nextflow_service_account}'
    cpuLimits = '{settings.worker_cpu_limit}'
    memoryLimits = '{memory_limit_k8s}'
}}
"""

    # Create ConfigMap first
    await _create_config_map(run_id, nextflow_config, settings)

    job_manifest = _build_job_manifest(run_id=run_id, params=params, settings=settings)

    def _create() -> k8s_client.V1Job:
        return kube.batch.create_namespaced_job(namespace=settings.nextflow_namespace, body=job_manifest)

    try:
        job = await asyncio.to_thread(_create)
        logger.info("Created job %s for run %s", job.metadata.name if job else "<unknown>", run_id)
        return job
    except ApiException as exc:
        logger.exception("Failed to create job for run %s: %s", run_id, exc)
        # Clean up the ConfigMap if job creation fails
        await _delete_config_map(run_id, settings)
        raise


async def _delete_config_map(run_id: str, settings: Settings) -> None:
    """Delete the ConfigMap for a run."""
    kube = get_kubernetes_client(settings)
    config_map_name = f"nextflow-config-{run_id}"

    def _delete() -> None:
        kube.core.delete_namespaced_config_map(
            name=config_map_name,
            namespace=settings.nextflow_namespace,
        )

    try:
        await asyncio.to_thread(_delete)
        logger.info("Deleted ConfigMap %s", config_map_name)
    except ApiException as exc:
        if exc.status == 404:
            logger.warning("ConfigMap %s already gone", config_map_name)
        else:
            logger.warning("Failed to delete ConfigMap %s: %s", config_map_name, exc)


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

        # Extract run_id from job_name (format: nextflow-run-{run_id})
        if job_name.startswith("nextflow-run-"):
            run_id = job_name.replace("nextflow-run-", "")
            await _delete_config_map(run_id, settings)
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
        kwargs = {
            "name": pod_name,
            "namespace": settings.nextflow_namespace,
            "container": container,
            "follow": False,
            "timestamps": True,
        }
        if since_time:
            # Calculate seconds since the provided time
            elapsed = (datetime.now(timezone.utc) - since_time).total_seconds()
            # Use since_seconds parameter (must be positive)
            if elapsed > 0:
                kwargs["since_seconds"] = int(elapsed)
        return kube.core.read_namespaced_pod_log(**kwargs)

    return await asyncio.to_thread(_logs)
