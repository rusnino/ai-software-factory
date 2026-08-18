# Research Note: AMAP-ML/LongHorizon-Harness

Date: 2026-08-17
Source: `github.com/AMAP-ML/LongHorizon-Harness`, arXiv:2608.01964 (Ziyu Ma et al., Aug 2026)
Status: Evaluated, not adopted. Recorded per the ADR-001 precedent (document, defer, don't forget).

## 1. What it is

LongHorizon-Harness is **not** a benchmark, a model, or a general orchestrator. It is an MIT-licensed
Python package (`lh-harness`, ~2 weeks old at time of evaluation, 807 stars / 92 forks) that is the
open-source companion to a fresh arXiv paper. Its job: wrap an existing coding/computer-use agent CLI
(Claude Code, Codex, OpenCode, DeepSeek Harness) so that a **single task** can run reliably for dozens of
hours across both desktop GUI apps and the terminal, without losing track of what has actually been done.

Its own README states the scope explicitly: *"does not train a new model or replace an existing agent; it
provides the durable execution loop around one."*

## 2. Core mechanism: Manage → Execute → Audit (MEA), repeated per round

- **Manager** — every round, rebuilds the plan from the original goal plus the last *independently
  verified* state (not the agent's self-report) plus failure evidence. Maintains a **task contract**: a
  persistent, plain-text spec (target state, authoritative inputs, state carrier, allowed process,
  persistence boundary, acceptance constraints, evidence) that survives across rounds and is
  re-validated, not just trusted, every round. Picks `next_step`: `gui | cli | done | blocked | invalid | ask`.
- **Executor** — starts with a **fresh context each round** (deliberately discards prior chain-of-thought
  to avoid context rot), performs one bounded subtask via a GUI or CLI backend, under a per-role timeout
  (`EpisodeBudget`, default 1800s).
- **Auditor** — **read-only**, independently re-inspects actual files/logs/tests/screenshots rather than
  trusting the Executor's self-report. Explicitly instructed to *"not assume the stable task contract is
  correct... independently reconstruct and challenge the acceptance constraints from the original
  request."* Emits a structured verdict: `status: complete|incomplete|blocked`,
  `integrity_status: clean|suspect|violation`, `contract_audit_status: aligned|unknown|needs_revision|invalid`,
  plus missing/blocker/action-guidance fields.

This is a strictly **linear per-task state machine** (Manager → Executor → Auditor → next round), bounded
globally (`MAX_ROUNDS=1000`, default 25) — not a DAG, not multi-agent parallelism, no subagent spawning.
GUI vs. CLI is a per-round routing choice, not concurrency.

## 3. Stack and integration surfaces (verified from source)

- **Backend abstraction**: `AgentAdapter` is a one-method `Protocol`
  (`run_episode(prompt, env, budget) -> EpisodeResult`). Concrete adapters exist only for `claude_code`,
  `codex`, `deepseek_harness`, `opencode` (plus a generic `cli_agent`). Each of Manager/Executor/Auditor can
  independently be configured to a different backend/model via TOML config — conceptually close to our
  role→harness mapping (SPEC-06 §6.3), but scoped to the 3 fixed MEA roles of *one task*, not to a
  project-wide team topology.
- **GUI/computer-use capability**: delegated to external MCP servers installed as plugins
  (`open-computer-use`, `clawdcursor`). LongHorizon-Harness is an MCP *client*, not an MCP *server* — it
  does not expose itself as a tool another agent could call.
- **Invocation surfaces**:
  - CLI: `lh-harness init|run|dashboard|web|plugin|doctor` (`uv tool install lh-harness`).
  - HTTP/WebSocket API (FastAPI, bearer-token auth): create/list runs, stream live events, fetch
    snapshots/trajectories/artifacts, inject instructions mid-run, resolve human-in-the-loop approvals,
    abort/stop/resume. This is the real surface by which another system could drive it programmatically.
  - Config: `./.lh-harness/config.toml` — per-role agent/model/timeout overrides, `base_url` override for
    OpenAI/Anthropic-compatible proxies.
- Auth is deferred entirely to whatever the underlying agent CLI already uses (Claude Code/Codex login
  sessions) — LongHorizon-Harness itself holds no API keys.
- Maturity caveat: no visible test/lint CI workflow (only a release-automation workflow), open issues about
  timeouts, shutdown crashes, and race conditions — genuinely young, a research artifact turned into a
  usable tool, not a hardened production system.

## 4. Fit against this project's architecture

Mapping LongHorizon-Harness's actual scope against each authoritative node (SPEC-02/03/05/06/09):

| Our node | Responsibility | Could LongHorizon-Harness replace it? |
|---|---|---|
| **Governance Controller** | Authoritative state machine, pre-execution approval chain, policy, tamper-evident audit log | **No.** Its "approvals" (`/approvals/{id}/resolve`) are mid-task human nudges *during* an already-running loop, not gates that block execution from starting. Same structural mismatch ADR-001 already identified for Temporal: an execution-layer tool cannot double as governance authority. |
| **macro-agent** | Multi-agent team topology, worktree/git-cascade, agent messaging, project-wide task DAG (`opentasks`), merge/landing across concurrent streams | **No.** LongHorizon-Harness is single-task, linear, no parallel agents, no DAG, no merge/landing model. It does not attempt this layer at all. |
| **Plane CE** | Human PM UI / projection | Unrelated layer — no overlap. |
| **Agent Harness (SPEC-06)** — OpenCode/Claude Code/Codex/Aider | Runs one role's actual coding work inside one worktree | **Possible fit, as an additional optional adapter, not a replacement of the abstraction.** It already wraps OpenCode/Claude Code/Codex as its own backends, so `lh-harness run --task ... --agent opencode` could be registered as one more `AgentHarness` implementation for tasks expected to run far longer than a normal bounded worker session, and/or requiring desktop GUI steps. It would sit *between* macro-agent and OpenCode/Claude/Codex, not instead of them. |
| **Verification layer (SPEC-09)** — Completion Contract + Reviewer agent | Deterministic checks + one post-landing read-only review before HUMAN_REVIEW | **No wholesale replacement**, but the Auditor role's pattern (independent, evidence-based, contract-re-validating, every round, not trusting agent self-report) is worth borrowing conceptually when SPEC-09 is hardened in Phase 3. It is embedded in one continuous task loop, not a standalone service callable with a git diff + CI results + Task Contract the way SPEC-09 §9.3 specifies. |
| Computer-use / GUI automation | Not present in any current SPEC | A genuinely new capability (desktop app automation via MCP plugins) our specs never scoped. Complementary, not a replacement of anything. |

### Bottom line

LongHorizon-Harness cannot replace any authoritative node (Governance Controller, macro-agent). It
operates one layer below macro-agent and inside where an `AgentHarness` currently sits, for the specific
sub-problem of "one task, many hours, must not lose track of itself." The only architecturally consistent
integration point is an optional, additional entry in the Provider Registry (SPEC-06 §6.4) / a new
`AgentHarness` adapter, used selectively for long-running or GUI-touching opentasks, invoked by macro-agent
exactly like it invokes OpenCode/Claude Code/Codex today — with Governance Controller's and macro-agent's
authority completely unchanged.

Given its ~2-week maturity (no test CI, open crash/race-condition issues) it carries meaningfully higher
pre-1.0 risk than even macro-agent (SPEC-05 §5.8), and it has zero relevance to the current Phase 1
priority (Governance Controller). It fits, if anywhere, as a Phase 3+ "full harness matrix" / "advanced
conflict recovery" candidate (SPEC-10 §10.3) — worth re-evaluating once macro-agent integration itself is
proven, and only if a real task shows up that genuinely needs multi-hour unattended execution or
desktop-GUI steps.

## 5. Recommendation

Do not integrate for Phase 1 or Phase 2. Revisit in Phase 3+ under the same conditions as any other
pre-1.0 dependency (SPEC-05 §5.8): pin an exact version, keep it behind the `AgentHarness` abstraction so
it can be dropped without affecting Governance Controller or macro-agent, and re-check its CI/maturity
signals before adopting.

## Sources

- Repo: https://github.com/AMAP-ML/LongHorizon-Harness
- README: https://raw.githubusercontent.com/AMAP-ML/LongHorizon-Harness/main/README.md
- Paper: https://arxiv.org/abs/2608.01964
- Landing page: https://lh-harness.pages.dev
- Source referenced: `pyproject.toml`, `src/lh_harness/types.py`, `config.py`, `prompt_texts.py`,
  `adapters/base.py`, `webapi/server.py`, `environment/local.py`,
  `plugins/community_computer_use.py` under
  `https://raw.githubusercontent.com/AMAP-ML/LongHorizon-Harness/main/src/lh_harness/...`
- LICENSE: https://raw.githubusercontent.com/AMAP-ML/LongHorizon-Harness/main/LICENSE

## Note on this file's history

This file (and the corresponding `RISK-13` row in `requirements/REQUIREMENTS.md`) was originally written
2026-08-17 but never committed. A later session ran `git stash` at the very start of the Phase 1
Governance Controller work (commit `d8281a8`, 2026-08-18 00:29:46), which shelved the then-uncommitted
`docs/NEXT_STEPS.md`/`requirements/REQUIREMENTS.md` edits without ever popping them — this file itself,
being untracked at the time, wasn't captured by the stash at all and was separately lost. Recovered and
recreated 2026-08-18 from conversation history while reviewing the Phase 1 work; the stash has been
dropped after manually reconciling its two tracked-file changes into the current versions of those files.
