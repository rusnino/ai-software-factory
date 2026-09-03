# Next Steps

## Current State

**Current status (2026-09-03): Round 17 re-broke gate-clean.** opencode closed all 6 open
`phase-2` issues (`#256`, `#297`, `#298`, `#299`, `#300`, `#308`) across 15 pushed commits
(`595bbcd`-`70f4b92`). The reviewer ran 10 parallel live-verification agents — one per issue/commit
cluster, plus 3 genuinely fresh angles (a dedicated `RISK-16`/`RISK-19` sweep of the whole batch, a
full real end-to-end pipeline run, and an unexplored-corners pass covering multi-tenancy, audit-chain
tooling, secret rotation, and macro-agent-service). **Five of the six closed issues are genuinely
fixed, independently live-reproduced** (real Postgres, real HTTP, real races — not diff-trust).
**The sixth, `#308`, is NOT actually fixed and has been reopened**: its "fix" commit (`70f4b92`) only
added a test; the query it claims to bound (`_poll_pending_cancellations`) is byte-identical
before and after, and a live `EXPLAIN (ANALYZE, BUFFERS)` at realistic scale (40k→400k audit rows)
shows execution time scaling ~linearly (54ms→760ms) — the existing 53-row regression test is too
small to have caught this. The fresh-angle work found **8 new issues (`#309`-`#316`)**, most
notably `#309` (**CRITICAL**, live-reproduced RCE: `git config <key> <value>` — the plain subcommand
form, as opposed to `-c`/`--config` — bypasses the dangerous-git-config-key check on both the
embedded and OPA policy backends) and `#311` (**HIGH**: the new execution-start recovery poller has
no backoff, live-reproduced retrying an unrecoverable failure forever). It also caught and corrected
this file's own prior claim that `#134` was fixed — it was not; a fresh full-stack live
reproduction (`tar --directory=<forbidden> -xf payload.tar` sails through both policy backends and
writes a file into the declared-forbidden directory) confirms `#134` is still open, while the
neighboring `#140`/`#141` (closed this round, independently re-verified) were genuinely fixed.
**Phase 2 is not gate-clean: `#309` (CRITICAL) and `#311` (HIGH) are both open with the `phase-2`
label**, on top of the pre-existing `#301` (HIGH, phase-1, macro-agent idempotency, still correctly
blocked pending a macro-agent API change) and `#134`/`#305` (phase-1, unrelated to this round's
phase-2 mandate). Do not trust a "gate-clean" claim in this file's history — it has been declared
prematurely at least three times before (each by the reviewer, later found wrong), and this round is
a second, sharper instance of the same lesson: a closed-issue label and a green regression test are
not proof a fix is real (`#308`) or that a docs claim is accurate (`#134`).
As of 2026-08-31, sixteen review rounds had run. Rounds 1-15 (`#154`-`#286`) landed and
hold up on re-verification; three recurring defect classes were established along the way —
`RISK-16` (write before CAS, committed regardless of outcome, 3 confirmed instances), `RISK-19`
(identity-map staleness, 5 confirmed instances), and a third, unnamed pattern where a fix for one
bug introduces a genuinely new regression of a different kind (`#280`'s advisory lock → `#283`'s
system-wide stall). Round 15 found `#283`-`#286`: a global audit-log advisory lock held across slow
Plane calls (CRITICAL-adjacent HIGH), a second `#277`-shaped Telegram auth gap, an unhandled
`KeyError` on a malformed macro-agent response, and an indistinguishable-error-code diagnostic gap.

**Round 16 is the largest single batch in this project's history: opencode landed 21 commits fixing
not just the 4 issues the reviewer filed (`#283`-`#286`) but 10 MORE issues opencode found and filed
itself (`#287`-`#296`) — new task-scoped Plane-projection advisory locks, idempotent Plane-issue
lookup-by-trace-field, a retry-execution "sentinel" mechanism for crash recovery, Pydantic-validated
macro-agent responses, and CAS-loss run-cancellation/attachment logic — then declared Phase 2
"gate-clean" in its own docs edit, before the reviewer had verified any of it.** Given this
project's history of premature gate-clean claims, the reviewer ran the largest verification effort
to date: 8 parallel agents, each independently live-reproducing (not diff-reading) one cluster of
the 14 closed issues, PLUS three genuinely fresh angles no prior round had tried — a systematic
`RISK-16`/`RISK-19`/`#283`-pattern sweep of this round's own new code, a full real end-to-end task
lifecycle (create→approve→run→verify→review→done) driven through the REAL HTTP API against a REAL
`macro_agent_service` process and a real fake-Plane server, including a genuine THREE-way concurrent
scenario (an approval, a `reconcile --fix`, and a poller pass, all racing on the same task via
separate real Postgres sessions/processes at once), and a dedicated look at two areas untouched by
this round's changes (the OPA policy backend, and intake's rate-limiter/duplicate-guard
interaction). **Result: all 14 issues (`#283`-`#296`) are CONFIRMED genuinely closed** — every fix
was independently reproduced against real Postgres/real HTTP servers/real concurrent sessions, and
every claimed regression test was independently verified to fail on the exact pre-fix commit and
pass on the post-fix commit. The full real end-to-end pipeline, including the three-way-concurrent
scenario, completed cleanly with a genuinely re-validated audit hash chain (57 rows written by 3
separate OS processes under real concurrent load, zero broken links — independently re-confirming
`#280`'s fix holds under load well beyond any single prior round's test). **But the fresh-angle work
found 4 more real, if lower-severity, gaps `#283`-`#296`'s own batch and the previously-stale areas
missed: `#297` (MEDIUM) — `#288`'s idempotent Plane-issue lookup scans every issue in the project on
every task creation, no server-side filter, an unbounded O(N) cost on the task-creation hot path,
architecturally the same "new fix, new performance cost" shape as `#283` but narrower; `#298` (LOW)
— `#283`'s early-commit fix leaves a narrow crash-timing-only window where state is durably
committed with zero audit trail that Plane was never told; `#299` (LOW) — the OPA policy backend's
Rego file has drifted significantly behind the embedded `PolicyEngine` across 16 rounds of
bypass-closing fixes to the latter, though the embedded-engine-is-authoritative guarantee itself was
live-proven to hold (a real OPA server allowed `sudo whoami`; the full backend correctly denied it
without even consulting OPA); `#300` (MEDIUM) — intake's per-IP rate limiter and per-sender
duplicate guard interact badly, letting a burst of ordinary duplicate-retry traffic exhaust the
IP-level budget and starve legitimate new submissions with misleading `429`s, live-reproduced.**
Every round that tried a genuinely new angle has found something no prior round's angles could have
found, without exception across all sixteen rounds so far — including this one, where "verify a
massive batch that already claims gate-clean" was itself the new angle. Query the live issue list
before trusting anything else in this file:

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
```

As of Round 17 (2026-09-03), the `phase-2`-labeled open set is **9 issues: `#309` (CRITICAL, git
config subcommand-form policy bypass — live RCE), `#311` (HIGH, execution-start recovery poller has
no backoff), `#310`/`#312` (MEDIUM: tar/find flag gaps; intake global-limiter bypass), and
`#308`/`#313`/`#314`/`#315`/`#316` (LOW: cancellation-poll still unbounded — reopened; poller
hardening latents; orphaned Plane-marker hygiene; no audit-chain verify tool; operational polish)**.
`#301` (HIGH) and `#305` (HIGH) remain open under the `phase-1` label — out of this round's
`phase-2` verification mandate but still real blockers to Phase 3. `#134` (CRITICAL, phase-1) is
also still open — this file previously claimed it fixed; it is not (see above).
**Phase 2 is not gate-clean: `#309` and `#311` are open, both `phase-2`-labeled, one CRITICAL and one
HIGH.** The Controller-side recovery and intake hardening from this round's batch is genuinely solid
(5 of 6 closed issues independently reproduced fixed, plus real concurrency wins like the sender-quota
race going from 40/40-reproducible to 0/40 post-fix) — but the policy-parser bypass class
(`#134`→`#140`/`#141`→`#309`/`#310`) keeps recurring in the same shape ("enumerate the safe flag
forms, miss one"), and this round is the fourth time it's produced a live RCE-class finding.

Phase 1 architectural summary: command validation uses an explicit `argv[0]` allowlist plus
per-binary dangerous-construct checks. Known-resolved bypass classes include wrapper/interpreter
smuggling, forbidden-path text scanning, destructive flags on allowlisted binaries,
command-execution primitives such as `git -c`, `tar --to-command`, `find -exec`, and `sed` `s///e`,
container escape flags, and removal of unauditable network/package-manager tools (`curl`, `wget`,
`apt`, `apt-get`, `dpkg`).

Phase 2 architectural summary: real Plane CE HTTP client + authenticated webhook receiver with
workspace-member actor resolution + allowlist; Controller→Plane projection service wired into
`ApprovalService.approve()`, now serialized per-task via a dedicated advisory lock (`#287`) held
across the outbound call, released before/after with commits scoped to keep the audit-tip lock
(`#280`/`#283`) database-only; a macro-agent-facing HTTP client wired to a self-declared Python
stand-in service (real `macro-agent` package deferred to Phase 3 — see
`decisions/ADR-002-macro-agent-stub-vs-package.md`), now validating every `POST /runs` response via
a Pydantic schema (`#285`); an opentasks DAG materializer reading live Plane dependencies with
guarded cycle detection, size cap, and concurrent fetching; a reconciliation service/CLI that reads
Controller DB state and can fix Plane state divergences with staleness checks, now sharing the same
task-scoped projection lock as the approval path (`#287`); authenticated intake adapters
(Telegram/Email/generic) using shared secrets or HMAC signatures, with body-size caps, creating
HTML-escaped Plane drafts, and a Telegram-approval path now authenticated the same way the CLI is
(`#284`); verification failure feedback to macro-agent and terminal alerting; optional OPA policy
backend that runs only after the embedded PolicyEngine passes and receives a minimized, optionally
   bearer-token-authenticated input document (the embedded-engine-authoritative guarantee live-proven
   this round, with the Rego policy brought back to parity and covered by the latest 34-case OPA
   suite). **Round 17 added**: durable pending-Plane-projection audit markers before every outbound
   Plane call (approval projection, task creation, CLI retry, reconciliation state-fix, verification
   alert — `#298`); a dedicated execution-handoff recovery poller for interrupted approval/
   execution-start/verification-retry/cancellation handoffs (`0b4d5b4`, still has hardening gaps —
   `#311`/`#313`); server-side Plane trace lookup replacing the full-project scan (`#297`); intake
   admission-accounting concurrency hardening for sender-quota races and stale-rate-limit-bucket
   clobbering (`6a98ad7`, both live-reproduced and closed); and closure of two of three
   `#134`-shaped policy-parser bypass classes (`#140`/`#141`, via `4b941d3`) — the third, `#134`
   itself, remains open, and a new fourth instance (`#309`, git config subcommand form) was found.

Test status (2026-09-03): **535 passed / 24 skipped** on SQLite, **557 passed / 2 skipped** on
PostgreSQL, OPA **34/34**, `ruff` clean, `mypy governance_controller` clean (69 source files) — all
independently re-run by the reviewer this round, not taken from opencode's own claim.
`macro_agent_service`
has **10 passed**, `ruff`/`mypy` clean — all independently re-run and confirmed by the reviewer, not
just taken from opencode's own claim. The two live Plane contract tests are skipped because
`GC_PLANE_API_TOKEN`, `GC_PLANE_WORKSPACE_SLUG`, and `GC_PLANE_PROJECT_ID` are not configured in
this environment. **CI is green** (`.github/workflows/ci.yml`, added by `#223`, had failed on all 13
runs since 2026-08-26 until Round 11's CI-infra fix) — confirmed via `gh run list`, current HEAD's
run included. Green tests plus a self-declared "gate-clean" are still not sufficient evidence of
correctness in this project — none of round 8's through round 17's findings, including every
CRITICAL found across those rounds and Round 17's own `#309` and its reopening of `#308`, were caught
by the test suite before their respective fixes/findings landed; they required killing a live
process, adversarially re-reviewing the immediately preceding round's own fix, throwing genuinely
concurrent real HTTP/DB/multi-process load at a live server or real Postgres, running the full real
pipeline end to end against real services, comparing a "fixed" query's `EXPLAIN ANALYZE` at a scale
two orders of magnitude past its own regression test, or simply trying to actually exercise a
documented workflow or a real (not mocked) network boundary that every existing test's fixture shape
happened to sidestep. Round 17 specifically re-learned that **a closed GitHub issue with a named
"regression test" commit is not proof either** — `#308`'s regression test passed identically against
both the pre-fix and (claimed) post-fix commit because no code actually changed between them; only
running the query at realistic scale, not just reading the diff or running the named test, surfaced
that.

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

**Current status (2026-09-03): Phase 2 is NOT gate-clean.** Round 17 closed 5 of 6 targeted
`phase-2` issues for real (`#256`, `#297`, `#298`, `#299`, `#300` — independently live-reproduced
fixed), reopened the 6th (`#308` — its "fix" never changed the code), and the fresh-angle pass found
`#309` (CRITICAL, live RCE) and `#311` (HIGH, unbounded retry) newly open under `phase-2`, plus
6 more MEDIUM/LOW gaps (`#310`, `#312`-`#316`). All 10 Phase 2 SDD tasks landed in `main`
between commits `7c0bd5c` and `4715c22`. Seventeen review
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
codebase, and racing the CLI against live HTTP API traffic for the first time, `#277`-`#279`; this
round also did a one-time regression-test backfill for 12 previously-untested severity:high/critical
fixes from rounds 8-13), Round 14 (verification of round 13's fixes, a regression-TEST-QUALITY audit
per the new Regression Coverage Policy, a live end-to-end check of the `reconcile` CLI command, a
`macro_agent_service`-specific `RISK-16`/`RISK-19` sweep, and a real-concurrency stress test of the
audit log's own hash chain, `#280`-`#282`), Round 15 (verification of round 14's fixes, an
adversarial review of `#280`'s OWN fix for a new regression, real HTTP-boundary testing against the
live `macro_agent_service` process, a systematic sweep for other tip-lookup serialization patterns,
and a replay/security audit of every intake/webhook adapter, `#283`-`#286`). **Round 16 (this round)
was the largest single batch yet: opencode landed 21 commits closing not just the 4 issues the
reviewer filed (`#283`-`#286`) but 10 more it found and filed itself (`#287`-`#296`), then declared
Phase 2 "gate-clean" in its own docs edit — unverified. The reviewer ran 8 parallel live-verification
agents (one per issue cluster, plus a systematic sweep of this round's own new code for `RISK-16`/
`RISK-19`/the-`#283`-pattern, a full real end-to-end task-lifecycle pipeline including a genuine
three-way-concurrent scenario across real Postgres sessions/processes, and dedicated fresh looks at
the previously-stale OPA and intake-rate-limiting areas) before trusting any of it. All 14 issues
(`#283`-`#296`) are CONFIRMED genuinely closed — independently re-reproduced, not diff-read — but the
fresh-angle work found 4 more real gaps: `#297` (MEDIUM, unbounded per-task-creation Plane-issue
scan, architecturally the same "new fix, new cost" shape as `#283`), `#300` (MEDIUM, intake's
rate-limiter and duplicate-guard interact badly and can starve legitimate submissions), `#298` (LOW,
a narrow crash-timing-only audit-trail gap in `#283`'s own fix), and `#299` (LOW, the OPA Rego policy
has drifted behind the embedded engine across 16 rounds of the latter's bypass-closing fixes — not
exploitable, the embedded-engine-authoritative guarantee was live-proven to hold). By this project's
own established definition, Round 16 genuinely reached zero open `severity:critical`/`severity:high`
— a real milestone.** **Round 17 broke it again, within one round.** opencode closed all 6
`phase-2` issues open after Round 16 (`#256`, `#297`-`#300`, `#308`) across 15 commits. The reviewer
ran 10 parallel live-verification agents (one per closed issue/commit cluster, plus a dedicated
`RISK-16`/`RISK-19` sweep of the whole batch, a full real end-to-end pipeline run, and an
unexplored-corners pass). **Result: 5 of 6 genuinely fixed** (`#256`, `#297`, `#298`, `#299`, `#300`
— independently reproduced, including a sender-quota race that reproduced 40/40 on pre-fix code and
0/40 post-fix). **`#308` is not fixed — reopened**: its commit only added a test; a live
`EXPLAIN ANALYZE` at 40k-400k rows shows the query it claims to bound still scales ~linearly. The
fresh-angle work found 8 new issues: `#309` (**CRITICAL** — `git config <key> <value>` subcommand
form bypasses the dangerous-config-key check on both policy backends, live RCE proven), `#311`
(**HIGH** — the new execution-start recovery poller has no backoff, live-reproduced retrying a
permanently-failing recovery forever), `#310`/`#312` (MEDIUM: more tar/find flag gaps; intake's
global rate limiter fully bypassable by rotating attacker-controlled fields once authenticated),
and `#313`-`#316` (LOW: poller hardening latents with no live corruption found; orphaned
Plane-projection-marker hygiene; no audit-hash-chain verify tool; assorted operational polish). The
fresh-angle pass also caught this file's own prior false claim that `#134` was fixed (it wasn't —
live-reproduced still-open) while confirming its siblings `#140`/`#141` genuinely were (closed this
round). **Phase 2 is not gate-clean: `#309` and `#311` are open under `phase-2`, one CRITICAL and one
HIGH.** The pattern of "a fix introducing a new, different-shaped regression" (`#280`→`#283`→`#297`,
now `#299`/`#134`'s policy-parser class → `#309`/`#310`) keeps recurring — the next round should
keep adversarially reviewing every fix, including previously-"fixed" ones, not just confirm each
closes its own reported bug.**

## Immediate Next Step: Fix `#309` (CRITICAL) and `#311` (HIGH), Then Reassess Phase 2

`#309` (git config subcommand-form policy bypass, live RCE) and `#311` (execution-start recovery
poller retries forever with no backoff) are the two `phase-2`-labeled blockers to gate-clean status
and should be fixed first — `#309` especially, given its live-proven RCE shape matches this
project's CRITICAL bar exactly. `#308` needs an actual query-level fix this time (not another
test-only commit) verified at realistic scale (tens of thousands of rows), not just against its
53-row regression test. `#301` (phase-1) requires a macro-agent API contract that makes `POST /runs`
idempotent by `controller_execution_id` or provides a lookup endpoint after response loss — still
blocked by the Phase 2 constraint against modifying macro-agent internals; two Controller-side
refinements were added to its issue this round (exception-type classification, and reusing the
original attempt's `Execution.id` on retry so a future macro-agent idempotency key would actually
help). Rerun the live critical/high issue queries before any Phase 3 work, and re-verify each
`#310`/`#312`-`#316` fix the same way this round verified `#256`/`#297`-`#300` — live reproduction,
not diff-trust.

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
- GitHub issue `#309` (CRITICAL): `git config <key> <value>` subcommand form bypasses the
  dangerous-config-key check on both policy backends — live RCE, blocks gate-clean.
- GitHub issue `#311` (HIGH): execution-start recovery poller has no backoff, retries a permanently
  failing recovery forever — blocks gate-clean.
- GitHub issue `#301`: response-loss recovery can create duplicate/orphaned macro-agent runs.
- GitHub issue `#308` (reopened): cancellation-recovery poll query still scales ~linearly with audit
  history at realistic scale — the prior "fix" only added a test, no code changed.
- GitHub issue `#134`: forbidden-path command scanning still misses `--directory=`/`-C`-style
  option-glued path arguments — a live, full-stack-reproduced bypass, not yet fixed despite prior
  docs claims.
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
