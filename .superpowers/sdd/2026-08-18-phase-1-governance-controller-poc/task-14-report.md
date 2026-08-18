# Task 14 Report: Docker Compose + Local Run

## Status

Completed.

## Commit

- `aaeb52d chore: add Dockerfile and docker-compose for local API + Postgres`

## Files Added

- `src/governance_controller/Dockerfile`
- `src/governance_controller/docker-compose.yml`
- `src/governance_controller/.dockerignore` (created; see note below)

## Implementation Summary

- `Dockerfile` uses the required `ghcr.io/astral-sh/uv:python3.13-bookworm` base.
- Multi-stage uv cache strategy: copy `pyproject.toml` + `uv.lock` and run `uv sync --frozen --no-install-project --no-dev` before copying `governance_controller/` source, so dependency layers are cached separately from code changes.
- Exposes port `8000` and defaults to `uvicorn governance_controller.main:app --host 0.0.0.0 --port 8000`.
- `docker-compose.yml` defines:
  - `db` service on `postgres:16-alpine` with requested credentials, port mapping, and healthcheck.
  - `api` service built from the project directory, using the requested `DATABASE_URL` and `LOG_LEVEL`, and `depends_on` with `condition: service_healthy`.
- `.dockerignore` excludes `.venv`, `__pycache__`, and `.git`.

## Verification Summary

1. `docker compose build` — succeeded.
2. `docker compose up -d db` — succeeded; container reached `healthy` status.
3. `docker compose run --rm api uv run alembic upgrade head` — skipped because alembic is not present in the project.
4. `docker compose down` — succeeded; containers and network removed cleanly.
5. `uv run ruff check Dockerfile docker-compose.yml .dockerignore` — skipped; ruff does not support Docker/non-Python files (it reports Python syntax errors for these files).

## Concerns

- `.dockerignore` was created but is excluded from the commit because the root `.gitignore` pattern `.dockerignore` ignores it project-wide. The file is present on disk and respected by Docker; if the project wants it tracked, the root `.gitignore` would need an exception. No other concerns.
