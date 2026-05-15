# ==============================================================================
# agent-nexus Dockerfile
# Multi-stage build for production-ready, HF Spaces-compatible image
# ==============================================================================

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install Python dependencies
RUN pip install --no-cache-dir --prefix=/install .

# --- Stage 2: Runtime ---
FROM python:3.12-slim AS runtime

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user (UID 1000 required by HF Spaces)
RUN useradd -m -u 1000 user
USER user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:/install/bin:$PATH \
    PYTHONPATH=/home/user/app \
    PYTHONUNBUFFERED=1 \
    TRANSFORMERS_CACHE=/tmp/models \
    HF_HOME=/tmp/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/tmp/sentence-transformers

WORKDIR $HOME/app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=user src/ ./src/
COPY --chown=user frontend/ ./frontend/

# Create required directories
RUN mkdir -p /tmp/agent-nexus-workspace /tmp/models /tmp/huggingface /tmp/sentence-transformers

# Expose port (7860 for HF Spaces)
EXPOSE 7860

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:7860/api/v1/health || exit 1

# Start the application
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "7860"]
