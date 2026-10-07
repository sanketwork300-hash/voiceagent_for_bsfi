# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /app

# Install dependencies first for layer caching. INSTALL_VOICE=1 adds LiveKit Agents + plugins (voice worker image).
ARG INSTALL_VOICE=0
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    mkdir -p app mock_bank && touch app/__init__.py mock_bank/__init__.py && \
    if [ "$INSTALL_VOICE" = "1" ]; then uv pip install --system ".[voice]"; else uv pip install --system "."; fi

COPY app ./app
COPY mock_bank ./mock_bank
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./
COPY data/sample_documents ./data/sample_documents

RUN useradd --create-home --uid 10001 bfsi && mkdir -p /app/data/uploads && chown -R bfsi /app/data
USER bfsi
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
