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
| RISK-02 | macro-agent is pre-1.0 | Pin version, abstraction layer, thin fork | SPEC-05 |
| RISK-03 | Event Bridge single point of failure | Healthcheck + fallback polling | SPEC-05 |
| RISK-04 | Two sources of approvals (Plane and direct) | Single POST /approvals endpoint | SPEC-03 |
| RISK-05 | Double DAG (Plane vs opentasks) divergence | Controller materializes Plane -> opentasks, reconciliation alerts | SPEC-05 |
| RISK-06 | Claude-specific assumptions in macro-agent | Provider registry + AGENT_SPAWN_GAP analysis | SPEC-06 |
| RISK-07 | Secrets leaked through prompts/logs | Scoped secrets, no secrets in prompts | SPEC-08 |
| RISK-08 | Unclear macro-agent version stability | Track upstream, have rollback plan | SPEC-05 |
| RISK-09 | REQUIREMENTS.md may become out of sync with SPEC updates | Mandatory "update REQUIREMENTS.md" step after governance/plane/macro-agent SPEC changes | SPEC-03, SPEC-04, SPEC-05 |
| RISK-10 | Naive webhook translation treats bulk/state changes as approvals | Translation conditions in SPEC-04 §4.3a + revert mechanism | SPEC-04 |
| RISK-11 | Pre-execution approval chain incompatible with workflow-per-task model if Temporal is used as Governance core | Temporal rejected for Governance on Phase 1; revisit only as Execution layer component in Phase 3+ | ADR-001 |
| RISK-12 | Temporal/OPA migration risks deferred (workflow determinism, versioning, audit retention gap, OPA latency, Rego learning curve) | Revisit in Phase 3+ with explicit risk re-evaluation; OPA acceptable as Phase 2 Policy Engine backend | ADR-001 |
| RISK-13 | Long-running or GUI-touching opentasks may exceed a single bounded harness session before losing task state | Evaluated AMAP-ML/LongHorizon-Harness as an optional additional `AgentHarness` adapter (SPEC-06 §6.4); not adopted for Phase 1/2, pre-1.0 maturity risk noted; revisit Phase 3+ | docs/research-longhorizon-harness.md |
| RISK-14 | Governance Controller's `VerificationService` executes agent-controlled `CompletionContract.command`/`TaskContract.verification["commands"]` strings via unsandboxed shell subprocess | Mitigated: `PolicyEngine` now allowlist-validates every such command (forbidden shell tokens/patterns, destructive/privilege-escalation/docker-socket substrings) before approval — `GAP-024` (HIGH) `CLOSED` since REVIEW-005 (`fb53778`). `GAP-095` residual (REVIEW-024) fixed the timeout half: `_run_check()` now spawns with `start_new_session=True` and kills the whole process group on timeout, so a shell-forked child cannot outlive the shell. Residual risk: mitigation is allowlist-based, not execution-isolation-based (no sandboxing) — still true unsandboxed shell execution of an approved command, tracked as a Phase 2/3 "Advanced isolation" candidate in `docs/NEXT_STEPS.md`, not a Phase 1 blocker | SPEC-08, reviews/GAPS.md (GAP-024, GAP-095) |
| RISK-15 | macro-agent is pre-1.0 (RISK-02/08) with no proven fallback if it doesn't pan out; Meta Orchestrator (SPEC-01 §1.3) is unbuilt with no evaluated tooling | Evaluated alexngai/openswarm as a concrete macro-agent alternative (untested API surface), alexngai/openhive as a Phase 3+ multi-swarm federation candidate, and sudocode-ai/sudocode as a Meta Orchestrator candidate (OpenSpec integration, Spec/Issue graph model); also evaluated Untrivial-ai/agent-orchestrator (fleet manager for coding-agent CLI sessions, worktree-per-task, pluggable agent/runtime/SCM adapters — same untested-API-surface caveat as openswarm) as a second concrete macro-agent alternative; none adopted for Phase 1/2 | docs/research-alexngai-ecosystem-and-sudocode.md, docs/research-agent-orchestration-and-governance-survey-2026-08.md |
| RISK-16 | Async SQLAlchemy session commit/rollback/CAS semantics have proven unusually error-prone: the same defect class (missing `session.commit()`, `expire_on_commit=False` masking stale-object reads, tests that pass for the wrong reason, `synchronize_session` defaults silently reverting in-memory state) recurred across `GAP-022, GAP-023, GAP-031, GAP-043, GAP-044, GAP-046, GAP-054, GAP-055, GAP-056` — 9 of 56 tracked gaps spanning 9 of 12 review rounds — and REVIEW-013 found the same class still live in `VerificationService.verify_and_advance()` (`GAP-058`). REVIEW-024 found the same defect again in `EventBridge.handle()`: the dedup-key write for a retry `executor.start()` failure was added inside `finally` but not committed, so `get_db()` rolled it back (`GAP-097`) | Dedicated session-lifecycle test pattern established (`tests/test_db.py`/`test_approval_concurrency.py`: drive the real `get_db()` generator directly via `athrow`/`asynccontextmanager`, verify each commit-before-raise site with an empirical "remove this exact commit → does the right test go red" matrix); apply this pattern as the template for any new or modified code that touches a DB session, not just `approval_service.py`. `GAP-097` fixed by committing the dedup key inside `finally` before re-raising | reviews/GAPS.md (GAP-022/023/031/043/044/046/054/055/056/058/097) |
| RISK-17 | Every review round through REVIEW-015 tested exclusively against SQLite (`aiosqlite`), which silently tolerates things `asyncpg`/PostgreSQL reject or behave differently on — tz-naive-vs-aware datetimes, `FOR UPDATE`/row-lock semantics, constraint-name and error-message shapes. This produced `GAP-078` (CRITICAL — the Controller had never actually worked against real Postgres at all), `GAP-079` (HIGH), and `GAP-080` (HIGH, and its first fix attempt was itself SQLite-blind — REVIEW-017 found the underlying symptom persists via a different mechanism a Postgres-only test would have caught). There is no CI workflow anywhere in the repo, so the dual-DB `GC_TEST_DATABASE_URL` test path added alongside the `GAP-078` fix is opt-in and never runs automatically | Dual-DB test fixtures (`tests/conftest.py`'s `isolated_db`/`db_session`/`client_db_session`/`patched_db`, all reading `GC_TEST_DATABASE_URL`) let the full suite run against real Postgres on demand — confirmed genuine, not a silent SQLite fallback, in REVIEW-017 by pointing the env var at a Postgres URL with deliberately bad credentials and observing every DB-touching test fail with a real `asyncpg` auth error. Residual risk: this path is opt-in with no CI enforcing it, so a future regression of this exact class (as already happened once, with `GAP-080`) would not be caught automatically | reviews/GAPS.md (GAP-078/079/080/081) |
| RISK-18 | A fix scoped correctly for its own intended call site can silently change behavior for every other consumer of a shared mechanism it touches (a state-transition table, a validation helper, shared middleware, a dialect-conditional code path) if its author doesn't audit all callers/cases, not just the one being fixed. Manifested twice: `GAP-077`'s fix placed a new `FAILED -> RUNNING` edge in `StateMachine`'s *global* transition table, which `EventBridge.handle()`'s generic event validation also reads for 9 unrelated event types; `GAP-094`'s fix for SQLite's `StaticPool` rejecting Postgres-only pool kwargs was itself re-audited in REVIEW-023 and found to have the identical shape one level deeper — `GAP-098`: its `startswith("sqlite")` check correctly handles `:memory:` but silently drops legitimate, operator-configured pool settings for file-based SQLite too, which uses a different, compatible pool class | No dedicated tooling exists for this yet; the review process's practice of tracing every call site/sub-case of a modified shared table/registry/dialect-branch (not just the intended one) before accepting a fix as scoped-correctly is the current mitigation, applied in REVIEW-019 and REVIEW-023 | reviews/GAPS.md (GAP-077, GAP-094, GAP-098) |

## Open Questions

1. Should META ORCH run continuously or on schedule/trigger?
2. Should Idea Ingestion be triggered or batch?
3. Which CI system is canonical for Phase 1? (Woodpecker vs Gitea Actions)
4. Should merge be automated after HUMAN_REVIEW or always create PR?
5. What is the rollback procedure if macro-agent creates bad commits?
