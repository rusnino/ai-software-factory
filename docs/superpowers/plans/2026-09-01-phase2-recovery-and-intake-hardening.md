# Phase 2 Recovery and Intake Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the reviewed Phase 2 cancellation, intake concurrency, and OPA parity gaps without weakening Controller authority.

**Architecture:** Durable append-only audit markers identify interrupted execution handoffs. The existing `StuckExecutionPoller` resumes only through CAS-guarded Controller methods and retries failed external cancellation. Intake authentication releases a pre-auth IP admission token only after all endpoint authentication succeeds, while PostgreSQL sender quota checks run under a transaction-scoped advisory lock. OPA follows the embedded parsed-argv policy but cannot override it.

**Tech Stack:** Python 3.13, FastAPI, SQLModel/SQLAlchemy async sessions, PostgreSQL, SQLite test fixtures, pytest, Rego/OPA 1.19.1, uv.

**Spec:** `docs/superpowers/specs/2026-09-01-phase2-recovery-and-intake-hardening-design.md`

## Global Constraints

- The Governance Controller remains authoritative; Plane is a projection and macro-agent is an execution adapter.
- Keep existing `TaskState` values and do not add a generic `FAILED -> RUNNING` transition.
- Do not modify macro-agent internals or add external dependencies.
- Durable markers must be committed before non-authoritative or cancellable external calls.
- PostgreSQL concurrency claims require independent real sessions; SQLite tests must remain green.
- Embedded `PolicyEngine` remains authoritative; OPA is additive-only.
- Every production behavior change requires a RED test followed by a GREEN test.

---

### Task 1: Recover Interrupted Execution Handoffs

**Files:**
- Modify: `src/governance_controller/governance_controller/services/approval_service.py:304-314,448-691`
- Modify: `src/governance_controller/governance_controller/services/verification_service.py:432-613,676-877`
- Modify: `src/governance_controller/governance_controller/services/stuck_execution_poller.py:75-151`
- Test: `src/governance_controller/tests/test_approval_service.py`
- Test: `src/governance_controller/tests/test_approval_concurrency.py`
- Test: `src/governance_controller/tests/test_verification_service.py`
- Test: `src/governance_controller/tests/test_stuck_execution_poller.py`

**Interfaces:**
- `ApprovalService.approve()` writes `execution_start_pending` before the execution handoff and starts execution before any optional approval-state Plane projection.
- `StuckExecutionPoller.poll()` runs pending-cancellation cleanup, stale approved-start recovery, and stale verification-retry recovery in addition to its existing passes.
- Recovery uses `TaskContract(**task.task_contract_json)`, `TaskService.get_profile_by_project_id()`, and the existing `MacroAgentExecutor`.
- Audit completion events are `execution_start_recovered`, `verification_retry_recovered`, and `execution_cancel_completed`; unresolved cleanup uses `execution_cancel_pending`.

- [ ] **Step 1: Write the failing approval recovery tests.** Add a test that patches `_trigger_execution` to raise `asyncio.CancelledError` through the real `get_db()` dependency and then asserts a fresh session still contains `EXEC_APPROVED`, the approval row, and an `execution_start_pending` audit row. Add a poller test that seeds a stale `EXEC_APPROVED` task, stored project profile, valid task contract, and pending marker, then asserts a fake executor is started once and the task reaches `RUNNING`.

```python
async def test_cancelled_execution_handoff_leaves_recoverable_marker(
    service: ApprovalService,
    task: Task,
    contract: TaskContract,
    profile: ProjectProfile,
    local_session: sessionmaker,
    task_id: str,
):
    async def cancel_before_start(*_args, **_kwargs):
        raise asyncio.CancelledError

    service._trigger_execution = cancel_before_start  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        async with asynccontextmanager(get_db)() as db:
            service.db = db
            await service.approve(
                task=task,
                contract=contract,
                profile=profile,
                approval_type=ApprovalType.EXECUTION,
                source="test",
                actor="admin",
                idempotency_key="key-cancel-start",
            )

    async with local_session() as check:
        task = await check.scalar(select(Task).where(Task.id == task_id))
        assert task is not None and task.state == TaskState.EXEC_APPROVED
        audit_rows = (
            await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
        ).scalars().all()
        assert any(row.event_type == "execution_start_pending" for row in audit_rows)
```

- [ ] **Step 2: Run only the new approval tests and verify RED.**

Run: `uv run pytest tests/test_approval_service.py tests/test_stuck_execution_poller.py -k 'recoverable_marker or approved' -q`

Expected: FAIL because no `execution_start_pending` marker or `EXEC_APPROVED` recovery pass exists.

- [ ] **Step 3: Write the failing verification retry and cleanup tests.** Add tests for cancellation before the scoped retry transition, stale `verification_retry_pending` recovery from `FAILED`, and a failed CAS cleanup whose pending cancellation is successfully retried by the poller. Add a PostgreSQL-only independent-session test proving retry attachment increments `Task.version`, so a stale `_poll_retry_start` CAS cannot mark the attached execution failed.

```python
async def test_cancelled_verification_retry_leaves_recoverable_marker(
    db: AsyncSession,
    task: Task,
    contract: TaskContract,
    profile: ProjectProfile,
    fake_executor: MacroAgentExecutor,
    local_session: sessionmaker,
):
    async def cancel_transition(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        StateMachine,
        "atomic_transition_from_failed_to_running",
        staticmethod(cancel_transition),
    )
    with pytest.raises(asyncio.CancelledError):
        await VerificationService.verify_and_advance(
            db, task, contract, profile=profile, executor=fake_executor
        )

    await db.rollback()
    async with local_session() as check:
        assert any(
            row.event_type == "verification_retry_pending"
            for row in (await check.execute(select(AuditLog))).scalars()
        )
```

- [ ] **Step 4: Run the new verification tests and verify RED.**

Run: `uv run pytest tests/test_verification_service.py tests/test_stuck_execution_poller.py -k 'pending or cleanup or version or retry or cancel' -q`

Expected: FAIL because pending retry/cancel markers and recovery methods do not exist, and retry attachment does not change the task version.

- [ ] **Step 5: Implement the approval marker and ordering.** In `ApprovalService.approve()`, for `ApprovalType.EXECUTION`, append and commit `execution_start_pending` after the approval record. Call `_trigger_execution()` before the generic `_project_state_to_plane()` call; `_trigger_execution()` already projects the authoritative `RUNNING` state. Leave plan and merge projection behavior unchanged. Before each CAS-loser cancellation, commit `execution_cancel_pending`; log `execution_cancel_completed` after a successful cancel and preserve the existing failure audit on exceptions.

- [ ] **Step 6: Implement verification retry handoff markers.** In `VerificationService.verify_and_advance()`, for retries append and commit `verification_retry_pending` with the next attempt and report before the scoped retry transition. Perform the scoped `FAILED -> RUNNING` transition, create/commit the retry execution, and record `verification_retry_recovered` before Plane failure feedback. Keep terminal failure behavior unchanged. Add pending/completed cancellation events around retry-start CAS-loss cleanup.

- [ ] **Step 7: Implement poller recovery with CAS guards.** Extend `StuckExecutionPoller.poll()` with:

```python
pending_cancellations = await self._poll_pending_cancellations()
approved_starts = await self._poll_execution_start_pending()
retry_starts = await self._poll_verification_retry_pending()
actions = pending_cancellations + approved_starts + retry_starts
```

For approved starts, select stale `execution_start_pending` rows joined to tasks still in `EXEC_APPROVED`, load the stored contract/profile, and call `_trigger_execution()` with `actor="system:poller"`. For retry markers, require the marker attempt to equal the task's current attempt or next attempt, use the scoped CAS transition when the task is `FAILED`, and call `_start_retry_execution()` only when no current execution/sentinel exists. For pending cancellations, select unresolved pending markers, skip a run now legitimately attached to the task, call `executor.cancel()`, and append the completion event. Catch ordinary recovery errors and commit an audit failure; never swallow `asyncio.CancelledError`.

- [ ] **Step 8: Fix retry attachment CAS versioning.** In `_start_retry_execution()`, update both `latest_macro_agent_run_id` and `version=Task.version + 1` in the CAS statement, then update the in-memory `task.version` only after the rowcount is non-zero. Keep the sentinel predicate and cancellation path intact.

- [ ] **Step 9: Run all recovery tests and verify GREEN.**

Run: `uv run pytest tests/test_approval_service.py tests/test_approval_concurrency.py tests/test_verification_service.py tests/test_stuck_execution_poller.py -q`

Expected: all selected tests pass; PostgreSQL-only tests run when `GC_TEST_DATABASE_URL` is PostgreSQL.

- [ ] **Step 10: Commit the recovery unit.**

```bash
git add src/governance_controller/governance_controller/services/approval_service.py src/governance_controller/governance_controller/services/verification_service.py src/governance_controller/governance_controller/services/stuck_execution_poller.py src/governance_controller/tests/test_approval_service.py src/governance_controller/tests/test_approval_concurrency.py src/governance_controller/tests/test_verification_service.py src/governance_controller/tests/test_stuck_execution_poller.py
git commit -m "fix(phase-2): recover interrupted execution handoffs"
```

### Task 2: Harden Intake Admission and Sender Quotas

**Files:**
- Modify: `src/governance_controller/governance_controller/middleware.py:29-136`
- Modify: `src/governance_controller/governance_controller/api/intake.py:32-221`
- Modify: `src/governance_controller/governance_controller/services/idea_ingestion_service.py:1-186`
- Test: `src/governance_controller/tests/test_rate_limit.py`
- Test: `src/governance_controller/tests/test_idea_ingestion_service.py`

**Interfaces:**
- `InMemoryRateLimitMiddleware` stores an idempotent `release_intake_rate_limit` callback in `scope["state"]` for intake requests.
- `_release_intake_admission(request: Request)` invokes that callback after all endpoint-specific authentication succeeds.
- `IdeaIngestionService._guard_duplicate_and_rate_limit()` acquires a PostgreSQL transaction-scoped advisory lock before both duplicate and quota queries.

- [ ] **Step 1: Add the failing pre-auth limiter test.** In `test_rate_limit.py`, configure `rate_limit_per_minute=2`, send three missing-secret `POST /intake/idea` requests from one client IP, and assert `[401, 401, 429]`.

```python
responses = [
    await async_client.post("/intake/idea", json=payload("unauth-1")),
    await async_client.post("/intake/idea", json=payload("unauth-2")),
    await async_client.post("/intake/idea", json=payload("unauth-3")),
]
assert [response.status_code for response in responses] == [401, 401, 429]
```

- [ ] **Step 2: Run the pre-auth test and verify RED.**

Run: `uv run pytest tests/test_rate_limit.py -k pre_auth -q`

Expected: FAIL because all `/intake/*` paths currently bypass the middleware.

- [ ] **Step 3: Add the failing PostgreSQL sender-quota test.** Use two independent sessions and two source IDs for one sender with `intake_rate_limit_per_minute=1`. Have the first service pause after the real advisory lock is acquired, assert the second service cannot finish while the first transaction is paused, release the first, then assert exactly one success and one `IntakeRateLimitError`. Add a duplicate-at-limit case asserting `DuplicateIntakeError` wins before quota rejection.

- [ ] **Step 4: Run the quota tests and verify RED.**

Run: `GC_TEST_DATABASE_URL="$GC_TEST_DATABASE_URL" uv run pytest tests/test_idea_ingestion_service.py tests/test_rate_limit.py -k quota -q`

Expected: the new real-session quota race fails because both sessions can pass the count query before either insert commits.

- [ ] **Step 5: Implement two-stage intake admission.** Remove the early intake exemption. Reserve a token in the existing per-IP deque for every intake request, attach an idempotent release closure in `scope["state"]`, and retain the 409 response fallback. Add a reset for any new tracking structures. In email and generic idea routes release after shared-secret authentication; in Telegram release only after both shared-secret and Telegram webhook authentication succeed. Invalid authentication leaves its token consumed.

- [ ] **Step 6: Implement sender advisory locking.** Add a stable signed 64-bit lock key derived from `"intake-sender:" + sender.strip().casefold()`. On PostgreSQL execute `SELECT pg_advisory_xact_lock(:key)` before the duplicate `SELECT`; keep the lock through sender count, insert/flush, and the existing commit. Use the normalized sender for quota comparison and persisted intake records so case/whitespace variants share one quota. Do not add a migration or change SQLite locking behavior.

- [ ] **Step 7: Run intake tests and verify GREEN.**

Run: `uv run pytest tests/test_rate_limit.py tests/test_idea_ingestion_service.py tests/test_intake.py -q`

Expected: the pre-auth limiter, sequential duplicate release, in-flight duplicate burst, and sender-quota tests all pass.

- [ ] **Step 8: Commit the intake unit.**

```bash
git add src/governance_controller/governance_controller/middleware.py src/governance_controller/governance_controller/api/intake.py src/governance_controller/governance_controller/services/idea_ingestion_service.py src/governance_controller/tests/test_rate_limit.py src/governance_controller/tests/test_idea_ingestion_service.py
git commit -m "fix(phase-2): enforce intake authentication and sender quotas"
```

### Task 3: Restore Embedded and OPA Command-Policy Parity

**Files:**
- Modify: `src/governance_controller/governance_controller/services/policy_engine.py:543-614`
- Modify: `src/governance_controller/policies/opa/governance.rego:180-235,516-553`
- Test: `src/governance_controller/tests/test_policy_engine.py:395-408,830-853`
- Test: `src/governance_controller/policies/opa/governance_test.rego:54-165`

**Interfaces:**
- `_has_sed_dangerous_flag(argv)` recognizes `-e`, `-eSCRIPT`, `--expression`, and `--expression=SCRIPT` while inspecting the parsed script.
- `_is_recursive_force_rm(argv)` remains case-insensitive for aggregate short flags and long `--no-preserve-root`.
- Rego uses `input.parsed_commands[*].argv` when available and applies equivalent flag/script predicates.

- [ ] **Step 1: Add the failing parity matrix.** Add Python parameterized cases that assert safe `sed -e 's/foo/bar/g'` is allowed, every quoted/attached/long `s///e` form is denied, `rm --recursive` is allowed as embedded behavior currently defines it, and split/uppercase recursive-force forms are denied. Add corresponding Rego tests using parsed command records rather than fallback raw strings.

```python
@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("sed -e 's/foo/bar/g' file.txt", True),
        ("sed 's/foo/bar/e' file.txt", False),
        ("sed --expression 's/foo/bar/e' file.txt", False),
        ("sed -e's/foo/bar/e' file.txt", False),
        ("rm --recursive /tmp/work", True),
        ("rm -R -F /tmp/work", False),
    ],
)
def test_embedded_command_parity_matrix(command: str, allowed: bool) -> None:
    contract = _make_contract(
        completion_contract=CompletionContract(
            task_id="task-1",
            required=[Check(type="command", command=command)],
            scope_check=ScopeCheck(description="policy parity matrix"),
        )
    )
    result = PolicyEngine.evaluate(contract, _make_profile(), ApprovalType.EXECUTION)
    assert result.allowed is allowed
```

- [ ] **Step 2: Run the Python parity tests and verify RED.**

Run: `uv run pytest tests/test_policy_engine.py -k parity_matrix -q`

Expected: FAIL on the embedded `--expression='s/foo/bar/e'` or `rm` cases before the policy implementation changes.

- [ ] **Step 3: Implement the minimal Python parser correction.** Recognize `--expression=<script>` in `_has_sed_dangerous_flag()` and preserve the existing conservative script checks. Do not change the embedded `rm` semantics to accommodate Rego drift.

- [ ] **Step 4: Implement parsed-argv Rego rules.** Remove the raw command sed regexes and match expression scripts from parsed argv, including attached and long options. Replace the separate case-sensitive/long-rm rules with one case-insensitive aggregate short-flag predicate plus the exact embedded long-option behavior. Keep all existing destructive-command and parse-error rules.

- [ ] **Step 5: Run both policy suites and verify GREEN.**

Run: `uv run pytest tests/test_policy_engine.py tests/test_opa_client.py -q`

Run: `docker run --rm -v "$PWD/policies/opa:/policy:ro" openpolicyagent/opa:1.19.1 check /policy`

Run: `docker run --rm -v "$PWD/policies/opa:/policy:ro" openpolicyagent/opa:1.19.1 test /policy`

Expected: Python tests pass, OPA check succeeds, and every OPA test passes.

- [ ] **Step 6: Commit the policy unit.**

```bash
git add src/governance_controller/governance_controller/services/policy_engine.py src/governance_controller/policies/opa/governance.rego src/governance_controller/policies/opa/governance_test.rego src/governance_controller/tests/test_policy_engine.py
git commit -m "fix(phase-2): align OPA command policy with embedded rules"
```

### Task 4: Integrated Verification and Release Gate

**Files:**
- Modify: `docs/NEXT_STEPS.md`
- Modify: `specs/SPEC-10-phase-plan.md`
- Inspect: `.github/workflows/ci.yml`, `README.md`

- [ ] **Step 1: Run the complete local verification matrix.**

```bash
cd src/governance_controller
uv run pytest -q
GC_TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/gc_test' uv run pytest -q
uv run ruff check src tests
uv run mypy src
cd ../macro_agent_service
uv run pytest -q
uv run ruff check src tests
uv run mypy src
```

- [ ] **Step 2: Review the final diff and run the independent adversarial checks.** Confirm no writes occur before CAS outcomes, no stale identity-map reads determine decisions, all external calls have durable markers, intake invalid-auth attempts consume tokens, and OPA/embedded matrix results agree.

- [ ] **Step 3: Update status docs accurately.** Replace the stale gate-clean claim with the actual current issue list and test counts. Do not mark Phase 2 complete while any critical/high review issue remains open.

- [ ] **Step 4: Request final code review and address every finding.** Run `gh issue list --repo rusnino/ai-software-factory --label severity:critical --state open` and the corresponding high-severity query separately before declaring the gate clear.

- [ ] **Step 5: Close resolved issues and push only after verification.**

```bash
gh issue close 256 --repo rusnino/ai-software-factory
gh issue close 297 --repo rusnino/ai-software-factory
gh issue close 298 --repo rusnino/ai-software-factory
gh issue close 299 --repo rusnino/ai-software-factory
gh issue close 300 --repo rusnino/ai-software-factory
git status --short --branch
git diff origin/main...HEAD
git push origin main
```
