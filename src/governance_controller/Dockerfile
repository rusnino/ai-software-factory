# Multi-stage build for the Governance Controller API.
#
# Builder stage installs dependencies and the project into a venv.
# Runtime stage copies only the venv and application code into a slim image.
# This keeps gcc/g++/git/ssh/curl/wget and other build tooling out of prod.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm AS builder

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install dependencies first, leveraging the uv lockfile for layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy application source and sync again to install the project itself.
COPY governance_controller ./governance_controller
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Runtime stage: minimal base image, no compiler or VCS tooling.
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Copy only the virtual environment and application code from the builder.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/governance_controller /app/governance_controller

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run the API as a non-root user.
RUN groupadd -r controller && useradd -r -g controller -d /app controller
RUN chown -R controller:controller /app
USER controller

CMD ["uvicorn", "governance_controller.main:app", "--host", "0.0.0.0", "--port", "8000"]
