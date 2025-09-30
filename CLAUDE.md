# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
# WebSocket endpoint at ws://localhost:8000/api/v1/pipeline/stream
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

### Core Components

**PipelineManager** (`app/services/pipeline_manager.py`)
- Orchestrates the complete lifecycle of a single Nextflow pipeline run
- Enforces single-run-at-a-time constraint via `StateStore`
- Handles run requests via `start_or_attach_run()` which either starts a new run or attaches to the active one
- Spawns background monitor tasks (`_monitor_run()`) that watch Kubernetes job completion and broadcast status updates
- Integrates `LogStreamer` for real-time log streaming and cleanup on completion/cancellation

**StateStore** (`app/services/state_store.py`)
- Manages active run state and bounded run history using in-memory deque
- Implements optimistic locking for active runs using Redis (when available) or in-memory-only mode
- TTL-based history expiration prevents unbounded memory growth
- Key methods: `acquire_active_run()` (lock acquisition), `update_active_status()`, `finish_active_run()`, `get_history()`
- Redis keys: `nextflow:pipeline:active-run` (lock), `nextflow:pipeline:state` (hash of active run metadata)

**LogStreamer** (`app/services/log_streamer.py`)
- Streams logs from Kubernetes pods in real-time via background asyncio tasks
- Broadcasts log lines through `Broadcaster` to all connected WebSocket clients
- Attempts to parse ISO timestamps from log lines for client-side ordering
- Automatically stops streaming when run completes or is cancelled

**Broadcaster** (`app/utils/broadcaster.py`)
- WebSocket fan-out primitive that maintains a set of connected clients
- Broadcasts JSON messages (run status, logs, completion events) to all active WebSocket connections
- Handles disconnections gracefully and removes stale clients

### Kubernetes Integration

**Client Management** (`app/kubernetes/client.py`)
- Loads in-cluster config when running in Kubernetes, falls back to local kubeconfig otherwise
- Caches `KubernetesClient` instances per context to avoid redundant config loading
- Exposes `CoreV1Api` (for pods) and `BatchV1Api` (for jobs)

**Job Creation** (`app/kubernetes/jobs.py`)
- `create_job()` builds Kubernetes Job specs with Nextflow parameters as env vars
- Uses the `NEXTFLOW_IMAGE` setting for the container image
- `delete_job()` removes completed/cancelled jobs with configurable grace periods

**Monitoring** (`app/kubernetes/monitor.py`)
- `wait_for_completion()` polls job status and calls `on_status` callback for state transitions
- Tracks job phases: PENDING → RUNNING → SUCCEEDED/FAILED/UNKNOWN
- Handles pod-level failures and timeouts

### Request Flow

1. **Start Run**: Client POSTs to `/api/v1/pipeline/run` → `PipelineManager.start_or_attach_run()`
   - Checks for active run via `StateStore.get_active_run()`
   - If active, returns existing run info with `attached=True`
   - Otherwise, generates `run_id`, acquires lock via `StateStore.acquire_active_run()`
   - Creates Kubernetes job via `jobs.create_job()`
   - Starts `LogStreamer` and schedules monitor task

2. **Monitor Run**: Background `_monitor_run()` task
   - Calls `wait_for_completion()` which polls job status
   - Broadcasts status changes via `Broadcaster`
   - On terminal state: stops log streamer, finishes run in state store, deletes job

3. **WebSocket Stream**: Client connects to `/api/v1/pipeline/stream`
   - Receives real-time logs and status events broadcasted by `LogStreamer` and `PipelineManager`
   - Messages include `{"type": "run_status"|"log"|"run_completed", "payload": {...}}`

4. **Cancel Run**: Client DELETEs `/api/v1/pipeline/cancel`
   - Calls `jobs.delete_job()` with grace period 0
   - Stops log streamer and marks run as CANCELLED in state store
   - Broadcasts cancellation event

### Configuration

**Settings** (`app/config.py`)
- Uses `pydantic-settings` to load from environment variables (`.env` file)
- Key settings:
  - `NEXTFLOW_NAMESPACE`: Kubernetes namespace for Nextflow jobs
  - `KUBE_CONTEXT`: Kubeconfig context (local dev only)
  - `REDIS_URL`: Optional Redis connection string
  - `NEXTFLOW_IMAGE`: Container image for pipeline runs
  - `RUN_TTL_MINUTES`, `RUN_HISTORY_LIMIT`: History retention tuning
  - `ALLOWED_ORIGINS`: CORS configuration

### Testing Strategy

- Tests use `pytest` with `pytest-asyncio` for async code
- Mock Kubernetes API calls using `pytest-mock`
- Tests cover state store locking, pipeline orchestration, and log streaming
- No live Kubernetes cluster required for test execution
- Run history TTL and expiration logic validated with time-based scenarios

## Release Process

- Uses Conventional Commits for semantic versioning
- `python-semantic-release` automatically updates version in `pyproject.toml` and generates `CHANGELOG.md`
- Release workflow builds container image and pushes to `ghcr.io/<owner>/nextflow-k8s-service`
- Tags: versioned (e.g., `1.0.4`) and `latest`