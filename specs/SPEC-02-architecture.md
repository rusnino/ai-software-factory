# SPEC-02: Architecture and Components

## 2.1 Component List

| Component | Technology | Role | License |
|---|---|---|---|
| Plane CE | Docker Compose, Postgres, Redis | Human PM UI / SSOT projection | AGPL CE |
| Governance Controller | Python, FastAPI, Pydantic, SQLAlchemy, PostgreSQL | Authoritative governance | own (MIT/AGPL) |
| Meta Orchestrator | OpenCode + BMAD + OpenSpec | Idea → decomposition | OSS |
| opentasks | Node/TS daemon | Runtime task graph / claiming | MIT (macro-agent deps) |
| macro-agent | Node/TS (alexngai/macro-agent) | Execution orchestration | MIT |
| macro-agent Event Bridge | Node/TS microservice | Event relay to Controller | own |
| Agent Harnesses | OpenCode, Claude Code, Codex CLI, Aider | Code execution | mixed |
| Intake Adapter | Python service | Multi-channel idea capture | own |
| CI / Verification | Woodpecker / Gitea Actions / GitHub Actions | Verification | OSS |

## 2.2 Boundaries of Responsibility

### Plane CE
- Source of truth for human-readable project structure.
- Epics, tasks, states, dependencies, Kanban.
- Accepts reads/writes from humans and META ORCH.
- Never enforces workflow transitions.
- Never stores authoritative approval records.

### Governance Controller
- Owns state machine, approvals, policy, audit.
- Reads Plane webhooks; writes execution state back to Plane.
- Materializes approved Plane tasks into opentasks runtime graph.
- Invokes macro-agent for execution.
- Consumes events from Event Bridge.
- Owns final merge authorization.
- Persists durable approval records.

### Meta Orchestrator
- Reads raw ideas/drafts from Plane.
- Researches architecture and existing solutions.
- Decomposes ideas into Epic + Task DAG.
- Writes proposed tasks back to Plane (status = PROPOSED).
- Does not execute code, merge, or deploy.

### opentasks
- Holds runtime task graph for a single macro-agent run.
- Tracks dependencies and task claiming.
- Is populated and controlled by Governance Controller.

### macro-agent
- Manages agent lifecycle, teams, topology.
- Uses opentasks for runtime task claiming.
- Uses git-cascade for worktrees and landing.
- Emits workspace events via event channel.
- Does not decide whether work may happen.

### Event Bridge
- Subscribes to `workspaceManager.onEvent` in macro-agent.
- Retranslates relevant events to Controller via HTTP.
- Handles retries, idempotency, ordering.
- Provides fallback polling endpoint for Controller.

### Agent Harnesses
- OpenCode: primary worker/reviewer/meta harness.
- Claude Code: optional subscription planner/architect.
- Codex CLI: optional subscription reviewer.
- Aider: optional open-source fallback worker.

## 2.3 Data Flow Overview

1. Human submits idea → Intake Adapter / Plane / Telegram / Email / Slack.
2. Idea Ingestion classifies and creates Plane Draft (status = PROPOSED).
3. META ORCH reads draft and creates Epic + Task DAG in Plane.
4. Human reviews and requests execution approval in Plane.
5. Plane webhook → Controller POST /approvals.
6. Controller validates against Policy Engine.
7. Controller writes approval record and transitions state.
8. Controller materializes approved subgraph into opentasks.
9. Controller invokes macro-agent with Task Contract.
10. macro-agent executes via harnesses, emits workspace events.
11. Event Bridge forwards events → Controller.
12. Controller updates state; syncs to Plane.
13. CI runs verification.
14. Reviewer agent evaluates results.
15. Controller transitions to HUMAN_REVIEW.
16. Human approves merge → Controller authorizes merge → DONE.

## 2.4 Authority Matrix

| Decision | Authority | Plane Role | Controller Role | macro-agent Role |
|---|---|---|---|---|
| Create/edit tasks below EXEC_APPROVED | Human + META ORCH | read/write content | ignore unless policy violated | none |
| Approve plan | Human | UI trigger | record + state transition | none |
| Approve execution | Human | UI trigger | record + state transition | none |
| Transition execution states | Controller | projection only | authority | none |
| Materialize runtime DAG | Controller | none | writer to opentasks | reads from opentasks |
| Start/stop agents | Controller | none | invokes macro-agent | executes |
| Land/merge work | macro-agent under Controller policy | projection | authorizes/final gate | performs landing |
| Final merge approval | Human | UI trigger | records + triggers merge | none |
