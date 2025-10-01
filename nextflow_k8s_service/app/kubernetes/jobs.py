"""Utilities for creating and managing Kubernetes Jobs for Nextflow runs."""

from __future__ import annotations

import asyncio
import logging
import math
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


def _build_demo_job_manifest(
    *,
    run_id: str,
    batch_count: int,
    settings: Settings,
) -> k8s_client.V1Job:
    """Build job manifest specifically for the demo workflow.

    This simplified version:
    - Uses workflow files bundled in the app container image
    - Init container copies workflows from app image to shared volume
    - Hardcodes demo-optimized resource limits
    - Only accepts batch_count parameter (no arbitrary parameters)
    """
    metadata = k8s_client.V1ObjectMeta(
        name=f"nextflow-run-{run_id}",
        labels=_job_labels(run_id),
    )

    # Simple args for demo workflow - run local file with batch count parameter
    # Set -name to match run_id so publishDir path matches API expectations
    args = [
        "run",
        "/workflows/demo.nf",
        "-name",
        run_id,
        "-c",
        "/etc/nextflow/nextflow.config",
        f"--batches={batch_count}",
    ]

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

    # Mount workflows from shared emptyDir (populated by init container)
    workflow_volume_mount = k8s_client.V1VolumeMount(
        name="workflows",
        mount_path="/workflows",
        read_only=True,
    )

    container = k8s_client.V1Container(
        name="nextflow",
        image=settings.nextflow_image,
        command=["nextflow"],
        args=args,
        env=[
            k8s_client.V1EnvVar(name="NXF_WORK", value="/workspace"),
        ],
        volume_mounts=[pvc_volume_mount, config_volume_mount, workflow_volume_mount],
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

    # EmptyDir for workflow files (populated by init container from app image)
    workflow_volume = k8s_client.V1Volume(
        name="workflows",
        empty_dir=k8s_client.V1EmptyDirVolumeSource(),
    )

    # Init container to copy workflows from app image
    init_container = k8s_client.V1Container(
        name="copy-workflows",
        image=settings.app_image,  # Use the app image which has workflows at /app/workflows
        command=["sh", "-c"],
        args=["cp -r /app/workflows/* /workflows/"],
        volume_mounts=[
            k8s_client.V1VolumeMount(
                name="workflows",
                mount_path="/workflows",
            )
        ],
        resources=k8s_client.V1ResourceRequirements(
            requests={"cpu": "100m", "memory": "64Mi"},
            limits={"cpu": "200m", "memory": "128Mi"},
        ),
    )

    template = k8s_client.V1PodTemplateSpec(
        metadata=k8s_client.V1ObjectMeta(labels=_job_labels(run_id)),
        spec=k8s_client.V1PodSpec(
            restart_policy="Never",
            service_account_name=settings.nextflow_service_account,
            init_containers=[init_container],
            containers=[container],
            volumes=[pvc_volume, config_volume, workflow_volume],
        ),
    )

    spec = k8s_client.V1JobSpec(
        template=template,
        backoff_limit=settings.job_backoff_limit,
        active_deadline_seconds=settings.job_active_deadline_seconds,
        ttl_seconds_after_finished=settings.job_ttl_seconds_after_finished,
    )

    return k8s_client.V1Job(api_version="batch/v1", kind="Job", metadata=metadata, spec=spec)


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


async def create_demo_job(run_id: str, batch_count: int, settings: Settings) -> k8s_client.V1Job:
    """Create Kubernetes job for the demo workflow.

    Simplified version specifically for the demo pipeline:
    - Uses hardcoded demo-optimized Nextflow config
    - Only accepts batch_count parameter
    - Mounts demo workflow from ConfigMap
    """
    kube = get_kubernetes_client(settings)

    # Demo-specific Nextflow config - hardcoded and optimized for 50Gi/14 CPU homelab
    # Controller: 2 CPU + 4Gi
    # Workers: 1 CPU + 4GB each, up to 12 workers (batch_count * 2)
    cpu_limit = int(settings.worker_cpu_limit) if not settings.worker_cpu_limit.endswith("m") else 1
    memory_limit_k8s = settings.worker_memory_limit.replace(" GB", "Gi").replace(" MB", "Mi")

    nextflow_config = f"""
process {{
    executor = 'k8s'
    cpus = {cpu_limit}
    memory = '{settings.worker_memory_limit}'
}}

params {{
    batches = {batch_count}
    max_cpus = {cpu_limit}
    max_memory = '{settings.worker_memory_limit}'
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

    job_manifest = _build_demo_job_manifest(run_id=run_id, batch_count=batch_count, settings=settings)

    def _create() -> k8s_client.V1Job:
        return kube.batch.create_namespaced_job(namespace=settings.nextflow_namespace, body=job_manifest)

    try:
        job = await asyncio.to_thread(_create)
        logger.info(
            "Created demo job %s for run %s (batch_count=%d)",
            job.metadata.name if job else "<unknown>",
            run_id,
            batch_count,
        )
        return job
    except ApiException as exc:
        logger.exception("Failed to create demo job for run %s: %s", run_id, exc)
        # Clean up the ConfigMap if job creation fails
        await _delete_config_map(run_id, settings)
        raise


async def create_job(run_id: str, params: PipelineParameters, settings: Settings) -> k8s_client.V1Job:
    kube = get_kubernetes_client(settings)

    # Build the Nextflow config with pod-level resource limits
    # The k8s.pod directive allows explicit resource requests/limits configuration
    # which is required when namespace has ResourceQuota policies

    # Parse CPU limit for Nextflow process directives
    # Nextflow cpus directive requires integers (whole CPUs), even though k8s limits can be fractional
    cpu_limit = settings.worker_cpu_limit
    if cpu_limit.endswith("m"):
        # Convert millicores to CPUs and round up (e.g., 500m -> 1 CPU)
        cpu_numeric = math.ceil(float(cpu_limit.rstrip("m")) / 1000)
    else:
        cpu_numeric = int(cpu_limit)

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


async def _delete_workspace_results(run_id: str, settings: Settings) -> None:
    """Delete workspace results for a run using a cleanup pod.

    Since the results are on a PVC, we need to run a pod with the PVC mounted
    to delete the run-specific results directory.
    """
    kube = get_kubernetes_client(settings)
    cleanup_pod_name = f"cleanup-{run_id}"

    # Create a simple pod that mounts the PVC and deletes the results
    pod_manifest = k8s_client.V1Pod(
        metadata=k8s_client.V1ObjectMeta(
            name=cleanup_pod_name,
            namespace=settings.nextflow_namespace,
            labels={"app": "nextflow-cleanup", "run-id": run_id},
        ),
        spec=k8s_client.V1PodSpec(
            restart_policy="Never",
            containers=[
                k8s_client.V1Container(
                    name="cleanup",
                    image="busybox:latest",
                    command=["sh", "-c", f"rm -rf /workspace/results/{run_id} /workspace/work-{run_id}*"],
                    volume_mounts=[
                        k8s_client.V1VolumeMount(
                            name="nextflow-work",
                            mount_path="/workspace",
                        ),
                    ],
                ),
            ],
            volumes=[
                k8s_client.V1Volume(
                    name="nextflow-work",
                    persistent_volume_claim=k8s_client.V1PersistentVolumeClaimVolumeSource(
                        claim_name="nextflow-work-pvc",
                    ),
                ),
            ],
        ),
    )

    def _create_cleanup_pod() -> None:
        kube.core.create_namespaced_pod(namespace=settings.nextflow_namespace, body=pod_manifest)

    def _delete_cleanup_pod() -> None:
        kube.core.delete_namespaced_pod(
            name=cleanup_pod_name,
            namespace=settings.nextflow_namespace,
            grace_period_seconds=0,
        )

    try:
        # Create cleanup pod
        await asyncio.to_thread(_create_cleanup_pod)
        logger.info("Created cleanup pod %s for run %s", cleanup_pod_name, run_id)

        # Wait a few seconds for cleanup to complete
        await asyncio.sleep(5)

        # Delete cleanup pod
        await asyncio.to_thread(_delete_cleanup_pod)
        logger.info("Deleted cleanup pod %s", cleanup_pod_name)
    except ApiException as exc:
        if exc.status == 409:  # Pod already exists
            logger.warning("Cleanup pod %s already exists", cleanup_pod_name)
        else:
            logger.warning("Failed to cleanup workspace for run %s: %s", run_id, exc)


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
            # Clean up workspace results to free storage
            await _delete_workspace_results(run_id, settings)
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
