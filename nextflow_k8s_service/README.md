# Data Pipeline Orchestrator - Portfolio Demo API

**Purpose-built portfolio showcase** demonstrating cloud-native parallel data processing with real-time streaming on Kubernetes.

> **Important**: This is a **demo-focused application**, not a generic Nextflow orchestrator. It runs a single hardcoded workflow (`workflows/demo.nf`) optimized for portfolio demonstration. Generic pipeline support was removed in v1.10.0.

## Features
- **Single demo workflow**: Hardcoded scatter-gather pattern with configurable parallelism (1-12 batches)
- **Optimistic locking**: Enforces single active run at a time (Redis or in-memory)
- **Real-time streaming**: WebSocket broadcasting of logs and status to multiple clients
- **Homelab-optimized**: Resource limits tuned for 50Gi/14 CPU quota
- **Demo API**: Simplified endpoints focused on portfolio showcase (`/api/v1/demo/*`)
- **Graceful lifecycle**: Health checks, rate limiting, and clean shutdown

## Project Layout
```
app/
  main.py                 FastAPI app factory, lifecycle, CORS, rate limiting
  config.py               Demo-optimized settings (homelab constants)
  models.py               Pydantic schemas (DemoRunRequest, DemoResultMetrics, etc.)
  api/
    routes.py             Demo-only endpoints (/api/v1/demo/*)
    websocket.py          WebSocket streaming (/api/v1/pipeline/stream)
    metrics.py            Prometheus metrics
  services/
    pipeline_manager.py   Orchestration (start_demo_run is the only entry point)
    state_store.py        Redis/in-memory state with optimistic locking
    log_streamer.py       Real-time log streaming from K8s pods
  kubernetes/
    jobs.py               Job creation (create_demo_job for hardcoded workflow)
    monitor.py            Job status polling
    client.py             K8s client management
  utils/broadcaster.py    WebSocket fan-out primitive
workflows/
  demo.nf                 Hardcoded demo workflow (GENERATE → ANALYZE → REPORT)
  nextflow.config         Nextflow configuration template
k8s-manifests/            Deployment, service, ConfigMap, RBAC specs
  workflows/              Workflow-specific ConfigMaps
.env.example              Sample configuration values
```

## Getting Started

### Local Development
1. **Install dependencies**
   ```bash
   cd /path/to/nextflow-k8s-service
   uv sync                    # Runtime dependencies
   uv sync --group dev        # Include dev/test dependencies
   ```

2. **Configure environment** (optional for local dev)
   - Copy `.env.example` to `.env` and customize as needed
   - Key settings: `NEXTFLOW_NAMESPACE`, `KUBE_CONTEXT`, `REDIS_URL`
   - Defaults work for most local development scenarios

3. **Run locally**
   ```bash
   uv run uvicorn app.main:app --reload --port 8000 --app-dir nextflow_k8s_service
   ```

4. **Access API**
   - OpenAPI docs: `http://localhost:8000/docs`
   - Start demo: `POST /api/v1/demo/run` with `{"batch_count": 5}`
   - WebSocket: `ws://localhost:8000/api/v1/pipeline/stream`

### Demo Workflow

The hardcoded workflow at `workflows/demo.nf`:
- **GENERATE**: Creates CSV batches (100 rows each) - 5s per batch
- **ANALYZE**: Computes statistics (sum, average) - 3s per batch
- **REPORT**: Aggregates into final JSON report
- **Parameter**: `--batches` (1-12, default 5)
- **Runtime**: ~45-60 seconds for 5 batches
- **Output**: `/workspace/results/{run_id}/report.json`

## API Overview (Demo Endpoints Only)

**Breaking change in v1.10.0**: Generic `/api/v1/pipeline/*` endpoints removed.

| Method | Path                         | Description |
| ------ | ---------------------------- | ----------- |
| POST   | `/api/v1/demo/run`           | Start demo with `{"batch_count": 5, "triggered_by": "..."}` |
| GET    | `/api/v1/demo/preview`       | Preview resource usage without running (query: `?batch_count=5`) |
| GET    | `/api/v1/demo/status`        | Current run status with progress percentage |
| GET    | `/api/v1/demo/active`        | Check if a run is currently active |
| DELETE | `/api/v1/demo/cancel`        | Cancel the active run |
| GET    | `/api/v1/demo/history`       | List recent runs (query: `?limit=10`) |
| WS     | `/api/v1/pipeline/stream`    | Real-time logs and status broadcasts |
| GET    | `/health`, `/healthz`        | Service health probe |

## Real-time Streaming

The `/api/v1/pipeline/stream` WebSocket endpoint supports multiple concurrent clients (up to 100, configurable via
`MAX_WEBSOCKET_CONNECTIONS`) and broadcasts structured JSON messages:

```json
{
  "type": "status" | "progress" | "log" | "complete" | "error",
  "data": { ... },
  "timestamp": "2024-04-01T12:00:00Z",
  "run_id": "r1234567890a"
}
```

**Message types:**
- **status** – Lifecycle transitions (QUEUED → STARTING → RUNNING → SUCCEEDED/FAILED/CANCELLED)
- **progress** – Parsed `[completed/total]` indicators with percentage (based on Nextflow process completion)
- **log** – Batched log entries (max 50 lines) with pod/container metadata and ISO timestamps
- **complete** – Terminal state with final `RunInfo` payload (includes duration, status, etc.)
- **error** – Kubernetes/Nextflow errors (WARN/ERROR lines, monitor failures, pod issues)

**REST fallback**: `GET /api/v1/demo/status` returns current state with `progress_percent`, `log_preview` (last 10 lines), and `websocket_url` for clients that prefer polling.

## Resource Allocation (Homelab-Optimized)

Designed for resource-constrained homelab with **50Gi memory and 14 CPU quota**:

| Component | CPU | Memory | Notes |
|-----------|-----|--------|-------|
| Controller pod | 2 CPU | 4Gi | Nextflow orchestrator (fixed) |
| Worker pod | 1 CPU | 4GB | GENERATE/ANALYZE processes (fixed) |
| **Max capacity** | **14 CPU** | **52GB** | 12 batches = 24 workers + controller |

**Scaling behavior:**
- 5 batches (default): 10 workers + controller = 12 CPU + 44GB ✅
- 12 batches (max): 24 workers + controller = 26 CPU + 100GB ❌ exceeds quota
- **Safe limit**: 12 batches (fits within quota with minimal overhead)

## Kubernetes Deployment

### Container Image
Automated via GitHub Actions on push to `main`:
```bash
# Image pushed to: ghcr.io/<owner>/nextflow-k8s-service:latest
# Also tagged with semantic version (e.g., v1.10.1)
```

### Manual Deployment (if needed)
1. **Build and push**:
   ```bash
   docker build -t <registry>/nextflow-k8s-service:latest .
   docker push <registry>/nextflow-k8s-service:latest
   ```

2. **Deploy to Kubernetes**:
   ```bash
   # Apply deployment manifests
   kubectl apply -f k8s-manifests/

   # Note: Workflows are bundled in the container image and deployed via init container
   # No separate ConfigMap creation needed
   ```

3. **Prerequisites**:
   - Namespace: `nextflow` (or custom via `NEXTFLOW_NAMESPACE`)
   - PVC: `nextflow-work-pvc` for shared workspace
   - ServiceAccount: `nextflow-runner` with permissions to create Jobs/Pods
   - Optional: Redis instance (URL via `REDIS_URL` env var)

### Configuration
Set environment variables in deployment:
- `NEXTFLOW_NAMESPACE`: K8s namespace (default: `nextflow`)
- `REDIS_URL`: Optional Redis URL (falls back to in-memory)
- `ALLOWED_ORIGINS`: CORS origins (default: `["*"]`)
- Resource limits are hardcoded in `app/config.py` (homelab-optimized)

## Operational Notes

**State management:**
- Redis-backed when `REDIS_URL` is set (recommended for production)
- Falls back to in-memory mode on Redis connection failure (logged at startup)
- Run history: last 5 runs, 30-minute TTL

**Log streaming:**
- ISO timestamps parsed from Nextflow logs for client-side ordering
- Non-conforming log lines still relayed (without parsed timestamp)
- Log batches capped at 50 lines per broadcast

**Lifecycle:**
- Single active run enforced via optimistic locking
- Graceful shutdown: cancels monitor tasks, closes connections
- Completed jobs auto-deleted after `job_ttl_seconds_after_finished` (15 minutes)

## Portfolio Showcase Focus

This application demonstrates:
1. **Cloud-native architecture**: Kubernetes-native orchestration with dynamic pod scheduling
2. **Real-time streaming**: WebSocket broadcasting to multiple clients with structured message types
3. **Resource optimization**: Quota-aware scheduling for resource-constrained environments
4. **Resilient state management**: Optimistic locking with Redis fallback
5. **Production patterns**: Health checks, rate limiting, graceful shutdown, bounded history

**Not included** (intentionally simplified for demo):
- Multi-tenancy or authentication (portfolio showcase has no auth requirements)
- Persistent result storage (results expire with PVC cleanup)
- Multi-workflow support (single hardcoded demo workflow only)
- Advanced Nextflow features (resume, profiles, complex DAGs)
