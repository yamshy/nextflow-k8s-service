"""Tests for the Kubernetes client helpers."""

from __future__ import annotations

from app.config import Settings
from app.kubernetes import client as client_module


def test_get_kubernetes_client_reuses_cached_instance(monkeypatch) -> None:
    """Repeated calls with equivalent settings should reuse the cached client."""

    # Ensure a clean cache for the test run.
    client_module._CLIENT_CACHE.clear()

    # Avoid touching a real kube config or API classes during the test.
    monkeypatch.setattr(client_module, "_load_kube_config", lambda *_: None)
    monkeypatch.setattr(client_module.k8s_client, "CoreV1Api", lambda: object())
    monkeypatch.setattr(client_module.k8s_client, "BatchV1Api", lambda: object())

    settings = Settings()

    first_client = client_module.get_kubernetes_client(settings)
    second_client = client_module.get_kubernetes_client(settings)

    assert first_client is second_client
