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
COPY pyproject.toml uv.lock ./
RUN uv sync --python 3.12 --extra ui --extra dev --no-dev

# Source
COPY src/ ./src/
COPY runs/ ./runs/
COPY --from=frontend-build /frontend/dist ./frontend/dist

# Environment
ENV AUTOAD_HOST=0.0.0.0
ENV AUTOAD_PORT=8000
ENV PYTHONPATH=/app/src

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "autoad_researcher.server.main:app", "--host", "0.0.0.0", "--port", "8000"]
