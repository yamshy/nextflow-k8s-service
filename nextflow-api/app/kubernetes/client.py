"""Helpers for obtaining configured Kubernetes API clients."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config

from ..config import Settings

logger = logging.getLogger(__name__)


def _load_kube_config(settings: Settings) -> None:
    try:
        k8s_config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes configuration")
    except k8s_config.ConfigException:
        logger.info("Falling back to local kubeconfig")
        if settings.kube_context:
            k8s_config.load_kube_config(context=settings.kube_context)
        else:
            k8s_config.load_kube_config()
        logger.info("Loaded kubeconfig for context '%s'", settings.kube_context or "current")


class KubernetesClient:
    """Thin wrapper that exposes the Kubernetes API clients we need."""

    def __init__(self, settings: Settings) -> None:
        _load_kube_config(settings)
        self.core: k8s_client.CoreV1Api = k8s_client.CoreV1Api()
        self.batch: k8s_client.BatchV1Api = k8s_client.BatchV1Api()


@lru_cache()
def get_kubernetes_client(settings: Optional[Settings] = None) -> KubernetesClient:
    if settings is None:
        settings = Settings()
    return KubernetesClient(settings)
