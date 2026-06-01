# Single backend image used by three services (api, trading-worker, db-writer),
# each with a different entrypoint command (REQ-011).
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps kept minimal; asyncpg ships wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Install dependencies first (better layer caching), then the source.
RUN pip install --upgrade pip && pip install ".[dev]" || pip install "."

COPY . .
RUN pip install -e .

# Default command runs the API; compose overrides for workers.
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
