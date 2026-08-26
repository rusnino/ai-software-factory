# SPEC-05: macro-agent Integration

## 5.1 macro-agent Source

- Repository: `github.com/alexngai/macro-agent`
- License: MIT
- Language: TypeScript / Node
- Status: pre-1.0, core claimed stable

## 5.2 Components of macro-agent

| Component | Responsibility | Integration Owner |
|---|---|---|
| macro-agent core | Orchestration, lifecycle, teams, topology, ACP/REST servers | macro-agent |
| agent-inbox | Messaging | macro-agent |
| opentasks | Runtime task graph / claiming / dependencies | macro-agent |
| git-cascade | Worktrees, streams, merge, cascade rebase | macro-agent |
| acp-factory | Agent process management | macro-agent |

## 5.3 Runtime DAG Ownership

- **Plane** holds coarse-grained project dependencies.
- **Controller** decides which Plane tasks are approved and materializes an approved subgraph into opentasks.
- **opentasks** is the only DAG agents read for claiming work.
- **Agents never read Plane directly for dependencies.**

### Materialization Flow

```
Controller selects approved Plane task(s)
  |
  v
Controller creates corresponding opentasks with metadata.plane_task_id
  |
  v
Controller links opentasks according to Plane dependencies
  |
  v
macro-agent agents claim tasks via MCP `claim_task` / `list_claimable_tasks`
```

## 5.4 Event Bridge

### Purpose

macro-agent has no outgoing webhooks. It exposes an in-process event channel (`workspaceManager.onEvent`) and optional REST API for polling. The Event Bridge subscribes to events and forwards them to Controller.

### Deployment

- Separate Node/TS microservice.
- Co-located or close to macro-agent instance.
- Own healthcheck and restart policy.

### Event Mapping

| macro-agent event | Controller state | Notes |
|---|---|---|
| `worktree:allocated` | `RUNNING` | agent started work |
| `stream:committed` | `RUNNING` | intermediate commit |
| `landing:started` | `RUNNING` | agent called `done()` |
| `landing:completed` | `AGENT_REVIEW` | code landed, pending verification |
| `conflict:created` | `BLOCKED` | landing conflict |
| `conflict:resolved` | `RUNNING` | conflict resolved |
| `stream:abandoned` | `FAILED` | work discarded |
| `mergeQueue:added` | `RUNNING` | queued for merge |
| `mergeQueue:ready` | `RUNNING` | merge possible |

### Retry and Idempotency

- Bridge retries with exponential backoff.
- Controller endpoint idempotent on `(task_id, event_type, event_timestamp, event_id)`.
- Out-of-order events handled by Controller with state-machine validation.

## 5.5 Controller to macro-agent API

Library wrapper in Python:

```python
class MacroAgentExecutor:
    async def start(task_contract: TaskContract) -> ExecutionRef: ...
    async def status(ref: ExecutionRef) -> ExecutionStatus: ...
    async def cancel(ref: ExecutionRef) -> None: ...
    async def collect(ref: ExecutionRef) -> ExecutionResult: ...
```

Uses macro-agent's REST/ACP interface.

## 5.6 Fallback Polling

If Event Bridge stops delivering events, the Controller detects stuck executions through the
``StuckExecutionPoller`` service and the ``poll-stuck-executions`` CLI command:

- Poll macro-agent REST API `/runs/{run_id}` via ``MacroAgentClient.status()``.
- Deadline is 2x the task's ``timeout_minutes`` from the latest ``Execution`` row's
  ``started_at``.
- When the run is not still active at deadline, move the task to ``BLOCKED``, finalize
  the execution row, and record an ``execution_blocked_timeout`` audit alert.

In Phase 2 this is a one-shot command meant to be invoked by an external scheduler alongside
``reconcile``. A future phase may add an in-process background loop if deployment complexity
justifies it.

## 5.7 Traceability

Every macro-agent stream/worktree carries metadata:

```yaml
metadata:
  controller_task_id: TASK-42
  controller_execution_id: EXEC-123
  opentasks_id: OT-456
  project_id: PROJECT-1
```

## 5.8 Risk: Pre-1.0 Dependency

- Pin exact npm version in `package.json`.
- Create abstraction layer so macro-agent can be replaced by custom orchestrator.
- Track upstream changelog and breaking changes.
- Consider thin fork for provider registry / harness selection.
