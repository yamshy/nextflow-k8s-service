import sys
from pathlib import Path

# Ensure the FastAPI app package is importable without installing the project
repo_root = Path(__file__).resolve().parents[1]
app_path = repo_root / "nextflow-api"
if str(app_path) not in sys.path:
    sys.path.insert(0, str(app_path))
