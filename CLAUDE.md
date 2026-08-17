# CLAUDE.md — AI Software Factory

## Role

You are implementing an AI Software Factory: a self-hosted, open-source system for orchestrating heterogeneous coding agents with durable human governance.

## Before You Code

Read in this exact order:

1. `AGENTS.md`
2. `README.md`
3. `docs/PROJECT_CONTEXT.md`
4. `specs/SPEC-01-vision.md` through `specs/SPEC-03-governance.md`
5. `decisions/ADR-001-governance-controller-implementation.md`
6. `specs/SPEC-04-plane-integration.md` through `specs/SPEC-10-phase-plan.md`
7. `requirements/REQUIREMENTS.md`
8. `docs/NEXT_STEPS.md`

## What You Must Not Do

- Do not make Plane CE authoritative for approvals or workflow transitions.
- Do not reimplement macro-agent internals (lifecycle, worktrees, merge, conflict recovery).
- Do not treat a git worktree as a security sandbox.
- Do not put secrets in prompts, YAML, or markdown files.
- Do not add new dependencies without documenting why.
- Do not allow agents to approve their own work.

## Current Priority

Implement Phase 1: the Governance Controller.

Focus on:

- State machine
- `POST /approvals` endpoint
- Task Contract / Project Profile / Completion Contract validation
- Append-only audit log
- Plane adapter interface with in-memory stub

Do not start Phase 2 work (Plane UI, Meta Orchestrator, Intake) until Phase 1 acceptance criteria pass.

## Development Conventions

- Use Python 3.13+ and `uv` / `uvx`.
- Use FastAPI + Pydantic + SQLModel + PostgreSQL for the Controller.
- Write tests for every significant change.
- Keep the Controller authoritative. Plane is a projection.

## When You Finish a Task

1. Run tests.
2. Update `docs/NEXT_STEPS.md` if priorities changed.
3. Commit with a clear message.
4. Summarize what you did and what remains.
