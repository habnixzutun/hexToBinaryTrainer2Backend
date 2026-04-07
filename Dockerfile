# Dockerfile
FROM python:3.12-slim

# Lade uv direkt vom offiziellen Image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Kopiere die Dependency-Dateien
COPY pyproject.toml uv.lock ./

# Installiere Abhängigkeiten, ohne das Projekt selbst zu installieren
RUN uv sync --no-dev --no-install-project

# Kopiere den Quellcode
COPY src ./src

# Starte den FastAPI Server
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]