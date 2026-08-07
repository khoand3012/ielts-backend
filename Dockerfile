FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY core ./core
COPY api ./api
COPY worker ./worker
COPY alembic.ini ./
COPY alembic ./alembic
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# API by default; the worker process overrides the command (see compose / Fly).
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
