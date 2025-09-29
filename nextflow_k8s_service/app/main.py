"""FastAPI application entrypoint for the Nextflow pipeline controller service."""
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from .api.routes import router as pipeline_router
from .api.websocket import router as websocket_router
from .config import Settings, get_settings
from .services.log_streamer import LogStreamer
from .services.pipeline_manager import PipelineManager
from .services.state_store import StateStore
from .utils.broadcaster import Broadcaster

logger = logging.getLogger("nextflow.pipeline")
logging.basicConfig(level=logging.INFO)


limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    logger.info("Initializing service with namespace '%s'", settings.nextflow_namespace)

    broadcaster = Broadcaster()
    state_store = await StateStore.create(settings)
    log_streamer = LogStreamer(settings=settings, broadcaster=broadcaster)
    pipeline_manager = PipelineManager(
        settings=settings,
        state_store=state_store,
        log_streamer=log_streamer,
        broadcaster=broadcaster,
    )

    app.state.settings = settings
    app.state.state_store = state_store
    app.state.pipeline_manager = pipeline_manager
    app.state.broadcaster = broadcaster
    app.state.log_streamer = log_streamer

    try:
        yield
    finally:
        logger.info("Shutting down service")
        await pipeline_manager.shutdown()
        await state_store.close()
        await log_streamer.close()


def get_pipeline_manager(request: Request) -> PipelineManager:
    return request.app.state.pipeline_manager


def get_state_store(request: Request) -> StateStore:
    return request.app.state.state_store


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Nextflow Pipeline Controller",
        version="1.0.9",
        lifespan=lifespan,
    )

    app.state.limiter = limiter

    app.add_middleware(
        SlowAPIMiddleware,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(pipeline_router, prefix="/api/v1")
    app.include_router(websocket_router, prefix="/api/v1")

    async def _healthcheck(state_store: StateStore = Depends(get_state_store)) -> dict[str, str]:
        try:
            await state_store.ping()
            status = "ok"
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Healthcheck failed: %s", exc)
            status = "degraded"
        return {"status": status}

    app.get("/healthz", tags=["health"])(_healthcheck)
    app.get("/health", tags=["health"])(_healthcheck)

    return app


app = create_app()
