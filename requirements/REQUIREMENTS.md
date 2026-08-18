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
| FR-19 | Controller shall run deterministic verification and reviewer agent before human review | SPEC-09 | P0 |
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
| IFR-02 | Controller shall expose POST /tasks, GET /tasks/{id}, POST /tasks/{id}/execute, GET /executions/{id} | SPEC-03, SPEC-10 | P0 |
| IFR-03 | Controller shall consume Plane webhooks | SPEC-04 | P0 |
| IFR-04 | Controller shall write execution state back to Plane | SPEC-04 | P0 |
| IFR-05 | Event Bridge shall POST /webhooks/macro-agent to Controller | SPEC-05 | P0 |
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
| RISK-14 | Governance Controller's `VerificationService` executes agent-controlled `CompletionContract.command` strings via unsandboxed shell subprocess, with no allowlist and no cross-check against SPEC-08 §8.7 forbidden operations | Not yet mitigated; tracked as `GAP-024` (HIGH) in `reviews/GAPS.md` — needs command allowlisting/sandboxing before Phase 1 sign-off | SPEC-08, reviews/REVIEW-004-full-codebase-review.md |

## Open Questions

1. Should META ORCH run continuously or on schedule/trigger?
2. Should Idea Ingestion be triggered or batch?
3. Which CI system is canonical for Phase 1? (Woodpecker vs Gitea Actions)
4. Should merge be automated after HUMAN_REVIEW or always create PR?
5. What is the rollback procedure if macro-agent creates bad commits?
