# Task 14 Brief: Docker Compose + Local Run

**Goal:** Provide a `docker-compose.yml` and `Dockerfile` so the Governance Controller can be started locally with PostgreSQL for manual/acceptance testing.

**Files to create:**
- `src/governance_controller/Dockerfile`
- `src/governance_controller/docker-compose.yml`
- `src/governance_controller/.dockerignore`

**Exact values to use verbatim:**

Dockerfile base: `ghcr.io/astral-sh/uv:python3.13-bookworm`

Expose port: `8000`

Default command: `uvicorn governance_controller.main:app --host 0.0.0.0 --port 8000`

Docker Compose services:
- `db`: `postgres:16-alpine`
  - environment: `POSTGRES_USER=controller`, `POSTGRES_PASSWORD=controller`, `POSTGRES_DB=controller`
  - ports: `5432:5432`
  - healthcheck: `pg_isready -U controller -d controller`
- `api`: build from current directory
  - environment:
    - `DATABASE_URL=postgresql+asyncpg://controller:controller@db:5432/controller`
    - `LOG_LEVEL=info`
  - ports: `8000:8000`
  - depends_on db condition: service_healthy

**Constraints:**
- Use multi-stage or uv-based image efficiently (cache uv lock).
- Do not commit secrets; use compose env defaults only.
- Provide `.dockerignore` to exclude `.venv`, `__pycache__`, `.git`.

**Verification steps:**
1. Run `docker compose build` (should succeed).
2. Run `docker compose up -d db` and wait for healthcheck.
3. Run `docker compose run --rm api uv run alembic upgrade head` if alembic exists, else skip.
4. Run `docker compose down`.
5. Run `uv run ruff check Dockerfile docker-compose.yml .dockerignore` if ruff supports; otherwise skip.

**Commit message:** `chore: add Dockerfile and docker-compose for local API + Postgres`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-14-report.md`
