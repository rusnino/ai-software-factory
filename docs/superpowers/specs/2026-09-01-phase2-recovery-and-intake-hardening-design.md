# Phase 2 Recovery and Intake Hardening

## Context

The latest Phase 2 review confirmed three residual defect classes:

- cancellation can strand execution approval or verification retry state;
- intake authentication currently bypasses the global IP limiter, while sender
  quota checks race under PostgreSQL concurrency;
- the optional Rego policy has drifted from the embedded command policy.

The Governance Controller remains authoritative. Plane is a projection, and the
macro-agent remains an execution adapter rather than a workflow authority.

## Design

### Execution and verification recovery

Keep the existing `TaskState` values and CAS state machine. Add durable,
append-only audit operation markers before any non-authoritative or cancellable
handoff:

- `execution_start_pending` is committed after execution approval and before
  Plane projection or macro-agent startup.
- `verification_retry_pending` is committed before verification failure
  feedback and retry startup.
- `execution_cancel_pending` records an external run that must be cancelled
  after a CAS loser or another cleanup failure.
- Successful recovery/cleanup writes a corresponding completed audit event.

The existing `StuckExecutionPoller` will process stale markers. It will resume
approved execution or verification retry only through the existing authoritative
CAS transitions, so an original request and a recovery pass cannot start two
runs. It will retry external cancellation for a known run ID. Plane feedback is
performed after the authoritative execution handoff and never determines the
Controller state.

No new task state, macro-agent internal change, or generic `FAILED -> RUNNING`
transition is introduced. Recovery reconstructs the validated task contract and
stored project profile from Controller data and uses the existing executor
adapter.

### Intake admission and quota

Use a two-stage per-IP intake admission path:

1. An intake request consumes a pre-auth token, so invalid or missing secrets
   remain rate-limited.
2. Successful intake authentication releases that token immediately. Duplicate
   responses retain the current response-time release as an idempotent fallback.

Authentication stays in the FastAPI dependency; the middleware only provides a
release callback through request state and does not duplicate HMAC validation.
This preserves concurrent duplicate retries without allowing unauthenticated
traffic to bypass the IP budget.

For PostgreSQL, acquire a transaction-scoped advisory lock keyed by normalized
sender before the duplicate lookup and hold it through the sender count, insert,
and commit. SQLite keeps its existing serialized behavior. The unique
`(source, source_id)` index remains the duplicate backstop.

### Policy parity

The embedded `PolicyEngine` remains authoritative. Align OPA with its parsed
argv semantics rather than weakening embedded validation:

- recognize all supported `sed` expression forms, including attached and long
  options, and reject only dangerous `s///e` expressions;
- match case-insensitive aggregate recursive/force `rm` flags;
- fix embedded parsing for `--expression=<script>` where needed;
- add the same command matrix to Python and OPA regression tests.

## Verification

Add RED/GREEN tests before production changes:

- PostgreSQL cancellation/recovery tests for stale approval and retry markers,
  CAS races, and failed external cancellation;
- authenticated and unauthenticated intake IP-limit tests;
- PostgreSQL independent-session sender-quota and duplicate-at-limit tests;
- differential embedded/OPA `sed` and `rm` command matrices.

Run the full SQLite and PostgreSQL controller suites, macro-agent tests,
ruff/mypy, and OPA `check`/`test` using the CI-pinned OPA version. Update phase
status documentation only after the independent re-review passes.

## Out of scope

- New task lifecycle states or a general workflow engine.
- A new execution outbox schema or migration framework.
- Changes to macro-agent internals or Plane workflow enforcement.
- Distributed rate-limit storage; the existing in-memory IP limiter remains a
  documented single-process mechanism.
