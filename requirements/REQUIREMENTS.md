# AI Software Factory — Requirements Traceability Matrix

Derived from SPEC-01 through SPEC-10 and ADR-001.

## Functional Requirements

| ID | Requirement | Source | Priority |
|---|---|---|---|
| FR-01 | System shall be self-hosted on infrastructure we control | SPEC-01 | P0 |
| FR-02 | All critical dependencies shall be open-source and free for self-host | SPEC-01 | P0 |
| FR-03 | Human must approve a plan before it can be executed | SPEC-03 | P0 |
| FR-04 | Human must approve execution before macro-agent starts | SPEC-03 | P0 |
| FR-05 | Human must approve final merge before DONE | SPEC-03, SPEC-09 | P0 |
| FR-06 | Governance Controller shall be authoritative for approvals, state machine, policy, audit — implemented as custom Python/FastAPI/SQLAlchemy/PostgreSQL service | SPEC-02, SPEC-03, ADR-001 | P0 |
| FR-07 | Plane CE shall be used as human-facing project management UI | SPEC-02, SPEC-04 | P0 |
| FR-08 | Plane CE shall not be authoritative for workflow transitions | SPEC-02, SPEC-04 | P0 |
| FR-09 | META ORCH shall decompose ideas into Epic + Task DAG in Plane | SPEC-02, SPEC-07 | P0 |
| FR-10 | Controller shall materialize approved Plane subgraph into opentasks runtime DAG | SPEC-04, SPEC-05 | P0 |
| FR-11 | opentasks shall be the only DAG agents read for claiming work | SPEC-05 | P0 |
| FR-12 | macro-agent shall be execution orchestration layer | SPEC-02 | P0 |
| FR-13 | Event Bridge shall translate macro-agent workspace events to Controller state updates | SPEC-05 | P0 |
| FR-14 | Controller shall support multiple agent harnesses via provider registry | SPEC-06 | P0 |
| FR-15 | OpenCode shall be primary worker harness | SPEC-06 | P0 |
| FR-16 | System shall support Intake Adapter for Telegram, Email, Slack, API, Voice | SPEC-07 | P1 |
| FR-17 | Idea Ingestion Service shall classify raw ideas and create Plane drafts | SPEC-07 | P1 |
| FR-18 | Controller shall enforce Task Contract, Project Profile, Completion Contract | SPEC-03 | P0 |
| FR-19 | Controller shall run deterministic verification (implemented: `VerificationService`) and reviewer agent (deferred to Phase 3 — see `specs/SPEC-10-phase-plan.md` `[^phase1-reviewer]`; no reviewer-agent service exists yet, only harness-role metadata strings) before human review | SPEC-09 | P0 |
| FR-20 | Controller shall maintain append-only audit log | SPEC-03 | P0 |
| FR-21 | Controller shall reconcile Plane state, Controller DB, and opentasks periodically | SPEC-03, SPEC-04 | P1 |
| FR-22 | All approval paths shall converge to single Controller endpoint POST /approvals | SPEC-03 | P0 |
| FR-23 | Event Bridge and Controller endpoints shall be idempotent via `Idempotency-Key` header | SPEC-03, SPEC-05 | P0 |
| FR-23a | When `Idempotency-Key` is absent, Controller may use synthetic fallback key `(task_id, approval_type, actor, timestamp)` | SPEC-03 | P1 |
| FR-24 | Controller shall fallback to polling macro-agent if Event Bridge is unhealthy | SPEC-05 | P1 |
| FR-25 | System shall support progressive isolation: worktree, Docker, Firecracker/Kata | SPEC-08 | P1/P2 |
| FR-26 | Controller shall enforce scope check (allowed/forbidden paths) as part of Completion Contract | SPEC-03 | P0 |
| FR-27 | Controller shall translate Plane `state.changed` webhook into approval only when all translation conditions hold (human actor, eligible transition, non-stale previous state, non-bulk operation, policy validation) | SPEC-04 | P0 |
| FR-28 | Controller shall revert Plane status and add explanatory comment when webhook translation validation fails | SPEC-04 | P0 |

## Non-Functional Requirements

| ID | Requirement | Source | Priority |
|---|---|---|---|
| NFR-01 | Plane CE shall deploy via Docker Compose within 30 minutes | SPEC-02, SPEC-04 | P0 |
| NFR-02 | Governance Controller REST API p99 latency below 200ms | SPEC-03 | P1 |
| NFR-03 | Event Bridge shall retry failed events with exponential backoff | SPEC-05 | P0 |
| NFR-04 | Controller shall survive Plane downtime by queuing projection updates | SPEC-04 | P1 |
| NFR-05 | Audit log shall be append-only | SPEC-03 | P0 |
| NFR-06 | Secrets shall never appear in prompts, task descriptions, or Plane comments | SPEC-08 | P0 |
| NFR-07 | System shall support at least OpenCode + one other harness in Phase 1 | SPEC-06, SPEC-10 | P0 |
| NFR-08 | macro-agent exact version shall be pinned | SPEC-05 | P0 |
| NFR-09 | System shall have abstraction layer allowing replacement of macro-agent | SPEC-05 | P1 |
| NFR-10 | All reconciliation divergences shall alert a human, not auto-correct execution state | SPEC-03 | P1 |

## Interface Requirements

| ID | Requirement | Source | Priority |
|---|---|---|---|
| IFR-01 | Controller shall expose POST /approvals with idempotency | SPEC-03 | P0 |
| IFR-02 | Controller shall expose POST /tasks, GET /tasks/{id}, GET /executions/{id}; execution is triggered implicitly by `POST /approvals` with `approval_type=execution` rather than a dedicated `/execute` route (per FR-22's single-convergence-point design) | SPEC-03, SPEC-10 | P0 |
| IFR-03 | Controller shall consume Plane webhooks | SPEC-04 | P0 |
| IFR-04 | Controller shall write execution state back to Plane | SPEC-04 | P0 |
| IFR-05 | Event Bridge shall POST macro-agent workspace events to Controller; implemented as `POST /events`, idempotent on `(task_id, event_type, event_timestamp, event_id)` | SPEC-05 | P0 |
| IFR-06 | Controller shall start macro-agent via its REST/ACP API | SPEC-05 | P0 |
| IFR-07 | META ORCH shall read Plane via MCP/API and create tasks via Plane API | SPEC-02, SPEC-04 | P0 |
| IFR-08 | Intake Adapter shall normalize inputs to RawIdea schema and forward to Idea Ingestion | SPEC-07 | P1 |
| IFR-09 | Merge approval (`approval_type=merge`) shall converge to `POST /approvals` from both Plane UI and direct Controller calls | SPEC-04, SPEC-09 | P0 |

## Security Requirements

| ID | Requirement | Source | Priority |
|---|---|---|---|
| SR-01 | No execution without human approval | SPEC-03 | P0 |
| SR-02 | No merge without human approval | SPEC-03, SPEC-09 | P0 |
| SR-03 | Worktree shall not be treated as security sandbox | SPEC-08 | P0 |
| SR-04 | Docker socket access default deny | SPEC-08 | P0 |
| SR-05 | Destructive shell commands default deny | SPEC-08 | P0 |
| SR-06 | Secret injection scoped per Project Profile | SPEC-08 | P0 |
| SR-07 | All shell commands logged | SPEC-08 | P1 |
| SR-08 | Policy violations trigger alerts | SPEC-08 | P1 |
| SR-09 | Completion Contract shall enforce scope check to prevent agent from modifying files outside allowed paths | SPEC-03, SPEC-08 | P0 |

## Risks and Mitigations

| ID | Risk | Mitigation | Source |
|---|---|---|---|
| RISK-01 | Plane CE cannot enforce workflow transitions | Governance Controller is authority | SPEC-04 |
| RISK-02 | macro-agent is pre-1.0 | Pin version, abstraction layer, thin fork; see `decisions/ADR-002-macro-agent-stub-vs-package.md` for current stub-vs-package decision | SPEC-05, ADR-002 |
| RISK-03 | Event Bridge single point of failure | Healthcheck + fallback polling | SPEC-05 |
| RISK-04 | Two sources of approvals (Plane and direct) | Single POST /approvals endpoint | SPEC-03 |
| RISK-05 | Double DAG (Plane vs opentasks) divergence | Controller materializes Plane -> opentasks, reconciliation alerts | SPEC-05 |
| RISK-06 | Claude-specific assumptions in macro-agent | Provider registry + AGENT_SPAWN_GAP analysis | SPEC-06 |
| RISK-07 | Secrets leaked through prompts/logs | Scoped secrets, no secrets in prompts | SPEC-08 |
| RISK-08 | Unclear macro-agent version stability | Track upstream, have rollback plan; see `decisions/ADR-002-macro-agent-stub-vs-package.md` | SPEC-05, ADR-002 |
| RISK-09 | REQUIREMENTS.md may become out of sync with SPEC updates | Mandatory "update REQUIREMENTS.md" step after governance/plane/macro-agent SPEC changes | SPEC-03, SPEC-04, SPEC-05 |
| RISK-10 | Naive webhook translation treats bulk/state changes as approvals | Translation conditions in SPEC-04 §4.3a + revert mechanism | SPEC-04 |
| RISK-11 | Pre-execution approval chain incompatible with workflow-per-task model if Temporal is used as Governance core | Temporal rejected for Governance on Phase 1; revisit only as Execution layer component in Phase 3+ | ADR-001 |
| RISK-12 | Temporal/OPA migration risks deferred (workflow determinism, versioning, audit retention gap, OPA latency, Rego learning curve) | Revisit in Phase 3+ with explicit risk re-evaluation; OPA acceptable as Phase 2 Policy Engine backend | ADR-001 |
| RISK-13 | Long-running or GUI-touching opentasks may exceed a single bounded harness session before losing task state | Evaluated AMAP-ML/LongHorizon-Harness as an optional additional `AgentHarness` adapter (SPEC-06 §6.4); not adopted for Phase 1/2, pre-1.0 maturity risk noted; revisit Phase 3+ | docs/research-longhorizon-harness.md |
| RISK-14 | Governance Controller's `VerificationService` executes agent-controlled `CompletionContract.command`/`TaskContract.verification["commands"]` strings via unsandboxed shell subprocess | Mitigated: `PolicyEngine` now allowlist-validates every such command (forbidden shell tokens/patterns, destructive/privilege-escalation/docker-socket substrings) before approval — `GAP-024` (HIGH) `CLOSED` since REVIEW-005 (`fb53778`). `GAP-095` residual (REVIEW-024) fixed the timeout half: `_run_check()` now spawns with `start_new_session=True` and kills the whole process group on timeout, so a shell-forked child cannot outlive the shell. Residual risk: mitigation is allowlist-based, not execution-isolation-based (no sandboxing) — still true unsandboxed shell execution of an approved command, tracked as a Phase 2/3 "Advanced isolation" candidate in `docs/NEXT_STEPS.md`, not a Phase 1 blocker | SPEC-08, reviews/GAPS.md (GAP-024, GAP-095) |
| RISK-15 | macro-agent is pre-1.0 (RISK-02/08) with no proven fallback if it doesn't pan out; Meta Orchestrator (SPEC-01 §1.3) is unbuilt with no evaluated tooling | Evaluated alexngai/openswarm as a concrete macro-agent alternative (untested API surface), alexngai/openhive as a Phase 3+ multi-swarm federation candidate, and sudocode-ai/sudocode as a Meta Orchestrator candidate (OpenSpec integration, Spec/Issue graph model); also evaluated Untrivial-ai/agent-orchestrator (fleet manager for coding-agent CLI sessions, worktree-per-task, pluggable agent/runtime/SCM adapters — same untested-API-surface caveat as openswarm) as a second concrete macro-agent alternative; none adopted for Phase 1/2 | docs/research-alexngai-ecosystem-and-sudocode.md, docs/research-agent-orchestration-and-governance-survey-2026-08.md |
| RISK-16 | Async SQLAlchemy session commit/rollback/CAS semantics have proven unusually error-prone: the same defect class (missing `session.commit()`, `expire_on_commit=False` masking stale-object reads, tests that pass for the wrong reason, `synchronize_session` defaults silently reverting in-memory state) recurred across `GAP-022, GAP-023, GAP-031, GAP-043, GAP-044, GAP-046, GAP-054, GAP-055, GAP-056` — 9 of 56 tracked gaps spanning 9 of 12 review rounds — and REVIEW-013 found the same class still live in `VerificationService.verify_and_advance()` (`GAP-058`). REVIEW-024 found the same defect again in `EventBridge.handle()`: the dedup-key write for a retry `executor.start()` failure was added inside `finally` but not committed, so `get_db()` rolled it back (`GAP-097`). After the switch to GitHub issues, the specific "blind write staged before a CAS check, committed regardless of whether the CAS won" shape recurred a further 3 times: `#262` (`ApprovalService._trigger_execution`), `#263` (`StuckExecutionPoller._poll_agent_review`), and `#266` (`VerificationService._start_retry_execution`) — found across rounds 9-10 by a systematic sweep of every `StateMachine.atomic_transition` call site, prompted by the second instance recurring | Dedicated session-lifecycle test pattern established (`tests/test_db.py`/`test_approval_concurrency.py`: drive the real `get_db()` generator directly via `athrow`/`asynccontextmanager`, verify each commit-before-raise site with an empirical "remove this exact commit → does the right test go red" matrix); apply this pattern as the template for any new or modified code that touches a DB session, not just `approval_service.py`. `GAP-097` was originally fixed by committing the dedup key inside `finally` before re-raising; that `finally` block was later removed and superseded by a different mechanism (commit `50677c8`, closing GitHub issue #240): `EventBridge.handle()` now writes and commits an in-progress dedup marker *before* verification runs, deleting it only if no state transition occurred — this also closes a duplicate-delivery race #240 found that the `finally`-based approach didn't cover. `grep -n finally event_bridge.py verification_service.py` returns no matches in the current tree. **Residual process gap (see `RISK-20`): the fixes for `#262`/`#263`/`#266` shipped with no new regression test covering the specific "write before CAS, commit on loss" shape — each was caught only by a dedicated live-concurrency review pass, not by CI. `CLAUDE.md`'s Regression Coverage Policy now requires a live two-session test for any future fix in this class** | reviews/GAPS.md (GAP-022/023/031/043/044/046/054/055/056/058/097), github.com/rusnino/ai-software-factory/issues/{240,262,263,266} |
| RISK-17 | Every review round through REVIEW-015 tested exclusively against SQLite (`aiosqlite`), which silently tolerates things `asyncpg`/PostgreSQL reject or behave differently on — tz-naive-vs-aware datetimes, `FOR UPDATE`/row-lock semantics, constraint-name and error-message shapes. This produced `GAP-078` (CRITICAL — the Controller had never actually worked against real Postgres at all), `GAP-079` (HIGH), and `GAP-080` (HIGH, and its first fix attempt was itself SQLite-blind — REVIEW-017 found the underlying symptom persists via a different mechanism a Postgres-only test would have caught). There is no CI workflow anywhere in the repo, so the dual-DB `GC_TEST_DATABASE_URL` test path added alongside the `GAP-078` fix is opt-in and never runs automatically | Dual-DB test fixtures (`tests/conftest.py`'s `isolated_db`/`db_session`/`client_db_session`/`patched_db`, all reading `GC_TEST_DATABASE_URL`) let the full suite run against real Postgres on demand — confirmed genuine, not a silent SQLite fallback, in REVIEW-017 by pointing the env var at a Postgres URL with deliberately bad credentials and observing every DB-touching test fail with a real `asyncpg` auth error. Residual risk: this path is opt-in with no CI enforcing it, so a future regression of this exact class (as already happened once, with `GAP-080`) would not be caught automatically | reviews/GAPS.md (GAP-078/079/080/081) |
| RISK-18 | A fix scoped correctly for its own intended call site can silently change behavior for every other consumer of a shared mechanism it touches (a state-transition table, a validation helper, shared middleware, a dialect-conditional code path) if its author doesn't audit all callers/cases, not just the one being fixed. Manifested twice: `GAP-077`'s fix placed a new `FAILED -> RUNNING` edge in `StateMachine`'s *global* transition table, which `EventBridge.handle()`'s generic event validation also reads for 9 unrelated event types; `GAP-094`'s fix for SQLite's `StaticPool` rejecting Postgres-only pool kwargs was itself re-audited in REVIEW-023 and found to have the identical shape one level deeper — `GAP-098`: its `startswith("sqlite")` check correctly handles `:memory:` but silently drops legitimate, operator-configured pool settings for file-based SQLite too, which uses a different, compatible pool class | No dedicated tooling exists for this yet; the review process's practice of tracing every call site/sub-case of a modified shared table/registry/dialect-branch (not just the intended one) before accepting a fix as scoped-correctly is the current mitigation, applied in REVIEW-019 and REVIEW-023 | reviews/GAPS.md (GAP-077, GAP-094, GAP-098) |
| RISK-19 | `AsyncSession`'s identity map silently defeats any "re-read this row to check whether it changed since an earlier read in this session" pattern that uses `Session.get()` or a bare `select()` without `.execution_options(populate_existing=True)`/`session.refresh()` — the session returns the already-loaded, stale Python object instead of querying the database, so the staleness check always reports "unchanged" even when a concurrent session has genuinely committed a different value. Found independently in five call sites across three review rounds, each in an unrelated subsystem: `EventBridge.handle()`'s post-verification staleness check (`#271`, CRITICAL — under genuinely concurrent `landing:completed` redelivery, this let verification run multiple times and let every losing racer delete the `#240` dedup marker the winner had legitimately written); `ReconciliationService._task_still_in_state` (`#275`, MEDIUM — pushed a stale Controller state to Plane's non-authoritative projection and posted a comment falsely claiming it was synced to the "authoritative" state); and `ApprovalService.approve()`'s idempotent-duplicate-delivery re-fetch (`#278`, HIGH — this exact re-fetch was added specifically to fix `#242` in round 6 ("return the true post-approval state, not a stale copy"), meaning that fix has silently never worked since the day it shipped: a client retrying a timed-out approval gets back the wrong task state in both the HTTP response and the audit log's `previous_state`/`new_state` fields) | Use `select(Model).where(...).execution_options(populate_existing=True)` (or an explicit `session.refresh(obj)`) for any read whose purpose is checking whether a row changed since an earlier read in the same session — never `Session.get()` or a bare `select()` for this purpose; both were fixed to this pattern at their three respective call sites (`event_bridge.py`, `reconciliation_service.py`, `approval_service.py`). Per `CLAUDE.md`'s Regression Coverage Policy, treat any new/modified "staleness check" as suspect until independently verified to use one of these, and sweep sibling call sites for the same shape whenever a new instance is found — the same discipline established for `RISK-16` | github.com/rusnino/ai-software-factory/issues/{240,242,271,275,278} |
| RISK-20 | Fix commits for `severity:critical`/`severity:high` review findings have repeatedly shipped with zero new regression-test coverage, even though `CLAUDE.md` has always required tests for every significant change: round 8's two CRITICALs (`#253`/`#254`, 175 new lines of poller logic) landed with no new test file; round 10's `#266` (CRITICAL) and `#267`/`#268` (HIGH) landed with none; round 11's `#271` (CRITICAL, this project's most severe concurrency bug) landed with none; round 12's `#274` (HIGH) landed with none. A green test suite was repeatedly treated as sufficient evidence a fix was real and would stay fixed. It wasn't — this is the direct mechanism by which `RISK-16`'s defect class recurred 3 times and `RISK-19`'s recurred 5 times, each instance undetected by CI and caught only by a dedicated live-reproduction review pass | `CLAUDE.md`'s "Regression Coverage Policy" (added after round 13) makes a fix-specific regression test an explicit closure requirement for `severity:high`/`severity:critical` issues, not an implicit expectation folded into "write tests for every significant change" — closing such an issue now requires confirming a test exists that fails pre-fix and passes post-fix, with a live/real-Postgres test mandated specifically for concurrency-shaped fixes. Not yet verified to hold going forward; the next several rounds' fix commits are the test of whether this policy actually changes behavior | github.com/rusnino/ai-software-factory/issues/{253,254,266,267,268,271,274} |

## Open Questions

1. Should META ORCH run continuously or on schedule/trigger?
2. Should Idea Ingestion be triggered or batch?
3. Which CI system is canonical for Phase 1? (Woodpecker vs Gitea Actions)
4. Should merge be automated after HUMAN_REVIEW or always create PR?
5. What is the rollback procedure if macro-agent creates bad commits?
