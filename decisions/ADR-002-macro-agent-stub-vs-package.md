# ADR-002: macro-agent integration — Python scaffold instead of npm package

## Status

Accepted (Phase 2). Revisit before Phase 3 if/when the real `macro-agent` npm
package exposes a stable programmatic API that the Controller can consume.

## Context

Phase 2 planned to integrate the real `macro-agent@latest` npm package under
`src/macro_agent_service/` as a TypeScript wrapper that boots `bootV2`, exposes
REST endpoints, and forwards workspace events to the Controller. During
implementation we hit blockers that make a real package integration premature.

## Considered options

### Option A: integrate the real npm `macro-agent` package now

**Pros:**
- Uses the intended upstream runtime for macro-agent orchestration.
- Would eventually provide the real worktree/agent lifecycle/merge behavior the
  project needs.

**Cons (decisive for Phase 2):**
- `macro-agent` pre-1.0 API surface is unstable and not documented as a
  programmatic library. It is primarily a CLI tool.
- Booting `bootV2` requires a Node.js/TypeScript runtime and team YAMLs, adding
  a large build/runtime dependency chain to the Controller dev loop.
- There is no stable contract for the Controller to call into it beyond
  spawning a subprocess, which would recreate the same problem the service
  abstraction is meant to solve.
- The Phase 2 stop condition (SPEC-10 §10.2) explicitly says: stop and document
  a blocker if "OpenCode cannot receive required macro-agent MCP tools without
  invasive changes" or "per-role harness selection requires deep changes to
  macro-agent that break upstream compatibility." We are not yet far enough to
  prove or disprove those conditions, but forcing integration now would make
  Phase 2 depend on an uncontrolled upstream.

### Option B: Python scaffold service that mirrors the planned REST contract

**Pros:**
- Keeps the Controller → macro-agent adapter (`MacroAgentClient`,
  `MacroAgentExecutor`) stable and exercised.
- Lets Phase 2 prove the end-to-end path: Controller state machine → execution
  trigger → HTTP call → worktree landing → event bridge → verification → retry
  feedback.
- Does not block Plane sync, opentasks materializer, reconciliation, intake,
  OPA backend, or verification feedback — all of which were the actual Phase 2
  goals.
- Can be replaced by the real macro-agent service later without changing the
  Controller's adapter interface.

**Cons:**
- The scaffold does not run real agents, so it cannot prove OpenCode/macro-agent
  MCP compatibility or real git-cascade landing.
- A future migration to the real package is required work.

## Decision

Use a **self-declared Python scaffold** (`src/macro_agent_service/`) for Phase 2.
It implements the `/runs` REST contract the Controller expects and records
feedback, but does not execute real agents. The real `macro-agent@latest`
integration is deferred to Phase 3.

## Consequences

- `src/macro_agent_service/` is explicitly a stand-in. Its README/docstring must
  state this.
- The Controller's `MacroAgentExecutor`/`MacroAgentClient` are the stable seam;
  only the service behind them changes in Phase 3.
- `requirements/REQUIREMENTS.md` RISK-02/08 (macro-agent pre-1.0 risk) remains
  open until the real package is integrated and its API contract validated.
- Phase 2 can be declared gate-clean once the scaffold path is secure and
  reviewed; Phase 3 starts with real macro-agent integration.
