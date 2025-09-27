# Nextflow K8s Service – Agent Guide

Welcome! This guide explains how to work effectively inside this repository. Read it fully before making
changes—future tasks may depend on the conventions described here.

## Repository Overview
- **Language & Runtime**: Python 3.13 (see `.python-version`). Runtime deps are managed with [`uv`](https://github.com/astral-sh/uv).
- **Entrypoints**:
  - `main.py` (repo root) starts the FastAPI app via uvicorn for local dev.
  - `nextflow_k8s_service/app/main.py` constructs the FastAPI application, sets up lifespan hooks, middleware, and routers.
- **Core packages** live under `nextflow_k8s_service/app/`:
  - `api/` – REST (`routes.py`) and WebSocket (`websocket.py`) routers.
  - `config.py` – `pydantic-settings` configuration model.
  - `models.py` – Pydantic request/response/log schemas.
  - `services/` – orchestration (`pipeline_manager.py`), log streaming, and state persistence.
  - `kubernetes/` – thin wrappers around the Kubernetes Python client (job manifests, polling, etc.).
  - `utils/broadcaster.py` – fan-out helper for WebSocket clients.
- Supporting assets include `k8s-manifests/` (deployment YAML), `examples/` (sample Nextflow workflow), and integration tests under `tests/`.

## Development Workflow
1. **Install deps** with `uv sync` (include `--group dev` for test-only packages).
2. **Run tests** using `uv run pytest` from the repo root. Tests rely on `pytest`, `pytest-asyncio`, and `pytest-mock`.
3. **Start the API locally** with `uv run uvicorn app.main:app --reload --port 8000 --app-dir nextflow_k8s_service` (or run `python main.py`).
4. **Kubernetes access**: runtime helpers use the Python Kubernetes client. Blocking calls are wrapped with `asyncio.to_thread`; match this pattern when adding new Kubernetes operations.

## Code Style & Conventions
- Use [PEP 484](https://www.python.org/dev/peps/pep-0484/) type hints everywhere. Most modules start with `from __future__ import annotations`; new files in `nextflow_k8s_service/app/` should follow suit.
- Favor `async`/`await` for IO-bound workflows. Coordinate background work with `asyncio.create_task`, `asyncio.Lock`, and `asyncio.Event` as done in existing services.
- Log via the `logging` module (see `app/main.py`, `services/`, and `kubernetes/` modules for patterns). Avoid `print`.
- When extending the API, use FastAPI dependency injection (`Depends`) and the existing `PipelineManager`/`StateStore` accessors.
- For WebSocket fan-out, route everything through `Broadcaster.broadcast`; do not write directly to the socket set.
- Maintain immutability of Pydantic models where practical; when adding models use `BaseModel` and enums consistent with `models.py`.
- Prefer raising domain-specific errors and let FastAPI translate them, or convert to `HTTPException` in routers as needed.

## State & Concurrency Notes
- `StateStore` manages the single active run invariant. Changes that touch active run logic must preserve locking discipline (`self._lock`) and ensure Redis + in-memory paths stay in sync.
- `PipelineManager` is responsible for scheduling the Kubernetes job monitor and pushing events through `Broadcaster`. When extending it, update tests in `tests/test_pipeline_manager.py` accordingly.
- Log streaming in `LogStreamer` polls pods using per-run tasks and stop events. Extend this carefully to avoid leaking tasks.

## Testing Guidance
- Existing tests focus on state management, orchestration, and log parsing. Add or update tests alongside functional changes.
- Use `pytest.mark.asyncio` for async tests and `pytest-mock`'s `mocker` fixture for patching async methods (`AsyncMock`).
- Keep test imports relative to the FastAPI package (thanks to `tests/conftest.py` adding `nextflow_k8s_service` to `sys.path`).

## Dependency Management
- Add runtime dependencies in `pyproject.toml` under `[project.dependencies]` and re-run `uv sync` to refresh `uv.lock`.
- Dev/test-only deps belong in `[dependency-groups.dev]`; release tooling goes in `[dependency-groups.release]`.
- Never hand-edit `uv.lock`; let `uv` regenerate it.

## Docs & Operational Assets
- Update `README.md` (root and `nextflow_k8s_service/README.md`) if you introduce new commands, environment variables, or deployment changes.
- Deployment YAML in `k8s-manifests/` should reflect any new config knobs or container images.

## Pull Request Expectations
- Keep commits conventional (e.g., `feat:`, `fix:`) to satisfy semantic-release.
- Ensure `uv run pytest` passes locally before submitting.
- Include relevant documentation/test updates with feature or bugfix changes.

Following these guidelines will keep the Nextflow K8s Service consistent and maintainable. Happy hacking!
