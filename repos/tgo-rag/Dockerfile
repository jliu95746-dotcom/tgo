# Multi-stage Dockerfile for RAG Service
# Optimized for maximum layer caching

# =========================
# Stage 1: Base with system deps (shared by all stages)
# =========================
FROM python:3.11-slim AS base-deps

# Install common system dependencies + Playwright browser deps
# Combining all apt operations into one layer for better caching
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    # Build tools
    build-essential \
    curl \
    libpq-dev \
    libmagic1 \
    # Playwright/Chromium dependencies (pre-install to avoid playwright install-deps)
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Install Poetry (cached layer)
ARG PIP_INDEX_URL=https://pypi.org/simple/
RUN pip install --no-cache-dir poetry==2.1.4 poetry-plugin-export==1.9.0

# =========================
# Stage 2: Python dependencies builder
# =========================
FROM base-deps AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

WORKDIR /app

# Copy ONLY dependency files first (for better caching)
COPY pyproject.toml poetry.lock* ./

# Install only production dependencies into .venv
# Use BuildKit cache mount for faster rebuilds
RUN --mount=type=cache,target=/tmp/poetry_cache \
    poetry export --only=main -f requirements.txt -o /tmp/requirements.lock && \
    python -m venv /app/.venv && \
    /app/.venv/bin/pip install --no-deps --require-hashes -r /tmp/requirements.lock && \
    /app/.venv/bin/pip check && rm /tmp/requirements.lock

# Download NLTK data (cached layer)
RUN .venv/bin/python -m nltk.downloader -d /app/nltk_data punkt punkt_tab averaged_perceptron_tagger averaged_perceptron_tagger_eng

# =========================
# Stage 3: Playwright browser download (separate for caching)
# =========================
FROM base-deps AS playwright-cache

ENV PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/playwright-browsers

WORKDIR /app

# Copy virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

# Download Playwright browsers to a fixed path (NOT using cache mount here)
# This layer is cached and only rebuilds when base-deps or venv changes
RUN playwright install chromium

# =========================
# Stage 4: Development (keeps Poetry + dev deps)
# =========================
FROM base-deps AS development

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_CACHE_DIR=/tmp/poetry_cache

WORKDIR /app

# Install additional dev system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    libreoffice-core \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Install dependencies including dev
RUN --mount=type=cache,target=/tmp/poetry_cache \
    poetry install --with dev --no-root

# Install Playwright browsers
RUN playwright install chromium

# Download NLTK data
RUN python -m nltk.downloader -d /app/nltk_data punkt punkt_tab averaged_perceptron_tagger averaged_perceptron_tagger_eng
ENV NLTK_DATA=/app/nltk_data

# Create uploads directory
RUN mkdir -p /app/uploads

# Copy source code (last step for dev)
COPY . .

EXPOSE 8082

CMD ["poetry", "run", "uvicorn", "src.rag_service.main:app", "--host", "0.0.0.0", "--port", "8082", "--reload"]

# =========================
# Stage 5: Production runtime (minimal, uses cached artifacts)
# =========================
FROM python:3.11-slim AS production

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/rag \
    XDG_CACHE_HOME=/home/rag/.cache \
    PATH="/app/.venv/bin:$PATH" \
    NLTK_DATA=/app/nltk_data \
    PLAYWRIGHT_BROWSERS_PATH=/home/rag/.cache/ms-playwright

# Install ONLY runtime system dependencies (no build tools)
# Pre-install Playwright browser deps to avoid playwright install-deps
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    libmagic1 \
    libreoffice-core \
    # Playwright/Chromium runtime dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean \
    && rm -rf /var/cache/apt/*

# Create non-root user and directories
RUN groupadd -r rag && useradd -r -g rag rag && \
    mkdir -p /home/rag/.cache /app/uploads /app/logs && \
    chown -R rag:rag /home/rag /app

WORKDIR /app

# Copy virtualenv from builder (cached - only changes when deps change)
COPY --from=builder --chown=rag:rag /app/.venv /app/.venv

# Copy NLTK data from builder (cached)
COPY --from=builder --chown=rag:rag /app/nltk_data /app/nltk_data

# Copy Playwright browsers from playwright-cache stage (cached independently)
COPY --from=playwright-cache --chown=rag:rag /playwright-browsers /home/rag/.cache/ms-playwright

# Copy source code LAST (this changes most frequently)
COPY --chown=rag:rag . .

USER rag

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8082/health || exit 1

CMD ["uvicorn", "src.rag_service.main:app", "--host", "0.0.0.0", "--port", "8082", "--workers", "4"]
