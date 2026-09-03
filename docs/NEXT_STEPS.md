# Next Steps

## Current State

**Current status (2026-09-03): Round 18 — Phase 2 is not gate-clean, and worse than Round 17 left
it.** opencode closed all 9 `phase-2` issues Round 17 left open (`#308`-`#316`) across 5 pushed
commits (`6ff4674`-`37482c7`). The reviewer ran 9 parallel live-verification agents — one per
closed-issue cluster, plus 3 fresh angles (a `RISK-16`/`RISK-19` sweep of the batch, a round-2
adversarial policy-fuzzing pass explicitly hunting for a fifth instance of the recurring
"enumerate-the-safe-forms, miss one" bypass class, and a full live e2e run plus unexplored corners).
**Result: only 5 of 9 closed issues hold up as genuinely, fully fixed** (`#309`, `#310`, `#312`,
`#315`, `#316` items 3-5). **`#308` was closed a third time and is reopened a second time** — its
new fix adds a real index and `SKIP LOCKED` (genuine progress) but still scales ~linearly at
realistic history size (677ms→737ms at 400k rows via a disk-sorting `Merge Left Join`, not the
claimed bounded cost), and its regression test again doesn't differentiate pre-fix from post-fix
code — the second time in a row this specific pattern has recurred for this specific issue.
**`#311`/`#313`/`#314` are each partially fixed**: the backoff/retryable classification, CAS-gating,
and orphaned-marker sweeper mechanisms are all real and independently verified, but each has at
least one live-reproduced residual gap (see below). **`#316` item 2 (OPA docker-compose) actively
regressed**: the shipped image (`0.68.0`) cannot even parse this project's own `governance.rego`
(128 parse errors) — the service as shipped cannot serve real policy at all, on top of two smaller
gaps (no `profiles:` gate, broken healthcheck). The fresh-angle work found **10 new issues**,
including **3 more CRITICAL live RCE findings in the policy-parser family** — this is now the FIFTH
consecutive round to find a new instance of the "enumerate the safe command/flag forms, miss one"
defect class (`#134`→`#140`/`#141`→`#309`/`#310`→this round's `#317`/`#318`/`#319`) — plus **2 more
HIGH findings** in the new recovery-poller machinery, and confirmed that this round's own `#313` fix
**worsened `#305`** (a phase-1 issue) into a permanently-unretryable stuck task. As of Round 18, the
`phase-2`-labeled open set has grown from 0 (Round 16's genuine milestone) to 12. Query the live
issue list before trusting anything else in this file — this file has now been wrong about
"gate-clean" or "this issue is fixed" often enough that the query, not the narrative, is the source
of truth:

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
```

As of Round 18 (2026-09-03), the `phase-2`-labeled open set is **12 issues**: `#317`/`#318`/`#319`/`#320`
(**CRITICAL** — three live-RCE policy-parser bypasses in the git-config/sed family, plus a
completely non-functional OPA docker-compose deliverable), `#321`/`#322` (**HIGH** — two independent
recovery-poller coordination/logic bugs, one of which silently drops the human-notification safety
net for terminal task failures), `#323`/`#324` (**MEDIUM** — overlapping-poller-pass dedup still not
fixed; a new TOCTOU race in the per-IP intake limiter), and `#308`/`#325`/`#326`/`#327` (**LOW** —
cancellation-poll still not bounded at scale, reopened a second time; a missed sweeper operation
type plus a commit-message accuracy gap; `gc verify-audit`'s unbounded memory use; two small
usability nits). Additionally, `#305` (phase-1, HIGH) was worsened by this round's own `#313` fix —
see its issue comment — and `#134`/`#301` (phase-1) remain open, unaffected by this round.
**Phase 2 is further from gate-clean than at any point since Round 15.**

Round 17's own summary (superseded, kept for continuity): opencode closed all 6 open
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
   **Round 18 added**: a real index + `SKIP LOCKED` for cancellation-poll cost (`#308`, still not
   sufficient — reopened again); backoff/retryable classification for execution-start recovery
   (`#311`, partially real — see `#325`); a `plane_projection_pending` orphan sweeper (`#314`,
   partially real — inverted logic for terminal-failure alerts found this round, `#322`); a
   per-IP intake budget independent of sender rotation (`#312`, closed, but with a new TOCTOU race
   found this round, `#324`); `gc verify-audit` and `gc approve --idempotency-key` (`#315`/`#316`
   item 1, both genuinely closed); and closure of the `git config`-subcommand and `tar`/`find` flag
   gaps (`#309`/`#310`, genuinely closed) — immediately followed by **three more instances of the
   same bypass class** found this round (`#317` `--config-env=`, `#318` direct `.git/config` writes
   via non-git commands, `#319` sed `w`/`W`), now five consecutive rounds finding a new instance.

Test status (2026-09-03): **558 passed / 26 skipped** on SQLite, **582 passed / 2 skipped** on
PostgreSQL, OPA **38/38**, `ruff` clean — all independently re-run by the reviewer this round, not
taken from opencode's own claim. **`mypy governance_controller` is NOT clean**: `cli.py:151` has a
genuine (if trivial) type error in the new `verify_audit` command's `order_by(AuditLog.id)` call —
contradicts what would otherwise be claimed here; not filed as a separate issue given its triviality,
but noted so this file doesn't repeat the same "trust the claim" mistake at a smaller scale.
`macro_agent_service` has **10 passed**, `ruff`/`mypy` clean. The two live Plane contract tests are
skipped because `GC_PLANE_API_TOKEN`, `GC_PLANE_WORKSPACE_SLUG`, and `GC_PLANE_PROJECT_ID` are not
configured in this environment. **CI is green** (`.github/workflows/ci.yml`, added by `#223`, had
failed on all 13 runs since 2026-08-26 until Round 11's CI-infra fix) — confirmed via `gh run list`,
current HEAD's run included. Green tests plus a self-declared "gate-clean" are still not sufficient
evidence of correctness in this project — none of round 8's through round 18's findings, including
every CRITICAL found across those rounds, Round 17's `#309` and its reopening of `#308`, or Round
18's four new CRITICALs and `#308`'s second reopening, were caught by the test suite before their
respective fixes/findings landed; they required killing a live process, adversarially re-reviewing
the immediately preceding round's own fix, throwing genuinely concurrent real HTTP/DB/multi-process
load at a live server or real Postgres, running the full real pipeline end to end against real
services, comparing a "fixed" query's `EXPLAIN ANALYZE` at a scale two orders of magnitude past its
own regression test, or simply trying to actually exercise a documented workflow or a real (not
mocked) network boundary that every existing test's fixture shape happened to sidestep. Round 17
first learned that **a closed GitHub issue with a named "regression test" commit is not proof
either** — `#308`'s round-17 regression test passed identically against both the pre-fix and
(claimed) post-fix commit because no code actually changed between them. **Round 18 re-learned the
same lesson a second time on the same issue**: `#308`'s round-18 fix DID change the query (a real
index, real `SKIP LOCKED`) and its regression test genuinely differs from round 17's, yet the test
still doesn't reproduce the actual failure mode — it seeds rows that never enter the query's driving
join at all, passing identically on pre-fix and post-fix code once again. Two rounds in a row, two
different regression tests, the same defect class going uncaught both times: a test that runs and
passes is not evidence it exercises the code path it claims to.

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

**Current status (2026-09-03): Phase 2 is NOT gate-clean — and further from it than at any point
since Round 15.** Round 18 closed 5 of 9 targeted `phase-2` issues for real (`#309`, `#310`, `#312`,
`#315`, `#316` items 3-5), left `#311`/`#313`/`#314` each only partially fixed (real mechanisms,
live-reproduced residual gaps), reopened `#308` a second time (a real index + `SKIP LOCKED` this
time, still not bounded at scale), and found the shipped OPA docker-compose service (`#316` item 2)
cannot even parse this project's own policy file. The fresh-angle pass found 4 new CRITICALs
(`#317`-`#320` — three more live RCEs in the policy-parser family, plus the OPA version mismatch)
and 2 new HIGHs (`#321`/`#322`) in the recovery-poller machinery, on top of confirming this round's
own `#313` fix worsened the phase-1 `#305` into a permanently-unretryable stuck task. All 10 Phase 2
SDD tasks landed in `main` between commits `7c0bd5c` and `4715c22`. Eighteen review
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
closes its own reported bug.** **Round 18 confirms that advice was correct and didn't go far
enough.** opencode closed all 9 `phase-2` issues Round 17 left open across 5 commits
(`6ff4674`-`37482c7`). 9 parallel live-verification agents plus 3 fresh angles (a `RISK-16`/`RISK-19`
sweep, a round-2 adversarial policy-fuzzing pass, a full e2e run) found: **5 of 9 genuinely fixed**
(`#309`, `#310`, `#312`, `#315`, `#316` items 3-5 — including a live-reproduced sender-quota-style
win: the original `#312` bypass went from 25/25-succeeding to exactly-bounded post-fix). **`#308`
closed a third time, reopened a second time**: the new fix adds a real Postgres index and
`FOR UPDATE SKIP LOCKED` — genuine engineering progress over round 17's test-only non-fix — but a
live `EXPLAIN ANALYZE` at 400k rows still shows the planner falling back to a disk-sorting
`Merge Left Join` (677-737ms, ~linear with history size), and the new regression test again doesn't
exercise the actual failure mode (seeds rows that never enter the query's join). **`#311`/`#313`/
`#314` each partially fixed**: `#311`'s retryable/backoff classification is real and live-verified,
but the "exponential backoff" the commit message claims is a flat 1-minute constant, and a genuine
macro-agent-start failure during recovery is never actually retried a second time at all (intentional
per its own test, but message-inaccurate — `#325`); `#313`'s CAS-gating fix for the exception-handler
write is real and live-differentiated against a genuine 3-writer race, but its own consequence is
that CAS-loss now permanently strands the execution row in `RUNNING` with no poller able to reap it
— worsening the phase-1 `#305` into a task that can never retry successfully again; `#313`'s
overlapping-poller-pass lock is NOT actually effective — `FOR UPDATE SKIP LOCKED` is released by each
individual per-marker `commit()` inside the processing loop, and a live forced interleave
deterministically reproduces duplicate audit rows and duplicate `executor.cancel()` calls on every
run (`#323`); `#314`'s orphaned-marker sweeper is real and live-verified for the `update_state`
operation type, but is missing the `reconciliation_state_fix` operation entirely (one of the four
call sites `#314` originally named) and has inverted resolve logic for `terminal_failure_alert` that
silently marks undelivered human-notification alerts as resolved (**HIGH**, `#322`) — the exact
governance guarantee this project exists to provide. `#316` item 2's OPA docker-compose service ships
`openpolicyagent/opa:0.68.0`, which cannot parse this project's own `governance.rego` at all (128
parse errors; the file needs `future.keywords.contains`, which CI's separately-pinned `1.19.1`
doesn't require) — a verified one-line fix exists but wasn't applied — compounded by a missing
`profiles:` gate and a healthcheck that can never pass on that image (**CRITICAL**, `#320`). The
fresh-angle work's most consequential result: **a dedicated round-2 adversarial policy-fuzzing pass
found THREE more live-RCE instances of the same recurring bypass class in one sitting** —
`git --config-env=<key>=<envvar>` (`#317`, single-command RCE, no two-command chain even needed),
writing `.git/config` directly via allowlisted non-git commands like `cp`/`tar -x` (`#318`, live RCE
via `cp`+`git fetch`), and GNU sed's `w`/`W` commands as an unblocked arbitrary-file-write primitive
that also evades the forbidden-path scanner (`#319`, live-proven overwrite of a file inside a
declared-forbidden directory). This is now the FIFTH consecutive round to find a new instance of
"enumerate the safe command/flag forms, miss one" (`#134`→`#140`/`#141`→`#309`/`#310`→`#317`/`#318`/
`#319`). A separate RISK-16/RISK-19 sweep found two more live-reproduced bugs unrelated to the
policy-parser family: the `terminal_failure_alert` sweeper inversion above, and a TOCTOU race in the
new per-IP intake limiter (`#312`'s own fix) caused by invoking an in-memory check-then-append
closure from a FastAPI plain-`def` dependency, which Starlette dispatches via a real OS threadpool
rather than the event loop the closure's shape assumes — live-reproduced 2x oversell under genuine
thread concurrency (**MEDIUM**, `#324`). **Phase 2's `phase-2`-labeled open set went from 0 (Round
16's genuine milestone, lasting less than 3 days) to 12 (Round 18) via two intervening rounds that
each closed everything they were asked to close and each introduced or uncovered more than they
closed.** The lesson repeats, sharper each time: closing every issue in a batch is not evidence
Phase 2 is healthier than before the batch — only independent, adversarial, live re-verification of
the same code from a fresh angle each round has ever actually told this project whether it's
converging or just moving.**

## Immediate Next Step: Fix the Four New CRITICALs (`#317`-`#320`) First, Then the Two HIGHs, Then Reassess

`#317` (`--config-env=` bypass), `#318` (direct `.git/config` write via non-git commands), and `#319`
(sed `w`/`W` unblocked write primitive) are three live-RCE-class findings — fix all three together as
one pass over the policy-parser's whole approach, not as three independent patches, given this is now
the fifth consecutive round finding a new instance of the same "enumerate and miss one" shape
(`#134`→`#140`/`#141`→`#309`/`#310`→`#317`/`#318`/`#319`). Seriously consider whether continuing to
patch individual flag/subcommand forms is the right strategy at this point, versus a structurally
different approach (e.g. treating `.git/`, and any config file a later command reads, as always
outside the writable scope of completion-contract checks, rather than enumerating which commands are
allowed to touch it). `#320` (OPA docker-compose can't parse the real policy) has a verified one-line
fix (`import future.keywords.contains`) plus two smaller sub-fixes (profiles gate, healthcheck) —
should be quick. Then `#321`/`#322` (HIGH: recovery-poller coordination gap; inverted sweeper logic
silently dropping human-notification alerts) — `#322` especially, since it defeats this project's
core governance purpose for the specific case it's supposed to guarantee. `#308` needs an actual
bounded-cost fix this time verified at realistic scale via `EXPLAIN ANALYZE` at 100k+ rows (not just
a query that runs faster than before, and not just its own named regression test, which two rounds in
a row has failed to exercise the real failure mode) — an index on `(event_type, id)` alone was not
sufficient; the query itself likely needs restructuring so the planner can't fall back to a full
disk-sort merge join. `#323` (overlapping-poller dedup) and `#324` (per-IP intake TOCTOU) are MEDIUM
concurrency bugs needing a genuinely concurrent regression test each, matching the live reproduction
techniques already documented in their issues. `#305` (phase-1, worsened this round) needs a third
option beyond "write unconditionally" vs. "never write on CAS loss" for `_start_retry_execution`'s
exception handler — finalize the orphaned execution to a distinct terminal state that's excluded from
`active_execution_exists` without falsely claiming to have won the task transition. `#301` (phase-1)
still requires a macro-agent API contract change, out of Phase 2's scope. Rerun the live critical/high
issue queries before any Phase 3 work, and re-verify every fix in this batch the same way this round
verified the round-17 batch — live reproduction, not diff-trust, and specifically re-run any "fixed"
query's `EXPLAIN ANALYZE` at a scale meaningfully larger than its own regression test before trusting
it — that specific gap has now let the same issue (`#308`) go uncaught twice in a row.

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
- GitHub issues `#317`/`#318`/`#319` (CRITICAL): three more live-RCE instances of the recurring
  policy-parser bypass class (`--config-env=`; direct `.git/config` writes via non-git commands;
  sed `w`/`W`) — block gate-clean, the fifth consecutive round finding a new instance of this shape.
- GitHub issue `#320` (CRITICAL): OPA docker-compose service ships an OPA version that cannot parse
  this project's own `governance.rego` at all — verified one-line fix exists, not yet applied.
- GitHub issue `#321` (HIGH): two crash-recovery pollers don't share terminal-outcome state — a
  crash correctly resolved by one gets silently reopened and re-attempted by the other.
- GitHub issue `#322` (HIGH): the Plane-projection-marker sweeper silently marks undelivered
  terminal-failure human-notification alerts as resolved — defeats this project's governance purpose
  for the exact case it's meant to guarantee.
- GitHub issue `#308` (reopened a second time): cancellation-recovery poll still scales ~linearly at
  realistic history size despite a real index + `SKIP LOCKED` this round — two fixes in a row for
  this issue have shipped with a regression test that doesn't exercise the actual failure mode.
- GitHub issue `#305` (phase-1, worsened this round): CAS-loss in verification retry now leaves an
  execution row permanently stranded in `RUNNING`, unreachable by any poller, and the task can never
  successfully retry again through this path — a side effect of this round's own `#313` fix.
- GitHub issue `#301`: response-loss recovery can create duplicate/orphaned macro-agent runs.
- GitHub issue `#134`: forbidden-path command scanning still misses `--directory=`/`-C`-style
  option-glued path arguments — a live, full-stack-reproduced bypass, not yet fixed despite prior
  docs claims, unaffected by this round.
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
