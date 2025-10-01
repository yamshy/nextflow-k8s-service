# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

**This is a portfolio showcase application**, not a generic Nextflow orchestration service. It demonstrates:
- Cloud-native parallel data processing on Kubernetes
- Real-time WebSocket streaming of logs and status
- Scatter-gather pattern with a hardcoded demo workflow
- Resource-constrained deployment (optimized for 50Gi/14 CPU homelab)

**Key constraint**: Only runs the demo workflow at `/workflows/demo.nf`. Generic pipeline support was removed in v1.10.0.

## Development Commands

### Setup
```bash
uv sync                    # Install runtime dependencies
uv sync --group dev        # Install runtime + dev dependencies
```

### Running the Service
```bash
# Run locally with auto-reload
uv run uvicorn app.main:app --reload --port 8000 --app-dir nextflow_k8s_service

# Access OpenAPI docs at http://localhost:8000/docs
# Demo API: POST /api/v1/demo/run with {"batch_count": 5}
# WebSocket: ws://localhost:8000/api/v1/pipeline/stream
```

### Testing
```bash
uv run pytest                              # Run all tests
uv run pytest tests/test_state_store.py    # Run specific test file
uv run pytest -v                           # Verbose output
uv run pytest -k test_name                 # Run tests matching pattern
```

### Linting
```bash
uv run ruff check .         # Check for lint errors
uv run ruff format .        # Format code
```

## Development Workflow

### Making Changes

**IMPORTANT:** Always create changes in new branches and submit pull requests. Never commit directly to `main`.

**Workflow:**
1. **Start from main:**
   ```bash
   git checkout main
   git pull origin main
   ```

2. **Create a new branch:**
   ```bash
   git checkout -b fix/descriptive-name       # For bug fixes
   git checkout -b feat/descriptive-name      # For new features
   git checkout -b docs/descriptive-name      # For documentation
   ```

3. **Make changes and test:**
   ```bash
   # Make your changes
   uv run pytest -v              # Run tests
   uv run ruff check .           # Check for lint errors
   uv run ruff format .          # Format code
   ```

4. **Commit with conventional commit messages:**
   ```bash
   git add -A
   git commit -m "fix: add counter fallback for job status detection"
   # Or: feat:, docs:, chore:, refactor:, test:
   # Keep commit messages concise - focus on WHAT changed, not WHY
   # Details go in PR description, not commit message
   ```

5. **Push and create pull request:**
   ```bash
   git push -u origin fix/descriptive-name
   gh pr create --title "fix: description" --body "Detailed explanation"
   ```

### Branch Naming Conventions
- `fix/*` - Bug fixes
- `feat/*` - New features
- `docs/*` - Documentation updates
- `chore/*` - Maintenance tasks
- `refactor/*` - Code refactoring
- `test/*` - Test additions/improvements

### Pull Request Requirements
- All tests must pass (`uv run pytest -v`)
- Code must be linted and formatted (`uv run ruff check . && uv run ruff format .`)
- Use conventional commit format in PR title (e.g., `fix: add counter fallback`)
- **Commit messages should be concise** - state what changed, not why
- Include detailed explanation of changes and motivation in PR description
- Reference related issues if applicable

## Architecture Overview

### Demo Workflow

**`workflows/demo.nf`** - The only pipeline this service runs
- **GENERATE** process: Creates CSV data batches in parallel (5s per batch)
- **ANALYZE** process: Computes statistics on each batch (3s per batch)
- **REPORT** process: Aggregates all statistics into `report.json`
- Parameter: `--batches` (1-12, default 5) controls parallelism
- Each batch spawns 2 worker pods (GENERATE + ANALYZE)
- Results published to `/workspace/results/{run_id}/report.json`

**Resource allocation (homelab-optimized for 50Gi/14 CPU)**:
- Controller pod: 2 CPU + 4Gi memory
- Worker pods: 1 CPU + 4GB memory each
- Max parallelism: 12 batches = 24 workers (12 CPU + 48GB) + controller (2 CPU + 4Gi) = fits quota

### Core Components

**PipelineManager** (`app/services/pipeline_manager.py`)
- Orchestrates demo workflow lifecycle (single run at a time)
- **Key method**: `start_demo_run(request: DemoRunRequest)` - only way to start a pipeline
- Enforces single-run constraint via `StateStore` optimistic locking
- Spawns background `_monitor_run()` task that watches Kubernetes job completion
- Integrates `LogStreamer` for real-time log broadcasting

**StateStore** (`app/services/state_store.py`)
- Manages active run state and bounded history (last 5 runs, 30min TTL)
- Optimistic locking: Redis-backed when available, in-memory fallback
- Key methods: `acquire_active_run()`, `update_active_status()`, `finish_active_run()`, `get_history()`
- Redis keys: `nextflow:pipeline:active-run` (lock), `nextflow:pipeline:state` (run metadata hash)

**LogStreamer** (`app/services/log_streamer.py`)
- Streams logs from Kubernetes pods in real-time via background asyncio tasks
- Parses ISO timestamps from log lines for client-side ordering
- Broadcasts log chunks through `Broadcaster` to all WebSocket clients
- Auto-stops when run completes or is cancelled

**Broadcaster** (`app/utils/broadcaster.py`)
- WebSocket fan-out: maintains set of connected clients (max 100)
- Broadcasts JSON messages: `{type: "status"|"log"|"complete", data: {...}, timestamp, run_id}`
- Graceful disconnect handling

### Kubernetes Integration

**Client Management** (`app/kubernetes/client.py`)
- Auto-detects: in-cluster config in K8s, local kubeconfig otherwise
- Caches `KubernetesClient` per context
- Exposes `CoreV1Api` (pods) and `BatchV1Api` (jobs)

**Job Creation** (`app/kubernetes/jobs.py`)
- **`create_demo_job()`**: Builds K8s Job for demo workflow only
  - Mounts demo workflow from ConfigMap `demo-workflow`
  - Creates run-specific ConfigMap with Nextflow config
  - Sets `--batches` parameter and resource limits
- **`create_job()`**: Legacy function (kept for backward compatibility, not used in demo)
- **`delete_job()`**: Cleans up job, ConfigMap, and workspace results

**Monitoring** (`app/kubernetes/monitor.py`)
- `wait_for_completion()` polls job status every 2.5s
- Tracks phases: QUEUED → STARTING → RUNNING → SUCCEEDED/FAILED/CANCELLED/UNKNOWN
- Calls `on_status` callback for state transitions

### Request Flow

1. **Start Demo Run**: `POST /api/v1/demo/run` with `{"batch_count": 5}`
   - `PipelineManager.start_demo_run()` checks for active run
   - If active, returns existing run with `attached=True` (attach pattern)
   - Otherwise, acquires lock via `StateStore.acquire_active_run()`
   - Generates `run_id` (format: `r{11-char-hex}` for Nextflow compatibility)
   - Creates job via `jobs.create_demo_job()` with hardcoded demo workflow
   - Starts `LogStreamer` and schedules `_monitor_run()` task

2. **Monitor Run**: Background task `_monitor_run()`
   - Polls job status via `wait_for_completion()`
   - Broadcasts status changes through `Broadcaster`
   - On terminal state: stops log streamer, finishes run in state store, deletes job

3. **WebSocket Stream**: `WS /api/v1/pipeline/stream`
   - Clients receive real-time messages: status updates, log batches (max 50 lines), completion events
   - Message types: `status`, `progress`, `log`, `complete`, `error`

4. **Cancel Run**: `DELETE /api/v1/demo/cancel`
   - Deletes K8s job with 0s grace period
   - Stops log streamer, marks run as CANCELLED
   - Broadcasts cancellation event

### Configuration

**Settings** (`app/config.py`) - Demo-optimized configuration
- Uses `pydantic-settings` to load from `.env` file
- **Homelab constants**: `HOMELAB_TOTAL_MEMORY_GI=50`, `HOMELAB_TOTAL_CPU=14`
- **Demo workflow**: `DEMO_WORKFLOW_PATH=/app/workflows/demo.nf`
- Key settings:
  - `NEXTFLOW_NAMESPACE`: K8s namespace (default: `nextflow`)
  - `NEXTFLOW_IMAGE`: Nextflow container image (default: `nextflow/nextflow:25.04.7`)
  - `DEFAULT_BATCH_COUNT=5`, `MAX_BATCH_COUNT=12`: Demo parameter bounds
  - Controller resources: `CONTROLLER_CPU_LIMIT=2`, `CONTROLLER_MEMORY_LIMIT=4Gi` (fixed)
  - Worker resources: `WORKER_CPU_LIMIT=1`, `WORKER_MEMORY_LIMIT=4 GB` (fixed)
  - `RUN_HISTORY_LIMIT=5`, `RUN_TTL_MINUTES=30`: History retention
  - `REDIS_URL`: Optional (falls back to in-memory)
  - `KUBE_CONTEXT`: Local kubeconfig context (dev only)
  - `ALLOWED_ORIGINS`: CORS config (default: `["*"]`)

### API Endpoints (Demo-Only)

All endpoints under `/api/v1/demo/*`:
- **`POST /demo/run`**: Start demo with `{"batch_count": 5, "triggered_by": "..."}`
- **`GET /demo/preview?batch_count=5`**: Preview resource usage without running
- **`GET /demo/status`**: Current run status with progress
- **`GET /demo/active`**: Check if run is active
- **`DELETE /demo/cancel`**: Cancel active run
- **`GET /demo/history?limit=10`**: Recent run history
- **`WS /pipeline/stream`**: Real-time logs and status (WebSocket)
- **`GET /health`, `/healthz`**: Health checks

**Breaking change in v1.10.0**: Generic `/api/v1/pipeline/*` endpoints removed. Only demo endpoints supported.

### Testing Strategy

- `pytest` with `pytest-asyncio` for async code
- Mock Kubernetes API calls using `pytest-mock` (no cluster required)
- Tests cover: state store locking, pipeline orchestration, log streaming, WebSocket broadcasting
- Run history TTL and expiration validated with time-based scenarios

## Release Process

- Conventional Commits drive semantic versioning via `python-semantic-release`
- GitHub Actions automatically:
  1. Updates version in `pyproject.toml`
  2. Generates `CHANGELOG.md`
  3. Builds container image
  4. Pushes to `ghcr.io/<owner>/nextflow-k8s-service` with version tag + `latest`
- **Container deployment**: Single-purpose demo orchestrator for portfolio showcase