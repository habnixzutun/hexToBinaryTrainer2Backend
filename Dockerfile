# Dockerfile - Optimized version
FROM python:3.12-slim as builder

# Pin uv version for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:0.4.25 /uv /uvx /bin/

WORKDIR /app

# Build tools for packages that compile C/C++ extensions (e.g. greenlet)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies without installing the project itself
RUN uv sync --no-dev --no-install-project --python 3.12

# Copy source code
COPY src ./src

# Final stage - minimal runtime image
FROM python:3.12-slim

WORKDIR /app

# Copy uv from builder
COPY --from=ghcr.io/astral-sh/uv:0.4.25 /uv /uvx /bin/

# Copy only the virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy source code
COPY --from=builder /app/src /app/src

# Create non-root user
RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Run init_db first, then start uvicorn if successful
CMD ["sh", "-c", "uv run python -m src.init_db && uv run uvicorn src.main:app --host 0.0.0.0 --port 8000"]
