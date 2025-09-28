import sys
from pathlib import Path

import pytest

# Ensure the FastAPI app package is importable without installing the project
repo_root = Path(__file__).resolve().parents[1]
app_path = repo_root / "nextflow_k8s_service"
if str(app_path) not in sys.path:
    sys.path.insert(0, str(app_path))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: marks tests that hit external services or clusters")
    config.addinivalue_line("markers", "contract: marks tests that validate generated specs or schemas")
    config.addinivalue_line("markers", "smoke: marks lightweight post-deploy smoke tests")
    config.addinivalue_line("markers", "kubernetes: marks tests that require a live Kubernetes cluster")
