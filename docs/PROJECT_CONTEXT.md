# Project Context

## Goal

Build a preferably free/open-source and self-hosted AI Software Factory where a human can submit a high-level idea, have a Meta-Orchestration layer analyze/decompose it, review the resulting work as tickets, approve execution, and let heterogeneous coding agents perform work in isolated workspaces.

## Why This Exists

Existing tools either:

- Are SaaS-only and require cloud access.
- Rely on a single proprietary agent.
- Do not provide durable human-in-the-loop governance.
- Treat the project management UI as the workflow authority.

This project attempts to combine:

- Plane CE as a familiar, self-hosted project management UI.
- A custom Governance Controller as the authoritative policy/approval/state engine.
- macro-agent as a multi-agent execution orchestrator.
- OpenCode as the primary open-source harness, with optional Claude Code / Codex / Aider.

## Conceptual Vocabulary

- **SSOT** — Single Source of Truth.
- **Meta Orchestrator** — analyzes the high-level idea, requirements, and architecture; decomposes work.
- **Governance Controller** — deterministic control plane for state, policy, approvals, task DAG, executions.
- **Agent Registry** — catalog of available agent profiles/capabilities.
- **Agent Selector** — chooses the best eligible profile for a task.
- **Agent Harness** — OpenCode, Claude Code, Codex, Aider.
- **Agent Behavior** — skills/methodologies such as Superpowers.
- **Task Contract** — structured machine-readable task input.
- **Project Profile** — trusted per-project execution/security constraints.
- **Completion Contract** — deterministic technical verification.
- **Semantic Reviewer** — separate agent that judges whether implementation satisfies intent.
- **Human Gate** — explicit human approval stored by the Controller.
- **Execution Backend** — adapter that launches an agent: macro-agent + harnesses.
- **Projection** — UI copy/view of authoritative Controller state, e.g., Plane ticket status.

## Important Evolution

The early design was roughly:

```text
Plane → Windmill → Orca → agents
```

with BMAD/OpenSpec above Plane.

A detailed product review changed this. Plane CE cannot be trusted to enforce required workflow transitions/approval guards. Windmill CE should not be the authoritative policy layer. The current design makes a custom Python Controller + PostgreSQL authoritative and treats Plane, macro-agent, and other tools as replaceable adapters/interfaces.

## Preferred Development Conventions

- Python projects and utilities use `uv` / `uvx`.
- Start with tests; do not merge without verification.
- Keep components replaceable behind clear interfaces.
