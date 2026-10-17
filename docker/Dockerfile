# API image. Build from the repo root:
#   docker build -f docker/Dockerfile -t loanrag-api .
FROM python:3.12.7-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    HF_HOME=/opt/hf \
    HF_HUB_DISABLE_TELEMETRY=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app
# Dependencies first (cached layer), pinned by the lockfile (S14). CPU-only torch is pulled
# in by sentence-transformers.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY eval ./eval
COPY scripts ./scripts
COPY alembic.ini ./
COPY docker/keycloak ./docker/keycloak
RUN uv sync --frozen --no-dev

# Bake the embedding model into the image (no download at startup; read-only at runtime).
RUN /opt/venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')" \
    && chmod -R a+rX /opt/hf
ENV HF_HUB_OFFLINE=1

RUN useradd --system --uid 10001 --home /app loanrag && mkdir -p /data && chown loanrag /data
USER loanrag
ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD curl -fsS http://localhost:8000/health || exit 1
# Migrations run with the owner URL, then the server starts as the restricted app role.
CMD ["sh", "-c", "python -m app.cli migrate && exec uvicorn --factory app.api.main:app_factory --host 0.0.0.0 --port 8000 --no-server-header"]
