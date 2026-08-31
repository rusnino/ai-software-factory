# AI Software Factory

Self-hosted, open-source, free AI Software Factory for orchestrating heterogeneous coding agents.

## Goal

A human submits a high-level idea, reviews decomposed tasks, approves execution, and lets heterogeneous coding agents do the work in isolated workspaces — with explicit human gates and deterministic governance.

## Core Principles

- **Self-hosted** — runs on infrastructure we control.
- **Open-source + free** — no license fees, minimal TCO.
- **Human-in-the-loop** — human approval before execution and before final merge.
- **Governance authority** — custom Controller owns approvals, state machine, policy, audit.
- **Plane as projection** — Plane CE is the human UI, not the workflow authority.
- **Heterogeneous agents** — OpenCode (primary), Claude Code, Codex CLI, Aider, and more.

## Architecture Layers

```text
Human
  ↓
Plane CE (human project layer / projection)
  ↓ (webhooks)
Governance Controller (authoritative)
  ↓
macro-agent (execution orchestration)
  ↓
OpenCode / Claude Code / Codex / Aider
  ↓
Git + CI + Reviewer + Human Review → Done
```

## Repository Layout

| Path | Purpose |
|---|---|
| `specs/` | Design specifications (SPEC-01..SPEC-10) |
| `decisions/` | Architecture Decision Records |
| `requirements/` | Requirements traceability matrix |
| `docs/` | Supporting docs, diagrams, deferred-risk notes |
| `reviews/` | Review findings (`REVIEW-NNN-*.md`); the cross-review gap ledger `GAPS.md` is frozen/superseded — gap tracking moved to GitHub Issues, see `AGENTS.md` § Review Findings and Gap Tracking |
| `src/governance_controller/` | Phase 1 Governance Controller (FastAPI/SQLModel/PostgreSQL); its own `tests/` live inside this subproject, not at repo root. See the live `gh issue list --label phase-1` / `--label phase-2` for the current gap ledger, not `reviews/GAPS.md`. |
| `tmp/` | Generated archives, diagrams, scratch files (ignored by git) |

## Documentation

Start with:

1. `specs/SPEC-01-vision.md`
2. `specs/SPEC-02-architecture.md`
3. `specs/SPEC-03-governance.md`
4. `decisions/ADR-001-governance-controller-implementation.md`
5. `requirements/REQUIREMENTS.md`

## Configuration

The Governance Controller is configured through environment variables prefixed
with `GC_`. A reference `src/governance_controller/docker-compose.yml` sets
local dev placeholders for the required secrets.

Required in any production-like deployment:

| Variable | Purpose | Fails closed if empty |
|---|---|---|
| `GC_DATABASE_URL` | PostgreSQL DSN | Yes |
| `GC_CONTROLLER_API_SECRET` | Shared secret for sensitive Controller mutations (`X-Controller-Secret`) | Yes |
| `GC_EVENT_BRIDGE_SECRET` | Shared secret for macro-agent event callbacks (`X-Event-Bridge-Secret`) | Yes |
| `GC_EVENT_BRIDGE_HUMAN_SECRET` | Separate secret for `conflict:resolved` events that unblock a `BLOCKED` task (`X-Human-Admin-Secret`) | Yes |

Optional or context-dependent:

| Variable | Purpose |
|---|---|
| `GC_MACRO_AGENT_API_SECRET` | Secret sent from Controller to macro-agent service |
| `GC_PLANE_WEBHOOK_SECRET` | Shared secret for Plane webhook callbacks |
| `GC_PLANE_API_TOKEN` | Plane CE API token |
| `GC_INTAKE_SECRET` | Shared secret for generic/email intake endpoints |
| `GC_TELEGRAM_WEBHOOK_SECRET_TOKEN` | Telegram bot webhook secret |
| `GC_OPA_API_TOKEN` | Bearer token for OPA (when OPA is used) |
| `GC_ADMINS` | Comma-separated list of emails allowed to grant `EXECUTION`/`MERGE` approvals |

See `src/governance_controller/governance_controller/config.py` for the full
settings definition and defaults.

When `GC_OPA_BASE_URL` is configured, deploy OPA 1.0 or newer. The bundled Rego
uses Rego v1 syntax; CI validates it with `openpolicyagent/opa:1.19.1`.

## Development Approach

This project is designed to be implemented incrementally by AI agents under human supervision. See `AGENTS.md` for agent behavior rules, context order, and current implementation priority.

## License

To be determined. All dependencies are open-source and self-hostable.
