#!/usr/bin/env python3
"""Generate OpenAPI spec from the FastAPI application."""
import json
import sys
from pathlib import Path

# Add parent directory to path to import app
sys.path.insert(0, str(Path(__file__).parent.parent / "nextflow_k8s_service"))

from app.main import create_app


def main():
    app = create_app()
    openapi_schema = app.openapi()

    output_path = Path(__file__).parent.parent / "openapi.json"

    with open(output_path, "w") as f:
        json.dump(openapi_schema, f, indent=2)

    print(f"OpenAPI spec generated at {output_path}")


if __name__ == "__main__":
    main()