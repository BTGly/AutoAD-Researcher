# Stage 1: React build
FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python app
FROM python:3.12-slim
WORKDIR /app

# uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_LINK_MODE=copy

# Python deps
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --python 3.12 --frozen --no-dev --no-install-project

# Source
COPY src/ ./src/
COPY --from=frontend-build /frontend/dist ./frontend/dist
RUN uv sync --python 3.12 --frozen --no-dev

# Environment
ENV AUTOAD_HOST=0.0.0.0
ENV AUTOAD_PORT=8000
ENV AUTOAD_RUNS_ROOT=/app/runs
ENV AUTOAD_EMBEDDED_WORKER=1
ENV PYTHONPATH=/app/src

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "autoad_researcher.server.main:app", "--host", "0.0.0.0", "--port", "8000"]
