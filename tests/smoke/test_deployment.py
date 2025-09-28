from __future__ import annotations

import os
from typing import Final

import pytest
import requests

API_URL: Final[str | None] = os.getenv("SMOKE_TEST_API_URL")
pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(API_URL is None, reason="SMOKE_TEST_API_URL not configured"),
]


def test_health_endpoint() -> None:
    response = requests.get(f"{API_URL}/healthz", timeout=10)
    assert response.status_code == 200


def test_k8s_connectivity_flag() -> None:
    response = requests.get(f"{API_URL}/api/v1/pipeline/status", timeout=10)
    assert response.status_code == 200
    data = response.json()
    assert "active" in data


def test_create_test_job() -> None:
    response = requests.post(
        f"{API_URL}/api/v1/pipeline/run",
        json={"parameters": {"pipeline": "hello", "parameters": {"test_mode": True}}},
        timeout=10,
    )
    assert response.status_code in (200, 202)
