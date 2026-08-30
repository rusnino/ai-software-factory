# Next Steps

## Current State

**Neither phase is gate-clean. Do not trust a "gate-clean" claim in this file's own history —
it has been declared prematurely at least three separate times, each later found wrong by
independent live verification.** As of 2026-08-30, eleven review rounds have run. Rounds 1-8
(`#154`-`#258`) landed and hold up on re-verification. Round 9 found opencode's `#253`/`#254` fix
reused this project's single most recurring defect class — "blind write committed before a CAS
check, regardless of the CAS outcome" (`REQUIREMENTS.md` `RISK-16`) — introducing `#262`/`#263`
(both CRITICAL). Round 10 verified those genuinely closed but found the exact same `RISK-16` pattern
a THIRD time (`#266`, in code no prior round had looked at) via a systematic sweep of every other CAS
call site, plus a genuinely new angle — event-level authorization — immediately found `#267` (HIGH,
the only path to unblock a `BLOCKED` task used the same flat secret as routine automated traffic).
**Round 11 verified ALL FIVE of Round 10's fixes (`#266`-`#270`) are genuinely closed via live races,
a real migration-simulation, and a real HTTP/two-secret test — the second consecutive round with zero
reopens. But a genuinely new angle — real concurrent HTTP-level event delivery, never tested before
this round despite `POST /approvals` having been raced this way in round 10 — found this project's
most severe concurrency bug yet: `#271` (CRITICAL). Genuinely concurrent redelivery of the SAME
`landing:completed` event (an expected, documented traffic pattern — macro-agent runtimes retry on
timeout) runs the real verification subprocess MULTIPLE TIMES (10 of 12 concurrent requests actually
executed it in one live reproduction) instead of the intended once, and afterward the `#240` dedup
marker that was supposed to prevent exactly this is left DELETED entirely — not just skipped, gone —
so even a later legitimate sequential redelivery of the same event is no longer deduplicated either.
Three compounding bugs cause this: `EventBridge.handle()`'s CAS guard is skipped entirely once the
task has already reached the target state (so late-arriving racers never even attempt a CAS), the
dedup marker's insert result is never checked (an `ON CONFLICT DO NOTHING` silently no-ops instead of
signalling "you lost"), and the failure-path's "did a transition happen" check reads a stale
identity-mapped `Task` object via `db.get()` instead of the true committed state, so every losing
racer wrongly concludes no transition happened and deletes the winner's legitimately-committed
marker.** Round 11 also found `#272` (MEDIUM — the `#267` fix's new required
`GC_EVENT_BRIDGE_HUMAN_SECRET` is undocumented anywhere in the repo, including the project's own
`docker-compose.yml`, so an existing deployment upgrading in place silently loses its only documented
`BLOCKED`-recovery path with zero warning) and `#273` (LOW — a misleadingly-named OPA test that
actually asserts fail-closed behavior while its name claims fail-open). **Separately, this round also
diagnosed and fixed a real CI infrastructure bug: the `governance-controller-postgres` CI job had
failed on EVERY SINGLE RUN since the workflow was added (13 consecutive failures, 2026-08-26 through
2026-08-30) — `run_migrations()`'s `get_engine()` is keyed by the current event loop, not by the
`db.engine` attribute two tests were monkeypatching, so those tests silently fell back to
`settings.database_url`'s hardcoded default credentials instead of `GC_TEST_DATABASE_URL`; this only
worked locally because local dev Postgres conventionally uses the same default credentials, while
CI's ephemeral Postgres service (`controller`/`controller`/`controller`) does not. Fixed by seeding
the per-loop engine cache directly, matching a pattern a third test in the same file already used
correctly; verified against a container replicating CI's exact image/credentials. CI is now green for
the first time in this project's history.** **Every round that tried a genuinely new angle — Phase 1
core in round 5, the read side in round 6, cross-tenancy in round 7, process-crash resilience in
round 8, adversarial review of round 8's own fix in round 9, a systematic CAS sweep in round 10, real
concurrent event-delivery racing in round 11 — found something no prior round's angles could have
found. Concurrency testing specifically has now found a live bug in EVERY round it's been tried with
a genuinely new target (approval racing in round 10 was clean, but event racing in round 11 was not)
— any endpoint that hasn't yet been raced with real concurrent HTTP load should be treated as
unverified, not assumed safe by analogy.** Query the live issue list before trusting anything else in
this file:

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
```

As of this writing: **3 open issues (1 CRITICAL, 0 HIGH, 1 MEDIUM, 1 LOW)** — `#271` (CRITICAL —
genuinely concurrent `landing:completed` redelivery runs verification multiple times and destroys the
`#240` dedup marker), `#272` (MEDIUM — the new `#267` secret is undocumented, silently breaking
`BLOCKED`-recovery on upgrade), `#273` (LOW — misleadingly-named OPA fail-closed test). This is the
first round in this project's history with zero open HIGH-severity issues. Every prior round's
findings (`#151`-`#270`) are closed and independently re-verified — the second consecutive round with
none reopened.

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
tests still pass, `ruff`/`mypy` clean. **CI is green for the first time in this project's history**
(`.github/workflows/ci.yml`, added by `#223`, had failed on all 13 runs since 2026-08-26 until this
round's CI-infra fix — see "Current State" above). Green tests are still not evidence of correctness
in this project beyond "nothing crashes" — none of round 8's, 9's, 10's, or 11's findings
(`#253`-`#273`), including all five CRITICALs found across the four rounds, were caught by this
suite; they required killing a live process, adversarially re-reviewing the immediately preceding
round's own fix, or throwing genuinely concurrent real HTTP load at a live server backed by real
Postgres. `#271` specifically (Round 11's CRITICAL) is a case where the suite's own concurrency tests
would not have caught it even in principle — the bug only manifests under genuinely overlapping
requests, which the existing test suite does not exercise for this endpoint.

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

All 10 Phase 2 SDD tasks landed in `main` between commits `7c0bd5c` and `4715c22`. Eleven review
rounds have run since: Round 1 (`#154`-`#163`), Round 2 (`#164`-`#185`), Round 3 (`#186`-`#213`),
Round 4 (fix-batch verification + fresh audit, `#189`-`#221` reopened/new), Round 5 (Phase 1 core,
deployment/CI, schema validation, docs-accuracy sweep, `#222`-`#234`), Round 6 (adversarial review
of rounds 4-6's own new code, GET-endpoint auth audit, concurrency sweep, fresh end-to-end pipeline,
`#236`-`#242`), Round 7 (macro-agent adversarial testing, cross-project isolation, resource
limits/rate limiting, secret-leakage audit, `#243`-`#249`), Round 8 (adversarial review of round 7's
own fixes, systematic project-scoping sweep, Controller crash/restart resilience, unconstrained
schema fields, `#250`-`#258`), Round 9 (verification of round 8's fixes, adversarial review of round
8's OWN new poller code, Docker/dependency security, macro-agent-side restart resilience, database-
failure resilience, `#259`-`#265`), Round 10 (verification of round 9's fixes, a systematic sweep of
every OTHER `StateMachine.atomic_transition` call site for the `RISK-16` pattern, a dedicated audit-
log integrity/completeness review, an event-authorization/BLOCKED-unblock-spoofing review, and real
concurrent-approval racing against a live server, `#266`-`#270`), Round 11 (verification of round
10's fixes, a sweep of every OTHER event type's authorization boundary, real concurrent event-
delivery racing against a live server, and an OPA fail-behavior/secret-hygiene sweep, `#271`-`#273`;
this round also diagnosed and fixed a real CI infrastructure bug unrelated to any filed issue — see
"Current State" above). **3 issues remain open, 1 CRITICAL, 0 HIGH — the first round in this
project's history with no open HIGH-severity issue.** Round 11 verified all five of round 10's fixes
are genuinely closed — the second consecutive round with none reopened — but a genuinely new angle,
real concurrent HTTP-level event delivery (approval racing was tested in round 10 and came back
clean; event racing had never been tried), found this project's most severe concurrency bug to date:
`#271` (CRITICAL), where genuinely concurrent redelivery of the same `landing:completed` event runs
real verification multiple times and destroys the `#240` dedup marker outright. **Concurrency testing
has now found a live bug in every round it's been pointed at a genuinely new target** (event
delivery in round 11; the specific `_trigger_execution`/CAS races in rounds 9-10) — any endpoint that
hasn't yet been raced with real concurrent HTTP load should be treated as unverified. `#272` (MEDIUM)
extends round 10's own `#267` fix with a documentation-completeness gap: the new required secret is
undocumented anywhere in the repo, silently breaking the one BLOCKED-recovery path on upgrade. **Do
not treat "Phase 2 review" as bounded to Phase 2 code, to any fixed set of angles, or to code the
current round didn't itself touch** — every round that tried a genuinely new angle found something
the previous rounds' angles couldn't have found. The next round should keep trying new angles.

## Immediate Next Step: Fix the Concurrent-Event-Delivery CRITICAL, Then Verify Gate-Clean

Prioritize `#271` first (genuinely concurrent `landing:completed` redelivery runs real verification
multiple times and destroys the `#240` dedup marker — a live, reproducible bug under a documented,
expected traffic pattern, not a theoretical one; the three compounding root causes are laid out in
the issue, and any fix here needs independent adversarial/live-concurrency verification before being
trusted, not just a green test suite, matching the pattern established for every CRITICAL found in
rounds 9-11). Then `#272` (MEDIUM — document `GC_EVENT_BRIDGE_HUMAN_SECRET`, and ideally do a full
pass documenting all 9 `GC_*` secret config fields, most of which are undocumented) and `#273` (LOW —
rename the misleadingly-named OPA test). Before declaring Phase 2 gate-clean, run a fresh
live-reproduction review and confirm the live issue list has no open `severity:critical` or
`severity:high` issues — and try an angle no prior round has tried yet, given the track record
above. Specifically worth trying next: `#271`'s finding suggests systematically checking every OTHER
endpoint that has a dedup/idempotency mechanism (approvals' `Idempotency-Key` handling was raced in
round 10 and held; the intake adapters' duplicate-submission guards, per `#256`, have not yet been
raced with genuinely concurrent HTTP load) for the same "CAS-guard skipped once already at target
state" or "unchecked upsert result" shape.

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
