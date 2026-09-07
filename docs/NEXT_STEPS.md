# Next Steps

## Current Worktree (2026-09-07)

**Phase 2 remains not gate-clean.** This worktree contains an uncommitted hardening batch for
the policy-parser, OPA, approval/recovery CAS, Plane marker, audit-chain, SQLite, and migration
paths. The latest verification evidence is:

- SQLite controller suite: `790 passed, 45 skipped, 2 xfailed`.
- PostgreSQL controller suite: `828 passed, 7 skipped, 2 xfailed`.
- OPA: `check` passed and `test` reports `68/68`.
- Ruff and mypy: clean; `git diff --check` clean.
- macro-agent service suite: `10 passed` when run from its own uv project.

The latest policy work added: a restricted `uv run` child allowlist; recursive child-path
extraction; Git long-option abbreviation matching; `git --config-env` separate-form handling;
`git config --edit/-e` and `--co` blocking; `git apply --unsafe-paths` and `git clone --template`
blocking; executable git-config key families (`diff.*.command`, `credential.*.helper`,
`submodule.*.update`, `gpg.ssh.*`, `core.alternateRefsCommand`, `difftool/mergetool.*.cmd`);
sed whitespace and `!`-negated address handling; npm tool-specific path extraction; and OPA
parsed-argv validation.

The live issue gate is still open and is the source of truth. `#301` remains an architectural
blocker because exact-once recovery after macro-agent response loss requires a macro-agent
idempotency contract or an outbox, and `#293` documents the SQLite `BEGIN IMMEDIATE` external-I/O
availability risk; both are outside this batch's constraints.

Do not mark Phase 2 complete until the critical/high queries are empty after independent review.

## Historical Round 18 State

**Current status (2026-09-07): Round 19 — methodology changed, coverage widened, still not
gate-clean.** This round's kickoff prompt was redesigned specifically because the prior one relied
on GitHub issue state (`--state open`) as the source of truth for "what needs verifying" — and the
coding agent has repeatedly fixed bugs without transitioning the issue to `Closed` (commits used
`Fixes #N` in parentheses rather than the exact keyword syntax GitHub auto-closes on). **The scope
of this round was instead determined by `git log d561d70..origin/main` — 11 pushed commits — not by
issue labels.** This immediately paid off: 9 of those 11 commits' target issues (`#134`, `#317`-
`#323`, `#325`) were still showing `Open` on GitHub despite genuine, live-verified fixes.

Result of live-reproducing all 11 commits: **10 issues closed by the reviewer** (`#134`, `#317`-
`#325` except `#324` grouped separately, `#320`, `#324` — see the round table below for the exact
mapping), **`#308` genuinely fixed on a 4th attempt** (a structurally different approach — an
indexed boolean flag on `Execution` instead of another patch to the `AuditLog`-anti-join query that
failed twice before — confirmed flat from 40k to 400k rows via live `EXPLAIN ANALYZE`, first round
where the shipped test would have caught the two prior rounds' failure mode), and **two issues that
were already `Closed` on GitHub turned out not to be real fixes** (`#326`, `#328` — both reopened
this round with live evidence). The fresh-angle work found **4 new issues** (`#336`-`#339`,
including a sixth instance of the "enumerate the safe command/flag forms, miss one" bypass class:
`git --config-env <key>=<envvar>`, the space-separated form of the exact flag `#317` closed the
glued form of). A live end-to-end pass also independently reproduced two CRITICALs opencode had
already self-found and filed (`#332`/`#333`, git transport-helper and `zip -T`/`-TT`) — both still
open, confirmed still live on `origin/main`, and part of a large **uncommitted** hardening batch
found sitting in the working tree (preserved via `git stash`, not touched, not verified — see
"Uncommitted work in flight" below).

`phase-2`-labeled open count: **13**. `phase-1`-labeled open count: **11** (unrelated to this
round's scope except where this round's own findings touched a phase-1 issue — `#328`).

Round 18's own summary (superseded, kept for continuity): opencode closed all 9 `phase-2` issues
Round 17 left open (`#308`-`#316`) across 5 pushed
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

As of Round 19 (2026-09-07), the `phase-2`-labeled open set is **13 issues**: `#309`/`#332`/`#333`/
`#336` (**CRITICAL** — `#309` reopened by opencode's own self-review after finding the original
fix only covered the plain-subcommand git-config form, missing a whole family of executable-config
keys, `filter.*.clean`/`diff.*.textconv`/`merge.*.driver`/`core.askPass`/`gpg.program`/etc., live
RCE-confirmed via a git filter; `#332`/`#333` are opencode's own self-found `git ... ext::`
transport-helper and `zip -T`/`-TT` bypasses, independently re-confirmed live on `origin/main` this
round; `#336` is this round's own find, a sixth instance of the "enumerate the safe forms, miss one"
class — `git --config-env <key>=<envvar>`, the space-separated form of the exact flag `#317` closed
the glued form of), `#328`/`#334` (**HIGH** — `#328` reopened this round: its own comment thread
already flagged a second `init_db()`/`create_all()` startup race that was drafted, tested, and never
committed before the issue got closed by an unrelated partial-fix commit, now live-reproduced with
real multi-process concurrency, 21/24 failures; `#334`, opencode self-found, `git.force_push` deny
doesn't constrain actual push flags), `#337`/`#338`/`#339` (**LOW/MEDIUM** — this round's own finds:
a weak regression-test case and a bare-`.` false-positive; a `RISK-19` staleness gap in 4 poller
queries newly consequential after `#321`'s fix; an event-loop-blocking inconsistency in the Telegram
intake path), and `#326`/`#329`/`#330`/`#331`/`#335` (**LOW/MEDIUM**, mostly opencode self-found —
`#326` reopened this round: its streaming fix is genuinely broken against PostgreSQL specifically,
passing only against the SQLite dev backend). **`#308` is genuinely fixed this round on the 4th
attempt** — the first two "fixes" were false positives (round 17: test-only, no code change; round
18: a real index that still scaled linearly at 400k rows); round 19's fix takes a structurally
different approach (an indexed boolean flag on `Execution`, bypassing the `AuditLog` anti-join
entirely) and holds flat from 40k to 400k rows under live `EXPLAIN ANALYZE`.
**Phase 2 remains not gate-clean**, though for the first time in several rounds the CRITICAL/HIGH
count reflects genuinely fresh findings (opencode's own aggressive self-review plus this round's
fresh angles) rather than the same recurring, previously-known gaps recurring again.

Round 18's own summary (superseded, kept for continuity): opencode closed all 9 `phase-2` issues
Round 17 left open (`#308`-`#316`) across 5 pushed commits (`6ff4674`-`37482c7`). The reviewer ran 9
parallel live-verification agents — one per closed-issue cluster, plus 3 fresh angles (a
`RISK-16`/`RISK-19` sweep of the batch, a round-2 adversarial policy-fuzzing pass explicitly hunting
for a fifth instance of the recurring "enumerate-the-safe-forms, miss one" bypass class, and a full
live e2e run plus unexplored corners). By the end of that round, the `phase-2`-labeled open set was
12 issues (`#317`-`#320` CRITICAL, `#321`/`#322` HIGH, `#323`/`#324` MEDIUM, `#308`/`#325`-`#327`
LOW), and `#305` (phase-1, HIGH) had been worsened by that round's own `#313` fix.

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

**Uncommitted work in flight (not part of this round's verification):** the working tree contains a
large uncommitted batch (6,500+ inserted lines, 22 files) implementing a "superpowers"-managed plan
(`docs/superpowers/plans/2026-09-03-phase2-open-issues-hardening.md`) targeting issues `#329`-`#335`
plus a second round of policy-parser hardening for `#309`/`#140` and others — including opencode's
own multi-round self-review (comments on `#309`/`#140` reference "Round-21"/"Round-22"
re-verification against this exact uncommitted tree). It was preserved via `git stash` rather than
committed, verified, or discarded — genuinely not this round's scope per the redesigned kickoff
prompt (git-log-based scope, not issue-state-based), but flagged here because its own draft
`NEXT_STEPS.md` edit claimed test counts (SQLite 790p/45s/2x, PostgreSQL 828p/7s/2x, OPA 68/68) that
do **not** match the clean, actually-committed `origin/main` baseline below — the draft's numbers
include this uncommitted work's own new tests. Do not trust those numbers as a description of what's
shipped until this batch is committed, pushed, and independently re-verified.

Test status (2026-09-07, against clean `origin/main`, `ed1122b`): **590 passed / 31 skipped** on
SQLite, **618 passed / 2 skipped / 1 failed** on PostgreSQL, OPA **42/42**, `ruff` clean (one trivial
import-order nit in `test_opa_compose.py` fixed directly this round, zero semantic risk), `mypy`
clean. **The one Postgres failure is real, not flaky**: `test_verify_audit_streams_without_materializing_rows`
fails deterministically under Postgres (100% of runs) and passes under SQLite (100% of runs) — `#326`
reopened this round, see below. `macro_agent_service` has **10 passed**, `ruff`/`mypy` clean. The two live Plane contract tests are
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
passes is not evidence it exercises the code path it claims to. **Round 19 learned two more variants
of this same family of lesson.** First: `#308` finally broke the streak — not by writing a better
test for the *same* query shape, but by changing the query's *architecture* entirely (an indexed
flag instead of an anti-join), which made the old failure mode structurally unreachable rather than
merely untested-for; sometimes the fix for "the test keeps missing this" is a different data
structure, not a better test. Second, and sharper: **a closed GitHub issue is not proof either, even
independent of test quality** — `#326`/`#328` were both closed by commits that made a *real* fix for
*part* of the problem, while the issue's own comment thread (written by the same coding agent, in
earlier self-review rounds) already documented a second, distinct failure mode that was drafted,
tested, and never committed. The issue got auto-closed by an unrelated commit's `Fixes #N` trailer
before the second half landed. Determining "what's actually done" now requires reading `git log`
against the issue's own comment thread, not just checking whether the GitHub state field says
`Closed`.

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

**Round 19 changed the methodology, not just the findings.** The kickoff prompt was rewritten to
determine scope from `git log`, not `gh issue list --state open` — the prior prompt's implicit
assumption (a fixed issue gets closed) had quietly broken down: opencode's commits increasingly used
`Fixes #N` inside a parenthetical (`"fix: close command policy bypasses (#134 #317 #318 #319)"`)
rather than the exact keyword syntax GitHub auto-closes on, so issue state had been silently drifting
from reality for at least this round's entire batch. Comparing `git log d561d70..origin/main` (11
commits) against `gh issue list --state open --label phase-2` found **9 of 11 commits' target issues
still showing Open** despite genuine fixes. All 11 commits were live-verified with the same
discipline as prior rounds (real Postgres, real HTTP, real concurrent sessions/processes, isolated
worktrees for pre-fix/post-fix differentials): **10 issues closed by the reviewer this round**
(`#134`, `#317`-`#319`, `#320`-`#325` except the two below), and **`#308` finally, genuinely fixed on
its 4th attempt** — this time via an architectural change (an indexed `cancellation_pending` boolean
on `Execution`, bypassing the `AuditLog` anti-join that failed twice before) rather than another
patch to the same query shape, confirmed flat from 40k to 400k rows via live `EXPLAIN ANALYZE` and
the first regression test that would have actually caught the prior two rounds' failure mode.

The sharper lesson this round: **two issues already showing `Closed` on GitHub were not actually
fully fixed**, and their own comment threads already said so. `#326` (`gc verify-audit` streaming)
was closed by a commit whose fix is real for the memory-scaling problem but is broken by a
second-event-loop bug that only manifests against PostgreSQL — confirmed deterministically failing
100% of the time in the Postgres CI lane, passing 100% of the time under SQLite, meaning the feature
is currently non-functional against the production database backend. `#328` (migration idempotency)
was closed by a commit that only fixed half the problem — its own comment thread (written by the
coding agent itself, in an earlier self-review pass) already documented a second startup race in
`init_db()`'s unprotected `create_all()` call, with a fix drafted and locally tested but never
committed, before an unrelated commit's `Fixes #328` trailer closed the issue anyway. Live-reproduced
this round with 8 genuinely separate OS processes: 21 of 24 concurrent-startup attempts crashed.
**A closed issue is not proof of anything, independent of test quality — the issue's own history has
to be read, not just its current state field.**

The fresh-angle work found 4 new issues (`#336`-`#339`), most notably a **sixth** instance of the
"enumerate the safe command/flag forms, miss one" class: `git --config-env <key>=<envvar>` (the
space-separated form of the exact flag `#317` closed the glued form of — real git treats them
identically). A live end-to-end pass also independently re-confirmed, on currently-committed
`origin/main`, two CRITICALs opencode had already self-found and filed during its own review passes
(`#332` git transport-helper `ext::`, `#333` zip `-T`/`-TT`) — both still genuinely open, part of a
large uncommitted hardening batch sitting in the working tree (preserved via `git stash`, explicitly
out of this round's scope, not verified). **Phase 2's `phase-2`-labeled open count is 13** — similar
order of magnitude to Round 18's 12, but this time made up mostly of issues opencode found and filed
against itself through its own increasingly aggressive self-review passes (`#309` reopened, `#328`
reopened, `#329`-`#335`), plus this round's own 4 new finds, rather than the same recurring class
resurfacing untouched.

## Immediate Next Step: Commit and Push the Stashed Batch, Then Re-Verify It — Do Not Trust Its Own Test-Count Claims

The single largest lever right now is not a code fix — it's getting the **uncommitted working-tree
batch** (preserved via `git stash`, 6,500+ lines across 22 files, targeting `#309` reopened,
`#140` reopened, `#329`-`#335`) committed and pushed. Until that happens, none of its claimed fixes
count for anything by this project's own established standard, and its own draft `NEXT_STEPS.md`
edit already overclaims test counts relative to what's actually shipped (see "Test status" above) —
the exact "trust the claim, not the query" mistake this file exists to keep correcting. When it
lands: do **not** re-run this round's git-log-based methodology from the pre-stash HEAD — diff from
the current `origin/main` (`ed1122b`) forward, and treat every one of that batch's own "Round-21"/
"Round-22" self-review comments already sitting on `#309`/`#140` as claims to independently
re-verify, not evidence.

Four newly-filed CRITICALs need fixing next, three of them a continuation of the same policy-parser
class: `#336` (`--config-env` two-token form — the sixth instance; strongly consider whether
continuing to patch individual flag/subcommand forms is the right strategy at this point, versus
canonicalizing all git flag forms — glued, spaced, abbreviated — to one representation before the
dangerous-key check, rather than enumerating each syntax as it's discovered), and the two opencode
already self-found and filed, `#332` (git `ext::`/`fd::` transport-helper RCE) and `#333` (zip
`-T`/`-TT` self-test-command execution) — both independently re-confirmed live on `origin/main` this
round via the real `/approvals` HTTP pipeline, not just a policy-engine unit call. `#328` (reopened,
HIGH) needs the already-drafted `init_db()`/`create_all()` fix committed — per the issue's own
comment thread, this exists and was locally tested, it just never got pushed. `#326` (reopened) needs
`verify_audit()`'s CLI entrypoint fixed to not create a second event loop around the (otherwise
correctly working) streaming call — the streaming/memory fix itself is genuinely good, only the
event-loop bridging around it is broken, and only against Postgres. `#338` (RISK-19 gap in 4 poller
queries) is a good candidate to fix alongside whatever touches `stuck_execution_poller.py` next,
given the established pattern of "narrow but real staleness gap becomes load-bearing once a later fix
depends on the field it's reading." Rerun the live critical/high issue queries before any Phase 3
work — and per this round's own sharpest lesson, cross-check each "closed" issue's own comment
thread, not just its state field, since a closed issue closed by a partial-fix commit is exactly the
failure mode `#326`/`#328` just demonstrated.

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

- **A large uncommitted hardening batch sits in the working tree** (preserved via `git stash`,
  targeting `#309`/`#140` reopened plus `#329`-`#335`) — not this round's scope, but the single
  biggest lever on Phase 2's status right now. Its own draft docs overclaim test counts relative to
  what's actually shipped; do not cite its numbers until it's committed, pushed, and independently
  re-verified.
- macro-agent API stability and `/runs` contract.
- GitHub issue `#336` (CRITICAL, new this round): `git --config-env <key>=<envvar>` two-token form —
  sixth instance of the recurring policy-parser bypass class.
- GitHub issue `#332` (CRITICAL, opencode self-found, independently re-confirmed live this round):
  git `ext::`/`fd::` transport-helper RCE via allowlisted `git push`/`git clone`.
- GitHub issue `#333` (CRITICAL, opencode self-found, independently re-confirmed live this round):
  allowlisted `zip -T`/`-TT` executes an arbitrary shell command.
- GitHub issue `#309` (CRITICAL, reopened by opencode's own self-review): the original
  git-config-key fix only covered the plain-subcommand form — a wider family of executable config
  keys (`filter.*.clean`, `diff.*.textconv`, `merge.*.driver`, `core.askPass`, `gpg.program`, etc.)
  remains open, live RCE-confirmed via a git filter, fix drafted in the uncommitted batch.
- GitHub issue `#328` (HIGH, reopened this round): `init_db()`'s unprotected `create_all()` call
  races under concurrent multi-process startup — 21/24 failures in a live 8-process reproduction. A
  fix was drafted and locally tested per the issue's own comment thread but never committed.
- GitHub issue `#326` (reopened this round): `gc verify-audit`'s streaming fix creates a second
  event loop in its CLI entrypoint, breaking `asyncpg`'s loop-bound connections — deterministically
  fails against PostgreSQL specifically, only passes against the SQLite dev backend.
- GitHub issue `#305` (phase-1, worsened by round 18's own `#313` fix): CAS-loss in verification
  retry leaves an execution row permanently stranded in `RUNNING`, unreachable by any poller — still
  open, unaffected by this round.
- GitHub issue `#301`: response-loss recovery can create duplicate/orphaned macro-agent runs.
- GitHub issue `#140` (reopened by opencode's own self-review, phase-1): a residual old-style tar
  cluster form (`tar vI 'cmd' -c -f archive.tar`) still bypasses the command-execution check — fix
  drafted in the uncommitted batch, not yet committed.
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
