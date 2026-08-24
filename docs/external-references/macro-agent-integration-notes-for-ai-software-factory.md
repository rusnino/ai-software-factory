# Notes: How to integrate and test macro-agent in AI Software Factory

> Context: ai-software-factory uses macro-agent as a runtime dependency, not as something to fork or patch. This document is written for the primary AI development agent working on the AI Software Factory project.

## What we know about the project state

- `/root/projects/ai-software-factory` is the project root.
- It has **no `package.json` at the repo root**. The project is Python-first: `src/governance_controller/pyproject.toml` uses FastAPI/SQLModel/uv.
- The Python Controller already has a **macro-agent executor abstraction** (`MacroAgentClient`, `MacroAgentExecutor`) and an **in-process Event Bridge**, but these are currently **stubs / in-process** implementations (see `docs/NEXT_STEPS.md` §"Real macro-agent integration").
- SPEC-05 (`specs/SPEC-05-macro-agent-integration.md`) defines the intended integration contract.
- The external documentation snapshot lives at `docs/external-references/macro-agent-github-documentation.md`.

## Recommended integration model

Do **not** add macro-agent at the repo root as a Node.js project. Keep the Python Controller as the authoritative runtime and treat macro-agent as an **external execution service**. Two practical options:

### Option A: macro-agent as a separate service (recommended for production)

1. Create a dedicated deployment directory, e.g. `src/macro_agent_service/` or a separate repo, containing a `package.json` that depends on `macro-agent`.
2. Build a thin Node.js/TypeScript wrapper that:
   - imports `macro-agent` programmatically (`bootV2`, `TeamManagerV2`, `WorkspaceManager`);
   - exposes the REST/ACP interface that the Controller expects;
   - forwards `workspaceManager.onEvent` events to the Controller `POST /events` endpoint (the Event Bridge role from SPEC-05 §5.4);
   - is configured via environment variables (host, port, Controller URL, git repo path, API keys).
3. The Python Controller talks to this service over HTTP, exactly as SPEC-05 §5.5 describes (`MacroAgentExecutor.start/status/cancel/collect`).
4. Ship macro-agent service in its own Docker container alongside the Controller and PostgreSQL in `docker-compose.yml`.

Pros:
- Clean separation of concerns: Python governance, Node.js agent execution.
- The Controller can treat macro-agent as a replaceable backend.
- Upgrades of macro-agent do not require touching the Python codebase.

Cons:
- One more service to operate.

### Option B: macro-agent installed alongside the Controller for local dev / tests

1. Add a `package.json` at the repo root **only if** the team accepts a hybrid Python+Node repo.
2. Run `npm install macro-agent` there.
3. Use `npx multiagent-cli` or `npx multiagent --acp` from the Python Controller via subprocess calls, or embed macro-agent in-process with a Node.js bridge spawned by Python.

This is **not recommended** for production because:
- it couples the Python runtime to Node.js installation and npm dependency resolution;
- it complicates CI/CD, Docker image, and version pinning;
- it makes macro-agent harder to replace later.

Use Option B only for quick local experimentation or if the team explicitly decides to keep everything in one repo.

## Version pinning policy

- macro-agent is pre-1.0. Pin an **exact version** in `package.json`:
  ```json
  "dependencies": {
    "macro-agent": "0.x.y"
  }
  ```
  Not `"^0.x.y"` and not `"*"`.
- Track the upstream changelog (`CHANGELOG.md`) before any upgrade.
- Maintain a compatibility matrix in `docs/` or `requirements/` showing which AI Software Factory release was tested against which macro-agent version.

## How to install for Option A

Inside the macro-agent service directory (e.g. `src/macro_agent_service/`):

```bash
npm init -y
npm install macro-agent@<exact-version>
# Also consider:
npm install --save-dev @types/node typescript tsx vitest
```

Then write the wrapper in TypeScript, build with `tsc`, run with `node dist/index.js` or `tsx src/index.ts` in dev.

Do **not** install macro-agent globally (`npm install -g`). Global installs make version pinning impossible and break reproducible builds.

## What needs to be decided by the primary development agent

1. **Service boundary**: does macro-agent live in `src/macro_agent_service/`, a sibling repo, or a Docker-only service?
2. **API contract**: the current `MacroAgentClient` in Python assumes an HTTP interface. macro-agent exposes ACP (WebSocket) and REST. Which one does the Controller use?
   - For event streaming, ACP/WebSocket is more natural than polling.
   - For simpler integration, REST polling plus the Event Bridge microservice is easier to debug.
3. **Team templates**: where do `.multiagent/teams/<name>/team.yaml` files live?
   - Suggestion: `src/macro_agent_service/teams/` or a config volume mounted at runtime.
4. **Git repository**: macro-agent operates on a git repo with `git-cascade`. Which repo does it use?
   - Likely the AI Software Factory repo itself, or a separate "execution workspace" repo where agents commit changes.
5. **Credentials / secrets**: macro-agent agents need API keys for LLM providers. These must be injected via environment variables, not stored in team YAML or prompts (see `AGENTS.md` §"Security Constraints").
6. **Harness selection**: the Controller has a Harness Provider Registry (OpenCode, Claude Code). Does the macro-agent service pick the harness per role, or does the Controller pass a pre-configured team YAML?
7. **Conflict recovery**: macro-agent can auto-resolve, defer, escalate, or spawn-resolver. The Controller state machine has `BLOCKED`. Which strategy is the default and how does the Controller learn about conflicts?
8. **Testing strategy**: see below.

## Suggested testing approach

Because macro-agent is pre-1.0 and its e2e tests require real Claude Code subprocesses, the AI Software Factory integration tests should **not** depend on a live macro-agent instance.

Recommended layers:

1. **Unit tests for the Python Controller**
   - Mock `MacroAgentClient` at the HTTP/ACP boundary.
   - Verify state machine transitions, event idempotency, and fallback polling.

2. **Contract tests between Controller and macro-agent service**
   - Stand up the macro-agent service in a test container.
   - Drive it through the same REST/ACP interface the Controller uses.
   - Assert that events emitted by macro-agent map correctly to Controller `POST /events` calls.
   - Use a throwaway git repo in `/tmp` or a Docker volume.

3. **macro-agent service tests**
   - Test that the wrapper correctly:
     - boots `macro-agent` (`bootV2`);
     - loads team YAML;
     - forwards events;
     - handles shutdown cleanly.

4. **Manual / nightly integration tests**
   - Run a real team template against a real git repo with a real LLM provider key.
   - Validate the full path: Controller approval → macro-agent execution → landing → verification → human review gate.

Do **not** run real macro-agent e2e tests in the main CI pipeline unless the environment has:
   - a real LLM provider key;
   - git-cascade-compatible repo;
   - long timeouts (agent runs can take minutes).

## Files the primary agent should read before implementing

1. `README.md` — project overview.
2. `AGENTS.md` — non-negotiable rules, current priority, gap tracking.
3. `specs/SPEC-05-macro-agent-integration.md` — integration spec.
4. `docs/NEXT_STEPS.md` — current state, Phase 2 candidate tasks.
5. `docs/external-references/macro-agent-github-documentation.md` — upstream API reference.
6. `src/governance_controller/pyproject.toml` — Python dependencies and tooling.
7. Existing `MacroAgentClient` / `MacroAgentExecutor` code in `src/governance_controller/`.

## What was done in this session

- Fetched and saved upstream macro-agent documentation to `docs/external-references/macro-agent-github-documentation.md`.
- Confirmed the project has no root `package.json` and is Python-first.
- Prepared this guidance document for the primary development agent.
- Did **not** install macro-agent or modify application code, per instruction to let the primary agent decide.
