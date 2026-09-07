# Phase 2 Open Issues Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every open Phase 2 critical/high issue, sweep the related recurring policy and CAS defect classes, and close additional medium/low issues when the fixes can be verified honestly.

**Architecture:** The embedded PolicyEngine remains authoritative and uses parsed command semantics shared with the OPA input. Recovery pollers use durable audit evidence and PostgreSQL row locks without adding a generic terminal-task resurrection edge. In-memory intake accounting is protected for the threadpool execution path, while audit recovery work is bounded by a dedicated queue or an equivalent query whose real PostgreSQL plan is demonstrated to be bounded.

**Tech Stack:** Python 3.13, FastAPI, SQLModel/SQLAlchemy async sessions, PostgreSQL, SQLite, pytest, Rego/OPA 1.19.1, Docker Compose, uv.

**Spec:** `docs/superpowers/specs/2026-09-01-phase2-recovery-and-intake-hardening-design.md`

## Global Constraints

- The Governance Controller remains authoritative; Plane is a projection and macro-agent is an execution adapter.
- Do not modify macro-agent internals or add external dependencies.
- Every severity:critical/high fix lands with a regression test that is red on the parent commit and green on the fix commit.
- Every concurrency/CAS regression uses real PostgreSQL and independent concurrent sessions or real threadpool requests; mocks cannot prove the fix.
- RISK-16 requires inspecting every modified CAS/write-before-outcome path; RISK-19 requires inspecting every decision-making re-read for `populate_existing=True` or `refresh()`.
- Embedded policy runs before OPA and OPA cannot weaken an embedded denial.
- Do not declare the phase gate clean while any critical/high issue remains open.

---

### Task 1: Harden Command Policy and Persistent Control-File Boundaries

**Issues:** #317, #318, #319, related open #134, and the #327 policy false-positive regression.

**Files:**
- Modify: `src/governance_controller/governance_controller/services/policy_engine.py`
- Modify: `src/governance_controller/governance_controller/services/policy_engine_backend.py`
- Modify: `src/governance_controller/policies/opa/governance.rego`
- Test: `src/governance_controller/tests/test_policy_engine.py`
- Test: `src/governance_controller/tests/test_opa_client.py`
- Test: `src/governance_controller/policies/opa/governance_test.rego`

**Interfaces:**
- `_has_git_dangerous_config(argv)` recognizes `-c`, `--config`, `-cKEY=VALUE`, `--config=KEY=VALUE`, plain `git config` after global options, and `--config-env=KEY=ENV` while only treating the actual git subcommand position as `config`.
- `_has_sed_dangerous_flag(argv)` rejects executable sed commands and all file-I/O commands (`r`, `R`, `w`, `W`, `s///w`, `s///W`) without rejecting letters inside a regex or a safe literal filename.
- `_extract_command_paths()` extracts option-glued tar directory paths and sed file-I/O arguments before forbidden-path comparison.
- Both policy backends reject explicit writes/references under `.git/config` and `.git/hooks`, while `git add config core.editor` remains allowed.

- [ ] **Step 1: Add failing embedded regressions.** Add cases for all six protected keys through `git --config-env=<key>=VALUE commit --amend`, direct `cp source victim/.git/config`, `mv source victim/.git/hooks/pre-commit`, `sed -n '1w <forbidden>/marker' input`, `sed 's/x/y/W <forbidden>/marker' input`, and `tar --directory=<forbidden> -xf archive.tar`. Add a safe `git add config core.editor` case.
- [ ] **Step 2: Run the new embedded tests and confirm RED.**

```bash
uv run pytest tests/test_policy_engine.py -k 'config_env or control_file or sed_file_io or tar_directory or literal_config' -q
```

Expected: the new bypass cases are allowed or the forbidden path is missed, and the literal filename case is incorrectly denied.

- [ ] **Step 3: Add failing OPA regressions.** Add Rego tests for the same `--config-env`, control-file, sed file-I/O, and literal `git add` cases using `parsed_commands` records where the Python backend supplies them.
- [ ] **Step 4: Run OPA tests and confirm RED.**

```bash
docker run --rm -v "$PWD/policies/opa:/policy:ro" openpolicyagent/opa:1.19.1 test /policy
```

Expected: the new bypass cases are allowed and the safe literal filename case is denied or not distinguished.

- [ ] **Step 5: Implement one shared Python semantic pass.** Add the smallest parser helpers needed to locate git global options/subcommand, parse sed command positions and substitution flags, extract sed/tar paths, and reject `.git/config`/`.git/hooks` targets. Feed those facts into both command-execution validation and forbidden-path extraction. Keep all existing allowlist and fail-closed parse behavior.
- [ ] **Step 6: Mirror the semantic rules in Rego.** Add parsed-argv rules for `--config-env`, git subcommand positioning, control-file path tokens, sed command/file-I/O forms, and tar directory path extraction. Preserve existing deny rules and data-minimized input shape.
- [ ] **Step 7: Run targeted tests and confirm GREEN.**

```bash
uv run pytest tests/test_policy_engine.py tests/test_opa_client.py -q
docker run --rm -v "$PWD/policies/opa:/policy:ro" openpolicyagent/opa:1.19.1 check /policy
docker run --rm -v "$PWD/policies/opa:/policy:ro" openpolicyagent/opa:1.19.1 test /policy
```

- [ ] **Step 8: Commit the policy hardening unit.**

```bash
git add src/governance_controller/governance_controller/services/policy_engine.py src/governance_controller/governance_controller/services/policy_engine_backend.py src/governance_controller/policies/opa/governance.rego src/governance_controller/policies/opa/governance_test.rego src/governance_controller/tests/test_policy_engine.py src/governance_controller/tests/test_opa_client.py
git commit -m "fix(phase-2): close command-policy bypass family; tests: config-env, control-file, sed I/O, tar directory, and literal git filenames"
```

The commit message must name the exact regression categories and include `Fixes #317`, `Fixes #318`, `Fixes #319`, and `Fixes #134` when the targeted live tests pass.

### Task 2: Make the OPA Compose Deliverable Runnable

**Issue:** #320.

**Files:**
- Modify: `src/governance_controller/docker-compose.yml`
- Modify: `.github/workflows/ci.yml`
- Test: `src/governance_controller/tests/test_opa_compose.py`

- [ ] **Step 1: Write a failing compose contract test.** Use `docker compose -f <file> --profile opa config --format json` and stdlib JSON parsing to assert image `openpolicyagent/opa:1.19.1`, `profiles: ["opa"]`, read-only `/policy` mount, `/policy` in the server command, and a shell-free OPA healthcheck.
- [ ] **Step 2: Run it and confirm RED.**

```bash
uv run pytest tests/test_opa_compose.py -q
```

Expected: current image, profile, mount, command, and healthcheck assertions fail.

- [ ] **Step 3: Update Compose and CI together.** Pin the CI-validated OPA image, make the service opt-in, mount the policy read-only, load `/policy`, use `CMD` with an OPA-native policy check, and run CI checks through the profiled Compose service rather than a second hardcoded image tag.
- [ ] **Step 4: Run the contract, exact image checks, and OPA suite.**

```bash
uv run pytest tests/test_opa_compose.py -q
docker compose --profile opa run --rm --no-deps opa check /policy
docker compose --profile opa run --rm --no-deps opa test /policy
```

- [ ] **Step 5: Commit the OPA unit.**

```bash
git add src/governance_controller/docker-compose.yml .github/workflows/ci.yml src/governance_controller/tests/test_opa_compose.py
git commit -m "fix(phase-2): make OPA compose policy runnable; tests: exact image, profile, mount, command, and healthcheck"
```

Include `Fixes #320`.

### Task 3: Coordinate Recovery Outcomes and Sweep RISK-16

**Issues:** #321, #322, #325 item 1, and related phase-1 #305 plus every confirmed same-shaped recovery write path.

**Files:**
- Modify: `src/governance_controller/governance_controller/services/stuck_execution_poller.py`
- Modify: `src/governance_controller/governance_controller/services/verification_service.py`
- Modify: `src/governance_controller/governance_controller/services/approval_service.py`
- Test: `src/governance_controller/tests/test_stuck_execution_poller.py`
- Test: `src/governance_controller/tests/test_verification_service.py`
- Test: `src/governance_controller/tests/test_approval_concurrency.py`

- [ ] **Step 1: Add a real PostgreSQL #321 regression.** Seed a stale `verification_retry_pending` marker, a retry sentinel execution, and a matching task. Run the poller twice against separate sessions as needed and assert the first recovery outcome from `_poll_retry_start` prevents a second retry execution or macro-agent start.
- [ ] **Step 2: Run the new #321 test against real PostgreSQL and confirm RED.**

```bash
GC_TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/gc_test' uv run pytest tests/test_stuck_execution_poller.py -k retry_start_terminal_outcome -q
```

- [ ] **Step 3: Add #322 and #325 sweeper regressions.** Seed stale `terminal_failure_alert` markers for terminal tasks and stale `reconciliation_state_fix` markers whose task state changed. Assert terminal failure markers remain pending without a delivery outcome and reconciliation markers resolve exactly once.
- [ ] **Step 4: Run those tests and confirm RED.**

```bash
uv run pytest tests/test_stuck_execution_poller.py -k 'terminal_failure_alert or reconciliation_state_fix' -q
```

- [ ] **Step 5: Add real PostgreSQL RISK-16 tests for every discovered CAS-loss path.** Use independent sessions to force task changes while initial approval start failure, verification retry start failure, and running status failure are being handled. Assert the losing execution is finalized with `ended_at`, is excluded from active execution queries, and has a durable outcome audit without mutating the concurrent winner.
- [ ] **Step 6: Run the CAS tests against real PostgreSQL and confirm RED.**

```bash
GC_TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/gc_test' uv run pytest tests/test_approval_concurrency.py tests/test_verification_service.py tests/test_stuck_execution_poller.py -k 'cas_loss or concurrent.*start or status.*race' -q
```

- [ ] **Step 7: Implement coordination and finalization.** Include the retry-start failure audit as a terminal outcome keyed by attempt, keep terminal-task resurrection scoped to the verification retry method, reverse the terminal-alert resolve predicate, handle `reconciliation_state_fix` with the same pending-state divergence rule, and ensure all losing executions are finalized only after their CAS result is known. Re-read changed rows with `populate_existing=True`.
- [ ] **Step 8: Lock and batch the Plane marker sweeper.** On PostgreSQL select pending markers with `FOR UPDATE SKIP LOCKED`, process the bounded batch, and commit once after the batch so the marker cannot be completed twice.
- [ ] **Step 9: Run targeted SQLite and PostgreSQL suites and confirm GREEN.**

```bash
uv run pytest tests/test_stuck_execution_poller.py tests/test_verification_service.py tests/test_approval_concurrency.py -q
GC_TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/gc_test' uv run pytest tests/test_stuck_execution_poller.py tests/test_verification_service.py tests/test_approval_concurrency.py -q
```

- [ ] **Step 10: Commit the recovery unit.**

```bash
git add src/governance_controller/governance_controller/services/stuck_execution_poller.py src/governance_controller/governance_controller/services/verification_service.py src/governance_controller/governance_controller/services/approval_service.py src/governance_controller/tests/test_stuck_execution_poller.py src/governance_controller/tests/test_verification_service.py src/governance_controller/tests/test_approval_concurrency.py
git commit -m "fix(phase-2): coordinate recovery terminal outcomes; tests: live Postgres retry, alert, reconciliation, and CAS-loss races"
```

Include `Fixes #321`, `Fixes #322`, and `Fixes #305`; include `Fixes #325` only after its item-1 regression is green.

### Task 4: Prevent Duplicate Cancellation Recovery

**Issue:** #323.

**Files:**
- Modify: `src/governance_controller/governance_controller/services/stuck_execution_poller.py`
- Test: `src/governance_controller/tests/test_stuck_execution_poller.py`

- [ ] **Step 1: Strengthen the existing live test with a deterministic interleave.** Use two independent PostgreSQL sessions, make poller A pause on its second cancel after its current code commits the first marker, start poller B, and assert the pre-fix implementation duplicates completion/cancel calls.
- [ ] **Step 2: Run the deterministic test against real PostgreSQL and confirm RED.**
- [ ] **Step 3: Remove per-marker commits from the locked cancellation batch and commit once after all markers are processed.** Preserve durable failure/completion records and dry-run behavior.
- [ ] **Step 4: Run the same test and the cancellation suite against PostgreSQL and confirm GREEN.**
- [ ] **Step 5: Commit with the test named.**

```bash
git add src/governance_controller/governance_controller/services/stuck_execution_poller.py src/governance_controller/tests/test_stuck_execution_poller.py
git commit -m "fix(phase-2): hold cancellation batch claims through processing; tests: deterministic live Postgres poller interleave"
```

Include `Fixes #323`.

### Task 5: Make Per-IP Intake Accounting Thread-Safe

**Issue:** #324.

**Files:**
- Modify: `src/governance_controller/governance_controller/middleware.py`
- Test: `src/governance_controller/tests/test_rate_limit.py`

- [ ] **Step 1: Add a genuine concurrent ASGI regression.** Configure a five-request per-IP intake cap, issue unique authenticated requests through the real FastAPI app with `asyncio.gather()`, force the real synchronous dependency into multiple AnyIO worker threads, and assert exactly five requests are admitted. Use no mock of the limiter or direct closure call.
- [ ] **Step 2: Run the test and confirm RED.**

```bash
uv run pytest tests/test_rate_limit.py -k concurrent_authenticated_intake_ip_budget -q
```

- [ ] **Step 3: Guard the entire in-memory intake bucket check/eviction/append/release sequence with one module-level `threading.Lock`.** Keep the lock free of I/O and do not replace it with `asyncio.Lock`; reset state under the same lock.
- [ ] **Step 4: Run the real concurrent test and all intake/rate-limit tests against SQLite and PostgreSQL where applicable.**
- [ ] **Step 5: Commit with the test named.**

```bash
git add src/governance_controller/governance_controller/middleware.py src/governance_controller/tests/test_rate_limit.py
git commit -m "fix(phase-2): serialize threadpooled intake IP budgets; tests: real concurrent FastAPI requests"
```

Include `Fixes #324`.

### Task 6: Finish Operational Low Issues Only With Differentiating Tests

**Issues:** #308, #326, #327, and remaining functional part of #325 if not already closed.

- [ ] **Step 1: For #308, add a durable recovery queue or equivalent schema-backed selector.** The selector must read only unresolved cancellation work, use `LIMIT`/`SKIP LOCKED`, preserve retry semantics, and have a PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` regression whose plan does not scan historical `auditlog` rows. Add migration/table creation and same-transaction enqueue/dequeue behavior only if the existing schema lifecycle supports it without an untracked migration.
- [ ] **Step 2: Run #308's test against the parent commit and a 100k+ row real PostgreSQL dataset; reject any test that passes identically or any plan that scales with resolved history.**
- [ ] **Step 3: For #326, add a failing test that makes `.all()` unavailable and implement `AsyncSession.stream_scalars()` with a bounded fetch size and previous-row tracking. Fix the known mypy column-expression error in the same change.
- [ ] **Step 4: For #327, reject empty/whitespace CLI idempotency keys explicitly and fix the git subcommand-position false positive if not already covered by Task 1. Add CLI and embedded/OPA regressions.
- [ ] **Step 5: Run targeted tests, OPA checks, and PostgreSQL scale checks; commit each logically separate low issue with its named differentiating test and `Fixes #...`.

### Task 7: Final Verification, Documentation, and Push

**Files:**
- Modify: `docs/NEXT_STEPS.md`
- Modify: `specs/SPEC-10-phase-plan.md`

- [ ] **Step 1: Run the complete verification matrix.**

```bash
uv run pytest -q
GC_TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/gc_test' uv run pytest -q
uv run ruff check governance_controller tests
uv run mypy governance_controller
docker run --rm -v "$PWD/policies/opa:/policy:ro" openpolicyagent/opa:1.19.1 check /policy
docker run --rm -v "$PWD/policies/opa:/policy:ro" openpolicyagent/opa:1.19.1 test /policy
uv run --project ../macro_agent_service pytest ../macro_agent_service/tests -q
```

- [ ] **Step 2: Inspect every commit and diff for missing same-commit regression coverage, uncommitted changes, stale identity-map reads, writes before CAS outcomes, and tests that pass on both parent and child.
- [ ] **Step 3: Update status docs from live GitHub issue queries.** Keep any unresolved medium/low items listed; never claim Phase 2 complete while critical/high queries return an issue.
- [ ] **Step 4: Query both critical and high labels separately, then push `main`.**

```bash
gh issue list --repo rusnino/ai-software-factory --label phase-2 --label severity:critical --state open
gh issue list --repo rusnino/ai-software-factory --label phase-2 --label severity:high --state open
```
