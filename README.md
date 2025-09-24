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

## Project Layout
- `pyproject.toml` – project metadata and dependencies managed by `uv`
- `uv.lock` – resolved dependency lockfile (generated via `uv lock`)
- `nextflow-api/` – FastAPI application, Kubernetes helpers, deployment manifests

Refer to `nextflow-api/README.md` for usage, API reference, and operational notes.
