# AGENTS.md — AI Software Factory

This project is designed to be implemented by AI coding agents (OpenCode, Claude Code, Codex, and others) under explicit human governance.

## Read Order for First Contact

Read in this order before writing application code:

1. `README.md`
2. `specs/SPEC-01-vision.md`
3. `specs/SPEC-02-architecture.md`
4. `specs/SPEC-03-governance.md`
5. `decisions/ADR-001-governance-controller-implementation.md`
6. `specs/SPEC-04-plane-integration.md`
7. `specs/SPEC-05-macro-agent-integration.md`
8. `specs/SPEC-06-harnesses.md`
9. `specs/SPEC-07-intake.md`
10. `specs/SPEC-08-security.md`
11. `specs/SPEC-09-verification.md`
12. `specs/SPEC-10-phase-plan.md`
13. `requirements/REQUIREMENTS.md`

## Non-Negotiable Architecture Rules

- **Governance Controller is authoritative** for approvals, state machine, policy, audit, task DAG, execution records.
- **Plane CE is a replaceable projection** for humans. Do not rely on Plane to enforce workflow transitions or approvals.
- **macro-agent** is execution orchestration only. It decides *who / when / how* approved work runs, not *whether* it may run.
- **Do not reimplement macro-agent internals**: agent lifecycle, worktrees, messaging, merge, conflict recovery.
- **Plane dependencies → runtime DAG**: Plane holds project-level dependencies; Controller materializes the approved subgraph into `opentasks`; agents claim work only from `opentasks`.
- **Event Bridge** translates macro-agent workspace events into Controller state updates. macro-agent has no outgoing webhooks.
- **All approval paths converge to `POST /approvals`** in the Controller, regardless of source (Plane, Telegram, CLI, dashboard).
- **Human approval must be explicit and machine-recorded** before sensitive transitions.
- **Worktree is not a security sandbox**. Progressive isolation: git worktree → Docker → Firecracker/Kata.
- **Secrets stay outside prompts, team YAML, and task descriptions**.
- **Python projects and utilities use `uv` / `uvx`**.

## Domain Model Separation

Keep these concepts separate in code and configuration:

- **agent harness** (OpenCode / Claude Code / Codex / Aider)
- **role** (planner / worker / reviewer)
- **provider / account**
- **model policy**
- **skills / behavior**
- **permissions**
- **execution backend**

## Current Implementation Priority

Build and prove this path first:

```text
Controller API → durable human approval → READY → Agent Registry → macro-agent
  → Direct CLI / ACP → OpenCode (+ optional harness) → worktree
  → git-cascade landing → verification → HUMAN_REVIEW → DONE
```

Do this **before** adding Plane UI, OPA, Temporal, advanced sandboxing, or more than two harnesses.

## Phase 1 Stop Conditions

Stop Phase 1 and document a blocker if:

- OpenCode cannot receive required macro-agent MCP tools without invasive changes.
- Per-role harness selection requires deep changes to macro-agent that break upstream compatibility.
- Durable approval or state machine cannot be guaranteed without Plane.

## Security Constraints for AI Agents

- Do not add new external dependencies without documenting why.
- Do not put secrets, API keys, or private credentials in any markdown/yaml/config file committed to git.
- Do not treat a git worktree as a sandbox.
- Do not allow agents to approve their own work.
- Do not make Plane, UI, or macro-agent authoritative for approvals.

## Skills and Methodology

- Use OpenCode skills / Superpowers where appropriate.
- Keep skills as methodology, not security boundaries.
- Prefer deterministic verification over LLM-based claims of correctness.

## Communication Rules

- When changing a SPEC, ADR, or REQUIREMENTS file, update this index and commit with a clear message.
- When adding risk items, keep `requirements/REQUIREMENTS.md` synchronized.
- Update `docs/NEXT_STEPS.md` when finishing or reprioritizing work.

## Review Findings and Gap Tracking

Any agent that reviews another agent's work (code review, spec-conformance review, security review) — whether asked to by a human or as a self-check before declaring something done — follows this convention so findings survive across agent sessions instead of living only in one session's chat history:

- Write the full findings to `reviews/REVIEW-NNN-<slug>.md`, numbered sequentially like `decisions/ADR-NNN-*.md`. A review file is a point-in-time record: once written, don't edit it after the fact — if a later review revisits the same code, write a new `REVIEW-NNN` file and reference the earlier one.
- Every finding, regardless of severity, gets one row in `reviews/GAPS.md` — the single running ledger across all reviews — with a stable `GAP-NNN` id, severity, one-line summary, source review, and status.
- Whichever agent closes a gap (fixes the bug, wires up the missing enforcement, etc.) updates that row's status to `CLOSED` with the closing commit hash. Never delete a row — a closed row is proof the gap was found and fixed, not just forgotten.
- **A phase may not be declared "complete" in `docs/NEXT_STEPS.md` or have its checklist ticked in `specs/SPEC-10-phase-plan.md` while any `CRITICAL` or `HIGH` gap tied to that phase is still `OPEN` in `reviews/GAPS.md`.** This is the Verification Rule below, applied to review findings specifically, not a separate exception to it.
- `reviews/GAPS.md` is an inter-agent workflow ledger, not the Governance Controller's own audit log (SPEC-03 §3.8) — do not conflate the two. It tracks the humans'/agents' review-and-fix workflow around this repo; it has no runtime effect on the Controller itself.

## Verification Rule

Every significant code change must be accompanied by tests or a clear explanation why tests are not yet feasible. Failed verification must never produce `DONE`.
