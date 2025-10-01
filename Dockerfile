FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    mv /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --no-dev --frozen --no-install-project

COPY nextflow_k8s_service ./nextflow_k8s_service

# Copy demo workflows into the image
# These will be mounted in jobs via ConfigMap, but having them in the image
# provides a backup and allows for direct file access if needed
COPY nextflow_k8s_service/workflows /app/workflows

RUN uv sync --no-dev --frozen

RUN chown -R 1001:1001 /app

USER 1001

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "nextflow_k8s_service"]
