# SPEC-06: Agent Harnesses

## 6.1 Harness Abstraction

Each harness is an adapter implementing a common interface:

```python
class AgentHarness(Protocol):
    name: str
    allowed_roles: list[Role]

    async def spawn(
        self,
        role: Role,
        task_contract: TaskContract,
        worktree: Path,
        mcp_servers: list[McpServer],
        env: dict[str, str],
    ) -> AgentSession: ...

    async def send_prompt(self, session: AgentSession, prompt: str) -> None: ...
    async def read_response(self, session: AgentSession) -> AsyncIterable[str]: ...
    async def terminate(self, session: AgentSession) -> None: ...
```

## 6.2 Supported Harnesses

### OpenCode (primary)

- License: open-source
- Roles: worker, reviewer, planner, meta
- Auth: provider-configured (local model / API)
- Notes: default worker harness; supports skills and MCP.

### Claude Code

- License: subscription
- Roles: planner, architect, reviewer
- Auth: subscription
- Notes: heterogeneous team candidate; default planner.

### Codex CLI

- License: subscription
- Roles: reviewer, verifier
- Auth: subscription
- Notes: heterogeneous team candidate; default reviewer.

### Aider

- License: open-source
- Roles: worker (multi-file edits), fallback
- Auth: provider-configured
- Notes: good for refactorings; integrate as optional backend.

## 6.3 Role-to-Harness Mapping

Default team configuration:

```yaml
team: default-pipeline
roles:
  planner:
    harness: claude-code
    purpose: decompose and coordinate
  worker:
    harness: opencode
    purpose: implement
  reviewer:
    harness: codex
    purpose: review
```

Fallback if subscription unavailable:

```yaml
roles:
  planner: opencode
  worker: opencode
  reviewer: opencode
```

## 6.4 Provider Registry

```yaml
providers:
  opencode:
    command: opencode
    auth: provider-configured
    supports_mcp: true
  claude-code:
    command: claude
    auth: subscription
    supports_mcp: true
  codex:
    command: codex
    auth: subscription
    supports_mcp: true
  aider:
    command: aider
    auth: provider-configured
    supports_mcp: true
```

Credentials must stay outside team YAML.

## 6.5 MCP Injection

Each harness receives the same MCP toolset based on role capabilities:

- `workspace.commit`
- `workspace.land`
- `workspace.resolve`
- `task.claim`
- `agent-inbox.send_message`
- `agent-inbox.check_inbox`

Highest-risk area: proving non-Claude harnesses can receive and use MCP tools.

## 6.6 Phase 1 Acceptance for Harnesses

- At least two distinct harnesses participate in one execution.
- OpenCode tested unless documented ACP blocker.
- Per-role harness selection is configuration-driven.
- Required macro-agent MCP tools work from non-Claude harness.
