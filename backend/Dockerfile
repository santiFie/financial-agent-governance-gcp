# ==============================================================================
# STAGE 1: Build & Dependencies in a Self-Contained Virtual Environment
# ==============================================================================
FROM python:3.11-slim AS builder

WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtualenv in /opt/venv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ==============================================================================
# STAGE 2: Final Lightweight Runtime Image (Cost & Security Optimized)
# ==============================================================================
FROM python:3.11-slim AS runner

WORKDIR /app

# Non-root user for security compliance in container environments
RUN addgroup --system appgroup && adduser --system --group appuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

# Copy the entire self-contained virtualenv from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application source code
COPY --chown=appuser:appgroup app /app/app

USER appuser

EXPOSE 8000

# Run FastAPI with Uvicorn. PORT is set by Cloud Run automatically or defaults to 8000
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
