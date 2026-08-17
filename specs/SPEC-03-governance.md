# SPEC-03: Governance Controller

## 3.1 Authority Rules

- The Governance Controller is the only system component that may transition tasks through governance-sensitive states.
- Plane CE is a projection; Plane state changes can request transitions but cannot enforce them.
- Approval records are stored in the Controller's PostgreSQL database.
- Audit log is append-only.
- All approval paths converge to a single endpoint: `POST /approvals`.

## 3.2 State Machine

```
PROPOSED
   |
   v (human plan approval + policy check)
PLAN_APPROVED
   |
   v (human execution approval + policy check)
EXEC_APPROVED
   |
   v (Controller ready + runtime materialization)
READY
   |
   v (macro-agent started)
RUNNING
   |
   |--> BLOCKED  (conflict / missing tool / crash / timeout)
   |
   v (landing completed)
AGENT_REVIEW
   |
   v (verification passed)
HUMAN_REVIEW
   |
   |--> FAILED  (verification failed, retry limit reached)
   |
   v (human merge approval)
DONE
```

Forbidden transitions without Controller approval:

- `PROPOSED -> EXEC_APPROVED`
- `PLAN_APPROVED -> READY`
- `EXEC_APPROVED -> RUNNING`
- `RUNNING -> DONE`
- `HUMAN_REVIEW -> DONE`

## 3.3 Approval Endpoint

### Request

```http
POST /approvals
Content-Type: application/json
Idempotency-Key: <uuid>

{
  "task_id": "TASK-42",
  "approval_type": "plan" | "execution" | "merge",
  "source": "plane" | "telegram" | "dashboard" | "cli",
  "actor": "user@example.com",
  "timestamp": "2026-08-17T12:00:00Z",
  "comment": "..."
}
```

### Response

- `200/201`: approval recorded, state advanced.
- `409`: conflicting state.
- `403`: policy violation.
- `422`: missing prerequisites.
- `400`: invalid request.

### Idempotency

Primary mechanism: HTTP header `Idempotency-Key: <uuid>` provided by the caller.

Fallback mechanism (only when `Idempotency-Key` is absent): synthetic key derived from `(task_id, approval_type, actor, timestamp)` truncated to seconds.

If a request with `Idempotency-Key` is received after a fallback-key request for the same logical approval, the Controller treats them as the same operation and returns the stored result. Plane webhook relay and Intake Adapter must always provide a stable `Idempotency-Key`.

## 3.4 Policy Engine

Validates before approval or execution:

- Task Contract completeness.
- Project Profile constraints.
- Forbidden paths / commands.
- Required human approval for sensitive operations.
- Harness allowlist.
- Approval chain correctness (e.g., plan before execution).

## 3.5 Task Contract Schema

```yaml
contract_version: "1.0"
task_id: TASK-42
project_id: PROJECT-1
objective: "Implement IPv6 forwarding for VPN clients"
inputs:
  - docs/architecture/networking.md
  - ADR-007
  - src/wireguard/
dependencies:
  - TASK-41
constraints:
  - python 3.13
  - use uv
  - no shell invocation for validation
  - IPv4 and IPv6 supported
acceptance:
  - malformed CIDR is rejected
  - duplicate peer IP is rejected
  - duplicate public keys are rejected
  - unit tests pass
deliverables:
  - implementation
  - tests
  - short implementation note
execution:
  team: default-pipeline
  harness: opencode
  timeout_minutes: 60
  max_retries: 2
verification:
  commands:
    - uv run pytest
    - uv run ruff check .
    - uv run mypy src
forbidden_paths:
  - ~/.ssh
  - /srv/production
approval_required: true
```

## 3.6 Project Profile Schema

```yaml
profile_version: "1.0"
project_id: PROJECT-1
project_name: "VPN Combiner"
repository:
  path: /srv/repos/vpn-combiner
  default_branch: main
security:
  forbidden_paths:
    - ~/.ssh
    - /srv/production
    - /etc/
  docker_socket: deny
  network: restricted
  destructive_shell: deny
  spawn_subagents: deny
git:
  force_push: deny
  merge_requires_human: true
  signed_commits: optional
execution:
  allowed_harnesses:
    - opencode
    - aider
  sandbox: worktree
  timeout_minutes: 60
  max_parallel_agents: 3
llm:
  gateway: litellm
  default_model: claude-sonnet-4
  allowed_models:
    - claude-sonnet-4
    - gpt-4.1
audit:
  retention_days: 365
```

## 3.7 Completion Contract Schema

```yaml
task_id: TASK-42
required:
  - type: test
    command: uv run pytest
    expect_exit: 0
  - type: lint
    command: uv run ruff check .
    expect_exit: 0
  - type: typecheck
    command: uv run mypy src
    expect_exit: 0
optional:
  - type: secret_scan
    command: uv run detect-secrets scan
forbidden_path_check:
  paths:
    - ~/.ssh
    - /srv/production
scope_check:
  description: "Verify agent did not modify files outside allowed scope"
  allowed_paths:
    - src/
    - tests/
  forbidden_paths:
    - pyproject.toml
    - .github/workflows/
```

## 3.8 Audit Log

Every governance event stored:

```yaml
event_id: uuid
event_type: approval | state_change | policy_check | execution_start | execution_end | merge | conflict | cancellation
task_id: TASK-42
execution_id: EXEC-123
actor: user@example.com | system:meta-orch | system:controller | system:macro-agent
source: plane | controller | macro-agent | ci | intake
 timestamp: ISO8601
payload:
  previous_state: EXEC_APPROVED
  new_state: READY
  reason: "human execution approval recorded"
```

Append-only, tamper-evident.

## 3.9 Reconciliation

Controller runs periodic reconciliation (default 15 min):

1. Compare Plane task list with Controller DB.
2. Compare Controller DB runtime graph with opentasks.
3. Detect divergence.
4. On divergence: alert human, do not auto-correct execution state.
5. Allow auto-correct only for projection fields (Plane labels, comments).
