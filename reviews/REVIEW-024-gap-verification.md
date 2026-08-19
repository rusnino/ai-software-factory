# REVIEW-024: Verification of REVIEW-023's fixes (11 gaps, commit range ccf3617..59be68c)

Date: 2026-08-19
Scope: live re-verification of all 11 gaps REVIEW-023 opened (`GAP-095` through `GAP-106`, excluding
`GAP-102` which was a direct doc fix). Three parallel verification agents, split by risk: (A) the two
HIGH findings touching the verification-lock/retry-exception area, (B) the security/state-machine
findings (`GAP-096`/`099`/`101`), (C) the remaining six lower-severity items. I personally re-verified
the residual bugs in `GAP-095` and `GAP-097` directly from source, and fixed `GAP-105`'s disposition
myself (see below).

## Baseline

`uv run pytest -q` → 223 passed (up from REVIEW-023's 212 — new tests from this round's fixes).
`uv run ruff check .` → clean. `uv run mypy governance_controller` → 0 errors.

## Verdict: 9 of 11 genuinely fixed; 2 (both HIGH) partially fixed with real, live-reproduced residual
## bugs; 1 ledger-hygiene correction (wrong hash) and 1 ledger-disposition correction (GAP-105).

### GAP-095 — PARTIALLY FIXED, REOPENED

The row-lock half of the fix is real: `event_bridge.py` now commits after the `AGENT_REVIEW`
transition and before `verify_and_advance()` runs verification commands. Live-reproduced against a
real Postgres container: a concurrent write to the same task row, previously blocked for the full
duration of a slow check, now returns in 0.04s.

The timeout half does not work, and I confirmed this myself by reading `_run_check`'s exception
handler directly: on `TimeoutError`, it calls `proc.kill()` then `await proc.wait()`. But
`asyncio.create_subprocess_shell` spawns `/bin/sh -c "<command>"`, and when the shell forks a real
child process to run the command (confirmed live: the shell's PID and the command's own PID differ
for something like `sleep 300`), `proc.kill()` only terminates the shell — not the child it spawned.
The reporting agent live-reproduced this precisely: `_run_check(command="sleep 300", timeout=3.0)`
returned only at the full 300 seconds, and `_run_check(command="cat", timeout=2.0)` (a command with no
natural exit) hung indefinitely and had to be killed externally. The checked-in regression test
(`sleep 10`/`timeout=0.1`) never asserts on elapsed time, so it passes by simply waiting out the ~10s
naturally — masking the exact defect it was meant to catch. This is the identical "untimed,
proposer-controlled command" risk `GAP-095` was opened to close, now only half-closed.

### GAP-096 — GENUINELY FIXED

`permission_service.py`'s `may_approve()` now rejects any actor matching the bare literals `"system"`/
`"agent"` OR starting with `"system:"`/`"agent:"`. Live-reproduced through the real ASGI app: the
original `actor="system:macro-agent"` bypass now correctly returns 422; over-blocking was checked and
ruled out (`"systemadmin"`, `"agentsmith@example.com"` both still correctly allowed, since the match
requires the colon); legitimate human approvals unaffected. One ledger hygiene nit found and
corrected: this row's `Closed By` cited a hash (`30f2914`) that is not an ancestor of `HEAD` (a
dangling self-referential artifact of amending a commit that cites its own hash) — corrected to the
real ancestor `cda9a91`, confirmed via `git merge-base`.

### GAP-097 — PARTIALLY FIXED, REOPENED

`event_bridge.py` now wraps `verify_and_advance()` in `try/finally`, writing the processed-event dedup
key in the `finally` block. I confirmed by reading the code directly that this write is never
committed there. `_start_retry_execution()`'s except-branch already commits the terminal-`FAILED`
transition before re-raising `RuntimeError`; that exception reaches the `finally` block (which writes
but doesn't commit the dedup key), then continues propagating to `get_db()`'s
`except BaseException: await session.rollback(); raise` — which discards the uncommitted `finally`
write along with anything else pending in that session. This is the same "write-then-raise without an
intervening commit" pattern that has recurred repeatedly in this exact codebase (`GAP-044`, `GAP-054`,
`GAP-058`). The reporting agent live-reproduced this end-to-end via the real ASGI app with the real,
unmodified `get_db()` (not the test fixtures, which don't exercise `get_db()`'s rollback-on-exception
path at all): a direct `psql` query immediately after a mocked `executor.start()` failure showed 0
rows in `processedevent`. A second reproduction, engineered so the exception occurs before any
state-transition commit (so the task stays `AGENT_REVIEW` rather than already-terminal), showed the
real unmasked bug: redelivering the identical event caused `verify_execution()` to run a second time
and the task to advance to `HUMAN_REVIEW` on the replay — genuine double-processing, exactly what
`GAP-097` was opened to prevent. Also flagged (a design question, not necessarily a defect): the fix's
choice to move straight to terminal `FAILED` on *any* `executor.start()` failure, even a one-off
transient blip on the first of several configured retries, forfeits the entire remaining retry budget
immediately rather than only once retries are exhausted — worth a second look, but arguably a
defensible fail-safe tradeoff against the alternative (double-consuming retries via replay).

### GAP-098 through GAP-106 — all confirmed genuinely fixed, with one disposition correction

`GAP-098` (file-based SQLite pool settings), `GAP-099` (BLOCKED only unblockable by
`conflict:resolved`, verified against all 5 previously-dangerous event types, not just the one the
checked-in test covers), `GAP-100` (structured `violations` list, no more comma-splitting corruption),
`GAP-101` (`role` now in the outbound macro-agent payload, verified at the actual HTTP wire level),
`GAP-103` (dead `atomic_transition_with_fields` removed, zero remaining references), `GAP-104`
(harness-registry test-isolation leak fixed, verified no cross-test leakage) — all live-reproduced by
the verifying agents and independently sound.

`GAP-105` (the SDD progress ledger's staleness) had been marked `ACCEPTED` as "workflow-hygiene debt."
The verifying agent correctly pushed back: this is the third occurrence of the identical drift
(`GAP-016` and `GAP-071` both hit the same pattern and were both backfilled, not accepted), the fix is
mechanical and low-cost, and accepting it a third time only guarantees a fourth recurrence. I agreed
and backfilled `progress.md` through Review 23 directly — using commit hashes read from `git log`
(`git log --oneline --grep="REVIEW-0"` and `git log --follow` for REVIEW-018's file, which turned out
to be bundled into the coding agent's own `ee80cd6` commit rather than a separate docs commit),
deliberately not from memory, to avoid the exact fabricated/dangling-hash pattern this ledger has
already flagged twice (`GAP-070`, `GAP-092`).

## Verification

`GAP-095`'s and `GAP-097`'s residual bugs were both personally re-confirmed by reading the exact
exception-handling code directly, not just trusting the reporting agents' descriptions. The three
reporting agents' live reproductions (real Postgres container for `GAP-095`'s lock half and `GAP-097`'s
E2E replay; real ASGI app for `GAP-096`/`100`; direct HTTP-wire mocking for `GAP-101`) are consistent
with this series' established rigor. `GAP-105`'s backfill is a direct documentation edit, not a code
change. No `governance_controller` application or test code was modified by this review — all changes
are to documentation/ledger files: `reviews/GAPS.md`, `docs/NEXT_STEPS.md`,
`.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/progress.md`.

## Milestone note

The gate reopens for the sixth time in this series, again on genuinely new (not repeated) defects —
this time both residuals are variations on patterns this codebase has hit before (untimed subprocess
execution, write-then-raise without a commit), just in code that hadn't been touched by those earlier
fixes. Both are narrow and well-understood; REVIEW-023's other 9 findings hold up genuinely closed.
