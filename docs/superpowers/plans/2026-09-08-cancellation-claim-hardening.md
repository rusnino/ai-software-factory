# Durable Cancellation Claims Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CAS-loser macro-agent cancellation single-flight across SQLite event loops/processes while preserving crash recovery and releasing SQLite writer locks before external I/O.

**Architecture:** `Execution` stores a cancellation claim token and timestamp in addition to the durable `cancellation_pending` flag. A shared SQL `UPDATE ... RETURNING` claims one pending execution, evaluates the authoritative task pointer in the same guarded statement, and commits before `cancel()`. Approval cleanup, verification-retry cleanup, and the stuck-execution poller all use the same claim/release protocol.

**Tech Stack:** Python 3.13, SQLModel/SQLAlchemy async sessions, SQLite, PostgreSQL, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-09-01-phase2-recovery-and-intake-hardening-design.md` plus GitHub issue #293 comment `5582310979`.

## Global Constraints

- The Controller remains authoritative; cancellation claims are durable Controller state.
- Do not modify macro-agent internals or add external dependencies.
- Claim acquisition is a short atomic database transaction and is committed before any external `cancel()` call.
- The task-pointer decision is evaluated in the same guarded SQL update as claim acquisition; no separately read stale pointer decides cancellation.
- Use the same claim/lease/release protocol in ApprovalService, VerificationService, and StuckExecutionPoller.
- Set the lease to `max(300 seconds, 10 * settings.macro_agent_timeout_seconds)`: the default macro-agent HTTP timeout is 30 seconds, so the five-minute floor provides 10x headroom for the external cancel p99 and scheduler delay; stale claims are retryable after that interval.
- Existing PostgreSQL `FOR UPDATE`/`SKIP LOCKED` coverage remains and SQLite must not hold a writer lock across external I/O.
- Every concurrency regression uses a real database and genuine independent loops/processes; executor doubles may only stand in for the external HTTP boundary.

---

### Task 1: Add the Durable Claim State and Shared SQL Protocol

**Files:**
- Modify: `src/governance_controller/governance_controller/models/execution.py`
- Modify: `src/governance_controller/governance_controller/db.py:230-253`
- Create: `src/governance_controller/governance_controller/services/cancellation_service.py`
- Test: `src/governance_controller/tests/test_audit_log.py`
- Test: `src/governance_controller/tests/test_stuck_execution_poller.py`

**Interfaces:**
- `Execution.cancellation_claim_token: str | None` and `Execution.cancellation_claimed_at: datetime | None` persist the current lease.
- `CancellationClaim` contains `execution_id`, `macro_agent_run_id`, `token`, and `task_owns_run`.
- `claim_cancellation(db, execution_id, macro_agent_run_id) -> CancellationClaim | None` atomically claims only a pending execution whose claim is absent or older than the lease. Its `UPDATE ... RETURNING` expression must also evaluate whether the current task is `RUNNING` and points at the same run.
- `release_cancellation_claim(db, claim, completed: bool) -> bool` clears the token/timestamp only when the token still matches; it clears `cancellation_pending` on success and leaves it set on failure. The caller writes its audit outcome and commits in the same transaction.

- [x] **Step 1: Add the failing cross-loop SQLite regression.** Use the file-backed URL from `isolated_db`, create two independent `AsyncEngine`/session makers in two OS threads, start the first poller's fake `cancel()` and hold it, then run the second poller while the first external call is still blocked. Assert exactly one call and one completion audit. The current per-event-loop `asyncio.Lock` must fail this test with two calls. Additional process-level coverage uses two spawned workers against the same file-backed database.

```python
def run_poller() -> None:
    asyncio.run(poll_once_with_a_fresh_engine())

first_thread.start()
assert cancel_started.wait(timeout=5)
second_thread.start()
second_thread.join(timeout=5)
release_cancel.set()
first_thread.join(timeout=5)
assert calls == [run_id]
```

- [x] **Step 2: Run the new regression and confirm RED.**

```bash
uv run pytest tests/test_stuck_execution_poller.py -k sqlite_cancellation_claim_is_single_flight -q
```

Expected: the pre-claim implementation invokes the external cancel twice because each event loop owns a different `asyncio.Lock`.

- [x] **Step 3: Add the migration/model fields.** Add nullable `VARCHAR` and timezone-aware timestamp columns to `Execution`; extend `run_migrations()` to add either column when an existing database lacks it. Do not change existing `cancellation_pending` values or historical audit rows.

- [x] **Step 4: Implement `claim_cancellation()`.** Generate a UUID token and `now`, calculate `lease_cutoff = now - max(300 seconds, 10 * settings.macro_agent_timeout_seconds)`, and execute one guarded update:

```python
UPDATE execution
SET cancellation_claim_token = :token,
    cancellation_claimed_at = :now
WHERE id = :execution_id
  AND macro_agent_run_id = :macro_agent_run_id
  AND cancellation_pending = TRUE
  AND (cancellation_claim_token IS NULL
       OR cancellation_claimed_at IS NULL
       OR cancellation_claimed_at < :lease_cutoff)
RETURNING cancellation_claim_token,
          CASE WHEN EXISTS (
              SELECT 1 FROM task
              WHERE task.id = execution.task_id
                AND task.state = 'RUNNING'
                AND task.latest_macro_agent_run_id = :macro_agent_run_id
          ) THEN TRUE ELSE FALSE END AS task_owns_run
```

Commit a successful claim before returning. Roll back/return `None` when another claimant owns the fresh lease or the execution is already complete. The returned `task_owns_run` is the atomic pointer decision; no later unguarded task read may decide whether `cancel()` runs.

- [x] **Step 5: Implement `release_cancellation_claim()`.** Guard the update by execution ID, macro-agent run ID, pending flag, and claim token. For `completed=True`, set `cancellation_pending=False`; for `completed=False`, retain `True`; in both cases clear the claim token and timestamp. Return whether exactly one row was updated so callers never emit a completion audit for a lost lease.

- [x] **Step 6: Add stale-claim recovery coverage.** Seed a claim older than the lease, run the poller, and assert it can reclaim, cancel once, clear the lease, and write one completion event. Seed a fresh claim and assert no second external call occurs. Also cover a cancelled worker leaving a reclaimable durable claim.

- [x] **Step 7: Run schema/helper tests and static checks.**

```bash
uv run pytest tests/test_audit_log.py tests/test_stuck_execution_poller.py -k 'cancellation_claim or migration' -q
uv run ruff check governance_controller/models/execution.py governance_controller/db.py governance_controller/services/cancellation_service.py tests/test_audit_log.py tests/test_stuck_execution_poller.py
uv run mypy governance_controller/models/execution.py governance_controller/db.py governance_controller/services/cancellation_service.py
```

### Task 2: Integrate All Three Cancellation Call Sites

**Files:**
- Modify: `src/governance_controller/governance_controller/services/approval_service.py:655-755`
- Modify: `src/governance_controller/governance_controller/services/verification_service.py:1057-1173`
- Modify: `src/governance_controller/governance_controller/services/stuck_execution_poller.py:137-297`
- Test: `src/governance_controller/tests/test_approval_concurrency.py`
- Test: `src/governance_controller/tests/test_verification_service.py`
- Test: `src/governance_controller/tests/test_stuck_execution_poller.py`

**Interfaces:**
- Each call site invokes `claim_cancellation()` after its durable pending marker is committed and before `executor.cancel()`.
- A `None` claim means another worker owns the lease or already completed cleanup; the caller must not cancel or emit a duplicate completion event.
- `task_owns_run=True` resolves the pending row through the claimant-only release path without calling the external cancellation endpoint.

- [x] **Step 1: Add failing call-site regressions for claim ownership.** Extend the existing SQLite approval and verification cleanup interleaves so the second independent worker uses a separate event loop/session and assert only the claimant calls `cancel()`. Keep the existing same-loop tests and PostgreSQL lock-order/pointer tests unchanged. The shared poller claim regression covers independent loops/processes; the approval and verification suites cover their integrations and crash/error paths.

- [x] **Step 2: Replace ApprovalService cleanup.** Remove its dependency on the per-event-loop lock, call the shared claim helper, skip duplicate work on `None`, resolve an attached run through the guarded release, or call `executor.cancel()` only for the returned claimant token. On cancellation failure retain `cancellation_pending` and clear only the lease.

- [x] **Step 3: Replace VerificationService retry cleanup.** Apply the identical claim/release/error protocol to `_start_retry_execution`; do not leave this path using a separate task/execution read or the old SQLite lock.

- [x] **Step 4: Replace poller cleanup.** Keep the bounded `Execution` selector and PostgreSQL `FOR UPDATE SKIP LOCKED` query, but use the shared atomic claim for each candidate. Remove the SQLite event-loop lock and task-pointer read used as the cancellation decision. Record outcomes only after a successful claimant release, with 404 treated as completed as before.

- [x] **Step 5: Verify crash semantics.** Add/retain tests that raise `asyncio.CancelledError` after claim commit and confirm the row remains pending with its claim; after advancing the claim timestamp beyond the lease, a fresh poller can recover it.

- [x] **Step 6: Run the focused SQLite suites and confirm GREEN.**

```bash
uv run pytest tests/test_approval_concurrency.py tests/test_verification_service.py tests/test_stuck_execution_poller.py -q
```

- [x] **Step 7: Run the focused PostgreSQL suites serially.** Use the real `gc_test` database and retain the existing `FOR UPDATE`/`SKIP LOCKED` tests.

```bash
GC_TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/gc_test' uv run pytest tests/test_approval_concurrency.py tests/test_verification_service.py tests/test_stuck_execution_poller.py -q
```

### Task 3: Final Verification and Commit

**Files:**
- Review: all files in Tasks 1-2
- Update: `docs/NEXT_STEPS.md` with the verified #293 status and test counts

- [x] **Step 1: Run the complete SQLite matrix.**

```bash
uv run pytest -q
uv run ruff check governance_controller tests
uv run mypy governance_controller
git diff --check
```

- [x] **Step 2: Run the complete PostgreSQL matrix serially.**

```bash
GC_TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/gc_test' uv run pytest -q
```

- [x] **Step 3: Inspect the final diff.** Confirm all three call sites use the shared token, no SQLite network call is inside a database writer transaction, stale claims are reclaimable, and no regression relies on a mock database or an event-loop-local lock.

- [x] **Step 4: Commit the issue fix.**

```bash
git add src/governance_controller/governance_controller/models/execution.py src/governance_controller/governance_controller/db.py src/governance_controller/governance_controller/services/cancellation_service.py src/governance_controller/governance_controller/services/approval_service.py src/governance_controller/governance_controller/services/verification_service.py src/governance_controller/governance_controller/services/stuck_execution_poller.py src/governance_controller/tests/test_audit_log.py src/governance_controller/tests/test_approval_concurrency.py src/governance_controller/tests/test_verification_service.py src/governance_controller/tests/test_stuck_execution_poller.py docs/NEXT_STEPS.md
git commit -m "fix(concurrency): use durable cancellation claims; Fixes #293"
```
