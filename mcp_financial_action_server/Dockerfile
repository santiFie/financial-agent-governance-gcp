# ==============================================================================
# STAGE 1: Build & Dependencies
# ==============================================================================
FROM python:3.11-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ==============================================================================
# STAGE 2: Lightweight Runtime Image
# ==============================================================================
FROM python:3.11-slim AS runner

WORKDIR /app

# Non-root user for security compliance
RUN addgroup --system mcpgroup && adduser --system --group mcpuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app" \
    MCP_SERVER_PORT=8001

COPY --from=builder /opt/venv /opt/venv

# Copy mcp_financial_action_server module
COPY --chown=mcpuser:mcpgroup . /app/mcp_financial_action_server

USER mcpuser

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"

CMD ["uvicorn", "mcp_financial_action_server.server:app", "--host", "0.0.0.0", "--port", "8001"]
