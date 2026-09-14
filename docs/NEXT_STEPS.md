# Next Steps

## Current State (2026-09-14) — Round 30

**Multica closed all 4 of round 29's open issues (`#364`-`#367`) plus 2 test-quality follow-ups
(`#370`, `#372`) it found and fixed itself. 4 of the 6 closes hold up as genuinely fixed. The other
2 don't: one closed issue's fix is real but incomplete (a 4th recurrence of the same test-masking
defect class, in the one test its own closing commit claimed to fix), and the other's fix doesn't
match its own closing comment's claim when checked against the live GitHub API. A fresh-angle pass
across areas no prior round had targeted as their own topic found one new HIGH (self-approval is
live-reproducible today) and one new LOW (an unbounded-nesting 500).** Scope was `git log
f3c1fc1..origin/main` (round 29's own doc commit to current `origin/main`, 9 commits: PRs #368/#369/
#371/#373/#374).

- **`#364` CONFIRMED genuinely fixed**: both `approval_service.py`'s and `verification_service.py`'s
  `_try_recover_orphaned_run` now key the benign-race guard off the `Execution` row's own fresh state
  (`!= FAILED`, via `populate_existing=True`) instead of requiring the winner's `Task.state ==
  RUNNING` — correctly covers every state past RUNNING (`AGENT_REVIEW`, `BLOCKED`, `DONE`), not just
  the one reported. Also fixed a companion RISK-16-shaped ordering bug in the same functions
  (execution-state flush moved to after the Task CAS instead of before). Verified via worktree against
  real Postgres: both new regression tests drive genuine concurrent sessions (one advances the task to
  `AGENT_REVIEW` mid-recovery via `asyncio.Event`-held lookup), fail pre-fix with the exact predicted
  clobbering sequence, pass post-fix.
- **`#365` CONFIRMED genuinely fixed**: a new falsy sentinel (`ORPHAN_LOOKUP_INCONCLUSIVE`) lets both
  recovery callers leave the task untouched on a lookup error instead of immediately failing it;
  `StuckExecutionPoller` gets up to 3 bounded, backed-off retries via a new
  `_orphan_lookup_recovery_status` helper before falling through to the pre-existing terminal-failure
  path. Verified fail-pre/pass-post via worktree against real Postgres; retry-exhaustion behavior
  checked directly (4th poll makes no further lookup call, task correctly still fails) — the original
  single-error permanent-strand harm is closed, a disclosed, honest residual (a *persistent* outage
  still eventually fails) is not a new gap.
- **`#367` CONFIRMED genuinely fixed**: `_resolve_actor_email` now collects all case-insensitive
  matches and fails closed (returns unresolvable) unless exactly one distinct email matches, replacing
  the old first-match logic. Live-reproduced: the shipped test, run against the pre-fix commit, shows
  the actual impersonation happening in the audit log (`actor=admin@example.com` attributed to an
  attacker's request); against the fix, the same webhook gets 403 and the task is untouched. Single
  call site codebase-wide, no duplicate unprotected logic found.
- **`#370` CONFIRMED genuinely fixed**: `test_webhook_rejects_unresolvable_actor` now exercises the
  real resolver and asserts the specific rejection detail, correctly distinguished from the
  neighboring "not authorised" branch it used to conflate.
- **`#372` REOPENED — fix incomplete.** The closing commit (928b915) correctly pinned two of the three
  tests it targeted (added a `list_workspace_members`-was-called assertion so disabling the resolver
  restore makes them fail as it should). But the third, `test_webhook_rejects_actor_not_in_allowed_list`
  — the one test this same commit's message specifically claims to have restored — still isn't pinned:
  disabling the resolver restore leaves it passing, because the `_auth_ok` fixture's fake resolver
  happens to also produce an "actor not authorised" outcome by coincidence, without the real
  resolution/ambiguity logic or `list_workspace_members` ever running. Same defect class as `#370`/
  `#372` itself, 4th recurrence, live-reproduced by disabling the restore and rerunning the suite.
- **`#366` REOPENED — closing comment doesn't match live state.** The close claimed branch protection
  now requires "1 approving review, stale reviews dismissed." The live API right now
  (`GET .../branches/main/protection/required_pull_request_reviews`, checked twice) shows
  `required_approving_review_count: 0`. Everything else claimed does check out live (required status
  checks, `enforce_admins: true`, force-push/deletion disabled) — but with 0 required approvals, a PR
  can still merge to `main` with no one having approved it, which is exactly the gap this issue was
  filed to close. (The repo's private→public flip, the mechanism used to unlock Free-plan branch
  protection, was checked for secret-leak exposure in the same round: clean, no real credential ever
  committed to history.)
- **New finding — `#376` (HIGH)**: self-approval is live-reproducible today, via a path no prior round
  had targeted as its own topic. The existing guard (`approval_service.py:139-140`) checks `actor ==
  task.proposed_by`, but `proposed_by` is an unauthenticated, client-supplied free string accepted
  as-is by `POST /tasks` — there is no authenticated per-caller identity at this layer at all (every
  internal caller shares one `X-Controller-Secret`). Live-reproduced against the real
  `ApprovalService`: one actor set an arbitrary `proposed_by`, then approved the same task through both
  PLAN and EXECUTION with zero rejection. Distinct from prior spoofing fixes (#1/#227/#165/#186/#199/
  #367), which all hardened the *approver*-side check, not the unauthenticated *proposer*-side value
  it's compared against.
- **New finding — `#377` (LOW)**: four untyped `dict[str, Any]` contract fields
  (`TaskContract.verification`/`.opentasks_dag`, `ProjectProfile.llm`/`.audit`) have no depth/size
  bound. A ~2.3KB authenticated request nested ~300 levels deep turns a should-be-422 into an
  unhandled 500 (`task_service.py:53`'s `model_dump(mode="json")` hits pydantic-core's own recursion
  guard). No lasting damage — `get_db()`'s rollback-on-exception means nothing durably commits.
- **Fresh-angle passes that found nothing new**: the Controller-authoritative-over-Plane invariant
  (CLAUDE.md: "Do not make Plane CE authoritative for approvals or workflow transitions") was checked
  end-to-end — the webhook path converges on the exact same `ApprovalService.approve()` as the direct
  API with identical checks, and all Controller/Plane writes are strictly one-directional
  (Controller → Plane, never the reverse); docs (`SPEC-04`, `PROJECT_CONTEXT.md`) still match the
  code. A full-history secrets scan of the now-public repo (env files, docker-compose, CI workflows,
  test fixtures) found nothing real, only dev placeholders.

**Total open: 5** — `#366` (HIGH), `#376` (HIGH), `#372` (MEDIUM), `#377` (LOW), plus `#375` (an
unlabeled design question, not a bug: "Could the Governance Controller separate durable memory from
audit logs?"). Zero CRITICAL.

```bash
gh issue list --repo rusnino/ai-software-factory --state open
```

## Historical Round 29 State

**New engineering team ("Multica") took over via a PR/code-review workflow — a marked quality jump
over the previous direct-to-main process. 3 PRs merged (#360, #361, #363) genuinely close `#301`
(8 reopens) and `#359`, and the team self-caught and fixed a real bug in their own work (`#362`)
before this review round even started.** But the review still found 4 new issues: a genuinely new
HIGH clobbering bug in the just-merged fix itself, a MEDIUM residual gap the team had already and
honestly flagged as unfixed, and two HIGH findings from a fresh-angle pass (one process/governance,
one a real webhook-actor-impersonation vector). Scope was `git log 5a62384..origin/main` (merge of
PRs #360/#361/#363, 7 commits).

- **`#301` (8x-reopened saga) CONFIRMED genuinely fixed for its primary scope**: `StuckExecutionPoller
  ._poll_ready`/`_poll_retry_start` now attempt the same idempotency-key lookup before failing a
  grace-window-expired execution, instead of unconditionally failing it. Verified via worktree:
  targeted tests fail pre-fix/pass post-fix, including a genuine 3-way real-Postgres concurrency test
  (in-process recovery vs. poller recovery vs. each other) — exactly one racer wins, the loser no-ops,
  no duplicate run, no orphan.
- **`#359` CONFIRMED genuinely fixed**: the CAS-loss branch in both `approval_service.py`'s and
  `verification_service.py`'s `_try_recover_orphaned_run` now marks the confirmed-live-but-orphaned
  run `cancellation_pending = True` instead of dropping it; `StuckExecutionPoller
  ._poll_pending_cancellations` verified to actually discover and cancel it. Regression test verified
  genuine (fails pre-fix, passes post-fix via worktree).
- **`#362` (self-found by the new team) CONFIRMED genuinely fixed for the reported scenario**: the
  benign-race guard added while resolving PR #361's merge conflict could refresh a loser's shared
  `task` object to a concurrent winner's committed RUNNING state, then return `False` — which both
  inline callers (`ApprovalService._trigger_execution`, `VerificationService._start_retry_execution`)
  treated as "really failed," letting their own subsequent CAS legally re-transition the winner's task
  back to FAILED. Fixed via a tri-state return (`True`/`None`/`False`); both new regression tests
  (real end-to-end callers, genuine concurrent winner) verified to fail pre-fix with the exact
  predicted clobbering sequence in the audit log, and pass post-fix.
- **New finding — `#364` (HIGH)**: the very benign-race guard `#362` just fixed only recognizes a
  winner still sitting in `TaskState.RUNNING`. If the winner has legitimately advanced *past* RUNNING
  (e.g. to `AGENT_REVIEW`) by the time a loser's stalled lookup resolves, the guard doesn't fire, and
  the "genuine orphan" branch clobbers the execution row back to `FAILED`/`cancellation_pending=True`
  anyway — corrupting the audit trail and queuing a wrongful `cancel()` against a run that's genuinely
  in (or past) review. Live-reproduced in both call sites. Same defect class as `#362`, just keyed on
  the wrong piece of state (task state instead of the execution's own state).
- **New finding — `#365` (MEDIUM)**: the residual "double-lookup-failure" gap PR #361 itself honestly
  flagged as unfixed is confirmed real and unchanged — if the recovery lookup *also* errors (not just
  "finds nothing"), the task lands permanently FAILED synchronously, before any poller cycle could
  intervene, so the new poller-side recovery never gets a chance to help this specific path. Narrower
  than the primary `#301` scenario (needs two stacked failures), but live-reproduced with no run
  correlation ever established — worse than `#359`'s leak in that respect.
- **New finding — `#366` (HIGH, process/governance)**: this repo is private on GitHub's Free plan,
  where branch protection and rulesets are both unavailable entirely (confirmed via `gh api` — 403
  "Upgrade to GitHub Pro or make this repository public"). The new PR workflow has no enforceable gate
  at all: nothing stops a direct push to `main`, a merge with red/in-flight CI, or an admin override.
  This round's 3 PRs did show real review and green CI, but that's a social convention, not a control.
- **New finding — `#367` (HIGH)**: `api/webhooks.py`'s `_resolve_actor_email` resolves a Plane
  webhook's self-reported actor display name against the workspace member list and returns the
  **first** match with no uniqueness check — but `display_name` is an ordinary, self-editable Plane
  field, not enforced-unique like email. Any workspace member can rename their own display name to
  collide with an allow-listed approver's, and if resolved first, an EXECUTION/MERGE approval gets
  attributed to the impersonated approver instead of the real actor. Live-reproduced directly against
  the real function. This is the sole authorization check gating webhook-driven approvals, and it
  fails open under attacker-controlled conditions instead of failing closed like every other branch
  of the same function.

**Total open: 4** — `#364` (HIGH), `#366` (HIGH), `#367` (HIGH), `#365` (MEDIUM). Zero CRITICAL.

```bash
gh issue list --repo rusnino/ai-software-factory --state open
```

## Historical Round 28 State

**`#357` and `#358` both confirmed genuinely fixed with genuine tests. `#301` reopened an 8th time —
again zero new code, comment-only close, same already-reviewed commit re-cited. A fresh-angle sweep
of the very files the `#358` fix touched found a new HIGH: the `#301` recovery machinery itself has a
sibling of the CAS-loss-leak bug that `#262` already fixed once, in *two* call sites.** Scope was
`git log e53c6ae..origin/main` (2 commits: `f0d8961` for `#357`, `f614f06` for `#358`).

- **`#301`**: `git log` showed exactly 2 commits in range, neither touching
  `approval_service.py`'s `_try_recover_orphaned_run`, `verification_service.py`'s equivalent, or
  `stuck_execution_poller.py`. Yet the issue was closed again by comment, re-citing `cf46044` — the
  same commit reviewed and found incomplete in rounds 26 and 27. Reopened with the same two
  live-reproduced residual windows restated (double response-loss; poller-CAS race), and this time
  the reopen comment spells out a concrete suggested diff directly (give `StuckExecutionPoller` its
  own orphan-recovery lookup before declaring a grace-window-expired execution FAILED), since
  restating the evidence twice has not produced a code change.
- **`#357` CONFIRMED genuinely fixed** — `_revert_plane_state`'s two exception-suppression blocks are
  now fully independent across all 11 rejection call sites; verified the shipped regression test
  genuinely fails pre-fix/passes post-fix via worktree, plus an out-of-band probe on a second call
  site (not just the one the test covers) to confirm the fix is structural, not path-specific.
- **`#358` CONFIRMED genuinely fixed** — the sweeper's new state-matching fallback correctly keys on
  task **and** target state (not just task), correctly ignores later completed markers for a
  *different* target state (verified live: seeded a stuck marker targeting FAILED plus a later
  completed marker targeting BLOCKED — correctly not resolved), and correctly resolves multiple stuck
  markers on the same task independently. One LOW cosmetic `ruff format` nit in
  `reconciliation_service.py` noted but not filed (doesn't fail CI, which only runs `ruff check`).
- **New finding — `#359` (HIGH)**: `ApprovalService._try_recover_orphaned_run` (the `#301` recovery
  path added by `1d4e1dc`) confirms a live macro-agent run exists by idempotency-key lookup, but if
  its own CAS to `RUNNING` then loses (e.g. the poller's READY-timeout sweep or a cancellation wins
  first), it just returns `False` without setting `execution.cancellation_pending = True` — unlike the
  structurally identical CAS-loss branch in the non-recovery happy path, which already handles this
  correctly (the original `#262` fix). The now-FAILED execution's `cancellation_pending` stays
  `False`, and `StuckExecutionPoller._poll_pending_cancellations` only ever looks at executions with
  `cancellation_pending IS TRUE` — so the just-discovered, genuinely live macro-agent run leaks
  forever: nothing in the system can ever find or cancel it again. Live-reproduced against real
  Postgres with a concurrent session winning the task CAS mid-recovery. A sweep of
  `verification_service.py`'s sibling `_try_recover_orphaned_run` (the retry-path equivalent) found
  the identical shape at its own CAS-loss branch (manual `UPDATE ... WHERE version = ...` losing) —
  filed as part of the same issue, both call sites need the fix in one commit per the Regression
  Coverage Policy's sweep requirement.

**Total open: 2** — `#301` (HIGH, reopened 8th time, unchanged), `#359` (HIGH, new). Zero MEDIUM/LOW
open for the first time in a long while — `#357`/`#358` both closed genuinely.

```bash
gh issue list --repo rusnino/ai-software-factory --state open
```

## Historical Round 27 State

**`#156` confirmed genuinely fixed. `#301` reopened a 7th time — but this time with no new code at
all: opencode closed it by comment only, citing the same commit already reviewed and found
incomplete last round, without addressing either documented residual gap.** Scope was `git log
9e6f49f..origin/main` (1 commit).

- **`#301`**: `gh issue list --state open` showed 0 at the start of this round, but `git log` showed
  zero commits touching `approval_service.py`'s recovery logic, `verification_service.py`'s
  equivalent, or `stuck_execution_poller.py`. The closing comment simply re-cited `cf46044` — the
  exact commit round 26's reopen comment already reviewed in depth and found leaves two specific,
  live-reproduced windows open (double response-loss; a race against
  `StuckExecutionPoller._poll_ready`). Reopened immediately with the same evidence restated, since
  nothing changed to address it.
- **`#156` CONFIRMED genuinely fixed** — verified across 5 distinct rejection paths (not just one),
  including correct handling of the legacy SPEC-04 payload shape that lacks a previous-state value
  (falls back to comment-only, no crash). The commit also added a related-but-technically-out-of-scope
  feature (projecting verification outcomes to Plane, a gap `#156` itself never named) — confirmed
  working correctly on its own merits with genuine pre/post test differentiation, and confirmed not to
  race the pre-existing `ReconciliationService` Plane-sync path (same advisory lock, same
  fresh-read-before-write pattern).
- **Two new findings from this round's verification and fresh-angle passes**:
  - `#357` (LOW) — `#156`'s fix wrapped both the new revert call and the pre-existing rejection
    comment in one exception-suppression block, so a Plane API failure on the revert now also
    silently swallows the explanatory comment that used to post unconditionally.
  - `#358` (MEDIUM) — `StuckExecutionPoller`'s `plane_projection_pending` sweeper (the mechanism
    behind `#314`/`#322`/`#325`) resolves orphaned markers by checking whether `Task.state` has
    drifted from the pushed value — but for `update_state`/`reconciliation_state_fix` markers, that
    value is always the task's *own* state at write time, so a successful fix (which only changes
    Plane's display, never `Task.state`) can never be observed by this heuristic. Live-reproduced: a
    genuinely successful subsequent fix leaves the original orphan marker permanently unresolved.
    Compounds operationally since the sweep's fixed-size batch has no way to skip known-stuck rows.

**Total open: 3** — `#301` (HIGH, reopened 7th time, unchanged), `#358` (MEDIUM), `#357` (LOW).

## Historical Round 26 State

**`#350` and `#356` confirmed genuinely fixed. `#301` finally shows real architectural progress — the
first of 6 attempts whose core mechanism actually works — but 2 narrow residual windows keep it open
for a 6th time. A fresh-angle pass also reopened a closed issue on a subsystem cold since round ~11.**
Scope was `git log b467d55..origin/main` (4 commits).

- **`#301`'s 6th attempt is a genuinely different design**: a new `GET
  /runs/by_controller_execution_id/{id}` lookup endpoint plus an inline recovery path
  (`_try_recover_orphaned_run`) that runs synchronously inside the same call that experiences a
  response-loss, before the task is ever marked FAILED — no retry/poller re-entry needed for the
  primary case. Live-verified working end to end (real macro-agent subprocess, real Postgres) for both
  `approval_service.py` and `verification_service.py`: a lost-response run is correctly recovered, no
  duplicate, correct final state. **But two narrower windows still reproduce the exact same defect
  class** (a FAILED task with a preserved key nothing ever consumes, a live orphaned macro-agent run):
  (1) if the recovery *lookup itself* also fails (double response-loss), and (2) a genuine race against
  `StuckExecutionPoller._poll_ready`, live-reproduced with real concurrent Postgres sessions. Reopened
  with both reproductions and a note that this attempt got the core mechanism right — the next one
  likely only needs to close these 2 specific windows, not redesign the approach again.
- **`#350` CONFIRMED genuinely fixed** — a third, more robust design: `RunStore`'s eviction logic now
  has a fail-safe tier that evicts the oldest terminal run even if never explicitly released, making
  the fix independent of which call site remembers to release. Verified it doesn't overcorrect (still
  503s when all runs are genuinely active). Note: a second commit this round carried an identical
  "Fixes #350" trailer but its actual diff was unrelated infrastructure for `#301` — a labeling
  mistake, not a duplicate fix; only one commit (`4d4842b`) does the real work.
- **`#356` CONFIRMED genuinely fixed** — all 3 original reproductions (oversized/empty email body,
  caption-less Telegram media) now handled cleanly; ordinary Telegram media without a caption gets a
  synthesized placeholder and real processing rather than a crash or silent drop.
- **New finding from the fresh-angle pass**: `#156` (HIGH) reopened — GitHub showed it Closed, but the
  closing commit only fixed 3 of its 4 sub-parts (wiring reconciliation, the CLI, projection fixes).
  The "revert doesn't revert" sub-part is byte-for-byte still present: `_revert_plane_state()` posts an
  explanatory Plane comment on every rejected webhook-driven approval but never calls
  `update_issue_state`, so Plane's UI permanently shows a state the Controller never accepted after any
  rejection, undermining the "Controller authoritative, Plane a projection" guarantee. Cold subsystem —
  last touched by review ~15 rounds ago.
- A real concurrent-Postgres sanity check of the audit-log hash chain (25 genuine concurrent writers)
  still held clean. Process note, not filed as a bug: the only *concurrent-writer* hash-chain
  regression test is skipped on Postgres, so this specific path has no permanent Postgres-side
  coverage even though it's currently correct.

**Total open: 2** — `#301` (HIGH, reopened 6th time, narrowed scope), `#156` (HIGH, reopened).

```bash
gh issue list --repo rusnino/ai-software-factory --state open
```

## Historical Round 25 State

**`#301` reopened a 5th time, `#350` a 3rd — both closed-again commits addressed a narrower slice than
the issue's actual remaining gap.** Scope was `git log 1f3c42a..origin/main` (4 commits: `313fe61`
docs-only research survey, plus 3 closing commits). `gh issue list --state open` showed 0 at the
start of this round.

- **`#301`'s 5th attempt**: the new commit only changes one thing — preserve an existing
  `Task.macro_agent_idempotency_key` instead of overwriting it. `TaskState.FAILED` still has zero
  outgoing transitions (`state_machine.py:35`), and no poller anywhere references
  `macro_agent_idempotency_key`, so the preserved key still has no consumer. Live-reproduced end to
  end with a real macro-agent subprocess: a real run gets created, the task dies `FAILED`, a
  re-`approve()` call raises `Invalid transition: failed -> exec_approved`, and a full poller sweep
  takes zero action — the external run stays orphaned forever, byte-for-byte the same operational
  outcome as round 24's reopen. The new regression test manually constructs a task state combination
  (`PLAN_APPROVED` with a pre-seeded key) that production can never actually produce, and contains no
  second `.approve()` call, so it doesn't exercise a retry at all. This is the 5th consecutive
  close-and-reopen cycle on this issue. **Recommendation recorded in the reopen comment: this needs a
  single complete design before the next attempt** — either a bounded, explicitly-gated
  `FAILED -> EXEC_APPROVED`/`RUNNING` re-entry keyed on the preserved key (with its own poller), or a
  reconciliation sweep querying macro-agent by that key for orphaned `FAILED` tasks — verified
  end-to-end with a test that drives two real attempts through the real API, not a single-call unit
  test.
- **`#350`'s 3rd reopen**: the closing commit adds a real, correct release-on-cancel to
  `RunStore.cancel()` — genuinely fixing the separate `#354` leak — but does nothing about `#350`'s
  actual root cause: the dominant on-time-completion path (`EventBridge`'s `landing:completed`
  handling) never calls `get()`/`collect()`/`cancel()` at all, so the leak persists for ordinary
  successful task completions. Live-reproduced the original permanent-503 failure using only that
  wiring path. `#354` and `#355` (the parity-test-strength fix) are both CONFIRMED genuinely fixed with
  proven pre/post differentiation.
- **New finding from the fresh-angle pass**: `#356` (MEDIUM) — the Email/Telegram intake adapters
  crash with an unhandled 500 on an empty or oversized string field, reachable through completely
  ordinary usage (any Telegram photo/sticker/voice/location message without a caption 500s instead of
  being handled), not just malformed/adversarial input like the already-closed `#258` this is a
  residual gap in.

**Total open: 3** — `#301` (HIGH, reopened 5th time), `#350` (HIGH, reopened 3rd time), `#356`
(MEDIUM).

```bash
gh issue list --repo rusnino/ai-software-factory --state open
```

## Historical Round 24 State

**`#301` and `#350` reopened a 4th and 2nd time respectively — the closing commits are real, correct
plumbing that is simply never reached in production.** Scope was `git log 791df2d..origin/main` (6
commits). opencode closed all 6 open issues; 4 hold up, 2 don't, and a related HIGH gap was found in
a third code path neither touched fix covered.

- **`#301`**: the new `Task.macro_agent_idempotency_key` field is correctly seeded, preserved across
  an orphan-suspected failure, and would be correctly reused on a subsequent start — proven with
  genuine pre-fix/post-fix test differentiation. But both call sites that hit this preservation logic
  then unconditionally transition the Task to `FAILED`, a terminal state with zero outgoing
  transitions system-wide (`state_machine.py:35`), and neither recovery poller
  (`_poll_execution_start_pending`, `_poll_verification_retry_pending`) ever sees a `FAILED` task
  again. Live-reproduced end to end with a real macro-agent subprocess: the external run is created,
  the Controller task dies `FAILED`, the preserved key is never consumed by anything — no duplicate
  run gets created, but only because no retry ever happens at all. Same core harm (an accepted
  macro-agent run becomes permanently uncorrelated), different shape.
- **`#350`**: the release-on-terminal-status logic only lives inside the post-execution-timeout
  poller path (`_poll_running`, gated on `now >= deadline`). The dominant real-world completion
  path — a task finishing normally via the `landing:completed` webhook, well within its timeout —
  never polls macro-agent status at all, so the release never fires. Live-reproduced the original
  permanent-503 failure against the real `RunStore` using only the wiring paths the real Controller
  actually exercises for on-time completions (no GET call).
- **New finding, same lifecycle, a THIRD leaking path**: a fresh-angle pass found `RunStore.cancel()`
  (used by the Controller's only CAS-loss cleanup path) also never releases its idempotency-key
  protection — filed as `#354` (HIGH). CAS races are routine, heavily-tested behavior in this
  codebase, so this leak accumulates continuously in any long-running deployment.
- **`#349` and `#351`/`#352` all CONFIRMED genuinely fixed**, each with proven pre-fix/post-fix
  differentiation on ephemeral worktrees. `#352`'s original CRITICAL rating was independently
  re-confirmed correct (not a false premise) — pre-fix, the embedded/primary engine really was
  vulnerable to the `addr1,+N` form, not just the optional OPA backend. An exhaustive 140-combination
  differential sweep of the full GNU sed address grammar found zero further divergence — this defect
  family (6 instances since round 20) appears genuinely closed now.
- A minor test-coverage gap was also filed as `#355` (LOW): `#352`'s Python-side regression test only
  asserts the two engines agree with each other, not that either one is actually correct — it would
  pass even if both fixes were reverted together.

**Total open: 4** — `#301` (HIGH, reopened 4th time), `#350` (HIGH, reopened again), `#354` (HIGH),
`#355` (LOW).

```bash
gh issue list --repo rusnino/ai-software-factory --state open
```

## Historical Round 23 State

**6 open issues — the worst count since round 18, though 0 of them are re-broken prior fixes.** Scope
was `git log eddf201..origin/main` (3 commits: `8f36c9c`, `1ac1a23`, `9795cf5`). One important process
note first: `#301` showed as auto-closed at the start of this round even though none of the 3 commits
touch it — this was a self-inflicted false positive from the PREVIOUS round's own docs commit
(`eddf201`), whose body contained the English sentence "...finally fixes #301 end-to-end," which
GitHub's keyword scanner matched as an auto-close trigger despite describing unfinished work. Reopened
immediately; `#301` remains exactly as broken as round 22 left it (`approval_service.py:529` and
`verification_service.py:824` still mint a fresh `Execution` UUID on every retry). **Lesson for future
docs commits: never write the literal string "fixes #&lt;number&gt;" adjacent to an issue number unless
actually closing it.**

Of the 3 real commits:
- **`#347` (HIGH) CONFIRMED FIXED** — live pre-fix/post-fix differentiation against the real `opa`
  binary on all 6 issue reproductions, using an ephemeral worktree. But the required sweep found a
  **5th instance** of the same "Rego helper wrong/undefined → deny rule silently doesn't fire" class,
  broader blast radius than any prior one: `#351` (HIGH) — `_sed_skip_digits` collects every digit
  position anywhere later in the script instead of a contiguous run, so ANY numeric sed address
  followed by any digit later in the script (e.g. a file path containing a year or port number)
  overshoots past the real command character. Separately, `#352` (CRITICAL, provisional pending
  confirmation) — the GNU `addr1,+N` forward-range address form is unrecognized by **both** backends,
  not just OPA — unlike every prior sibling in this family, this one isn't shielded by "the embedded
  engine evaluates first," because the embedded engine has the same gap.
- **`#348` (MEDIUM) — original defect CONFIRMED FIXED, but the fix creates a worse new bug.** The
  eviction-protection mechanism correctly prevents silent duplicate runs, but has no working release
  path in production (`RunStore.collect()` is dead code on the Controller side) — filed as `#350`
  (HIGH): every run becomes permanently un-evictable, so the store guaranteed-permanently 503s all new
  run creation once normal usage exceeds capacity. Trades an occasional silent bug for a total,
  permanent outage.
- **`#349` (LOW) — REOPENED, partially fixed.** `approval_service.py`'s initial-start path correctly
  rejects a terminal-status dedup hit (confirmed live, fault-injection proved). But
  `verification_service.py`'s retry-start path was never touched — live-reproduced the identical bug
  through that entry point instead.
- **New finding from the fresh-angle pass, directly related to `#350`**: `#353` (MEDIUM) —
  `RunStore.collect()` releases the idempotency-protection mapping unconditionally, even on a
  non-terminal run — a naive fix for `#350` (wiring up `collect()` calls without a status gate) would
  just relocate this exposure rather than close it.
- A broad differential fuzz of `find`/`npm`/`pnpm`/`pip`/`docker.sock`/`tar` command families (not
  previously swept) found zero new embedded-vs-OPA divergences. `governance.rego`'s naive `_shlex_split`
  fallback tokenizer was investigated as a candidate 6th instance of the family and confirmed safely
  dead code — the input-shape validation rule fails closed whenever `parsed_commands` doesn't match,
  and the Controller always supplies a matching entry.

**Total open: 6** — `#301` (HIGH), `#350` (HIGH), `#351` (HIGH), `#352` (CRITICAL, provisional),
`#353` (MEDIUM), `#349` (LOW).

```bash
gh issue list --repo rusnino/ai-software-factory --state open
```

## Historical Round 22 State

**`#301` false-closed a SECOND time; the OPA fail-open defect class found a FOURTH sibling.** Scope was
`git log bfb9ca3..origin/main` (4 commits: `a75c64e`, `56b98db`, `d9578ea`, `6c39087`). opencode reported
all 4 issues fixed and `gh issue list --state open` empty.

- **`#301` (HIGH) — REOPENED AGAIN.** `d9578ea` made `macro_agent_service`'s `RunStore.create()`
  genuinely and correctly dedupe `POST /runs` by `controller_execution_id` — confirmed live, including
  under real concurrency (20 concurrent same-key requests → 1 `run_id`, no TOCTOU). But zero lines
  changed in `src/governance_controller/`: `approval_service.py:527` and `verification_service.py:824`
  both still mint a fresh `Execution(id=str(uuid4()))` on every retry, so the "same key" the new dedup
  logic depends on never actually repeats. Live-reproduced end to end: real Controller retry pattern
  (fresh uuid4 per attempt) → two different `run_id`s, duplicate external run still created. This is
  exactly the "half the picture" pattern the round-21 reopen predicted even before this fix landed.
- **`#340`'s defect class found a FOURTH time.** `#344` (`uv run` flag-prefixed bypass) and `#345` (sed
  substitution address-prefix bypass) both CONFIRMED genuinely fixed — real pre-fix/post-fix
  differentiation proven via ephemeral worktrees, not just a green suite. But the fresh-angle sweep this
  round found the same "Rego helper undefined → rule silently doesn't fire" shape a fourth time:
  `#347` (HIGH) — `_sed_script_file_io`'s direct `r`/`w`/`R`/`W` command path (as opposed to the `s///`
  substitution path `#345` just fixed) still can't parse ordinary two-address range forms
  (`/start/,/end/w file`), so it's undefined and the deny rule doesn't fire. Four instances of the
  identical root cause in one week, each fixed the same day it's found, each immediately followed by a
  sibling — a dedicated audit of every remaining partial Rego helper is now overdue rather than optional.
- **`#346` (LOW) — CONFIRMED FIXED**, with fault-injection proof: the rewritten two-session test fails
  when `populate_existing=True` is temporarily removed, passes when restored.
- **New findings directly downstream of `#301`**: `#348` (MEDIUM) — `RunStore`'s capacity eviction
  silently defeats the just-shipped dedup contract once the store fills up (deterministic, not a race;
  not reachable via the Controller today since it doesn't reuse ids at all yet, but reachable directly
  against macro-agent's own tested public contract). `#349` (LOW) — the Controller never reads
  `MacroAgentStartResponse.status` on a dedup hit, so a stale/terminal run returned by a future
  retry-reuse path would be silently mistaken for a fresh one. Both should land in the same change that
  finally fixes `#301`'s Controller-side retry-id-reuse gap, not be fixed independently later.
- A suspicious SQLAlchemy warning opencode flagged but deliberately left unfixed (`SAWarning: New
  instance <Task ...> conflicts with persistent instance` in `test_create_task_duplicate_returns_409`)
  was investigated and confirmed a harmless test-fixture artifact — the shared-session test fixture
  doesn't mirror production's per-request session isolation. No issue filed.

**Total open: 4** — `#301` (HIGH, reopened), `#347` (HIGH), `#348` (MEDIUM), `#349` (LOW). Zero CRITICAL.

```bash
gh issue list --repo rusnino/ai-software-factory --state open
```

## Historical Round 21 State

**NOT gate-clean, despite opencode closing all 5 of Round 20's remaining issues same-day.** Scope
was `git log 07edca1..origin/main` (5 commits: `1d4e1dc`, `c04c659`, `ce8b48d`, `96f83e8`, `afd5e15`).
Of the 5 closed issues:

- **`#301` (HIGH) — REOPENED, false close.** The closing commit (`1d4e1dc`) only adds a
  classification label (`failure_class: orphan_suspected` etc.) to the existing failure-audit
  payload. It changes zero control flow: no idempotency key is sent to macro-agent, no lookup/dedup
  mechanism exists, retries still mint a fresh `Execution` UUID and issue a brand-new `POST /runs`,
  and `src/macro_agent_service/` still has no `controller_execution_id`/idempotency support. The
  commit's own message admits this: "Full run-id idempotency still requires a macro-agent API that
  accepts `controller_execution_id` as an idempotency key." A well-classified but still-unrecoverable
  orphan is not a fix — this is exactly the "test passes, architecture unchanged" pattern this
  project has hit before. Reopened with full evidence in the issue's comment thread.
- **`#340` (HIGH, OPA sed multi-segment-path parity) — CONFIRMED FIXED** for its original
  reproduction: real `opa` binary confirms all 3 originally-broken cases now denied, 78/78 OPA
  suite, 7/7 differential parity tests, fails-pre/passes-post confirmed by running the new test
  against the pre-fix policy file. **But the fix swept only this one helper function**, and the
  fresh-angle pass this round found the exact same "OPA fail-open on an undefined helper" defect
  shape twice more the same day (see `#344`, `#345` below) — this is now looking like a systemic
  property of `governance.rego`'s helper functions, not three unrelated bugs.
- **`#341` (MEDIUM, RISK-19 stale-read in `_finalize_current_execution`) — code fix CONFIRMED real**,
  but its regression test does not exercise the staleness scenario: reverting just the
  `populate_existing=True` line left the committed test passing unchanged. Filed as `#346`.
- **`#342` (LOW) and `#343` (LOW) — CONFIRMED FIXED**, both with genuine evidence (`#343`'s new test
  was fault-injection-verified: temporarily breaking the CAS guard it protects made the test fail as
  expected).

**3 new issues filed this round**, two of them HIGH and directly continuing `#340`'s defect class:
- `#344` (HIGH): `governance.rego`'s `uv run` child-command allowlist is **fully bypassed** whenever
  any flag precedes the child command (`uv run --quiet rm -rf /`, `uv run --python ... pytest`, ...)
  — the enforcing rule references an undefined helper and Rego's undefined-means-doesn't-fire
  semantics silently let everything through. Same root-cause shape as `#340`, much larger blast
  radius (total allowlist bypass, not a narrow flag miss).
- `#345` (HIGH): `#340`'s own fix is itself bypassable — sed scripts with a leading address
  containing the letter `s` (`/skip/s/foo/bar/w /tmp/a/b/out.bin`) make the new flags-extraction
  helper lock onto the wrong `s` and silently not fire. Ordinary sed usage, not a contrived edge
  case.
- `#346` (LOW): `#341`'s regression test doesn't exercise the bug it claims to guard (see above).

Neither `#344` nor `#345` is live-exploitable via the Controller's default path today (the embedded
`PolicyEngine` always runs first and short-circuits on deny before OPA is consulted) — but this is
the same mitigating factor `#340` had, and it is not a substitute for the OPA backend actually
matching the module's own stated "parity, not downgrade" invariant. **Recommend a dedicated audit of
every partial/undefined-prone helper function in `governance.rego`** rather than continuing to fix
these one at a time as they're independently rediscovered — three instances of the identical root
cause surfaced in a single day.

Total open issues: **4** (`#301`, `#344`, `#345`, `#346`), 3 HIGH, 1 LOW, 0 CRITICAL.

```bash
gh issue list --repo rusnino/ai-software-factory --state open
```

## Historical Round 20 State

**Nearly gate-clean: 5 open issues total, none of them a re-broken prior fix.** This round's scope
was `git log be95854..origin/main` (33 commits) — the full range since the last independently-run
review round, not just the single `a42953f "docs: record hardening batch verification"` commit
sitting on top of it, which is opencode's own self-declared verification note, not an
independently-confirmed review-round marker. Per this round's git-log-based methodology, only a
`docs(phase-2): record round N verification` commit from the reviewer counts as a review-round HEAD.

**All 23 issues opencode closed in that range are confirmed genuinely fixed by live reproduction**,
not diff-reading: 5 CRITICAL (`#140`, `#309`, `#332`, `#333`, `#336` — the policy-parser bypass
family, confirmed on both the embedded `PolicyEngine` and the `governance.rego` OPA backend, ~20
sibling syntactic variants swept with no new gaps found), 5 HIGH (`#293` — the durable
cancellation-claim design this session reviewed and approved before opencode implemented it,
confirmed live under the exact poller-first-vs-approval-cleanup overlap direction round-21 had
previously found broken, on both SQLite and real Postgres, with exactly one `cancel()` call and one
completion audit row; `#305`, `#329`, `#328`, `#334`), 5 MEDIUM (`#302`, `#306`, `#310`, `#335`,
`#338`), 8 LOW (`#303`, `#304`, `#307`, `#326`, `#330`, `#331`, `#337`, `#339`). `#328` was
re-verified against the exact gap that got it reopened last round (`init_db()`'s unprotected
`create_all()` running before the advisory lock) via a live 8-process startup race against a
freshly-dropped schema: 0/8 failures post-fix, 4/5 failures when the fix was temporarily reverted to
confirm the test is real, not a false green.

**Process note, not a defect**: 13 of the 23 closing commits (all 5 CRITICAL fixes plus 8 of the
lower-severity ones) are test-only — the actual production-code fix had already landed earlier in a
separate, broader commit (mostly `62f918a "fix(phase-2): policy-parser hardening and OPA parity
round"`, plus `0b4d5b4`/`6ff4674`/`4b941d3a`), with the closing commit added later purely to give
each issue an auto-closing `Fixes #N` commit. Every case was traced back and the underlying fix
substance confirmed real — this is not a "green test for the wrong reason" problem — but it's a
literal violation of CLAUDE.md's "every fix must land a regression test in the *same* commit"
clause. Batch-shipped code changes should carry their own regression tests as they land, not be
back-filled by a wave of issue-closing test commits days later.

**4 new issues found this round** (none blocking, none re-breaking a prior fix):
- `#340` (HIGH): the OPA `governance.rego` backend's sed `w`/`W`/`e` flag detection silently fails
  whenever the target file path has more than one `/` segment — a naive whole-script `split()`
  instead of position-based delimiter tracking, unlike the embedded engine's correct implementation.
  Not live-exploitable today (the embedded engine always evaluates first and short-circuits on
  deny), but breaks the file's own stated OPA-parity guarantee for most realistic paths. Found via a
  374-input differential fuzz of every `PolicyEngine.evaluate()` call in the test suite replayed
  through the real `opa` binary — 373/374 matched, this one didn't.
- `#341` (MEDIUM): `VerificationService._finalize_current_execution` reads `Execution` without
  `populate_existing=True`, right after a Task CAS in the same session — the `RISK-19` shape, found
  via the sweep required by `#338`'s fix; not yet independently live-raced.
- `#342` (LOW): `TaskService.get_by_id`/`get_by_plane_issue_id` lack the same guard — currently
  unreachable (fresh per-request sessions only today), pure hardening.
- `#343` (LOW): `#293`'s durable-claim fix is confirmed correct under the poller-first-vs-cleanup
  overlap direction, but that direction has no *committed* regression test — the ad-hoc test that
  proved it correct this round was not added to the suite.

**Only `#301` remains as a pre-existing open issue** — the `[HIGH]` macro-agent-start-not-idempotent
architectural blocker, unchanged this round, requiring a macro-agent idempotency contract or an
outbox before it can close. Total open issue count after this round: **5** (`#301`, `#340`-`#343`),
**zero CRITICAL, zero re-broken fixes**.

```bash
gh issue list --repo rusnino/ai-software-factory --state open
```

## Historical: hardening-batch note (2026-09-08, superseded by Round 20 above)

**Phase 2 remains not gate-clean.** Local and remote `main` now include the focused hardening
batch through `4b57e15`, covering the non-blocked worklist issues `#140`, `#302`-`#307`, and
`#329`-`#331`. The latest verification evidence is:

- SQLite controller suite: `853 passed, 49 skipped, 2 xfailed`.
- PostgreSQL controller suite: `892 passed, 10 skipped, 2 xfailed`.
- OPA policy suite: `77/77`.
- Ruff, mypy, and `git diff --check`: clean.
- Independent read-only review found no remaining issues in the pushed range.

The batch adds regression coverage for policy-parser parity, durable retry recovery, CAS-safe
polling, duplicate Plane completion, and dry-run non-mutation. The malformed retry-profile path
now records a durable non-retryable audit and the poller distinguishes profile validation errors
from operational profile-store failures.

The live issue gate remains open and is the source of truth. `#301` is the only remaining open
critical/high issue in this worklist and remains an architectural blocker because exact-once
recovery after macro-agent response loss requires a macro-agent idempotency contract or an outbox.
Do not mark Phase 2 complete while the critical/high queries contain `#301`.

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
