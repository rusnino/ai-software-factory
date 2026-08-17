# SPEC-01: Vision and Terminology

## 1.1 Goal

Build a self-hosted, open-source, free AI Software Factory where a human can:

1. Submit a high-level idea through multiple channels.
2. Have a Meta Orchestration layer analyze, research, and decompose it into tickets.
3. Review the proposed work in a human-friendly project management UI.
4. Approve execution through an authoritative governance layer.
5. Let heterogeneous coding agents execute the work in isolated workspaces.
6. Verify results automatically and through review agents.
7. Apply final human review before merge/done.

## 1.2 Core Principles

- **Self-hosted**: all critical components run on infrastructure we control.
- **Open-source**: all dependencies must be open-source.
- **Free**: no license fees for self-hosted use; minimal TCO.
- **Human-in-the-loop**: human approval at plan level and before final merge.
- **Governance authority**: a custom controller owns approvals, state machine, policy, audit.
- **Plane as projection**: Plane CE is the human-facing SSOT view, not the authority.
- **Heterogeneous agents**: support multiple harnesses (OpenCode, Claude Code, Codex, Aider).
- **Durable state**: project state lives in Plane + Controller DB + Git, not in agent sessions.
- **Separation of concerns**: WHAT (Meta), WHETHER (Governance), WHO/WHEN (macro-agent), HOW (harness + skills).

## 1.3 Conceptual Layers

| Layer | Question | Primary Component |
|---|---|---|
| Human | "Do I agree?" | Human user |
| Project View | "What is the official project view?" | Plane CE |
| Meta Orchestration | "What should be done?" | OpenCode (planner) + BMAD + OpenSpec |
| Governance | "May work happen?" | Governance Controller |
| Execution Orchestration | "Who / when / in what order?" | macro-agent + opentasks |
| Agent Harness | "How to implement?" | OpenCode / Claude Code / Codex / Aider |
| Behavior / Skills | "How to do it well?" | Superpowers + domain skills |
| Isolation | "Where to execute safely?" | worktree → Docker → Firecracker/Kata |
| Verification | "Is it correct?" | CI + Completion Contract + Reviewer |

## 1.4 Key Terminology

- **SSOT**: Single Source of Truth.
- **Human Gate**: mandatory human approval point.
- **Task Contract**: structured description of objective, inputs, constraints, acceptance.
- **Project Profile**: per-project security/execution policy.
- **Completion Contract**: deterministic verification checklist.
- **Runtime DAG**: opentasks task graph, derived from Plane.
- **Event Bridge**: component translating macro-agent workspace events to Controller state updates.
- **Harness**: CLI/runtime in which an LLM-agent executes (OpenCode, Claude Code, Codex, Aider).
- **Worktree**: isolated Git working directory for a task.
- **Landing**: finalization of work (merge, queue, push) by macro-agent.
