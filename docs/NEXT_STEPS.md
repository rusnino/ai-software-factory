# Next Steps

## Current State

**Neither phase is gate-clean. Do not trust a "gate-clean" claim in this file's own history —
it has been declared prematurely at least three separate times (`#152`'s first close, an earlier
"gate-clean" doc commit, and the third review round's `7622c77`), each later found wrong by
independent live verification.** As of 2026-08-29, seven review rounds have run. Rounds 1-4
(`#154`-`#221`) landed. Round 5 found 13 issues never seen before, including a hardcoded admin
skeleton key and a casing-based self-approval bypass in Phase 1 core code (`permission_service.py`)
that five rounds of narrow `policy_engine.py` hardening (`#107`-`#150`) never looked at from that
angle. Round 6 fixed round 5's two CRITICALs and immediately found the fix for one of them
(write-side auth, `#218`) had a same-shaped gap on the read side (`#236`: `GET /tasks`, `GET
/tasks/{id}/audit-log`, `GET /executions/{id}` had zero auth) plus a new stuck-execution-poller race
(`#237`). **Round 7 found the most severe issue yet: `POST /tasks` let any caller — using only the
one shared API secret every legitimate integration holds — silently overwrite ANY other project's
`ProjectProfile` security posture** (network restriction, forbidden paths, harness allowlist,
timeout/retry caps), by naming a foreign `project_id` in the `project_profile` half of the request
body while creating an unrelated task under their own project (`#244`). Nobody had tested
cross-project isolation before round 7. **Query the live issue list before trusting anything else
in this file:**

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
```

As of this writing: **7 open issues (1 CRITICAL, 2 HIGH, 2 MEDIUM, 2 LOW)** — `#244` (CRITICAL —
cross-project `ProjectProfile` poisoning via `POST /tasks`, live-reproduced), `#247` (HIGH — the
audit-log endpoint has no pagination, ~11.5MB response at 20k rows), `#248` (HIGH — no rate limiting
anywhere except `/intake/*`, live-verified 150/150 unthrottled requests), `#245` (MEDIUM — `gc
reconcile <project_id>` reads every project's tasks with no filter), `#243` (MEDIUM — the
macro-agent-service scaffold accepts unbounded/negative fields and has no cap on its in-memory
store — a real memory-exhaustion DoS), plus two LOW items (`#246`, `#249`). Every prior round's
findings (`#151`-`#242`) are closed and independently re-verified.

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

Test status (2026-08-29): **406 passed / 5 skipped** on SQLite, **409 passed / 2 skipped** on
PostgreSQL, `ruff` clean, `mypy governance_controller` clean (68 source files); `macro_agent_service`
tests still pass, `ruff`/`mypy` clean. Green tests are not evidence of correctness in this project —
re-read the "Current State" section above before trusting this number to mean anything beyond
"nothing crashes." A real CI workflow now exists (`.github/workflows/ci.yml`, added by `#223`,
live-verified to actually run both backends), but every "N passed" claim above this line is still
a manual local run.

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

All 10 Phase 2 SDD tasks landed in `main` between commits `7c0bd5c` and `4715c22`. Seven review
rounds have run since: Round 1 (`#154`-`#163`), Round 2 (`#164`-`#185`), Round 3 (`#186`-`#213`),
Round 4 (fix-batch verification + fresh audit, `#189`-`#221` reopened/new), Round 5 (Phase 1 core,
deployment/CI, schema validation, docs-accuracy sweep, `#222`-`#234`), Round 6 (adversarial review
of rounds 4-6's own new code, GET-endpoint auth audit, concurrency sweep, fresh end-to-end pipeline,
`#236`-`#242`), Round 7 (macro-agent adversarial testing, cross-project isolation, resource
limits/rate limiting, secret-leakage audit, `#243`-`#249`). **7 issues remain open, including 1
CRITICAL.** Round 7's CRITICAL (`#244`) is the most severe finding of any round so far — a
cross-tenant `ProjectProfile` poisoning vulnerability nobody had tested for because no prior round
had specifically looked at multi-project isolation boundaries. **Do not treat "Phase 2 review" as
bounded to Phase 2 code, or to any fixed set of angles** — every round that tried a genuinely new
angle (Phase 1 core in round 5, the read side in round 6, cross-tenancy in round 7) found something
the previous rounds' angles couldn't have found. The next round should keep trying new angles, not
repeat the ones already covered.

## Immediate Next Step: Fix the Cross-Tenant Profile-Poisoning CRITICAL, Then Verify Gate-Clean

Prioritize `#244` first (any caller can overwrite any other project's security posture via `POST
/tasks` — this is a live, actively exploitable vulnerability in the current codebase, not a
theoretical gap). Then the 2 HIGH issues (`#247` unbounded audit-log, `#248` no rate limiting) and
2 MEDIUM (`#243` macro-agent-service resource limits, `#245` reconcile CLI cross-project leak).
Before declaring Phase 2 gate-clean, run a fresh live-reproduction review and confirm the live issue
list has no open `severity:critical` or `severity:high` issues — and try an angle no prior round has
tried yet, given the track record above.

Once verified gate-clean, Phase 3 scope (from SPEC-10 §10.3) is:

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
