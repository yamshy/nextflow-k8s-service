from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.main import get_pipeline_manager as main_get_pipeline_manager
from app.main import get_state_store as main_get_state_store
from app.models import ActiveRunStatus, CancelResponse, RunResponse, RunStatus
from app.services.pipeline_manager import PipelineManager
from app.utils.broadcaster import Broadcaster


class DummyStateStore:
    async def ping(self) -> None:  # pragma: no cover - simple stub
        return None


@pytest.fixture
def pipeline_manager_stub() -> AsyncMock:
    manager = AsyncMock(spec=PipelineManager)
    manager.start_or_attach_run.return_value = RunResponse(
        run_id="integration-run",
        status=RunStatus.STARTING,
        attached=False,
        job_name="nextflow-run-integration-run",
    )
    inactive_status = ActiveRunStatus(active=False, run=None)
    manager.current_status.return_value = inactive_status
    manager.is_active.return_value = inactive_status
    manager.get_history.return_value = []
    manager.cancel_active_run.return_value = CancelResponse(
        run_id="integration-run",
        status=RunStatus.CANCELLED,
        cancelled=True,
        detail=None,
    )
    return manager


@pytest.fixture
def integration_app(pipeline_manager_stub: AsyncMock) -> Iterator[FastAPI]:
    app = create_app()

    @asynccontextmanager
    async def noop_lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield

    app.router.lifespan_context = noop_lifespan

    broadcaster = Broadcaster()
    app.state.pipeline_manager = pipeline_manager_stub
    app.state.state_store = DummyStateStore()
    app.state.broadcaster = broadcaster
    app.state.log_streamer = AsyncMock()
    app.state.settings = None
    app.user_middleware = [
        middleware
        for middleware in app.user_middleware
        if middleware.cls.__name__ != "SlowAPIMiddleware"
    ]

    def override_pipeline_manager() -> AsyncMock:
        return pipeline_manager_stub

    def override_state_store() -> DummyStateStore:
        return app.state.state_store  # type: ignore[return-value]

    app.dependency_overrides[main_get_pipeline_manager] = override_pipeline_manager
    app.dependency_overrides[main_get_state_store] = override_state_store

    yield app

    app.dependency_overrides.clear()


@pytest.fixture
async def async_client(integration_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=integration_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def sync_client(integration_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(integration_app) as client:
        yield client
