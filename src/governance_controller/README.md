# governance-controller

Governance Controller for the AI Software Factory.

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
