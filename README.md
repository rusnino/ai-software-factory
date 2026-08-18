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
| `reviews/` | Review findings (`REVIEW-NNN-*.md`) and the cross-review gap ledger (`GAPS.md`) — see `AGENTS.md` § Review Findings and Gap Tracking |
| `src/governance_controller/` | Phase 1 Governance Controller (FastAPI/SQLModel/PostgreSQL); its own `tests/` live inside this subproject, not at repo root. See `reviews/GAPS.md` before assuming this is production-ready — a `CRITICAL` durability gap (GAP-022) is open as of this writing. |
| `tmp/` | Generated archives, diagrams, scratch files (ignored by git) |

## Documentation

Start with:

1. `specs/SPEC-01-vision.md`
2. `specs/SPEC-02-architecture.md`
3. `specs/SPEC-03-governance.md`
4. `decisions/ADR-001-governance-controller-implementation.md`
5. `requirements/REQUIREMENTS.md`

## Development Approach

This project is designed to be implemented incrementally by AI agents under human supervision. See `AGENTS.md` for agent behavior rules, context order, and current implementation priority.

## License

To be determined. All dependencies are open-source and self-hostable.
