# Next Steps

## Current State

**Current status (2026-09-02): this file's 2026-08-31 gate-clean narrative is historical and
superseded.** The current hardening pass fixes `#134`, `#140`, `#141`, `#256`, `#299`, `#300`, and
`#302`-`#307` in the working tree and adds recovery-marker, dry-run, policy-bypass, and PostgreSQL
single-flight coverage. `#297` and `#308` remain open performance follow-ups.
Independent verification found one new HIGH blocker, `#301`: the macro-agent `/runs` contract is
not idempotent when the Controller loses an accepted response. Since this requires a macro-agent
API/internal change prohibited by the current Phase 2 plan, **Phase 2 must not be declared gate-clean
until that contract is resolved or explicitly re-scoped by a human.** Do not trust a "gate-clean"
claim in this file's history — it has been declared prematurely at least three times before (each
by the reviewer, later found wrong). As of 2026-08-31, sixteen review rounds have run. Rounds 1-15 (`#154`-`#286`) landed and
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

After the current change set is pushed, the expected open set is **4 issues: `#301` (HIGH,
macro-agent start idempotency), `#297` (MEDIUM, Plane issue-scan cost), `#298` (LOW, pending
Plane-projection auditability), and `#308` (LOW, cancellation-history scan cost)**. The working-tree
fixes for `#134`, `#140`, `#141`, `#256`, `#299`, `#300`, and `#302`-`#307` must be confirmed closed
after push.
**Phase 2 is not gate-clean while `#301` remains open.** The Controller-side recovery and policy
hardening is verified, but the macro-agent API contract remains an explicit blocker.

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
   suite).

Test status (2026-09-02): **534 passed / 23 skipped** on SQLite, **555 passed / 2 skipped** on
PostgreSQL, OPA **34/34**, `ruff` clean, `mypy governance_controller` clean (69 source files);
`macro_agent_service`
has **10 passed**, `ruff`/`mypy` clean — all independently re-run and confirmed by the reviewer, not
just taken from opencode's own claim. The two live Plane contract tests are skipped because
`GC_PLANE_API_TOKEN`, `GC_PLANE_WORKSPACE_SLUG`, and `GC_PLANE_PROJECT_ID` are not configured in
this environment. **CI is green** (`.github/workflows/ci.yml`, added by `#223`, had failed on all 13
runs since 2026-08-26 until Round 11's CI-infra fix) — confirmed via `gh run list`, current HEAD's
run included. Green tests plus a self-declared "gate-clean" are still not sufficient evidence of
correctness in this project — none of round 8's through round 16's findings, including all six
CRITICALs found across those rounds and this round's own `#297`-`#300`, were caught by the test
suite before their respective fixes/findings landed; they required killing a live process,
adversarially re-reviewing the immediately preceding round's own fix, throwing genuinely concurrent
real HTTP/DB/multi-process load at a live server or real Postgres, running the full real pipeline
end to end against real services, or simply trying to actually exercise a documented workflow or a
real (not mocked) network boundary that every existing test's fixture shape happened to sidestep.

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

**Current status (2026-09-02): Controller-side recovery, intake, and policy hardening is implemented
and freshly verified, but Phase 2 is NOT gate-clean because HIGH issue `#301` remains open. MEDIUM
issue `#297` and LOW issue `#298` also remain open; fixes for `#256`, `#299`, `#300`, and `#302`-`#307`
are in the current change set and require push/issue-status confirmation.** All 10 Phase 2 SDD tasks landed in `main`
between commits `7c0bd5c` and `4715c22`. Sixteen review
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
exploitable, the embedded-engine-authoritative guarantee was live-proven to hold).** **By this
project's own established definition — zero open `severity:critical`/`severity:high` — Phase 2 IS
genuinely gate-clean as of this round, for the first time surviving independent re-verification
rather than being declared and later found wrong. This is a real milestone, not a reason to relax:
4 live-reproduced MEDIUM/LOW gaps remain open, and the exact pattern that's repeated in this
project's history — a fix introducing a new, different-shaped regression (`#280`→`#283`→`#297`) —
just recurred a third time within a single fix's own aftermath (`#283`→`#297`/`#298`), so the next
round should keep adversarially reviewing every fix, not just confirm it closes its reported bug.**

## Immediate Next Step: Resolve the Remaining Blocker, Then Reassess Phase 2

`#301` requires a macro-agent API contract that makes `POST /runs` idempotent by
`controller_execution_id` or provides a lookup endpoint after response loss. This is currently
blocked by the Phase 2 constraint against modifying macro-agent internals. Reassess `#298`'s narrow
pre-call-commit audit gap, confirm `#256`, `#299`, `#300`, and `#302`-`#307` close after the current
commit is pushed, and rerun the live critical/high issue queries before any Phase 3 work. Keep
`#297` open until the Plane API filtering/performance tradeoff is resolved.

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
- GitHub issue `#301`: response-loss recovery can create duplicate/orphaned macro-agent runs.
- GitHub issue `#298`: narrow crash-timing window with no Plane-projection audit row.
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
