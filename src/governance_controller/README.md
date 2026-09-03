# governance-controller

Governance Controller for the AI Software Factory.

## Deployment / Runbook

### Secret rotation

All shared secrets are read once at startup and cached in memory. Rotating
any secret (Controller API, macro-agent, event bridge, intake, Plane
webhook, Telegram, OPA) requires a coordinated simultaneous restart of every
Controller instance and every caller that presents those secrets. A rolling
restart without a cutover will cause authenticated calls to fail with 401
until both sides load the new value.

## Development

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync --extra dev
uv run pytest
```

## Project layout

```
governance_controller/
├── api/              # HTTP API routers
├── models/           # SQLModel database models
├── schemas/          # Pydantic request/response schemas
├── constants/        # Shared constants and enums
├── services/         # Business logic services
├── adapters/         # External integrations
│   └── macro_agent/ # macro-agent event bridge
├── harness/          # Agent harness definitions
└── ...
tests/                # pytest test suite
```
