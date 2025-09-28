PYTHON ?= python3
PACKAGE ?= nextflow_k8s_service
TEST_PATH ?= tests
IMAGE ?= nextflow-k8s-service:dev

.PHONY: lint format test test-unit test-integration test-contract test-smoke coverage docker-build sbom run-local

lint:
@echo "Running static analysis suite"
ruff check $(PACKAGE) $(TEST_PATH)
black --check $(PACKAGE) $(TEST_PATH)
isort --check-only $(PACKAGE) $(TEST_PATH)
mypy $(PACKAGE) $(TEST_PATH)
bandit -q -r $(PACKAGE) -x tests
safety check -r requirements.txt

format:
black $(PACKAGE) $(TEST_PATH)
isort $(PACKAGE) $(TEST_PATH)

coverage:
pytest --cov=$(PACKAGE) --cov-report=xml --cov-report=term --cov-fail-under=80

test: coverage

test-unit:
pytest tests/unit -v --cov=$(PACKAGE) --cov-append

# Requires KIND/K3s setup and optional Redis/Kubernetes endpoints.
test-integration:
pytest tests/integration -v -m integration

# Validates generated Kubernetes manifests against expected schema contracts.
test-contract:
pytest tests/contract -v -m contract

# Smoke tests are executed post-deployment and require SMOKE_TEST_API_URL.
test-smoke:
pytest tests/smoke -v -m smoke

docker-build:
docker build -t $(IMAGE) .

sbom:
docker run --rm -v $$PWD:/workspace anchore/syft:latest packages $(IMAGE) -o spdx-json=/workspace/sbom.spdx.json

run-local:
uv run uvicorn app.main:app --reload --port 8000 --app-dir $(PACKAGE)
