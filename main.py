"""Local dev helper for running the Nextflow K8s Service API with uvicorn."""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        app_dir="nextflow_k8s_service",
    )


if __name__ == "__main__":
    main()
