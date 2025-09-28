# Nextflow K8s Service

This repository houses a FastAPI microservice for managing single-instance Nextflow pipeline runs on Kubernetes. The full service documentation lives in `nextflow_k8s_service/README.md`.

## Development Setup
1. Install Python 3.13 (see `.python-version`).
2. Use `uv` for dependency management:
   ```bash
   uv sync
   ```
3. Run the API locally using uv:
   ```bash
   uv run uvicorn app.main:app --reload --port 8000 --app-dir nextflow_k8s_service
   ```

## Testing
Install the development toolchain with either `uv` or `pip`:

```bash
# Using uv
uv sync --group dev

# Or with pip
python -m pip install -r requirements-dev.txt
```

Common testing targets are exposed through the repository `Makefile`:

```bash
make test          # Full suite with coverage enforcement (>=80%)
make test-unit     # Focused unit tests under tests/unit
make test-integration  # Requires KIND/K3s and optional Redis/Kubernetes endpoints
make test-contract # Validates generated Kubernetes manifests
make test-smoke    # Post-deploy verification (SMOKE_TEST_API_URL must be set)
```

The integration and smoke suites include environment-aware skips so that local runs do not require a live cluster unless explicitly configured (set `K8S_INTEGRATION=1` and provide Kubernetes credentials to execute cluster-backed checks).

## CI & Releases
- `.github/workflows/ci.yml` implements a multi-stage pipeline:
  - **Quality Gates** – runs `make lint` (Ruff, Black, isort, MyPy, Bandit, Safety).
  - **Test Suite** – provisions a KinD cluster, executes unit/integration tests with Redis, and uploads coverage to Codecov.
  - **Build & Publish** – builds the multi-stage Docker image, tags it with the semantic version, commit SHA, and `latest`, generates an SPDX SBOM via Syft, and scans the image with Trivy before pushing to the configured registry.
  - **Kubernetes Validation** – (main branch) validates manifests with kubeconform, performs `kubectl` dry-runs, checks RBAC permissions, and verifies ConfigMap/PVC availability when cluster credentials are supplied.
  - **Deploy** – rolls the `nextflow-k8s-service` deployment to the freshly built image.
  - **Smoke Tests** – runs the HTTP smoke suite against the deployed service when `SMOKE_TEST_API_URL` is provided.
- `.github/workflows/release.yml` continues to handle semantic releases for tagged builds.

Follow Conventional Commits when merging to `main`; semantic-release determines the next version and updates `pyproject.toml`, `CHANGELOG.md`, and the published container image automatically.

## Project Layout
- `pyproject.toml` – project metadata and dependencies managed by `uv`
- `uv.lock` – resolved dependency lockfile (generated via `uv lock`)
- `nextflow_k8s_service/` – FastAPI application, Kubernetes helpers, deployment manifests

Refer to `nextflow_k8s_service/README.md` for usage, API reference, and operational notes.
