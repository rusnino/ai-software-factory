FROM ghcr.io/astral-sh/uv:python3.13-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Install dependencies first, leveraging the uv lockfile for layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy application source and sync again to install the project itself.
COPY governance_controller ./governance_controller
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Run the API as a non-root user.
RUN groupadd -r controller && useradd -r -g controller -d /app controller
RUN chown -R controller:controller /app
USER controller

CMD ["uvicorn", "governance_controller.main:app", "--host", "0.0.0.0", "--port", "8000"]
