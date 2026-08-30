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

## Regression Coverage Policy

This exists because "write tests for every significant change" above was not enough on its own:
across review rounds 8-12, fix commits for `severity:critical`/`severity:high` issues repeatedly
shipped with **zero new regression tests** (rounds 10-12 specifically: `#266`/`#267`/`#268`, `#271`,
`#274` — all landed without a single new test file). A green test suite was treated as sufficient
evidence of correctness. It wasn't: the exact same defect class recurred multiple times, undetected,
in unrelated code — see `RISK-16` and `RISK-19` in `requirements/REQUIREMENTS.md`. Do not repeat this.

- **Every fix for a `severity:high` or `severity:critical` issue must land a regression test in the
  same commit** — a test that fails against the pre-fix code and passes against the post-fix code.
  A green suite alone is not evidence the fix is real or that a closed issue stays closed.
- **Concurrency/CAS/race-condition bugs need a live test against real Postgres with genuine
  concurrent sessions or requests, not a mock.** Mocks are exactly what let 5 separate instances of
  the same "stale identity-map read" pattern (`RISK-19`) go undetected across 3 review rounds, and 3
  separate instances of "write before CAS, commit regardless of outcome" (`RISK-16`) go undetected
  across 2 rounds — nothing in the suite exercised genuine concurrency.
- **If a fix is one instance of a known recurring defect class** (`RISK-16`: a write staged before a
  CAS check that gets committed even when the CAS loses; `RISK-19`: a "did this change?" re-read that
  uses `Session.get()` or a bare `select()` and silently returns a stale, already-loaded object
  instead of querying the database) — **sweep every other call site with the same shape as part of
  the same fix**, not just the one reported. This has found more live bugs than the original report
  alone every time it's been done as a dedicated pass.
- If a fix genuinely cannot be covered by an automated test (rare — e.g. a pure documentation/config
  change), say so explicitly in the commit message instead of silently omitting one.

## When You Finish a Task

1. Run tests.
2. Update `docs/NEXT_STEPS.md` if priorities changed.
3. Commit with a clear message.
4. Summarize what you did and what remains.
