FROM python:3.13-slim AS builder
ENV PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /src
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY uv.lock ./
COPY nextflow_k8s_service ./nextflow_k8s_service
COPY main.py ./main.py
RUN pip install --prefix=/install --no-cache-dir .

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"

WORKDIR /app
COPY --from=builder /install /usr/local
COPY nextflow_k8s_service ./nextflow_k8s_service
COPY main.py ./main.py

EXPOSE 8000
CMD [
  "uvicorn",
  "app.main:app",
  "--host",
  "0.0.0.0",
  "--port",
  "8000",
  "--app-dir",
  "nextflow_k8s_service"
]
