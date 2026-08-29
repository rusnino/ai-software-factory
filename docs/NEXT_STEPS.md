# Next Steps

## Current State

**Neither phase is gate-clean. Do not trust a "gate-clean" claim in this file's own history —
it has been declared prematurely at least three separate times, each later found wrong by
independent live verification.** As of 2026-08-30, nine review rounds have run. Rounds 1-7
(`#154`-`#249`) landed and hold up on re-verification. Round 8 found two issues that were, at the
time, the most severe of any round: a Controller crash at either of two specific points in the
approval/verification pipeline permanently strands a task with NO automatic recovery path
(`#253`/`#254`). **Round 9 verified opencode's `#253`/`#254` fix (a new `StuckExecutionPoller`
`_poll_ready`/`_poll_agent_review` pair) and found the fix code itself reuses this project's single
most recurring defect class — "blind write committed before a CAS check, regardless of the CAS
outcome" (`REQUIREMENTS.md` `RISK-16`) — introducing two MORE CRITICALs: `#262` (`_trigger_execution`
flushes the `Execution.state = RUNNING` write before the `Task`-level CAS to `RUNNING`, and commits
that flush even when the CAS reports `concurrent_modification`, leaving `Execution` and `Task` state
permanently disagreeing) and `#263` (`_poll_agent_review` unconditionally deletes the `#240` dedup
marker and commits, even when the `BLOCKED` CAS itself fails, silently re-opening the exact
duplicate-verification race `#240` existed to close).** Round 9 also reopened `#255` (HIGH —
`task_id="."."."` still path-traverses the verification worktree: the fix's charset regex admits
`..`, and `os.path.basename("..")` is a no-op so the defense-in-depth doesn't catch it either) and
found `#254`'s recovery docstring makes a false claim about how recovery works (`#259`), plus four
more MEDIUM gaps (`#260`-`#261`, `#264`-`#265`, see below). **Every round that tried a genuinely new
angle — Phase 1 core in round 5, the read side in round 6, cross-tenancy in round 7, process-crash
resilience in round 8, adversarial review of round 8's OWN fix code in round 9 — found something no
prior round's angles could have found.** Query the live issue list before trusting anything else in
this file:

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
```

As of this writing: **8 open issues (2 CRITICAL, 1 HIGH, 5 MEDIUM)** — `#262`/`#263` (CRITICAL —
both instances of the `RISK-16` blind-write-before-CAS defect class, introduced by round 8's own
`#253`/`#254` fix), `#255` (HIGH, reopened — `task_id=".."` still escapes the verification worktree
via `os.path.realpath` collapsing to the repo root, since `basename("..")` is a no-op), `#259`
(MEDIUM — `_poll_agent_review`'s docstring falsely claims a fresh event can recover the task; the
GAP-099 guard only accepts `conflict:resolved` to leave `BLOCKED`), `#260` (MEDIUM — Controller
Docker image is a 1.82GB single-stage build shipping gcc/g++/git/ssh/curl/wget, plus a
non-recursive `.dockerignore` pattern that leaks nested `__pycache__` dirs into the image, plus no
capability dropping or `HEALTHCHECK`), `#261` (MEDIUM — macro-agent-service has no signal
distinguishing "this run was lost because the service restarted" from any other terminal state),
`#264` (MEDIUM — the poller's crash-detection deadlines reuse `TaskContract.execution.timeout_minutes`
unmodified, so a short-timeout task's crash window is implausibly tight), `#265` (MEDIUM —
`poll-stuck-executions` CLI dumps a raw traceback instead of a structured log line when Postgres is
down). Every prior round's findings (`#151`-`#258` except the reopened `#255`) are closed and
independently re-verified.

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

Test status (2026-08-30): **414 passed / 5 skipped** on SQLite, **417 passed / 2 skipped** on
PostgreSQL, `ruff` clean, `mypy governance_controller` clean (68 source files); `macro_agent_service`
tests still pass, `ruff`/`mypy` clean. Green tests are not evidence of correctness in this project —
re-read the "Current State" section above before trusting this number to mean anything beyond
"nothing crashes." None of round 8's or round 9's findings (`#253`-`#265`) — including all four
CRITICALs found across both rounds — were caught by this suite; round 8's were found by killing a
live process and inspecting real Postgres state afterward, round 9's by adversarial code review of
round 8's own fix followed by live reproduction of the resulting race. A real CI workflow exists
(`.github/workflows/ci.yml`, added by `#223`), but it runs this same suite, so it would not have
caught any of these either.

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

All 10 Phase 2 SDD tasks landed in `main` between commits `7c0bd5c` and `4715c22`. Nine review
rounds have run since: Round 1 (`#154`-`#163`), Round 2 (`#164`-`#185`), Round 3 (`#186`-`#213`),
Round 4 (fix-batch verification + fresh audit, `#189`-`#221` reopened/new), Round 5 (Phase 1 core,
deployment/CI, schema validation, docs-accuracy sweep, `#222`-`#234`), Round 6 (adversarial review
of rounds 4-6's own new code, GET-endpoint auth audit, concurrency sweep, fresh end-to-end pipeline,
`#236`-`#242`), Round 7 (macro-agent adversarial testing, cross-project isolation, resource
limits/rate limiting, secret-leakage audit, `#243`-`#249`), Round 8 (adversarial review of round 7's
own fixes, systematic project-scoping sweep, Controller crash/restart resilience, unconstrained
schema fields, `#250`-`#258`), Round 9 (verification of round 8's fixes, adversarial review of round
8's OWN new poller code, Docker/dependency security, macro-agent-side restart resilience, database-
failure resilience, `#259`-`#265`). **8 issues remain open, including 2 CRITICAL.** Round 9's two
CRITICALs (`#262`, `#263`) are the most severe findings of any round so far, and the most alarming
in kind: they are new bugs *introduced by the fix* for round 8's CRITICALs, both instances of the
exact "blind write flushed before a Task-level CAS, then committed regardless of whether the CAS
succeeded" pattern that `REQUIREMENTS.md`'s `RISK-16` already documents as this project's most
recurring defect class. **This means fixing a stuck-state bug by adding more CAS-adjacent write
logic is not safe by default in this codebase — every such fix needs its own adversarial race
review before being trusted**, which is exactly what round 9 did and round 8 (which authored the
fix) did not. **Do not treat "Phase 2 review" as bounded to Phase 2 code, to any fixed set of
angles, or to code the current round didn't itself touch** — every round that tried a genuinely new
angle (Phase 1 core in round 5, the read side in round 6, cross-tenancy in round 7, crash resilience
in round 8, adversarial review of round 8's own fix in round 9) found something the previous
rounds' angles couldn't have found. The next round should keep trying new angles, and should
specifically adversarially review whatever fixes this round's findings, not just re-run past checks.

## Immediate Next Step: Fix the Two New CAS-Race CRITICALs, Then Verify Gate-Clean

Prioritize `#262` and `#263` first (both are the same recurring `RISK-16` defect class — a state
write committed even when the CAS it should have been contingent on failed — and both were
introduced by round 8's own fix for `#253`/`#254`, so any fix here needs independent adversarial
verification, not just a green test suite). Then the HIGH issue (`#255`, reopened — `task_id=".."`
still path-traverses the verification worktree; the fix needs to reject `.`/`..` path segments
explicitly, not just filter the charset) and the 5 MEDIUM issues (`#259` false recovery-mechanism
docstring, `#260` Docker image bloat/`.dockerignore`/capabilities/healthcheck, `#261` macro-agent
restart-loss signal, `#264` crash-detection deadline sizing, `#265` CLI raw-traceback-on-DB-down).
Before declaring Phase 2 gate-clean, run a fresh live-reproduction review and confirm the live issue
list has no open `severity:critical` or `severity:high` issues — and try an angle no prior round has
tried yet, given the track record above. Specifically: no round has yet done a dedicated adversarial
review of `ApprovalService`/`EventBridge`'s OTHER CAS call sites for the same `RISK-16` pattern
`#262`/`#263` were found in — that sweep has not been done systematically, only opportunistically.

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
