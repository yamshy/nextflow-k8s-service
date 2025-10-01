# Data Pipeline Orchestrator - Portfolio Demo

**Purpose-built portfolio showcase** demonstrating cloud-native parallel data processing on Kubernetes with real-time streaming.

> **Note**: This is **not** a generic Nextflow orchestration service. It runs a single hardcoded demo workflow optimized for portfolio demonstration. Generic pipeline support was removed in v1.10.0.

## What This Demonstrates

- **Parallel data processing**: Scatter-gather pattern with 1-12 parallel batches
- **Real-time streaming**: WebSocket broadcasting of logs and status to multiple clients
- **Cloud-native orchestration**: Kubernetes job management with graceful lifecycle handling
- **Resource optimization**: Designed for resource-constrained homelab (50Gi/14 CPU)
- **Single-instance constraint**: Optimistic locking ensures only one run at a time

## Demo Workflow

The demo pipeline (`workflows/demo.nf`) processes data batches in parallel:

1. **GENERATE**: Creates CSV data (100 rows × N batches) - 5s per batch
2. **ANALYZE**: Computes statistics on each batch - 3s per batch
3. **REPORT**: Aggregates results into `report.json`

**Parameters**: `--batches` (1-12, default 5) controls parallelism
**Runtime**: ~45-60 seconds for 5 batches
**Worker pods**: 2 per batch (GENERATE + ANALYZE)

## Quick Start

### Development Setup

```bash
# Install Python 3.13 (see .python-version)
uv sync                    # Install dependencies
uv sync --group dev        # Include dev/test dependencies

# Run locally
uv run uvicorn app.main:app --reload --port 8000 --app-dir nextflow_k8s_service

# Access API
# OpenAPI docs: http://localhost:8000/docs
# Start demo: POST /api/v1/demo/run with {"batch_count": 5}
# WebSocket: ws://localhost:8000/api/v1/pipeline/stream
```

### Testing

```bash
uv run pytest              # Run all tests (no K8s cluster required)
uv run pytest -v           # Verbose output
uv run ruff check .        # Lint
uv run ruff format .       # Format
```

## API Overview

**Demo Endpoints** (`/api/v1/demo/*`):
- `POST /demo/run` - Start demo with `{"batch_count": 5}`
- `GET /demo/preview?batch_count=5` - Preview resource usage
- `GET /demo/status` - Current run status with progress
- `GET /demo/active` - Check if run is active
- `DELETE /demo/cancel` - Cancel active run
- `GET /demo/history?limit=10` - Recent run history
- `WS /pipeline/stream` - Real-time logs and status

**Health**: `GET /health`, `GET /healthz`

## Resource Allocation

Optimized for homelab with 50Gi memory and 14 CPU quota:

- **Controller pod**: 2 CPU + 4Gi (Nextflow orchestrator)
- **Worker pods**: 1 CPU + 4GB each (GENERATE/ANALYZE processes)
- **Max capacity**: 12 batches = 24 workers (12 CPU + 48GB) + controller

## CI & Releases

- **CI** (`.github/workflows/ci.yml`): Runs tests on every push/PR
- **Release** (`.github/workflows/release.yml`): On push to `main`:
  1. Semantic versioning via Conventional Commits
  2. Updates `pyproject.toml` and `CHANGELOG.md`
  3. Builds container image
  4. Pushes to `ghcr.io/<owner>/nextflow-k8s-service` (versioned + `latest`)

**Commit convention**: Use `feat:`, `fix:`, `docs:`, `chore:`, etc. for semantic versioning

## Project Layout

```
nextflow_k8s_service/
├── app/
│   ├── main.py              # FastAPI app factory
│   ├── config.py            # Demo-optimized settings
│   ├── models.py            # Pydantic schemas (DemoRunRequest, etc.)
│   ├── api/
│   │   ├── routes.py        # Demo-only endpoints
│   │   ├── websocket.py     # WebSocket streaming
│   │   └── metrics.py       # Prometheus metrics
│   ├── services/
│   │   ├── pipeline_manager.py  # Orchestration (start_demo_run)
│   │   ├── state_store.py       # Redis/in-memory state
│   │   └── log_streamer.py      # Real-time log streaming
│   └── kubernetes/
│       ├── jobs.py          # K8s job creation (create_demo_job)
│       ├── monitor.py       # Job status polling
│       └── client.py        # K8s client management
├── workflows/
│   ├── demo.nf              # Hardcoded demo workflow
│   └── nextflow.config      # Nextflow configuration
tests/                       # pytest suite (no K8s required)
pyproject.toml               # Project metadata + dependencies
CLAUDE.md                    # Development guide for Claude Code
```

## Documentation

- **`CLAUDE.md`**: Architecture, development workflow, and component details
- **`nextflow_k8s_service/README.md`**: Detailed service documentation
- **OpenAPI docs**: http://localhost:8000/docs (when running)
