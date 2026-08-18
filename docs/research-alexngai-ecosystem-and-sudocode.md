# Research Note: alexngai/openhive, alexngai/openswarm, sudocode-ai/sudocode

Date: 2026-08-18
Status: Evaluated, not adopted. Recorded per the ADR-001/RISK-13 precedent (document, defer, don't forget).

## 1. Why these three, together

`alexngai/macro-agent` is already a core Phase 1 dependency (SPEC-05: execution orchestration — agent
lifecycle, worktrees via `git-cascade`, task-claiming via `opentasks`). The same author maintains two more
projects — `openhive` and `openswarm` — built on overlapping or identical primitives. A third, unrelated
project, `sudocode-ai/sudocode`, targets a problem this project has not yet built anything for: Meta
Orchestration (idea → structured, git-native task graph). All three were evaluated together because they
sit at different layers of the same stack this project already depends on or still needs to build.

## 2. What each one actually is

### alexngai/openhive

*"A self-hostable synchronization hub and coordination plane for agent swarms."* Confirmed via
`package.json`: it depends directly on `macro-agent`, `opentasks`, `agent-inbox`, and `git-cascade` — the
exact dependency chain SPEC-05 already integrates with — plus a new package, `cognitive-core`, not
otherwise referenced anywhere in this project's specs.

Architecture: a single Fastify server with a database (SQLite/Postgres) and a WebSocket event bus, four
functional layers:
- **MAP Hub** — swarm registration, peer discovery, onboard tokens. *Hives* are namespace tags, not content.
- **Threads** — a unified chat+mail surface: ACP live sessions, async mail threads, session trajectories,
  webhook→swarm event routing.
- **Work Pipeline** — *"specs author intent → dispatches hand a spec to swarms → tasks decompose the
  work. An orchestrator polls dispatches and routes to agents via ACP or mail."*
- **Cross-instance sync** — a pull-based JSON-RPC 2.0 mesh protocol federating memory banks, skills, and
  session trajectories across instances, with gossip peer discovery and eventual consistency.

It can also host and manage child `SwarmRunner` instances directly (spawn, health-monitor, inject
credentials, optional OS-level sandboxing) — i.e., it can run macro-agent-based swarms *for* you, not just
coordinate externally-run ones. `macro-agent`'s own README confirms the relationship from the other side:
*"can serve as a compute backend for cognitive-core/OpenHive."*

Maturity: 0 stars, 0 forks, 2 open issues, MIT, created 2026-01-31, actively pushed as recently as
2026-08-04. A young, single-author project with essentially no external adoption yet.

### alexngai/openswarm

*"See and steer a swarm of coding agents."* Confirmed via `package.json`: does **not** depend on
`macro-agent` — only on the same lower-level primitives (`agent-inbox`, `git-cascade`). It is a sibling
orchestration engine, not a consumer of macro-agent, packaged as an end-user CLI/TUI rather than a library.

Positioning, verbatim: *"a multi-agent-**first** coding CLI... One agent is a tool; **N coordinated agents
is the product.**"* Ships prebuilt binaries for macOS/Linux (no Bun install needed to run it), a live
multi-pane TUI (`team watch`) showing every team member's diffs/tokens/cost, mid-run steering
(`team send`), six team topologies (`fanout · pipeline · peer-team · coordinator · committee ·
critic-loop`), `--git-cascade` worktree isolation with auto-merge, and Agent Client Protocol support (runs
as an agent inside editors like Zed). Explicitly multi-provider: *"OpenSwarm stores zero credentials — it
reads what's already in your environment or keychain"* — Anthropic, OpenAI, xAI, Google, DashScope API
keys, plus Claude Max/ChatGPT Plus subscriptions, mixable per team member.

Maturity: 0 stars, 0 forks, 5 open issues, MIT, created 2026-04-20, pushed 2026-08-11 (most recently active
of the three). Claims "3,500+ tests" (vitest) and dual lockfiles (`npm`/`bun`) for a compiled-binary
release pipeline — more engineering scaffolding than star count would suggest, but still effectively
unadopted externally.

### sudocode-ai/sudocode

*"Automate the logistics of managing context and agents. Direct the work instead of babysitting agents."*
A different author/org entirely. Core model: **Specs** (human intent — requirements, RFCs, design
decisions; *what* to do) and **Issues** (agent runtime context and implementation plans; *how*), both
stored as git-native, human-and-agent-editable Markdown with YAML frontmatter under `.sudocode/`, organized
as a graph with dependency relationships. A dependency graph of issues can be run as an automated workflow:
each issue executes in topological order, accumulating changes in a temp branch/worktree, one commit per
issue (revertible to any point). Ships a CLI, a local server + web UI (issue kanban, spec editor,
execution/trajectory monitoring), and an MCP server; runs Claude Code/Codex/Cursor "+more" agents in
parallel. Already has **bidirectional** external integrations with **OpenSpec**, **Spec-Kit**, and
**Beads** (Jira/Linear "coming soon").

Maturity: **290 stars, 26 forks, 11 open issues, Apache-2.0**, created 2025-10-16, pushed 2026-03-18, own
domain (`sudocode.ai`, `docs.sudocode.ai`), active Discord. Meaningfully more mature and adopted than any
of the alexngai repos above, or than LongHorizon-Harness/Semantica evaluated previously.

## 3. Fit against this project's architecture

| Repo | Where it could help | Where it conflicts with our non-negotiables |
|---|---|---|
| **openswarm** | A concrete, real, actively-developed **Plan B for macro-agent itself** — exactly the scenario SPEC-05 §5.8 already anticipated ("create abstraction layer so macro-agent can be replaced by custom orchestrator"). Also directly answers `RISK-06` (Claude-specific assumptions in macro-agent) with genuine multi-provider support out of the box. | Swapping the execution-orchestration layer is an ADR-level decision, not a routine change. Unconfirmed whether it exposes an HTTP `/runs`-style API our `MacroAgentExecutor` (`start/status/cancel/collect`) could drive non-interactively — its documented surface is CLI/TUI/ACP-first (`team start --detach`, `team watch`, `team send`, `team stop`), which reads more like a human-operated daemon than a machine-callable service. Would need direct verification before any real evaluation. |
| **openhive** | Its Work Pipeline (specs → dispatches → tasks, an orchestrator routing to agents) is architecturally close to what Meta Orchestrator + Governance Controller + opentasks-materialization are supposed to become in Phase 2/3. Its MAP Hub + cross-instance sync is the natural shape of a **multi-swarm federation** layer if this project ever scales beyond one macro-agent instance/project — not currently scoped anywhere in SPEC-02/SPEC-10. | Its dispatch/task pipeline has no human-approval-gate concept at all. Adopting its Work Pipeline as-is would repeat exactly the mistake ADR-001 already rejected for Plane/Temporal: letting an external tool become the de-facto workflow authority. Any use would have to sit as a **projection/coordination layer *above* or *alongside* Governance Controller**, never replacing its approval-chain authority — the same discipline SPEC-04 already applies to Plane. |
| **sudocode** | The strongest fit of the three: SPEC-01 §1.3 names Meta Orchestration as *"OpenCode (planner) + BMAD + OpenSpec"* — sudocode already ships a working, bidirectional **OpenSpec integration**, plus a Spec/Issue graph model conceptually close to this project's own Task Contract / Project Profile YAML schemas (SPEC-03 §3.5-3.6). Could meaningfully accelerate or directly inform the still-unbuilt Meta Orchestrator (Phase 2/3) rather than building idea-decomposition tooling from scratch. | Its own "workflow automation" (topological issue execution accumulating commits in a temp branch/worktree) is execution orchestration — macro-agent's job, and AGENTS.md's "do not reimplement macro-agent internals" applies here by the same logic already used for BMAD/OpenSpec today. If ever adopted, use only the Spec/Issue **context-graph** layer (the WHAT/HOW-planning half), not its execution engine. |

## 4. Recommendation

Do not integrate any of the three for Phase 1 or Phase 2. All three are either too immature
(`openhive`/`openswarm`: 0 stars/forks, single-author, weeks old) or would require careful scoping to avoid
duplicating Governance Controller's or macro-agent's authority (`sudocode`, despite its real maturity).

- `openswarm` is worth remembering specifically as the concrete answer to "what if macro-agent doesn't pan
  out" (`RISK-02`/`RISK-08`) — revisit if macro-agent's pre-1.0 risk ever materializes into an actual
  blocker, and verify its programmatic/API surface first.
- `openhive` is a Phase 3+ candidate purely for multi-swarm federation, not before this project even
  proves single-swarm operation.
- `sudocode` is the one worth revisiting first and earliest — when Meta Orchestrator work actually starts
  (Phase 2/3), evaluate its Spec/Issue model and OpenSpec integration *before* building equivalent tooling
  from scratch, while keeping its execution engine out of scope.

## Sources

- https://github.com/alexngai/openhive (README, `package.json`, `api.github.com/repos/alexngai/openhive`)
- https://github.com/alexngai/openswarm (README, `package.json`, `api.github.com/repos/alexngai/openswarm`)
- https://github.com/sudocode-ai/sudocode (README, `api.github.com/repos/sudocode-ai/sudocode`)
- https://github.com/alexngai/macro-agent (README, for the "compute backend for cognitive-core/OpenHive" cross-reference)
