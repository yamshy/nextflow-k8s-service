# Nextflow K8s Service

This repository houses a FastAPI microservice for managing single-instance Nextflow pipeline runs on Kubernetes. The full service documentation lives in `nextflow-api/README.md`.

## Development Setup
1. Install Python 3.13 (see `.python-version`).
2. Use `uv` for dependency management:
   ```bash
   uv sync
   ```
3. Run the API locally using uv:
   ```bash
   uv run uvicorn app.main:app --reload --port 8000 --app-dir nextflow-api
   ```

## Testing
Install the dev dependencies and run the suite:

```bash
uv sync --group dev
uv run pytest
```

The tests cover the in-memory state store and pipeline orchestration logic so you can iterate without a live Kubernetes cluster.

## CI & Releases
- `.github/workflows/ci.yml` runs `uv sync --group dev` followed by `uv run pytest` on every push and pull request.
- `.github/workflows/release.yml` (pushes to `main` only) runs tests via uv, performs a Conventional Commit–driven semantic release, builds the container image, and pushes versioned and `latest` tags to `ghcr.io/<owner>/nextflow-k8s-service`.

Follow Conventional Commits when merging to `main`; semantic-release determines the next version and updates `pyproject.toml`, `CHANGELOG.md`, and the published container image automatically.

## Project Layout
- `pyproject.toml` – project metadata and dependencies managed by `uv`
- `uv.lock` – resolved dependency lockfile (generated via `uv lock`)
- `nextflow-api/` – FastAPI application, Kubernetes helpers, deployment manifests

Refer to `nextflow-api/README.md` for usage, API reference, and operational notes.
