# Next Steps

## Current State

**Neither phase is gate-clean. Do not trust a "gate-clean" claim in this file's own history —
it has been declared prematurely at least three separate times (`#152`'s first close, an earlier
"gate-clean" doc commit, and the third review round's `7622c77`), each later found wrong by
independent live verification.** As of 2026-08-26, five review rounds have run. Rounds 1-3
(`#154`-`#213`) were verified fixed. A fourth round found 14 of a "gate-clean" 28-issue fix batch
were still broken (8 reopened: `#189`/`#190`/`#198`/`#200`/`#203`/`#208`/`#214`/`#215`/`#217`, later
re-verified and re-closed, except `#221` which is **still open**) and found 6 new issues
(`#216`-`#221`). A fifth round then found **13 more issues never seen before**, including a
**hardcoded, unconfigurable admin skeleton key (`#226`)** and a **case-sensitivity bypass of the
self-approval check (`#227`)** in `permission_service.py`/`approval_service.py` — files that are
Phase 1 core, not Phase 2, and survived five separate `policy_engine.py`-focused Phase 1 hardening
rounds (`#107`-`#150`) untouched because every one of those rounds asked "what can an authenticated
caller do," never "does the caller need to be authenticated as who they claim at all." **Query the
live issue list before trusting anything else in this file:**

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
```

As of this writing: **14 open issues (2 CRITICAL, 6 HIGH, 5 MEDIUM, 1 LOW)** — `#221` (CLI silent
no-op for the documented invocation), `#226` (hardcoded `admin` skeleton key for EXECUTION/MERGE
approval), `#223` (no CI exists anywhere in this repo — every test/lint claim in this project's
history, including this file's, has been a manual local run), `#227` (self-approval/`system:`/`agent:`
actor-block bypass via casing), `#228` (neither spec'd background-polling mechanism — periodic
reconciliation, Event-Bridge-health fallback — was ever built), `#230` (`CompletionContract`
scope-check allowlist becomes a universal bypass via `"."`/`"/"`/`".."`), `#231` (empty/whitespace
`Check.command` silently "passes" without running anything), `#232` (network-access restriction
bypass via casing — third instance of the same string-comparison defect class as `#227`), plus six
MEDIUM/LOW items (`#222`, `#224`, `#225`, `#229`, `#233`, `#234`).

Phase 1 architectural summary: command validation uses an explicit `argv[0]` allowlist plus
per-binary dangerous-construct checks. Known-resolved bypass classes include wrapper/interpreter
smuggling, forbidden-path text scanning, destructive flags on allowlisted binaries,
command-execution primitives such as `git -c`, `tar --to-command`, `find -exec`, and `sed` `s///e`,
container escape flags, and removal of unauditable network/package-manager tools (`curl`, `wget`,
`apt`, `apt-get`, `dpkg`).

Phase 2 architectural summary: real Plane CE HTTP client + authenticated webhook receiver with
workspace-member actor resolution + allowlist; Controller→Plane projection service wired into
`ApprovalService.approve()`; a macro-agent-facing HTTP client wired to a self-declared Python stand-in
service (real `macro-agent` package deferred to Phase 3 — see
`decisions/ADR-002-macro-agent-stub-vs-package.md`); an opentasks DAG materializer reading live
Plane dependencies with guarded cycle detection, size cap, and concurrent fetching; a reconciliation
service/CLI that reads Controller DB state and can fix Plane state divergences with staleness checks;
authenticated intake adapters (Telegram/Email/generic) using shared secrets or HMAC signatures,
with body-size caps, creating HTML-escaped Plane drafts; verification failure feedback to macro-agent
and terminal alerting; optional OPA policy backend that runs only after the embedded PolicyEngine
passes and receives a minimized, optionally bearer-token-authenticated input document.

Test status (2026-08-26): **380 passed / 5 skipped** on SQLite, **383 passed / 2 skipped** on
PostgreSQL, `ruff` clean, `mypy governance_controller` clean (67 source files); `macro_agent_service`
tests still pass, `ruff`/`mypy` clean. Green tests are not evidence of correctness in this project —
re-read the "Current State" section above before trusting this number to mean anything beyond
"nothing crashes." There is still no CI (`#223`); every one of these numbers is a manual local run.

Implemented components:

- FastAPI application with `POST /tasks`, `POST /approvals`, `POST /events`, `GET /health`, `GET /tasks/{id}`,
  `GET /executions/{id}`, and `GET /tasks/{task_id}/audit-log`.
- SQLModel async PostgreSQL models: `Task`, `Execution`, `Approval`, `AuditLog`, `ProcessedEvent`.
- Deterministic state machine covering `PROPOSED → PLAN_APPROVED → EXEC_APPROVED → READY → RUNNING → AGENT_REVIEW → HUMAN_REVIEW → DONE / FAILED / BLOCKED`.
- Embedded Policy Engine validating task contracts, project profiles, harness allowlist,
  forbidden paths (with path-prefix matching), security posture, git settings, approval chain, and
  `CompletionContract` shell-command allowlisting.
- Append-only `AuditService` wired into task/profile creation, approvals, state transitions,
  execution starts, macro-agent events, and verification results.
- `PermissionService` wired into `ApprovalService` to reject self-approval and system/agent actors
  (now also rejects `system:*` / `agent:*` structured identifiers as of `GAP-096`).
- `ApprovalService` as the single convergence point for all approvals, with `Idempotency-Key`
  header support and key-based deduplication.
- Plane Adapter interface + in-memory stub.
- Harness Provider Registry with OpenCode and Claude Code metadata, including per-harness `allowed_roles`
  aligned with SPEC-06 §6.2.
- macro-agent executor abstraction (`MacroAgentClient`, `MacroAgentExecutor`) and `Execution` model,
  with explicit configurable HTTP timeout and outbound traceability metadata per SPEC-05 §5.7.
- Automatic execution trigger after `EXECUTION` approval, with `READY → RUNNING` transition.
- In-process macro-agent Event Bridge translating workspace events into Controller state updates;
  `landing:completed` triggers `VerificationService` and gates `AGENT_REVIEW → HUMAN_REVIEW`, with
  event-level idempotency.
- Dockerfile and Docker Compose for local API + PostgreSQL; image runs as non-root `controller` user.
- CLI (`approve`) and Telegram adapter stubs converging on `POST /approvals`.
- Verification service executes `CompletionContract`/`TaskContract.verification` `required`/`optional`
  shell commands, compares exit codes, and performs forbidden-path/scope checks.
- End-to-end Phase 1 smoke test.

*(Phase 1-scoped list above, kept as originally written. Phase 2's additions — real Plane client/
webhook/projection, macro-agent service stand-in, opentasks materializer, reconciliation, intake
adapter, OPA backend — are described in "Current State" above rather than itemized here, per the
same anti-staleness reasoning: itemized component lists in this file have gone stale repeatedly.)*

## Phase 1 Stop Conditions

None declared. OpenCode integration remains a stub path; no ACP/MCP blocker was encountered.

## Phase 2 Status

All 10 Phase 2 SDD tasks landed in `main` between commits `7c0bd5c` and `4715c22`. Five review rounds
have run since: Round 1 (`#154`-`#163`), Round 2 (`#164`-`#185`), Round 3 (`#186`-`#213`), Round 4
(fix-batch verification + fresh audit, `#189`-`#221` reopened/new), Round 5 (Phase 1 core,
deployment/CI, schema validation, docs-accuracy sweep, `#222`-`#234`). **14 issues remain open,
including 2 CRITICAL and 6 HIGH.** Round 5 in particular found that several of the most severe
open issues are not in Phase 2's own surface area at all — they're in Phase 1 core code
(`permission_service.py`, `approval_service.py`, `policy_engine.py`, the schema layer) that five
rounds of narrowly-scoped `policy_engine.py` command-validation review never looked at from this
angle. **Do not treat "Phase 2 review" as bounded to Phase 2 code** — the next round should keep
auditing wherever the live issue list and fresh eyes lead, not stop at a phase boundary.

## Immediate Next Step: Close the 14 Open Issues, Then Phase 3

Prioritize the 2 CRITICAL (`#221` CLI silent no-op, `#226` hardcoded admin skeleton key) and 6 HIGH
(`#223` no CI, `#227` case-sensitivity approval bypass, `#228` no background polling, `#230` scope-check
bypass, `#231` empty-command silent pass, `#232` network-access casing bypass) issues first. Given
this project's own track record — every "gate-clean" declaration so far has been wrong on
independent re-verification — the next round after closing these should be another full
live-reproduction review, not a trust-the-fix-commits pass.

Once the live issue list genuinely has no open `severity:critical`/`severity:high` issues, Phase 3
scope (from SPEC-10 §10.3) is:

- Docker sandboxing for verification/execution. See
  `docs/research-verification-sandboxing-scope-2026-08-24.md`.
- Full harness matrix (Claude Code, Codex, Aider) with runtime selection.
- Advanced conflict recovery.
- Project Profiles per repo.
- Semantic Reviewer.
- Better Completion Contract.

Before starting Phase 3, confirm the live issue list has no open `severity:critical` or
`severity:high` issues.

### Carried-forward Phase 2 deferred work

- **Durable execution**: evaluate Temporal or Celery for retry/collect workflows; persist the
  runtime task graph from the opentasks materializer beyond in-memory construction.
- **Meta Orchestrator (OpenCode + BMAD + OpenSpec)**: the intake→idea-ingestion path exists, but
  the actual decomposition/planning engine doesn't. Evaluate sudocode-ai/sudocode's Spec/Issue graph
  model before building this from scratch — see `docs/research-alexngai-ecosystem-and-sudocode.md`.
- **Real `macro-agent@latest` integration**: still a self-declared Python stand-in, not the actual
  npm package — see `decisions/ADR-002-macro-agent-stub-vs-package.md` and `#151`.

## Blockers to Watch

- macro-agent API stability and `/runs` contract.
- OpenCode ACP compatibility with macro-agent MCP tools.
- Plane CE self-hosted availability and API rate limits.

## Deferred to Phase 3+

- Full harness matrix (Claude Code, Codex, Aider) with runtime selection.
- Semantic Reviewer.
- Production hardening (metrics, tracing, HA).
- Evaluate LongHorizon-Harness (or similar durable-execution wrappers) as an optional `AgentHarness`
  adapter for long-running/GUI-touching opentasks — see `docs/research-longhorizon-harness.md`.
- If macro-agent's pre-1.0 risk (RISK-02/08) ever materializes into a real blocker, alexngai/openswarm is a
  concrete alternative execution engine (untested API surface, verify before evaluating further);
  alexngai/openhive is a multi-swarm federation candidate once single-swarm operation is proven — see
  `docs/research-alexngai-ecosystem-and-sudocode.md`.
- Same macro-agent-alternative scenario: Untrivial-ai/agent-orchestrator (fleet manager for coding-agent
  CLI sessions, worktree-per-task, pluggable agent/runtime/SCM adapters, ~9.9k stars, very active) is a
  second concrete candidate — its Kanban UI would need to stay out of scope (Plane already owns that
  role) and its programmatic API surface is unverified — see
  `docs/research-agent-orchestration-and-governance-survey-2026-08.md`.
- microsoft/agent-governance-toolkit (tool-call-level policy middleware, potentially complementary to the
  Governance Controller rather than competing with it) is a watch item only, not adopted and not formally
  risk-tracked — its maturity signals (6,091 stars on a ~5-6 month old repo, "Public Preview" versioning)
  don't hold up to a first pass of scrutiny; revisit only if independently corroborated beyond GitHub's
  own counters — see `docs/research-agent-orchestration-and-governance-survey-2026-08.md`.
