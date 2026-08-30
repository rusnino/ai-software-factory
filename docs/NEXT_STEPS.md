# Next Steps

## Current State

**Neither phase is gate-clean. Do not trust a "gate-clean" claim in this file's own history —
it has been declared prematurely at least three separate times, each later found wrong by
independent live verification.** As of 2026-08-30, thirteen review rounds have run. Rounds 1-8
(`#154`-`#258`) landed and hold up on re-verification. Round 9 found opencode's `#253`/`#254` fix
reused this project's single most recurring defect class — "blind write committed before a CAS
check, regardless of the CAS outcome" (`REQUIREMENTS.md` `RISK-16`) — introducing `#262`/`#263`
(both CRITICAL). Round 10 found the same pattern a third time (`#266`) via a systematic CAS sweep,
plus `#267` (HIGH, event-level authorization). Round 11 found `#271` (CRITICAL) — genuinely
concurrent `landing:completed` redelivery ran real verification multiple times and destroyed the
`#240` dedup marker — via a DIFFERENT recurring defect class: `db.get()`/non-`populate_existing`
re-reads silently returning a session's stale, already-loaded object instead of the true committed
state. Round 12 verified round 11's fixes clean, found the identical staleness bug a second time in
`ReconciliationService` (`#275`), and found an intake-adapter TOCTOU (`#274`, HIGH) plus a
pool-exhaustion polish gap (`#276`, MEDIUM). **Round 13 verified ALL THREE of Round 12's fixes
(`#274`-`#276`) are genuinely closed — the fourth consecutive round with zero reopens. The standing
sweep for the identity-map-staleness pattern (committed to after round 11) found it a FIFTH time,
and this instance is the most consequential yet: `#278` (HIGH) — `ApprovalService.approve()`'s
duplicate-delivery re-fetch, added specifically to fix `#242` back in round 6 ("return the true
post-approval state, not a stale copy"), uses `db.get()` and has therefore silently never worked
since the day it shipped. A legitimate client retrying a timed-out `POST /approvals` call gets back
the WRONG task state in the response body, and the resulting audit log entry's `previous_state`/
`new_state` fields are both wrong too. Separately, racing the CLI against live HTTP traffic — a
genuinely new angle — found something no round had checked directly in this project's history:
whether the documented `approve` CLI command actually works against a real server at all. It
doesn't, in any configuration: `#277` (HIGH) — `cli.py`'s `approve` command never sends
`X-Controller-Secret`, so `POST /approvals`'s fail-closed auth rejects it with a 401 every time,
whether the secret is configured or not. This has evidently been broken since whenever that auth
dependency was added, invisible because the CLI's own tests only mock `httpx.post` and never hit a
real server. A LOW finding rounds out the sweep: `#279` — an event correctly rejected by the
GAP-099 BLOCKED guard still returns a plain `204`, indistinguishable from being processed.** **Every
round that tried a genuinely new angle has found something no prior round's angles could have
found, without exception across all thirteen rounds so far. The identity-map-staleness defect class
has now been found in FIVE unrelated subsystems across three rounds (`EventBridge` round 11,
`ReconciliationService` round 12, `ApprovalService` round 13) and is a standing sweep target for
every future round — the fifth hit, inside `ApprovalService` itself, is a direct reminder that a
prior round's own "fix" for a different-looking bug can be this exact pattern in disguise and go
undetected for many rounds. Equally, `#277` is a reminder that a documented, core feature can be
completely non-functional in every real configuration for many rounds running, invisible to a test
suite that only ever mocks the boundary it's supposed to be testing — any future round should
periodically ask "does this documented workflow actually work against a real, unmocked server," not
just "is this code correct in isolation."** Query the live issue list before trusting anything else
in this file:

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
```

As of this writing: **3 open issues (0 CRITICAL, 2 HIGH, 0 MEDIUM, 1 LOW)** — `#277` (HIGH — the
`approve` CLI command never authenticates, cannot succeed against any real deployment), `#278`
(HIGH — `ApprovalService.approve()`'s duplicate-delivery re-fetch is stale, so round 6's `#242` fix
never actually worked), `#279` (LOW — a GAP-099-rejected event returns 204 instead of signaling the
drop). Every prior round's findings (`#151`-`#276`) are closed and independently re-verified — the
fourth consecutive round with none reopened.

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
tests still pass, `ruff`/`mypy` clean. **CI is green** (`.github/workflows/ci.yml`, added by `#223`,
had failed on all 13 runs since 2026-08-26 until Round 11's CI-infra fix). Round 13 made no
production-code changes of its own beyond what opencode's `7b765ed` fix commit already covered, so
these numbers are unchanged. Green tests are still not evidence of correctness in this project beyond
"nothing crashes" — none of round 8's through round 13's findings (`#253`-`#279`), including all five
CRITICALs found across those rounds, were caught by this suite; they required killing a live process,
adversarially re-reviewing the immediately preceding round's own fix, throwing genuinely concurrent
real HTTP load at a live server backed by real Postgres, or — for `#277` — simply trying to actually
use a documented CLI command against a real, unmocked server for the first time. `#271`, `#274`,
`#275`, and `#278` specifically are cases where the suite's own tests would not have caught them even
in principle — the bugs only manifest under genuinely overlapping requests or a genuinely fresh
session identity map, neither of which the existing test suite exercises for these code paths; `#277`
is a case where the suite's own mocking of the HTTP boundary is precisely what hid the bug.

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

All 10 Phase 2 SDD tasks landed in `main` between commits `7c0bd5c` and `4715c22`. Thirteen review
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
this round also diagnosed and fixed a real CI infrastructure bug unrelated to any filed issue), Round
12 (verification of round 11's fixes with a direct pre-fix/post-fix comparison, a sweep of every
OTHER dedup/idempotency mechanism in the codebase, a dedicated connection-pool-exhaustion resilience
check, and a reconciliation-service concurrent-drift review, `#274`-`#276`), Round 13 (verification
of round 12's fixes, a systematic sweep for the identity-map-staleness pattern across the rest of the
codebase, and racing the CLI against live HTTP API traffic for the first time, `#277`-`#279`). **3
issues remain open, 0 CRITICAL, 2 HIGH.** Round 13 verified all three of round 12's fixes are
genuinely closed — the fourth consecutive round with none reopened. The standing sweep for the
identity-map-staleness pattern (committed to after round 11, exercised once already in round 12)
found it a FIFTH time, in the most consequential place yet: `#278` (HIGH),
`ApprovalService.approve()`'s duplicate-delivery re-fetch — added specifically to fix `#242` back in
round 6 — uses `db.get()` and has therefore silently never worked since the day it shipped; a
legitimate client retrying a timed-out approval gets back the wrong task state in both the HTTP
response and the audit log. **This defect class has now been found in FIVE unrelated subsystems
across three rounds (`EventBridge` round 11, `ReconciliationService` round 12, `ApprovalService`
round 13) — the `ApprovalService` hit is a direct reminder that a prior round's own fix for a
different-looking bug can be this exact pattern wearing a disguise.** Racing the CLI against live
HTTP traffic — a genuinely new angle nobody had tried in this project's history — found something
just as consequential from a completely different direction: `#277` (HIGH), the documented `approve`
CLI command has never once been able to authenticate against a real server, in any configuration,
because it never sends `X-Controller-Secret` and the endpoint fails closed. This was invisible
because the CLI's own tests only mock `httpx.post`. `#279` (LOW) rounds out the sweep — a
GAP-099-rejected event returns 204 instead of signaling the drop. **Do not treat "Phase 2 review" as
bounded to Phase 2 code, to any fixed set of angles, or to code the current round didn't itself
touch** — every round that tried a genuinely new angle found something the previous rounds' angles
couldn't have found, without exception across all thirteen rounds so far. The next round should keep
trying new angles.

## Immediate Next Step: Fix the Two HIGHs, Then Verify Gate-Clean

Prioritize `#278` and `#277` (both HIGH, the only open issues besides one LOW). `#278`: replace
`ApprovalService.approve()`'s `await self.db.get(Task, task.id)` on the idempotent-duplicate path
with a `populate_existing=True` re-read, matching the fix already applied for `#271`/`#275` — and
because this is now the fifth confirmed instance of this exact pattern, do a fresh, EXPLICIT sweep
of the entire codebase for `db.get(`/bare `select()` staleness checks as part of this fix, not just
a fix at this one call site; treat any remaining instance as equally urgent. `#277`: give the CLI a
way to supply `X-Controller-Secret` (an option/env var), and add a live-server integration test for
at least the `approve` command so this class of regression — a documented feature silently broken in
every real configuration — can't recur invisibly again. Then `#279` (LOW — surface the GAP-099
rejection as a non-204 status). Before declaring Phase 2 gate-clean, run a fresh live-reproduction
review and confirm the live issue list has no open `severity:critical` or `severity:high` issues —
and try an angle no prior round has tried yet, given the track record above. Two concrete leads for
the next round: (1) now that `#277` showed a documented CLI command can be silently broken for many
rounds because its tests only mock the HTTP boundary, do the same live-server sanity check for the
`reconcile` and `poll-stuck-executions` CLI commands specifically (round 13 exercised
`poll-stuck-executions` against live traffic already and found it clean, but `reconcile` has not yet
been driven through a real, unmocked HTTP-adjacent path end to end); (2) the identity-map-staleness
sweep in round 13 was thorough but scoped to `governance_controller/` — `macro_agent_service`'s own
codebase has never been checked for the same pattern.

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
