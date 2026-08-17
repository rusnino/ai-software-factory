# SPEC-04: Plane CE Integration

## 4.1 Role of Plane CE

- Human-facing project management UI.
- Stores human-readable project structure: workspaces, projects, epics, tasks, states, dependencies, labels, comments.
- Source of webhooks for task/relation/status changes.
- Projection of authoritative Controller state for execution-status fields.

## 4.2 What Plane CE Is NOT

- Workflow-transition authority.
- Approval-record authority.
- Policy-enforcement engine.
- Execution orchestrator.

## 4.3 Two-Way Sync Rules

### Controller writes to Plane (projection)

Controller can update:

- execution-status fields (`READY`, `RUNNING`, `AGENT_REVIEW`, `HUMAN_REVIEW`, `DONE`, `FAILED`, `BLOCKED`)
- comments with execution results / reviewer notes
- labels indicating state or source

### Plane writes trigger Controller (request)

Plane webhooks notify Controller of:

- task created / updated / deleted
- state changed
- dependency created / deleted
- comment added
- assignee changed

Controller processes these as requests, validates, then updates its own state.

### Conflict Resolution

| Field Type | Authority | Rule |
|---|---|---|
| execution status | Controller | Controller wins always |
| content (title, description, deps) | Plane (if task state <= PLAN_APPROVED) | Plane wins |
| content (if task state >= EXEC_APPROVED) | Controller | Reject or require human escalation |
| merge approval | Controller | Single authoritative endpoint `POST /approvals` with `approval_type=merge`; both Plane UI triggers and direct calls converge here |
| labels/comments | Plane | Allow, but Controller may override execution labels |

## 4.3a Webhook to Approval Translation

A Plane webhook `state.changed` does NOT automatically become a Controller approval. It is translated only when all following conditions hold:

1. `actor` in the webhook payload is a real Plane user (not system account, not bulk-import job, not migration script).
2. The transition is from an eligible source state to a target state mapped to an approval type:
   - `PROPOSED -> PLAN_APPROVED` -> `approval_type=plan`
   - `PLAN_APPROVED -> EXEC_APPROVED` -> `approval_type=execution`
   - `HUMAN_REVIEW -> DONE` -> `approval_type=merge`
3. The previous Plane state in the webhook matches the current Controller state (rejects stale/race webhooks).
4. The webhook is not part of a bulk operation (`bulk: true` flag or missing per-event actor`).
5. Policy Engine validates the request (Task Contract, Project Profile, approval chain).

If any condition fails, the Controller:
- does NOT record the approval;
- reverts the Plane status to the previous value (Controller wins for execution status);
- adds a Plane comment explaining the rejection.

## 4.4 Webhook Events from Plane

```yaml
source: plane
event_type: task.created | task.updated | task.deleted | state.changed | dependency.created | dependency.deleted | comment.added
task_id: TASK-42
project_id: PROJECT-1
payload:
  previous: { ... }
  current: { ... }
  actor: user@example.com
  actor_type: human | system | import | bulk
  operation: single_update | bulk_update | migration | automation
```

The `actor_type` and `operation` fields are used by the Webhook to Approval Translation rules in §4.3a.

## 4.5 Plane State Mapping

| Plane State | Controller State | Notes |
|---|---|---|
| Proposed | PROPOSED | Initial draft |
| Plan Approved | PLAN_APPROVED | Human clicked approve plan |
| Approved | EXEC_APPROVED | Human clicked approve execution |
| Ready | READY | Controller materialized runtime DAG |
| In Progress | RUNNING | macro-agent executing |
| Agent Review | AGENT_REVIEW | landing completed, pending CI/reviewer |
| In Review | HUMAN_REVIEW | waiting human merge approval |
| Done | DONE | human approved merge |
| Blocked | BLOCKED | conflict/error |
| Failed | FAILED | retry limit or policy violation |

## 4.6 Triage Queue

Plane holds a `Needs Triage` state for intake items that cannot be auto-classified:

- Human classifies as `existing project` / `new project` / `spam`.
- On classification, Controller creates appropriate Plane item.

## 4.7 Custom Fields

Plane tasks require custom fields:

| Field | Type | Purpose |
|---|---|---|
| `controller_task_id` | text | Bidirectional traceability |
| `opentasks_id` | text | Runtime traceability |
| `source` | select | plane-ui / telegram / meta-orch / api |
| `approval_required` | boolean | From Task Contract |

## 4.8 Reliability

- Plane webhooks are at-least-once; Controller must be idempotent.
- If Plane is temporarily unavailable, Controller queues projection updates.
- If Controller rejects a Plane-initiated change, it comments the reason in Plane.
