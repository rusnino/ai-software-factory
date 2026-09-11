# Research: Macro-Agent Alternatives — Broad Survey (2026-09-11)

Date: 2026-09-11
Status: research memo (comparative survey). No architectural decisions made; no
code/spec changes.

## 1. Scope

The user supplied a list of 26 additional OSS projects (beyond the ones already
surveyed in `docs/research-proposed-orchestration-stack-2026-08-23.md` §8 and
the `docs/research-*-vs-ai-software-factory-comparison-2026-09-07.md` series)
and asked which could serve as a replacement for the "agent launcher" cube —
i.e., `macro-agent`'s execution-orchestration role (agent lifecycle, team
topology, workspace isolation, task claiming from an external DAG, inter-agent
messaging, landing/merge strategy, conflict recovery, and an external API/MCP
surface a Governance Controller can drive) — not the Governance Controller
itself.

All metadata was verified via the GitHub API (`gh api repos/<owner>/<repo>`) on
2026-09-11, not web search or the list's own descriptions. READMEs were fetched
directly for the plausible candidates before drawing conclusions.

## 2. Functional requirements for the "macro-agent" cube (restated)

From `specs/SPEC-05-macro-agent-integration.md` and `AGENTS.md`, unchanged since
`docs/research-proposed-orchestration-stack-2026-08-23.md`:

1. Agent lifecycle (spawn/kill/monitor/restart) for multiple concurrent agents.
2. Team/topology — multiple roles cooperating on one logical task.
3. Workspace isolation — one git worktree/stream per active task.
4. Task claiming from an external DAG — reads already-approved work; does not
   decide *whether* work may happen, only *who/when*.
5. Inter-agent messaging (`agent-inbox` equivalent).
6. Deterministic, configurable landing/merge strategies.
7. Conflict recovery strategies (defer/abandon/escalate/auto-resolve/spawn-resolver).
8. MCP tool surface for harnesses (`workspace.commit/land/resolve`,
   `task.claim`, `agent-inbox.*`).
9. An outbound event stream a Governance Controller can consume — not
   autonomous governance decisions.
10. A REST/ACP API an external Controller can drive (start/status/cancel/collect).
11. No claim to be a security sandbox itself.
12. Subordinate to an external approval authority — the single hardest
    requirement, since almost nothing in this space is built with a separate
    governance authority in mind.

## 3. Triaged out — wrong domain or wrong layer

| Project | Why it doesn't fit |
|---|---|
| `TauricResearch/TradingAgents`, `The-Swarm-Corporation/AutoHedge` | Financial trading multi-agent frameworks — unrelated domain. |
| `THU-MAIC/OpenMAIC` | Educational multi-agent "interactive classroom" — unrelated domain. |
| `aiming-lab/AutoResearchClaw`, `alphaXiv/OpenResearch` | Research-paper-writing agents — unrelated domain. |
| `elder-plinius/T3MP3ST` | Offensive-security/red-teaming meta-harness — relevant to a security-scanning cube (parallel to `Tencent/AI-Infra-Guard`, already noted elsewhere), not execution orchestration. |
| `ZSeven-W/openpencil` | AI-native vector design tool — unrelated domain. |
| `Significant-Gravitas/AutoGPT` | Old single-agent autonomous-loop architecture; not a coding-fleet execution engine with worktrees/lifecycle. |
| `langgenius/dify`, `genkit-ai/genkit`, `mastra-ai/mastra` | Low-code AI app / agentic-workflow *building* frameworks — the same "wrong layer" verdict already reached for LangGraph/MAF/CrewAI in the orchestration-stack memo §4.5: they build agent reasoning graphs, not execution-orchestration infrastructure. |
| `elizaOS/eliza` | "Agentic operating system" oriented at social/chat personas, historically crypto-agent-adjacent — not a coding-fleet orchestrator. |
| `wshobson/agents` | A marketplace of Claude Code subagent/plugin *definitions* — content, not an orchestration engine. |
| `cline/cline` | A single-agent coding assistant (SDK/IDE extension/CLI) — same category as OpenCode/Aider, a harness, not a fleet orchestrator. |
| `FoundationAgents/MetaGPT` | Conceptually adjacent ("First AI Software Company," SOP-based simulation), 70.3k★, but **last pushed 2026-01-21** — roughly 8 months of silence as of this writing despite the star count. Treated as effectively unmaintained. |
| `666ghj/MiroFish` | "Swarm intelligence engine, predicting anything" — a forecasting engine, not a coding execution orchestrator. |
| `tinyhumansai/openhuman` | Personal AI assistant (local-first memory, deep research) — closer to the "Hermes" category already evaluated, not a governed execution-orchestration engine. |
| `bytedance/deer-flow` | Real and active (82.2k★, pushed today), but a general-purpose "super agent harness" for deep research/skills with Docker/AIO sandbox-per-task, not git-worktree-per-task coding-fleet orchestration with landing/merge semantics. Noted for completeness, not pursued further. |

## 4. Real candidates — checked against the functional-requirements checklist

READMEs fetched and read directly for each of the following.

| Project | ★ (2026-09-11) | Worktree/lifecycle | Messaging (`agent-inbox` equivalent) | Task-claiming/DAG | Merge/landing | Conflict recovery | External API for a Controller | License |
|---|---|---|---|---|---|---|---|---|
| `stablyai/orca` | 65,857 | Isolated git worktree per agent | None found | Partial (browses GitHub/Linear boards) | "Compare results, merge the winner" — manual | None | CLI, scriptable (`orca worktree create/snapshot/click/fill`) | MIT |
| `superset-sh/superset` | 14,056 | Isolated git worktree, "100+ agents in parallel" | None found | None | Manual, via diff viewer | None | **MCP Server** — agents create/manage their own workspaces | ⚠️ **Elastic License 2.0** (source-available; explicitly forbids reselling Superset itself as a service) |
| `chaitanyagiri/munder-difflin` | 6,839 | Optional per-agent git worktree | **Literal outbox/inbox pattern between agents** — the closest match to `agent-inbox` found in this whole survey | Partial — a "GOD" agent routes/assigns/escalates | Not clearly described | Human gate specifically on spend/scope/destructive ops | Slack & webhooks (spawns ephemeral workers) | MIT |
| `block/buzz` | 32,532 | Not worktree-oriented at all | **A Nostr relay: every message, reaction, workflow step, review approval, and git event is a signed event in one log** — a genuinely stronger primitive than a plain inbox | None | Mentioned ("merge decision lands in the room") but not automated | None | WS + REST; `buzz-acp` (ACP↔MCP bridge for Goose/Codex/Claude Code) | Apache-2.0 |
| `777genius/agent-teams-ai` | 2,094 | Not explicitly described | Agents message and review each other | Kanban + org hierarchy | None | None | No external API/MCP surface found | ⚠️ **AGPL-3.0** (copyleft) |
| `PrimeIntellect-ai/prime-agent` | 20,495 | Explicitly disclaims being a security sandbox ("not a security sandbox," own words) | Agent-to-agent messaging | None (single-session oriented) | None | None | None — session-centric, not fleet-oriented | MIT |
| `tutti-os/tutti` | 3,708 | Not explicit | Implied via "shared visibility... avoid or resolve conflicts" | Idea → decomposed sub-tasks → human review → assignment | None | Explicit cross-provider conflict-avoidance concept (Claude/Codex/OpenClaw/Hermes agents coordinating) | None found | Apache-2.0 |
| `traycerai/traycer` | 1,454 | Not explicit | Agent-to-agent debate/peer-review, gated by a "capability matrix" | None | None | None | Capability matrix limits what one agent can do to another | MIT |

## 5. Recommendation

No single project closes every requirement. Two genuinely complementary
candidates stand out:

1. **`chaitanyagiri/munder-difflin`** is the closest conceptual match to what
   `SPEC-05` describes for macro-agent as a whole: a literal outbox/inbox
   message-routing model between agents (= `agent-inbox`), optional per-agent
   git worktrees (= `git-cascade`), a coordinating "GOD" agent (= topology/
   team coordinator), and explicit human gates on spend/scope/destructive
   operations. MIT-licensed. The caveat: it is very young (created 2026-05-31,
   ~3 months old, 6,839★) — a materially higher immaturity risk than
   `alexngai/macro-agent` itself, offset somewhat by visible traction (#5
   Product of the Day on Product Hunt) that suggests more community attention
   than macro-agent's near-zero visibility (see
   `docs/research-proposed-orchestration-stack-2026-08-23.md` §8.3).

2. **`block/buzz`** is not a full macro-agent replacement, but a strong
   candidate specifically for the **`agent-inbox` + audit-log** sub-component:
   every message, review approval, and git event is a **cryptographically
   signed** Nostr event in one log, with the same identity model for humans and
   agents. This is a stricter non-repudiation guarantee than a plain
   hash-chain (a signature proves *who* produced the event, not just that the
   log wasn't altered after the fact) and directly addresses the concern raised
   in `docs/research-proposed-orchestration-stack-2026-08-23.md` §8.3: that
   `alexngai/agent-inbox` has no currently-public source. `block/buzz` is
   built by the Goose team (Block, Inc.), Apache-2.0, with a real ACP↔MCP
   bridge for Goose/Codex/Claude Code already built.

3. **`stablyai/orca`** and **`superset-sh/superset`** are more mature on the
   worktree/lifecycle axis specifically (65.9k★ and 14k★ respectively, both
   actively growing) but neither has a task-claiming DAG or a conflict-recovery
   concept as a product feature — both solve "run many agents in parallel and
   compare/review results," not "deterministically carry one approved task
   through to a landed merge." Superset's license (Elastic License 2.0,
   source-available, forbids reselling it as a hosted service) is the same
   caveat category already raised for `nodeterm` and `loki-mode` elsewhere in
   this research thread.

None of these candidates were designed with an external, separate governance
authority in mind (requirement #12 above) — none subordinate their own
task-start decisions to anything like a pre-execution approval gate. Adopting
any of them would still require building the same Controller-side integration
work `macro-agent` already requires (materializing an approved subgraph,
translating workspace events into Controller state, gating invocation on
`EXEC_APPROVED`). This is not a reason to prefer `macro-agent` over them — it
is the same integration cost regardless of which execution-orchestration tool
sits underneath.

## 6. Sources

All metadata via GitHub API (`gh api repos/<owner>/<repo>`), READMEs fetched
directly, 2026-09-11:

- https://github.com/stablyai/orca
- https://github.com/block/buzz
- https://github.com/chaitanyagiri/munder-difflin
- https://github.com/bytedance/deer-flow
- https://github.com/superset-sh/superset
- https://github.com/777genius/agent-teams-ai
- https://github.com/PrimeIntellect-ai/prime-agent
- https://github.com/traycerai/traycer
- https://github.com/tutti-os/tutti
- Plus metadata-only checks on: `TauricResearch/TradingAgents`,
  `The-Swarm-Corporation/AutoHedge`, `THU-MAIC/OpenMAIC`,
  `aiming-lab/AutoResearchClaw`, `alphaXiv/OpenResearch`,
  `elder-plinius/T3MP3ST`, `ZSeven-W/openpencil`,
  `Significant-Gravitas/AutoGPT`, `langgenius/dify`, `genkit-ai/genkit`,
  `mastra-ai/mastra`, `elizaOS/eliza`, `wshobson/agents`, `cline/cline`,
  `FoundationAgents/MetaGPT`, `666ghj/MiroFish`, `tinyhumansai/openhuman`
- Companion memo: `docs/research-proposed-orchestration-stack-2026-08-23.md`
  §8 (the `macro-agent` vendor-concentration finding this survey follows up on)
