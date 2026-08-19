# REVIEW-023: Full fresh review — gate reopened after reaching zero open rows

Date: 2026-08-19
Scope: a full fresh review of the entire Phase 1 codebase, explicitly requested to look for newly
found gaps even without gating on old ones (moot at the time, since REVIEW-022 had just reached zero
`OPEN`/`IN_PROGRESS` rows across all 94 tracked gaps — the first such milestone in this 23-round
series). Three parallel reviewers: (A) core services, (B) adapters/API surface, (C) specs/docs/
requirements/tests conformance. I personally re-verified the two most severe individual claims
(`GAP-095`'s missing commit + unbounded subprocess, `GAP-096`'s literal-string actor blocklist, and
`GAP-097`'s exception-path dedup gap) directly from source before writing this up.

## Baseline

`uv run pytest -q` → 212 passed. `uv run ruff check .` → clean. `uv run mypy governance_controller` →
0 errors. `grep -rn "ponytail:"` → only the one already-`ACCEPTED` GAP-072 marker, no new self-disclosed
corner-cuts. None of this round's findings are tooling-detectable.

## Verdict: 3 new HIGH, 5 new MEDIUM (1 closed directly as a doc fix), 4 new LOW findings. Zero
## regressions among the 94 previously-closed gaps.

### HIGH — personally re-verified from source

**`GAP-095`** — `EventBridge.handle()`'s `AGENT_REVIEW` transition (`event_bridge.py:137`) is never
committed before falling into `verifier.verify_and_advance(...)` (line 181), which runs the task's
verification shell commands via `asyncio.create_subprocess_shell()` + `await proc.communicate()`
(`verification_service.py:38-45`) — confirmed by reading both sites directly: no commit between them,
no timeout anywhere around the subprocess call (`macro_agent_timeout_seconds` only bounds the outbound
httpx client, never this local execution). This is the same defect class `GAP-080` fixed in
`approval_service.py::_trigger_execution`, in a sibling call path that fix never touched — and it's
worse here, since GAP-080's exposure was bounded by a 30s HTTP timeout and this one has no bound at
all. The reporting agent live-reproduced this against a real Postgres container: a `sleep 4`
completion-contract check held a real MVCC row lock for the full duration, blocking a concurrent write
to the same task row for 3.03s.

**`GAP-096`** — `PermissionService.may_approve()` (`permission_service.py:24`): `if actor in {"system",
"agent"}: return False` — confirmed by reading the file directly, this is an exact-literal-string
check with no pattern/prefix matching. `specs/SPEC-03-governance.md` itself documents `system:*`
identifiers (`system:meta-orch`, `system:controller`, `system:macro-agent`) as the real actor format
for non-human callers — any of those sails past this check, and `may_approve()` unconditionally
returns `True` for `ApprovalType.PLAN` afterward. Live-reproduced through the real ASGI app: a task
proposed by `agent-1`, approved via `actor="system:macro-agent"`, succeeds with no rejection. This is
the first of 23 rounds to check the blocklist's *contents* against SPEC-03's own documented
convention, not just whether `PermissionService` is wired in at all (`GAP-001`'s original scope).

**`GAP-097`** — `VerificationService._start_retry_execution()` commits the retry's state transition
before calling `executor.start()` (correct, per `GAP-080`'s pattern) — but if that call raises, the
exception propagates out of `verify_and_advance()` and `EventBridge.handle()` before
`_record_processed_event()` (called only after `verify_and_advance()` returns without raising) ever
runs. Confirmed by reading the exception handler directly: it audits and re-raises `RuntimeError`,
with no path back to record the dedup key. SPEC-05 §5.4 requires redelivery on failure — so the exact
same event, now undeduplicated, gets reprocessed as new: a second retry, a second `Execution` row, a
second live macro-agent call, for one underlying event. The reporting agent live-reproduced this with
a mocked `executor.start()` that fails once then succeeds.

### MEDIUM

- **`GAP-098`**: `GAP-094`'s SQLite fix checks `startswith("sqlite")` broadly, silently dropping
  configured pool settings for file-based SQLite too (which uses a pool class that actually supports
  them) — the identical `RISK-18` pattern one level deeper than `GAP-094` itself. Live-reproduced:
  `pool.size()` reports SQLAlchemy's silent default `5`, not the configured `7`.
- **`GAP-099`**: `EventBridge` validates transitions as generic `(state, state)` pairs with no
  awareness of which event drove them — 5 of the 6 event types mapped to `RUNNING` can silently
  un-block a `BLOCKED` task, not just the intended `conflict:resolved`. Live-reproduced with
  `stream:committed`.
- **`GAP-100`**: `api/approvals.py`'s 403 handler splits a formatted violations string on `,` — a
  comma inside a caller-controlled path (`PolicyEngine`'s forbidden-path violation message) corrupts
  the structured `violations` array into bogus fragments. Live-reproduced.
- **`GAP-101`**: `TaskContract.execution.role` is policy-validated (`GAP-032`) but never included in
  the outbound macro-agent payload — the same "validated but not forwarded" class `GAP-051` fixed for
  sibling fields, missed for this one.
- **`GAP-102`** (closed directly): `SPEC-03` §3.3's response-code list was missing `404`/`503`,
  corrected.

### LOW

- **`GAP-103`**: `StateMachine.atomic_transition_with_fields` is dead code with a factually false
  docstring (independently found by two of the three parallel reviewers this round) — same
  orphaned-dead-code pattern already closed four times (`GAP-008/029/063/082`).
- **`GAP-104`**: a test-isolation leak in `test_harness_registry.py` (mutates the module-level registry
  singleton with no cleanup), currently masked only by alphabetical test-collection order.
- **`GAP-105`**: the SDD `progress.md` ledger is stale again — third occurrence of the `GAP-016`/
  `GAP-071` pattern, now missing Review 14 through 22.
- **`GAP-106`**: `TaskResponse` doesn't expose `execution_attempts`, undermining `GAP-090`'s own
  "poll `GET /tasks/{id}`" discovery story for retry status.

## Also corrected directly this round (doc-accuracy, found by the specs/docs reviewer)

- `SPEC-10-phase-plan.md`'s `[^phase1-durability]`/`[^phase1-verification]` footnotes were stale,
  still describing `GAP-080`/`GAP-077` as open five and two rounds respectively after they closed —
  updated to reference the new `GAP-095`/`GAP-097` findings that keep the gate open now instead.
- `docs/NEXT_STEPS.md`'s former candidate-task item 10 cited `GAP-077`/`085`/`086`/`087` as "all OPEN,
  HIGH" — all four had been closed for one to four rounds. Rewritten.
- `RISK-18` updated to cross-reference `GAP-094`/`GAP-098` alongside `GAP-077` as manifestations of the
  same defect class.

## Verification

`GAP-095`, `GAP-096`, and `GAP-097`'s root cause were all personally re-confirmed by reading the exact
source directly (the missing commit and unbounded subprocess call; the literal-string blocklist; the
exception handler skipping dedup-key recording) — not just trusting the reporting agents' descriptions.
The three reporting agents' live reproductions (real Postgres container for `GAP-095`, real ASGI app
for `GAP-096`, mocked-failure-then-success for `GAP-097`) are consistent with this series' established
rigor. No application or test code was modified — all changes this round are to documentation/ledger
files: `reviews/GAPS.md`, `docs/NEXT_STEPS.md`, `specs/SPEC-10-phase-plan.md`,
`specs/SPEC-03-governance.md`, `requirements/REQUIREMENTS.md`.

## Milestone note

REVIEW-022 was the first round to reach zero open rows and have that state survive an immediate
re-check. REVIEW-023 shows why that shouldn't be read as "nothing left to find": it's the state of
known defects at a point in time, and a genuinely fresh, differently-angled pass (this time explicitly
probing the interaction between recently-fixed pieces — verification timing, the permission
blocklist's literal contents, retry-exception paths) found three new HIGH issues in code every prior
round had already looked at from a narrower angle. The gate is open again, on three well-understood,
independently-confirmed defects.
