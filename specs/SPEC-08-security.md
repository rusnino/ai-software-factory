# SPEC-08: Security and Isolation

## 8.1 Security Principles

- Worktree is not a security sandbox.
- Human approval required for execution and merge.
- Least privilege for each harness role.
- Secrets never stored in agent prompt or team YAML.
- All executions logged and auditable.

## 8.2 Isolation Levels

| Level | Technology | Use Case | Phase |
|---|---|---|---|
| 1 | Git worktree | Baseline, trusted code | Phase 1 |
| 2 | Separate shell/session | Better separation | Phase 2 |
| 3 | Docker container | Untrusted code / network | Phase 3 |
| 4 | Firecracker / Kata / gVisor | Autonomous production | Phase 4 |

## 8.3 Worktree Isolation

- One worktree per active task/stream.
- Worktrees live under `.worktrees/` or configured base.
- Agents cannot access sibling worktrees without policy exception.

## 8.4 Docker Sandbox Goals

- No host network unless explicitly allowed.
- Read-only root filesystem where possible.
- No Docker socket unless project explicitly allows.
- Secret injection via ephemeral files, not env.

## 8.5 Network Policy

Project Profile controls:

- outbound network allow/deny
- egress domains
- LLM gateway routing
- CI callback URLs

## 8.6 Secret Management

- Use dedicated secret store (e.g., Dagger secrets, HashiCorp Vault, Docker secrets).
- Agent receives only scoped secrets per Project Profile.
- No secrets in Plane comments or task descriptions.
- Rotate secrets on suspicious activity.

## 8.7 Forbidden Operations

Default deny:

- destructive shell (`rm -rf /`, `dd`, etc.)
- Docker socket access
- force push
- production access
- spawn subagents without explicit role capability

## 8.8 Audit and Observability

- Every shell command logged.
- Every file change tracked (Change-Id via git-cascade).
- Every policy decision logged.
- Alerts on policy violations.
