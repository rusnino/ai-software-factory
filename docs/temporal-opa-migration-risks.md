# Temporal / OPA Migration Risks

Status: deferred risks — applicable when/ if these tools are introduced in later phases (OPA in Phase 2, Temporal in Phase 3+).

Source: ADR-001 analysis.

## OPA Risks

| Risk | Description | Mitigation |
|---|---|---|
| OPA-001 | Rego learning curve for the team | Training, simple policies first, pair OPA with embedded Python as fallback |
| OPA-002 | Policy bundle versioning / deployment | Version policies alongside Controller releases; use OPA bundle API or sidecar |
| OPA-003 | Latency budget | Local OPA sidecar, caching, policy evaluation timeout < 100ms |
| OPA-004 | Divergence between embedded policy and OPA rules | Single source of truth for policy schemas; migrate rules incrementally |
| OPA-005 | Decision log retention | Configure OPA decision logs to forward to audit store; do not rely on OPA as sole audit source |

## Temporal Risks

| Risk | Description | Mitigation |
|---|---|---|
| TEMP-001 | Workflow determinism requirements | Activities must be deterministic; all non-deterministic work in Activities, not Workflows |
| TEMP-002 | Workflow code versioning | Use Temporal versioning APIs; test upgrade paths before deployment |
| TEMP-003 | Temporal Server operational complexity | Self-host only when ops team ready; start with embedded/lite mode if available |
| TEMP-004 | Audit retention gap | Continue storing approvals and governance events in Controller PostgreSQL audit log |
| TEMP-005 | Temporal replaces only execution workflow, not governance | Keep Governance Controller authoritative; Temporal manages already-approved execution lifecycle |
| TEMP-006 | Persistence backup/restore | Regular backups of Temporal persistence; documented DR procedure |
| TEMP-007 | Long-running workflow with human signals | Design signals carefully; handle duplicate/late signals idempotently |
| TEMP-008 | Temporal Event History not a compliance audit log | Do not treat Event History as proof of human approval for compliance |
