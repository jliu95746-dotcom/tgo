# =========================
# Builder stage
# =========================
FROM python:3.11-slim AS builder

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set work directory
WORKDIR /app

# Install system build dependencies (builder-only)
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry (pinned to existing version)
ARG PIP_INDEX_URL=https://pypi.org/simple/
RUN pip install poetry==2.1.4 poetry-plugin-export==1.9.0

# Configure Poetry to use a project-local virtualenv
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

# Copy Poetry files and install dependencies
COPY pyproject.toml poetry.lock* ./
RUN poetry export --only=main -f requirements.txt -o /tmp/requirements.lock && \
    python -m venv /app/.venv && \
    /app/.venv/bin/pip install --no-deps --require-hashes -r /tmp/requirements.lock && \
    /app/.venv/bin/pip check && \
    rm -rf "$POETRY_CACHE_DIR" /tmp/requirements.lock

# Copy application source (for any build-time needs)
COPY . .

# =========================
# Runtime stage
# =========================
FROM python:3.11-slim AS runtime

# Runtime environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Minimal runtime dependencies
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy virtualenv from builder (keep same path to preserve shebang)
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy application code
COPY . .

# Create non-root user and fix ownership
RUN adduser --disabled-password --gecos '' --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8081

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8081/health || exit 1

# Run the application via uvicorn directly (no Poetry at runtime)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
