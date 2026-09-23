# =========================
# Builder stage
# =========================
FROM python:3.11-slim AS builder

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System build deps (only for build stage)
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry (pinned to 1.8.3 to avoid 2.0+ breaking changes)
ARG PIP_INDEX_URL=https://pypi.org/simple/
RUN pip install --no-cache-dir poetry==2.1.4 poetry-plugin-export==1.9.0

# Configure Poetry to use a local venv
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

# Install Python deps
COPY pyproject.toml poetry.lock* ./
RUN poetry export --with dev -f requirements.txt -o /tmp/requirements.lock && \
    python -m venv /app/.venv && \
    /app/.venv/bin/pip install --no-deps --require-hashes -r /tmp/requirements.lock && \
    /app/.venv/bin/pip check && \
    rm -rf "$POETRY_CACHE_DIR" /tmp/requirements.lock

# Verify alembic is installed in builder
RUN ./.venv/bin/python -m alembic --version

# Copy application source
COPY . .

# =========================
# Runtime stage
# =========================
FROM python:3.11-slim AS runtime

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    VIRTUAL_ENV=/app/.venv

# Add venv to PATH
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

# Minimal runtime deps
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
