from __future__ import annotations

import pytest

from app.models import PipelineParameters, RunRequest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_start_pipeline_run(async_client, pipeline_manager_stub) -> None:
    payload = RunRequest(parameters=PipelineParameters(pipeline="hello", parameters={"profile": "test"}))
    response = await async_client.post("/api/v1/pipeline/run", json=payload.model_dump())

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "integration-run"
    assert data["status"] == "starting"
    assert pipeline_manager_stub.start_or_attach_run.await_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_endpoint(async_client, pipeline_manager_stub) -> None:
    response = await async_client.get("/api/v1/pipeline/status")

    assert response.status_code == 200
    data = response.json()
    assert data["active"] is False
    assert pipeline_manager_stub.current_status.await_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cancel_endpoint(async_client, pipeline_manager_stub) -> None:
    response = await async_client.delete("/api/v1/pipeline/cancel")

    assert response.status_code == 200
    data = response.json()
    assert data["cancelled"] is True
    assert pipeline_manager_stub.cancel_active_run.await_count == 1
