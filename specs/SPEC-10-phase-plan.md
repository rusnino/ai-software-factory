# SPEC-10: Phase Plan and Acceptance Criteria

## 10.1 Phase 1 — Core Governance + macro-agent PoC

Goal: prove Governance Controller can authorize and launch macro-agent with at least two distinct harnesses.

### Estimated Effort

- **Governance Controller** is the largest Phase 1 component.
- Estimated: **3.5–5 months** of one senior backend engineer, or **6–9 weeks** with two engineers working in parallel on clearly separated modules (state machine/approvals, Plane adapter, macro-agent integration, Event Bridge, harness provider registry).
- Estimate assumes ~4300–7000 lines of production Python code and a comparable volume of tests.

### Deliverables

1. Governance Controller service (FastAPI + SQLAlchemy + PostgreSQL).
2. State machine and single authoritative `POST /approvals` endpoint.
3. Task Contract v1 and Project Profile v1.
4. Plane adapter interface and in-memory/test-only stub, ready for Phase 2 integration.
5. macro-agent integration (start/status/cancel/collect).
6. Event Bridge (workspace events -> Controller with idempotency and retry).
7. Provider registry for OpenCode and at least one other harness.
8. Heterogeneous team PoC: tiny repo, `/health` endpoint + tests.
9. E2E test: create -> approve -> READY -> execute -> verify -> HUMAN_REVIEW -> DONE.

### Phase 1 Acceptance Criteria

- [x] Controller stores task independently of macro-agent. [^phase1-durability]
- [x] Task cannot execute without durable human approval. [^phase1-durability]
- [x] Controller starts macro-agent only after policy check. [^phase1-policy-bypass]
- [x] Controller correlates its execution ID with macro-agent run/team IDs.
- [ ] At least two distinct agent harnesses participate in one execution. [^phase1-harnesses]
- [ ] OpenCode is tested unless documented ACP blocker. [^phase1-harnesses]
- [x] Per-role harness selection is configuration-driven. [^phase1-harnesses]
- [ ] Required macro-agent MCP tools work from non-Claude harness. [^phase1-mcp]
- [ ] git-cascade worktree/stream flow works. [^phase1-git-cascade]
- [ ] Agent messaging works where topology needs it. [^phase1-messaging]
- [ ] Reviewer can issue a verdict. [^phase1-reviewer]
- [x] Failed verification never produces DONE. [^phase1-verification]
- [x] Human review required before final DONE/merge.
- [x] Phase 1 has no runtime dependency on Plane or Macro UI (E2E acceptance uses direct `POST /approvals` calls, not Plane webhooks).
- [x] Governance logic remains outside macro-agent fork.

[^phase1-harnesses]: Phase 1 PoC registers OpenCode and Claude Code in the provider registry, but real multi-harness execution and per-role selection require macro-agent runtime integration planned for Phase 2.
[^phase1-mcp]: OpenCode ACP/MCP compatibility with macro-agent tools is a documented Phase 1 stop-condition risk; no invasive testing was performed and no blocker was encountered for the stub path.
[^phase1-git-cascade]: The `GitCascadeService` landing stub referenced here was removed as orphaned dead code when GAP-029/030 were closed (`0920b65`) — no landing stub of any kind exists today. Real git worktree/stream flow and cascade landing require git integration deferred to Phase 2/3.
[^phase1-messaging]: Agent messaging is out of scope for the Phase 1 Governance Controller PoC; handled by macro-agent runtime.
[^phase1-reviewer]: Reviewer verdict workflow is deferred to Phase 3 (Semantic Reviewer). Phase 1 enforces HUMAN_REVIEW gate before DONE.
[^phase1-verification]: `VerificationService` is invoked by `EventBridge.handle()` on `landing:completed`, executes `required`/`optional` `Check` commands via real subprocess with real exit-code comparison, and gates `AGENT_REVIEW -> HUMAN_REVIEW` vs `FAILED` on the result. Both the failing and passing paths are covered by automated tests (GAP-006, GAP-021 closed). `GAP-058` (HIGH, commit-before-raise in `verify_and_advance()`) and `GAP-074` (HIGH, `TaskContract.forbidden_paths` silently dropped when a `CompletionContract` is attached) are both `CLOSED` per `reviews/GAPS.md` — `GAP-074`'s fix (`1cb5a44`/`d31ebd4`) was independently live-reproduced as a genuine union-of-both-sources fix in REVIEW-017, not just a diff read. REVIEW-019 found this checklist item's guarantee was threatened by `GAP-077`'s first fix attempt (a `FAILED -> RUNNING` edge added to `StateMachine`'s shared global transition table, letting 6 unrelated event types resurrect a terminally-`FAILED` task) and `GAP-085` (the "retry" never actually re-invoking the macro-agent). REVIEW-020 confirmed both are now genuinely fixed: the transition table no longer contains that edge at all, and the retry path genuinely restarts execution. `GAP-077`'s remaining residual (a replayed `landing:completed` event re-entering the retry path) was fixed and independently verified `CLOSED` in REVIEW-021. `GAP-097` (HIGH) was fixed by `85a1fd8` and tracked as GitHub issue #97; the dedup key for a retry `executor.start()` failure is now committed inside the `finally` block before the exception can propagate, so `get_db()`'s rollback does not discard it. Phase 1 gate is clear on this path.
[^phase1-durability]: `get_db()` commits per-request sessions on success and rolls back on exception (GAP-022 closed). Audit rows for rejected approvals are committed before the rejection response is returned (GAP-044 closed). REVIEW-016 found every `datetime` column was `TIMESTAMP WITHOUT TIME ZONE` while every `utc_now()` call produced a timezone-aware value, which `asyncpg` rejects outright — live-reproduced as `POST /tasks` failing on its very first write against real Postgres. `GAP-078` (CRITICAL, storage didn't work against Postgres at all) and `GAP-079` (HIGH, TOCTOU race silently dropping a task) are both `CLOSED`, independently re-verified against a live Postgres container in REVIEW-017 — checked above on that basis. `GAP-080` (HIGH, a lock held across the live macro-agent call in `_trigger_execution`) went through two fix attempts and is `CLOSED` since REVIEW-020, independently verified with a real lock-timing measurement (0.02s vs. the original ~3.5s block). `GAP-095` (HIGH) was fixed by `85a1fd8` and tracked as GitHub issue #95: `EventBridge.handle()` now commits the `AGENT_REVIEW` transition before `verify_and_advance()`, and `_run_check()` enforces a hard timeout by killing the whole process group (`start_new_session=True` + `os.killpg`) so a shell-forked child cannot outlive the shell. Phase 1 gate is clear on this path.
[^phase1-policy-bypass]: `GAP-057` (CRITICAL, forbidden-path traversal bypass) and `GAP-074` (HIGH, `TaskContract.forbidden_paths` never read by `PolicyEngine`) are both `CLOSED` per `reviews/GAPS.md` — see `[^phase1-verification]`.
[^phase3-docker-sandbox]: Scoped (not started) at ~1.5-3 weeks solo-engineer effort for sandboxing `VerificationService`'s command execution specifically, distinct from macro-agent's own task-execution sandbox. The biggest open decision is the execution-backend architecture — Docker socket mounted into the Controller vs. a dedicated sandbox-executor sidecar vs. delegating to macro-agent's own sandbox — see `docs/research-verification-sandboxing-scope-2026-08-24.md`. Motivated by five consecutive Phase 1 review rounds (issues #107-#150) finding that `policy_engine.py`'s argv-string allowlist/denylist approach keeps discovering new bypasses on different allowlisted binaries; #147 documented this as an inherent limitation of argv-level command validation, not a fixable bug.

### Stop Condition

If OpenCode/Codex cannot receive required macro-agent MCP tools without invasive changes, stop and document blocker.

## 10.2 Phase 2 — Plane UI + Meta Orchestrator + OPA

**Status (2026-09-13): a new engineering team ("Multica") took over via a PR/code-review workflow —
a real quality jump — and genuinely closed the 8x-reopened `#301` saga plus `#359` across 3 merged
PRs, self-catching and fixing a real bug in their own work (`#362`) before this round even started.
Phase 2 is still NOT gate-clean: the review found a genuinely new HIGH clobbering bug in the
just-merged fix itself, plus 3 more findings.** Scope: `git log 5a62384..origin/main` (PRs #360/#361/
#363, 7 commits).

`#301` and `#359` CONFIRMED genuinely fixed: `StuckExecutionPoller` now attempts the same
idempotency-key lookup before failing a grace-window-expired execution (verified via a genuine 3-way
real-Postgres concurrency test — in-process recovery vs. poller recovery vs. each other, exactly one
winner, no orphan); the CAS-loss branch in both recovery helpers now durably queues a confirmed-live
run for cancellation cleanup instead of dropping it. `#362` (found and fixed by the new team
themselves) CONFIRMED genuinely fixed: a benign-race guard that could let a losing recovery attempt
clobber a concurrent winner's committed RUNNING state now returns a proper tri-state so both callers
short-circuit cleanly. New findings: `#364` (HIGH) — that same benign-race guard only recognizes a
winner still in `RUNNING`; a winner that's legitimately advanced further (e.g. to `AGENT_REVIEW`) gets
its execution row clobbered back to FAILED anyway, live-reproduced in both call sites. `#365`
(MEDIUM) — the "double-lookup-failure" gap PR #361 itself honestly flagged as unfixed is confirmed
real and unchanged, since it fails synchronously before any poller cycle could intervene. `#366`
(HIGH, process) — this private Free-plan repo cannot configure branch protection or rulesets at all
(confirmed via the GitHub API), so the new PR workflow has no enforceable gate. `#367` (HIGH) — the
Plane webhook actor-resolution function returns the first display-name match with no uniqueness
check, letting any workspace member impersonate an allow-listed approver's identity by renaming their
own profile. Total open: 4 (`#364`/`#366`/`#367` HIGH, `#365` MEDIUM) — zero CRITICAL.

Round 28 (superseded by the above): `#357`/`#358` both confirmed genuinely fixed; `#301` reopened an
8th time with no new code, a fresh-angle sweep found `#359` inside the recovery machinery itself.

Round 27 (superseded by the above): `#301` reopened a 7th time with no new code at all — closed by
comment only, re-citing the same commit already reviewed and found incomplete. `#156` confirmed
genuinely fixed across 5 rejection paths. `#357`/`#358` first filed.

Round 26 (superseded by the above): `#301` reopened a 6th time (narrowed to 2 specific residual
windows after real architectural progress — the first of six attempts to get the core recovery
mechanism right), `#350`/`#356` confirmed genuinely fixed, `#156` reopened after 15 rounds thought
closed.

Round 25 (superseded by the above): `#301` reopened a 5th time, `#350` a 3rd, each on narrower grounds
each time.

Round 21 (superseded by the above): opencode closed all 5 of round 20's remaining issues same-day;
found `#301` was a false close the first time (classification-only, no dedup mechanism at all) and
found the OPA fail-open shape twice (`#344`, `#345`) the same day `#340` fixed one instance of it.

Round 20 (superseded by the above): scoped via `git log be95854..origin/main` (33 commits) and
live-reproduced all 23 issues opencode closed in it — 5 CRITICAL policy-parser bypasses
(`#140`/`#309`/`#332`/`#333`/`#336`), 5 HIGH (including `#293`'s durable cancellation-claim design,
reviewed and approved this session before implementation), 5 MEDIUM, 8 LOW — all genuinely fixed at
the time, plus the process note that 13 of 23 closing commits were test-only with the real fix
bundled into an earlier commit (substance confirmed real, but a literal Regression Coverage Policy
violation).** Twenty prior review rounds have run. Rounds 1-4
(`#154`-`#221`) fixed 62+ live-reproduced gaps. Round 5 found 13 more, including two in Phase 1 core
code that five rounds of `policy_engine.py`-focused hardening never surfaced. Round 6 fixed both and
found the write-side auth fix had a same-shaped read-side gap, plus a stuck-execution-poller race —
round 6 also shipped `#242`'s fix for `ApprovalService`'s idempotent-duplicate-delivery path, which
round 13 would find had never actually worked. Round 7 found `#244`, a live cross-tenant
`ProjectProfile` poisoning vulnerability. Round 8 found `#253`/`#254`, a Controller process crash
leaving a task permanently stuck. Round 9 found `#262`/`#263`, both introduced by round 8's own fix,
both the "blind write committed regardless of CAS outcome" defect class `RISK-16` tracks. Round 10
found the exact `RISK-16` pattern a THIRD time (`#266`), plus `#267` (HIGH, event-level
authorization). Round 11 found `#271` (CRITICAL) via a different, related defect class: a "stale
identity-mapped re-read instead of the true committed state" (`RISK-19`). Round 12 found the same
staleness pattern a second time (`#275`) plus an intake TOCTOU (`#274`, HIGH). Round 13 found
`RISK-19` a FIFTH time — `#278` (HIGH), revealing round 6's own `#242` fix had silently never worked
— plus `#277` (HIGH, the `approve` CLI command had never once authenticated against any real server)
and a one-time regression-test backfill. Round 14 found this project's worst finding to date: `#280`
(CRITICAL) — the `AuditLog` hash chain silently forked under real concurrent writers. Round 15 found
`#280`'s OWN fix introduced a genuinely NEW regression: `#283` (HIGH), a global advisory lock held
across slow outbound Plane calls, able to stall every audit-log write system-wide — plus `#284`
(HIGH, `TelegramAdapter`'s `/approve` command had the exact same missing-auth bug as `#277`, in a
second code path) and `#285`/`#286` (an unhandled `KeyError` on a malformed macro-agent response, and
an indistinguishable-error-code diagnostic gap).

**Round 16 was the largest single batch in this project's history: opencode landed 21 commits
closing not just the 4 issues the reviewer filed (`#283`-`#286`) but 10 MORE it found and filed
itself (`#287`-`#296`) — new task-scoped Plane-projection locks, idempotent Plane-issue
lookup-by-trace-field, a retry-execution crash-recovery sentinel, Pydantic-validated macro-agent
responses, and CAS-loss run-cancellation/attachment logic — then declared Phase 2 "gate-clean" in
its own docs edit, UNVERIFIED. Given this project's documented history of three prior premature
gate-clean declarations, the reviewer ran the largest verification effort to date: 8 parallel agents
independently live-reproducing each issue cluster (not diff-reading), a systematic sweep of this
round's own new code for `RISK-16`/`RISK-19`/the-`#283`-pattern, a full real end-to-end task
lifecycle including a genuine THREE-way-concurrent scenario across real Postgres sessions/processes,
and fresh looks at two previously-stale areas (OPA, intake rate-limiting). Result: all 14 issues
(`#283`-`#296`) are CONFIRMED genuinely closed, including a re-validated audit hash chain (57 rows
from 3 concurrent OS processes, zero broken links) that independently reconfirms `#280`'s fix holds
under load beyond any single prior round's test. But the fresh-angle work found 4 more real gaps:
`#297` (MEDIUM — `#288`'s idempotent Plane-issue lookup scans every issue in the project on every
task creation, no server-side filter, the same "new fix, new cost" shape as `#283` but narrower),
`#300` (MEDIUM — intake's per-IP rate limiter and per-sender duplicate guard interact badly, letting
ordinary duplicate-retry traffic starve legitimate new submissions with misleading `429`s), `#298`
(LOW — `#283`'s early-commit fix leaves a narrow crash-timing-only window with zero audit trail that
Plane was never told), and `#299` (LOW — the OPA Rego policy had drifted behind the embedded engine
across 16 rounds, though the embedded-engine-authoritative guarantee itself was live-proven to hold:
a real OPA server allowed `sudo whoami`, the full backend correctly denied it without even consulting
OPA).** By this project's own tracked severity bar (zero open
`severity:critical`/`severity:high`), Phase 2 genuinely reached gate-clean at that historical
checkpoint — a real milestone, surviving independent re-verification for the first time.

**Round 17 broke it again within one round.** opencode closed all 6 `phase-2` issues open after
Round 16 (`#256`, `#297`-`#300`, `#308`) across 15 pushed commits. The reviewer ran 10 parallel
live-verification agents — one per closed issue/commit cluster, plus a dedicated `RISK-16`/`RISK-19`
sweep of the whole batch, a full real end-to-end pipeline run, and an unexplored-corners pass
(multi-tenancy, audit-hash-chain tooling, secret rotation, macro-agent-service). **Result: 5 of 6
genuinely fixed** (`#256`, `#297`, `#298`, `#299`, `#300` — independently reproduced; the intake
sender-quota race went from reproducing 40/40 on pre-fix code to 0/40 post-fix). **`#308` is NOT
fixed and was reopened**: its commit (`70f4b92`) added only a test — the query it claims to bound
is byte-identical before and after, and a live `EXPLAIN (ANALYZE, BUFFERS)` at 40k-400k audit rows
shows execution time scaling ~linearly (54ms→760ms), invisible to the existing 53-row test. The
fresh-angle work found 8 new issues: `#309` (**CRITICAL** — `git config <key> <value>`, the plain
subcommand form as opposed to `-c`/`--config`, bypasses the dangerous-git-config-key check on both
policy backends; live RCE proven via `git config core.editor "touch ...; true #"` +
`git commit --amend --allow-empty`, each individually policy-approved), `#311` (**HIGH** — the new
execution-start recovery poller has no backoff or retryable classification, live-reproduced retrying
a permanently-failing recovery on every single poll pass forever), `#310`/`#312` (MEDIUM: more
tar/find flag gaps in the same policy-parser family as `#134`/`#140`/`#141`/`#309`; authenticated
intake fully bypasses the global rate limiter via attacker-controlled `sender`/`source_id`
rotation), and `#313`-`#316` (LOW: three latent, non-corrupting hardening gaps in the new recovery
poller; orphaned Plane-projection markers with no dedicated recovery mechanism; no operational tool
to verify the audit hash chain; assorted operational polish). The fresh-angle pass additionally
caught and corrected `docs/NEXT_STEPS.md`'s prior false claim that `#134` was fixed — a fresh
full-stack live reproduction (`tar --directory=<forbidden> -xf payload.tar`, passing both policy
backends and writing into the declared-forbidden directory) confirms it is not — while independently
re-verifying and closing its two true siblings, `#140`/`#141`.

**Phase 2 is not gate-clean: `#309` (CRITICAL) and `#311` (HIGH) are open under `phase-2`.** The
`#280`→`#283`→`#297` "fix introduces a new, differently-shaped regression" pattern has now also
manifested in the policy-parser family (`#134`→`#140`/`#141`→`#309`/`#310`, the "enumerate the safe
flag forms, miss one" shape recurring a fourth time) and, in `#308`'s case, as a "regression test
commit that isn't actually a fix" — the sharpest instance yet of this project's standing lesson that
a closed issue and a green test are not proof. This project's own history means this status line
should never be trusted without re-running `gh issue list --label phase-2 --state open` first.

**Round 18 closed all 9 `phase-2` issues Round 17 left open** (`#308`-`#316`, commits
`6ff4674`-`37482c7`). 9 parallel live-verification agents plus 3 fresh angles (a `RISK-16`/`RISK-19`
sweep, a round-2 adversarial policy-fuzzing pass specifically hunting for a fifth instance of the
recurring bypass class, a full e2e run) found: **5 of 9 genuinely fixed** (`#309`, `#310`, `#312`,
`#315`, `#316` items 3-5). **`#308` closed a third time, reopened a second time**: a real index +
`FOR UPDATE SKIP LOCKED` this time (genuine progress over round 17's test-only non-fix), but a live
`EXPLAIN ANALYZE` at 400k rows still shows a disk-sorting `Merge Left Join` (677-737ms, ~linear with
history size), and the new regression test again doesn't exercise the actual failure mode — two
rounds in a row, two different tests, the same "test runs green but doesn't reproduce the bug" defect
class going uncaught both times. **`#311`/`#313`/`#314` each partially fixed**: real
backoff/retryable classification, real CAS-gating, and a real orphan sweeper all shipped and were
independently live-verified — but `#311`'s "exponential backoff" is a flat constant and macro-agent
failures are never actually re-attempted (message-inaccurate, not a functional bug — `#325`);
`#313`'s CAS-gating fix worsened the phase-1 `#305` into a permanently-stranded execution row no
poller can reap, and its overlapping-poller-pass lock is NOT effective (`SKIP LOCKED` released by
each per-marker `commit()` inside the loop — deterministically reproduced duplicate audit rows and
duplicate `executor.cancel()` calls — `#323`); `#314`'s sweeper has inverted resolve logic for
`terminal_failure_alert` markers, silently marking undelivered terminal-failure human-notification
alerts as resolved (**HIGH**, `#322`) — defeating this project's governance purpose for exactly the
case it exists to guarantee — and is missing the `reconciliation_state_fix` operation entirely, one
of `#314`'s own four originally-named call sites (`#325`). `#316` item 2's OPA docker-compose service
ships `openpolicyagent/opa:0.68.0`, which cannot parse this project's own `governance.rego` at all
(128 parse errors — the file needs `future.keywords.contains`, which CI's separately-pinned `1.19.1`
doesn't require); a verified one-line fix exists but wasn't shipped (**CRITICAL**, `#320`). **The
fresh-angle work's most consequential result**: a dedicated round-2 adversarial policy-fuzzing pass
found **three more live-RCE instances of the same recurring bypass class in one sitting** —
`git --config-env=<key>=<envvar>` (`#317`, single-command RCE), writing `.git/config` directly via
allowlisted non-git commands like `cp`/`tar -x` (`#318`, live RCE via `cp`+`git fetch`), and GNU
sed's `w`/`W` commands as an unblocked arbitrary-file-write primitive that also evades the
forbidden-path scanner (`#319`) — the FIFTH consecutive round finding a new instance of "enumerate
the safe command/flag forms, miss one" (`#134`→`#140`/`#141`→`#309`/`#310`→`#317`/`#318`/`#319`). A
separate RISK-16/RISK-19 sweep additionally found a TOCTOU race in the new per-IP intake limiter
(**MEDIUM**, `#324`) — an in-memory check-then-append closure invoked from a FastAPI plain-`def`
dependency, which Starlette dispatches via a real OS threadpool rather than the event loop the
closure's atomicity assumes; live-reproduced 2x oversell under genuine thread concurrency.

**Phase 2's `phase-2`-labeled open set went from 0 (Round 16's genuine milestone, lasting under 3
days) to 12 (Round 18) via two intervening rounds that each closed everything asked of them and each
introduced or uncovered more than they closed.** Closing every issue in a batch is not evidence
Phase 2 is converging — only independent, adversarial, live re-verification from a fresh angle each
round has ever told this project whether it's converging or just moving. Fix `#317`/`#318`/`#319`
together as one pass over the policy parser's whole approach (not three independent patches, given
five rounds of the same shape recurring); `#320` has a verified one-line fix; `#321`/`#322` need
real fixes for their coordination/logic gaps; `#308` needs a bounded-cost fix verified at 100k+ rows
via `EXPLAIN ANALYZE`, not just "faster than before."

- [x] Deploy Plane CE. *(local dev instance running; real deployment story not yet exercised)*
- [x] Build Plane adapter for bidirectional sync. *(read side works; projection write side wired
  into `ApprovalService.approve()`; webhook actor resolved via workspace members + allowlist;
  reconciliation state comparison resolves Plane state UUIDs to names.)*
- [ ] Meta Orch integration: OpenCode + BMAD + OpenSpec. *(not started — intake→triage path exists,
  the decomposition/planning engine does not)*
- [x] Intake adapter (Telegram/Email/API). *(Telegram and Email auth use HMAC signatures/shared
  secrets; body-size cap applies.)*
- [x] Idea Ingestion Service.
- [ ] Human Triage queue in Plane. *(drafts land in Plane; no dedicated triage-queue view/workflow
  built beyond that)*
- [x] Optional OPA policy backend. *(embedded PolicyEngine always runs first; OPA is additive only
  and receives a minimized input document with optional bearer-token auth.)*

## 10.3 Phase 3 — Hardening and Runtime Diversity

- Docker sandboxing.[^phase3-docker-sandbox]
- Full harness matrix (Claude Code, Codex, Aider).
- Advanced conflict recovery.
- Project Profiles per repo.
- Semantic Reviewer.
- Better Completion Contract.
- Optional: introduce Temporal inside Execution Orchestration layer for durable long-running execution workflows (not as replacement for Governance Controller); revisit only if proven need exists.

## 10.4 Phase 4 — Production Hardening

- Firecracker/Kata isolation.
- Secret scoping (Vault/Doppler).
- Observability (metrics, alerts).
- Backups and disaster recovery.
- Distributed workers (only if needed).

## 10.5 Explicitly Postponed

- Kubernetes
- Redis unless justified
- Vector DB
- Telegram/Matrix as core communication
- HA before validation
- Dozens of agent profiles
- Nested subagent orchestration
- Windmill as core controller
- macro-inc/macro as PM
- Temporal as Governance Controller core (allowed only in Phase 3+ inside Execution layer)
- Replacing entire Policy Engine with OPA before Phase 2
