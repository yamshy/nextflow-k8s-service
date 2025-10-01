# Nextflow Pipeline Controller API

FastAPI-based microservice that orchestrates single-instance Nextflow pipeline executions on Kubernetes. It serializes pipeline runs, surfaces real-time status/log updates to multiple clients, and provides deployment artifacts for rapid adoption.

## Features
- Enforces a single active Nextflow run at a time with optimistic locking (memory/Redis)
- REST API for run orchestration, status queries, cancellation, and history
- WebSocket broadcast channel for live logs and status updates to multiple clients
- Kubernetes integration for job creation, monitoring, and cleanup
- Rate-limited endpoints, health checks, and graceful shutdown hooks

## Project Layout
```
app/
  main.py                 FastAPI app factory, lifecycle, CORS, rate limiting
  config.py               Environment-aware settings
  models.py               Pydantic schemas for requests/responses/events
  api/                    REST + WebSocket routers
  services/               Orchestration, state, and log streaming logic
  kubernetes/             Client helpers, job builders, monitoring utilities
  utils/broadcaster.py    WebSocket fan-out primitive
examples/simple-pipeline/ Minimal Nextflow workflow for testing
k8s-manifests/            Deployment, service, ConfigMap, RBAC specs
  workflows/              Workflow-specific ConfigMaps
.env.example              Sample configuration values
```

## Getting Started
1. **Install dependencies**
   From the repository root run:
   ```bash
   uv sync
   ```
   This creates `.venv/` (ignored by git) with all runtime requirements and links `nextflow_k8s_service` for imports.
2. **Configure environment**
   - Copy `.env.example` to `.env` and update namespace, Redis, and image values.
   - Ensure the Kubernetes context referenced by `KUBE_CONTEXT` has permissions to manage Jobs/Pods.
3. **Run locally**
   ```bash
   uv run --directory nextflow_k8s_service uvicorn app.main:app --reload --port 8000
   ```
4. **Access API**
   - Open `http://localhost:8000/docs` for interactive OpenAPI docs.
- WebSocket clients connect to `ws://localhost:8000/api/v1/pipeline/stream`.

## API Overview
| Method | Path                         | Description |
| ------ | ---------------------------- | ----------- |
| POST   | `/api/v1/pipeline/run`       | Start a run or attach to the active run |
| GET    | `/api/v1/pipeline/status`    | Retrieve active or most recent run status |
| GET    | `/api/v1/pipeline/active`    | Query whether a run is currently active |
| DELETE | `/api/v1/pipeline/cancel`    | Cancel the active run |
| GET    | `/api/v1/pipeline/history`   | List historical runs (bounded) |
| WS     | `/api/v1/pipeline/stream`    | Receive live logs/status broadcasts |
| GET    | `/health`, `/healthz`        | Service health probe |

## Real-time Streaming

The `/api/v1/pipeline/stream` WebSocket endpoint accepts multiple concurrent clients (up to the limit specified by
`MAX_WEBSOCKET_CONNECTIONS`) and fan-outs structured JSON envelopes:

```json
{
  "type": "status" | "progress" | "log" | "complete" | "error",
  "data": { ... },
  "timestamp": "2024-04-01T12:00:00Z",
  "run_id": "4f3a7c9b1d2e"
}
```

- **status** – lifecycle transitions (`queued`, `starting`, `running`, `cancelled`, etc.) and keep-alive pings.
- **progress** – parsed `[completed/total] process > ...` indicators with a computed percentage.
- **log** – batched log entries (max 50 lines) that include pod/container metadata and timestamps.
- **complete** – terminal state summaries containing the persisted `RunInfo` payload.
- **error** – surfaced Kubernetes or Nextflow errors (`WARN`/`ERROR` lines, monitor failures, pod startup issues).

Polling the REST status endpoint still works, but now returns `progress_percent`, a rolling `log_preview`, and the
current WebSocket URL so dashboards can pivot to streaming when available.

### Test Workflows

`examples/test_workflows.yaml` documents the demo pipelines used in end-to-end testing:

```yaml
hello:
  expected_duration: 30        # seconds
  expected_processes: 1
  log_patterns:
    - "Simple single process output"

rnaseq-test:
  expected_duration: 120       # ~2 minutes
  expected_processes: "5-7"
  log_patterns:
    - "FASTQC"
    - "trimming"
    - "alignment"

parallel-demo:
  expected_duration: 180       # ~3 minutes
  expected_processes: "10-15"
  log_patterns:
    - "Parallel execution"
    - "scatter"
    - "gather"
```

These references help QA teams validate log parsing and progress tracking during manual or automated smoke tests.

## Kubernetes Deployment
1. Build and push the service image (context is repo root):
   ```bash
   docker build -t <registry>/nextflow-k8s-service:latest .
   docker push <registry>/nextflow-k8s-service:latest
   ```
2. Update the image reference in `k8s-manifests/deployment.yaml` and the ConfigMap values as needed.
3. Apply manifests:
   ```bash
   kubectl apply -f k8s-manifests/
   kubectl apply -f k8s-manifests/workflows/
   ```
4. Ensure the `nextflow` namespace includes the required resource quotas and that Redis is reachable (`REDIS_URL`).

## Example Pipeline
`examples/simple-pipeline/` contains a minimal Nextflow DSL2 pipeline (`main.nf`) with a matching `nextflow.config` profile for Kubernetes execution. Use it to verify the service end-to-end by referencing the directory in your run parameters.

## Operational Notes
- The service attempts Redis connectivity on startup; failures fall back to in-memory state and are logged.
- Logs are timestamp-parsed when emitted with ISO strings; non-conforming lines are still relayed without timestamps.
- Completed runs are retained in bounded history with TTL trimming to avoid unbounded memory usage.

## Documented Next Steps
1. **Automated Testing** – Add unit/integration tests (e.g., pytest + respx) to validate pipeline orchestration, state transitions, and WebSocket flows.
2. **Observability Enhancements** – Integrate structured logging, metrics (Prometheus), and tracing to monitor run performance and failures in production.
3. **Authentication & Authorization** – Protect REST/WebSocket endpoints using an auth provider (e.g., OAuth2/JWT) and enforce per-user rate limits.
4. **Persistent State Backend** – Promote Redis (or alternative store) to mandatory with high availability and persistence for multi-instance deployments.
5. **Pipeline Parameter Validation** – Introduce schema-driven validation against a catalog of supported Nextflow pipelines to prevent misconfigured runs.
6. **CI/CD Automation** – Configure build pipelines that lint, test, and deploy both the API service and associated Kubernetes assets.
