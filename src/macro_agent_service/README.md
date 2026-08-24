# macro-agent service scaffold

This is a **Phase 2 Python stand-in** for the planned real `macro-agent`
integration. It implements the `/runs` REST contract that the Governance
Controller's `MacroAgentClient` expects, including `POST /runs`,
`GET /runs/{run_id}`, `POST /runs/{run_id}/cancel`,
`GET /runs/{run_id}/collect`, and `POST /runs/{run_id}/feedback`.

It does **not** run real agents, manage worktrees, or perform git-cascade
landings. Those responsibilities remain with the real `macro-agent` package,
which will be integrated in Phase 3.

See `decisions/ADR-002-macro-agent-stub-vs-package.md` for the architectural
rationale.
