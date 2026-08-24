# Research: Proposed Orchestration Stack vs. Current AI Software Factory Architecture

Date: 2026-08-23 (updated 2026-08-24)
Status: research memo (no architectural decisions made; no code/spec changes)

## 1. What was reviewed

A stack diagram was proposed for evaluation:

```
                    ┌──────────────────┐
                    │   User / Telegram│
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │ Hermes / Meta-agent│
                    │ planning + routing │
                    └──────┬─────┬──────┘
                           │     │
                 ┌─────────▼─┐ ┌─▼──────────┐
                 │ Plane      │ │ LiteLLM    │
                 │ task board │ │ model GW   │
                 └──────┬─────┘ └─┬──────────┘
                        │          │
                 ┌──────▼──────────▼──────┐
                 │ LangGraph / MAF / CrewAI│
                 │ workflow orchestration  │
                 └──────┬─────────────────┘
                        │
                 ┌──────▼──────────┐
                 │ Temporal/Hatchet│
                 │ durable execution│
                 └──────┬──────────┘
                        │
          ┌─────────────┼─────────────┐
          │             │             │
   ┌──────▼─────┐ ┌─────▼─────┐ ┌────▼─────┐
   │ OpenHands  │ │ Goose     │ │ custom   │
   │ coding     │ │ agent     │ │ workers  │
   │ harness    │ │ harness   │ │ via MCP  │
   └────────────┘ └───────────┘ └──────────┘
```

Each node was checked against the already-decided architecture in `AGENTS.md`,
`specs/SPEC-01` through `SPEC-10`, `decisions/ADR-001-governance-controller-implementation.md`,
`requirements/REQUIREMENTS.md`, and `docs/NEXT_STEPS.md`, plus external research on
any named technology absent from the specs (Hermes, LangGraph, MAF, CrewAI, Hatchet,
OpenHands, Goose — Temporal and LiteLLM already appear in the existing docs).

## 2. Summary verdict

| Diagram node | Verdict | Notes |
|---|---|---|
| User / Telegram | **Matches existing design** | Telegram Intake Adapter already exists, converges on `POST /approvals`. |
| Hermes / Meta-agent (planning + routing) | **Resolved 2026-08-24** | Confirmed by the user: a real, already-running Hermes Agent instance on separate, unrelated infrastructure — not a candidate for adoption into this project. See §4.2. |
| Plane task board | **Matches existing design** | Already spec'd as human-facing projection, not authoritative — no gap. |
| LiteLLM model gateway | **Already planned, not yet built** | Exists as a config placeholder (`llm.gateway: litellm`) in the Project Profile schema; needs implementation, not a new decision. |
| LangGraph / MAF / CrewAI | **Wrong layer — do not adopt as orchestration** | Agent-reasoning/graph libraries, not execution-orchestration systems. Would duplicate macro-agent's job or repeat ADR-001's rejected pattern if used for governance. |
| Temporal / Hatchet | **Phase 3+ only, execution layer only — never governance** | Temporal already evaluated and rejected as governance core (ADR-001). Hatchet has the identical structural limitation, plus a weaker audit trail. |
| OpenHands / Goose / custom via MCP | **Compatible, viable Phase 3 harness candidates** | Both are legitimate general-purpose MCP clients; fit the existing Harness Provider Registry contract as *additional* harnesses. |

**Critical addendum (2026-08-24):** independent GitHub API verification found that
`macro-agent` and its entire declared dependency chain (`git-cascade`, `opentasks`,
`agent-inbox`) — plus both alternatives already logged in RISK-15
(`openswarm`, `openhive`) — are owned by a single individual (`alexngai`), several
with near-zero visibility (0-2 stars), one dependency (`agent-inbox`) with no
currently-public GitHub repository despite being referenced as an open MIT
component in `SPEC-05`. See §8 — this materially updates the risk picture already
flagged in RISK-02/08/15 and is unrelated to the original diagram but surfaced
during this research.

## 3. What was already decided before (don't relitigate)

This project has already run essentially this same evaluation twice. Any new proposal
that repeats one of these patterns should be checked against the existing rejection
first.

**Plane/Windmill precedent** (`docs/PROJECT_CONTEXT.md`): the original design was
`Plane → Windmill → Orca → agents`, with Plane and Windmill treated as authoritative
for workflow/policy. A product review concluded "Plane CE cannot be trusted to
enforce required workflow transitions/approval guards. Windmill CE should not be the
authoritative policy layer." This is the direct origin of the current design: a
custom Governance Controller as sole authority, Plane reduced to a projection.
`SPEC-10 §10.5` (Explicitly Postponed) still lists "Windmill as core controller" and
"macro-inc/macro as PM" as rejected approaches.

**Temporal precedent** (`decisions/ADR-001-governance-controller-implementation.md`):
a proposal to replace the Governance Controller with Temporal + OPA + a thin FastAPI
gateway was evaluated and rejected. The decisive reason: the state machine
(`PROPOSED → PLAN_APPROVED → EXEC_APPROVED → READY → RUNNING`) requires human
approval *before* execution starts, but Temporal's signal/update mechanism only
applies to an *already-running* workflow. The only workable pattern keeps approval
state in Postgres and starts Temporal only after `EXEC_APPROVED` — at which point
Temporal is an execution tool, not a governance authority, which defeats the premise
of using it as governance core. Additional reasons: Temporal's Event History is not
a tamper-evident, actor-attributed audit log; the "3x less code" claim moves
complexity into workflow/activity/versioning/worker-pool concerns; self-hosting
Temporal (frontend/history/matching/worker services, optional Cassandra) is a much
larger operational footprint than "one Python service + PostgreSQL." Temporal is
allowed back only in **Phase 3+, strictly inside the Execution Orchestration layer**
(managing an already-approved execution's lifecycle), never as a Governance
Controller replacement, and only if a proven need for long-running concurrent
execution workflows materializes.

## 4. Node-by-node detail

### 4.1 User / Telegram

Already implemented: `src/governance_controller/governance_controller/adapters/telegram.py`
parses `/approve <task_id> [approval_type]` commands, authenticates via
`X-Telegram-Bot-Api-Secret-Token`, derives an actor identity, and POSTs to the
Controller's own `/approvals` endpoint. Outbound notification and full Intake
normalization were Phase 2 candidate work — note that as of 2026-08-24 the repo's
own commit history shows a multi-channel Telegram+Email intake adapter has since
landed (`feat(intake): add multi-channel intake adapter for Telegram and Email`).
No gap versus the diagram.

### 4.2 Hermes / Meta-agent (planning + routing)

**Update 2026-08-24:** confirmed by the user — "Hermes" in the diagram refers to a
real, already-running Hermes Agent instance on the user's own separate
infrastructure (not the planned ai-software-factory deployment target). See
`docs/research-self-hosted-deployment-constraints-2026-08-24.md` for the full
context. This resolves the ambiguity noted below in favor of the
`NousResearch/hermes-agent`-shaped reading; the conclusion is unchanged — an
autonomous dispatcher-style meta-agent still cannot sit above, or substitute for,
the Governance Controller's approval authority. Since it lives on unrelated
infrastructure, it also does not need to be wired into this project's Meta
Orchestrator (Phase 2) or Controller for the diagram's architecture to be
satisfied; a future integration, if ever needed, would be as an external Intake
channel (analogous to the Telegram adapter) or an external consumer of the
Controller's own API — not as a routing layer the Controller reports into.

Original research findings, still valid as background: web research found exactly
one substantial real candidate that matches the diagram's shape (a meta-agent
sitting above a task board, routing to a model gateway and orchestration
frameworks): **`NousResearch/hermes-agent`**. Independently re-verified via GitHub
API on 2026-08-24: **235,523 stars, 47,487 forks, 35,211 open issues**, created
2025-07-22, pushed today. The fork/issue counts scale plausibly with the star
count (organic-looking engagement, not an obviously bought/inflated pattern), so
the number itself, while extraordinary, is not obviously fake — but it remains
worth independent scrutiny given how unusual that growth rate is for a project this
young. Its own architecture docs name a Model Gateway, a Task Board/Kanban, an
orchestration core, and a multi-platform messaging gateway — structurally close to
the diagram, but its dispatcher **autonomously claims and spawns tasks by default**,
with no pre-execution approval chain, policy engine, or audit log comparable to
`SPEC-03`. Adopting it as-is would repeat the exact governance gap that
Plane/Windmill and Temporal were already rejected over.

### 4.3 Plane task board

No gap. Matches `SPEC-04 §4.2` exactly: Plane is the human-facing Kanban/Epic/Task
UI, explicitly not a workflow-transition or approval-record authority.

### 4.4 LiteLLM model gateway

Already present, but only as a configuration value, not a built component:

```yaml
# specs/SPEC-03-governance.md, Project Profile schema
llm:
  gateway: litellm
  default_model: claude-sonnet-4
  allowed_models:
    - claude-sonnet-4
    - gpt-4.1
```

No LiteLLM service, deployment, or routing code exists in the repo. Including it in
the diagram confirms an already-assumed choice that still needs implementation, not
a new architectural decision.

### 4.5 LangGraph / MAF / CrewAI (workflow orchestration)

None of these three are adopted anywhere in the specs. LangGraph and CrewAI appear
exactly once, as adapter targets of a third-party tool
(`microsoft/agent-governance-toolkit`) noted in
`docs/research-agent-orchestration-and-governance-survey-2026-08.md` as an unverified
watch item. Microsoft Agent Framework ("MAF") does not appear anywhere else in the
repo.

All three occupy the same architectural layer: a library for structuring an
individual agent's internal reasoning/tool-calling as a graph or role-based crew.
None own git worktrees, task claiming, agent lifecycle, or merge/landing — the
things `AGENTS.md` explicitly forbids reimplementing and that `macro-agent` already
owns. Their checkpoint/interrupt mechanisms only work on an **already-started** run
— none offer a pre-execution human-approval-gate primitive, so using any of them as
a Governance Controller substitute would repeat ADR-001's core objection verbatim.

**Recommendation:** do not adopt any of the three as the workflow-orchestration layer
between Plane/Controller and macro-agent. The only legitimate use, if any, is fully
internal to a single harness's own implementation — invisible to, and requiring no
sign-off from, the Controller or macro-agent.

### 4.6 Temporal / Hatchet (durable execution)

Temporal's disposition is already fixed by ADR-001 (§3 above): rejected as
governance core, allowed only Phase 3+ inside execution orchestration.

Hatchet (`hatchet-dev/hatchet`, ~7,800 stars, MIT, Postgres-backed) was researched
fresh. Findings:

- **Same structural limitation as Temporal.** Its "Durable Event Waits" primitive
  requires a task to already be running before it can pause and wait for an event —
  no way to create a workflow in a not-yet-started, awaiting-approval state.
- **Weaker audit trail than Temporal, not stronger.** Its execution history is
  ordinary mutable Postgres state; its actual "Audit Logs" feature is narrower
  still (tenant/admin-plane security events only, **auto-deleted after 30 days**).
- **Lower TCO than Temporal, but not zero.** Needs Postgres + RabbitMQ (or Postgres
  as broker) + engine service + dashboard — real added surface, though lighter than
  Temporal's frontend/history/matching/worker cluster.
- **Small, early-stage vendor.** ~$500K seed (April 2024), ~4-person team, no
  Series A found.

**Recommendation:** treat Hatchet exactly as ADR-001 already treats Temporal — a
Phase 3+ execution-orchestration candidate only, never a governance-layer
replacement.

### 4.7 OpenHands / Goose / custom workers via MCP

Both are legitimate, actively maintained coding-agent projects with genuine
general-purpose MCP **client** support — configuration only, no code changes needed
to receive macro-agent's MCP toolset (`workspace.commit`, `workspace.land`,
`workspace.resolve`, `task.claim`, `agent-inbox.send_message`,
`agent-inbox.check_inbox`).

**OpenHands** (`OpenHands/OpenHands`, MIT, ~72,500 stars): event-stream
architecture, headless CLI mode, dedicated MCP integration layer (stdio/HTTP/SSE +
OAuth). Default runtime is Docker-sandboxed; an explicit Local Runtime exists with
no sandbox isolation at all, mapping onto this project's Phase 1 worktree-only
model. Downsides: Docker-first assumptions create CI friction; monorepo is being
actively split.

**Goose** (`block/goose`, transitioning to the Linux Foundation's AAIF, Apache 2.0,
Rust): MCP is its native extension mechanism. Lean CLI, explicit headless mode,
parameterized YAML "recipes." No built-in sandbox by default — isolation is left
entirely to the invoker, a clean fit for this project's own worktree→Docker→
Firecracker isolation model. Strong internal adoption at Block; governance moved to
the Linux Foundation (reduces single-vendor risk).

**Recommendation:** both are legitimate candidates for the already-planned Phase 3
harness-matrix expansion, fitting the existing Harness Provider Registry contract
without requiring any change to that design. Neither is a case for replacing
OpenCode as the default.

**Separate open item, resolved 2026-08-24 — see §8:** an earlier research pass
could not locate a public `github.com/alexngai/macro-agent` repository via web
search. Direct GitHub API verification now confirms it exists, but surfaces a much
larger risk than "couldn't find it." See §8 for the full finding.

## 5. Open questions for the user

1. ~~What specifically is "Hermes" in the diagram?~~ **Resolved 2026-08-24**: a
   real, already-running Hermes Agent instance on the user's own separate
   infrastructure, unrelated to ai-software-factory's deployment target. See §4.2.
2. ~~Worth an internal check: confirm `github.com/alexngai/macro-agent`'s current
   public/private status and version pin.~~ **Resolved 2026-08-24 via GitHub API —
   see §8. This surfaced a materially more serious finding than expected and is
   worth a real conversation with the team, not just a footnote.**

## 6. Sources consulted

Internal: `AGENTS.md`, `README.md`, `docs/PROJECT_CONTEXT.md`, `specs/SPEC-01`
through `SPEC-10`, `decisions/ADR-001-governance-controller-implementation.md`,
`requirements/REQUIREMENTS.md`, `docs/NEXT_STEPS.md`,
`docs/research-agent-orchestration-and-governance-survey-2026-08.md`,
`docs/research-alexngai-ecosystem-and-sudocode.md`,
`docs/research-longhorizon-harness.md`, `docs/temporal-opa-migration-risks.md`,
`docs/ai-software-factory-v2.mmd`.

External (GitHub API, npm registry, and project documentation; dated 2026-08-23/24):
- https://github.com/NousResearch/hermes-agent
- https://github.com/langchain-ai/langgraph, https://github.com/crewAIInc/crewAI,
  https://github.com/microsoft/agent-framework
- https://github.com/hatchet-dev/hatchet, https://docs.hatchet.run/*
- https://github.com/OpenHands/openhands, https://docs.openhands.dev/*
- https://github.com/block/goose, https://goose-docs.ai/*
- https://github.com/Untrivial-ai/agent-orchestrator
- https://github.com/google/ax
- https://github.com/alexngai/macro-agent, opentasks, git-cascade, and related repos
- https://registry.npmjs.org (agent-inbox, opentasks, git-cascade, acp-factory,
  agent-workspace, openteams, swarm-dispatch)
- https://github.com/bradAGI/awesome-cli-coding-agents (2026-08-24 snapshot)

## 7. Conclusion (original diagram)

None of the diagram's nodes require a change to the already-decided architecture.
Plane, LiteLLM, and Telegram already match the current design. LangGraph/MAF/CrewAI
and "Hermes" (in its most literal reading) would either duplicate or conflict with
components already owned by the Governance Controller and macro-agent. Temporal and
Hatchet remain execution-layer-only, Phase 3+ candidates per ADR-001's reasoning.
OpenHands and Goose are credible additional harnesses for the already-planned Phase
3 harness matrix, requiring no architectural change to adopt.

## 8. Addendum (2026-08-24): GitHub API verification — "rebuild from scratch" follow-up

A separate conversation asked: if this system were being designed from scratch
today, given a much larger catalog of OSS coding-agent/orchestration projects (the
`bradAGI/awesome-cli-coding-agents` list), would a different execution-orchestration
tool be chosen instead of `macro-agent`? Two candidates were identified from that
list as worth a real bake-off: `Untrivial-ai/agent-orchestrator` (already logged as
an evaluated-but-not-adopted alternative in `requirements/REQUIREMENTS.md` RISK-15)
and Google's `google/ax`. Both, plus `macro-agent` itself and its full dependency
chain, were independently verified via the GitHub API and the npm registry (not web
search) on 2026-08-24.

### 8.1 Untrivial-ai/agent-orchestrator — verified

```json
{
  "stargazers_count": 9918, "forks_count": 1411, "open_issues_count": 732,
  "created_at": "2026-02-13", "pushed_at": "2026-08-24 (today)",
  "license": "Apache-2.0", "archived": false
}
```

Five most recent commits are all from today (2026-08-24), spanning observability,
session-recovery, and desktop-UI fixes — genuine, active, multi-area development.
Fork/issue counts scale plausibly with star count. This matches the ~9.9k star
figure already on record in RISK-15, giving independent cross-source confirmation
that this specific number is stable and not a snapshot artifact.

### 8.2 google/ax — verified

```json
{
  "stargazers_count": 1967, "forks_count": 116, "open_issues_count": 35,
  "created_at": "2026-03-30", "pushed_at": "2026-08-20",
  "license": "Apache-2.0", "archived": false
}
```

Genuinely under the `google` GitHub organization. Smaller and slightly less active
than agent-orchestrator (last push 4 days before this check, vs. same-day for
agent-orchestrator) but real, with plausible fork/issue engagement for its size.

**Correction (2026-08-25):** the initial framing above ("Kubernetes-native,"
implying a mandatory K8s dependency) overstated the case. Direct inspection of the
actual README and `go.mod` (not just the one-line catalog description) shows AX has
three genuinely distinct deployment modes with very different overhead:

1. **Local mode** (`ax --input "..."`) — a single statically-compiled Go binary,
   no server, no external dependency. Event log defaults to a local SQLite file
   (`eventlog/log.sqlite`). Overhead is essentially that of invoking any CLI
   harness — comparable to or lighter than macro-agent's Node.js process.
2. **Server mode** (`ax serve`) — one long-running Go daemon (gRPC server) backed
   by SQLite by default; `go.mod` also vendors `jackc/pgx/v5`, so Postgres is an
   optional backend, never a hard requirement. No broker, no cache layer, no
   cluster — lighter than even a minimal Hatchet deployment (which needs at least
   Postgres + a broker).
3. **Production mode** (README: "recommended deployment option for production
   use") — requires [`agent-substrate/substrate`](https://github.com/agent-substrate/substrate)
   running on Kubernetes as the control service that suspends/resumes per-execution
   pods. This is the only mode that actually conflicts with `SPEC-10 §10.5`'s
   "Explicitly Postponed: Kubernetes" stance. Verified via GitHub API: real,
   active project (1,614 stars, 266 forks, 402 open issues, pushed today), but
   young (created 2026-05-13, ~3 months old at time of writing).

Net: a Phase 1/2-scale bake-off against macro-agent would **not** require adopting
Kubernetes at all — modes 1 and 2 are self-contained and arguably lighter than
macro-agent's current footprint. The Kubernetes tradeoff only applies if/when AX is
pushed into its recommended production topology, and even then it buys something
concrete in return (suspending idle agent workloads waiting on human approval,
rather than an idle Node/Python process holding memory the whole time).

**Separate, non-resource caveat:** the README carries an explicit maturity warning —
"🚧 AX is in active early development... will introduce major breaking changes
prior to a stable release... temporarily pausing the acceptance of external Pull
Requests." This is a different risk category than macro-agent's bus-factor-of-one:
not a single unmonitored maintainer, but an actively-iterating team explicitly
telling users the API isn't stable yet.

### 8.3 alexngai/macro-agent — verified, and this is the important finding

The repository is real (an earlier research pass's web search simply failed to
surface it). Direct API/registry inspection shows:

```json
{
  "stargazers_count": 1, "forks_count": 0, "open_issues_count": 2,
  "created_at": "2025-12-09", "pushed_at": "2026-08-01",
  "license": "MIT", "archived": false
}
```

Most recent commits on the default branch are from **2026-07-11** (tagged
`v0.2.6`) — over six weeks stale as of 2026-08-24, in sharp contrast to
agent-orchestrator's same-day commits. Contributors: `alexngai` (337 commits) and a
`claude` account (36 commits) — i.e., one person using an AI coding assistant, not a
team. The README's documented architecture (`bootV2`, `YamlDrivenTopology`,
`WorkspaceManager`, `LandingStrategy`, `ConflictRecoveryStrategy`, dependency on
`git-cascade`, delegation to `agent-inbox` and `opentasks`) matches `SPEC-05`'s
description of macro-agent's internals closely — so the earlier spec research was
accurate, and the tool does what SPEC-05 says it does. The concern is not that
macro-agent is fictional or non-functional; it is who stands behind it.

**Tracing the full dependency chain surfaced a materially larger risk than "pre-1.0,
single maintainer" (as currently captured in RISK-02/08/15):**

| Package | GitHub repo status | Stars | Last publish/push |
|---|---|---|---|
| `macro-agent` | public, `alexngai/macro-agent` | 1 | 2026-07-11 (commit) |
| `git-cascade` | public, `alexngai/git-cascade` | 0 | 2026-07-07 |
| `opentasks` | public, `alexngai/opentasks` | 2 | 2026-08-01 |
| `agent-inbox` | **not found** — npm package.json points to `github.com/alexngai/agent-inbox.git`, but no such repo exists in `alexngai`'s public repo list (checked via `gh api users/alexngai/repos`, full listing) | n/a | npm last published 2026-07-02 |
| `acp-factory` | public, but under a **different** org: `sudocode-ai/acp-factory` | 3 | 2026-06-02 |
| `openswarm`, `openhive` | public, both `alexngai/*` — these are the exact "evaluated alternatives" logged in RISK-15 | 0, 1 | 2026-08-11, 2026-08-04 |

Two concrete findings follow from this table:

1. **`agent-inbox` — one of the four components `SPEC-05 §5.2` names as an owned
   OSS sub-component with defined responsibility ("Messaging") — currently has no
   publicly visible source repository**, despite macro-agent's own README
   describing it as an open dependency and REQUIREMENTS.md/AGENTS.md's framing
   assuming macro-agent's internals are auditable, forkable OSS. It may be private,
   deleted, or renamed since its last npm publish (2026-07-02); either way, the
   code this project would depend on cannot currently be reviewed on GitHub.

2. **RISK-15's fallback options are not independent of the primary risk.**
   `requirements/REQUIREMENTS.md` lists `alexngai/openswarm` and `alexngai/openhive`
   as evaluated alternatives to fall back on if macro-agent (RISK-02/08) turns out
   to be a dead end. Both are also owned by `alexngai`, confirmed via
   `gh api users/alexngai/repos` alongside macro-agent, git-cascade, opentasks, and
   ~15 other tightly related micro-packages (`skill-tree`, `minimem`, `sessionlog`,
   `agentic-mesh`, `agent-workspace`, `agent-iam`, `swarm-codex`, `gitswarm`,
   `agent-crdt`, and more — all 0-2 stars, all touched within the last few months by
   the same single account). `alexngai` is also confirmed as the person behind
   `sudocode-ai` (already noted in `docs/research-alexngai-ecosystem-and-sudocode.md`)
   via the `acp-factory` package now living under that org. In other words: the
   entire execution-orchestration dependency graph this project has adopted or
   evaluated as a fallback — with the sole exception of `Untrivial-ai/agent-orchestrator`
   and `google/ax`, which were never chosen — traces back to one individual. A
   bus-factor-of-one for the primary dependency is already flagged in RISK-02; what
   was not previously visible is that the documented mitigation path (switch to
   openswarm/openhive) shares the identical bus factor.

**This does not mean macro-agent is unusable** — the code is real, documented, and
its architecture matches the spec closely, and a single skilled maintainer using AI
assistance can produce solid software. But it does mean RISK-02/08/15's mitigation
language ("evaluated alternatives exist") should be revisited: as currently
documented, there is no actual alternative maintainer or organization behind this
part of the stack, and one dependency in the chain is not independently auditable
right now. This is worth a direct conversation with the team about whether to (a)
proceed with eyes open and increase monitoring/vendoring of macro-agent's source as
a hedge, (b) run a real bake-off against `Untrivial-ai/agent-orchestrator` (real
independent maintainers, ~10x the visibility, same worktree-per-task/PR-routing
shape) before Phase 2/3 execution work goes further, or (c) accept `google/ax`'s
Kubernetes dependency as a worthwhile trade for genuine independent backing. This
memo does not make that call — it is a governance/ADR-level decision, not a
research conclusion.

### 8.4 Broader note on list reliability

Spot-checking a handful of other high-star entries from the same awesome-list
(`openclaw/openclaw` 387k★, `bytedance/deer-flow` 80.8k★, `headroomlabs-ai/headroom`
67.4k★, `anomalyco/opencode` 200.9k★) found plausible-looking fork/issue ratios and
active same-day commits across the board — the extraordinary star counts across
this list do not, on this sample, look like an obviously fabricated or bought-star
pattern; 2026's AI-coding-agent space genuinely appears to move at this velocity.
That said, the list itself contains internal quality issues (at least five
duplicate entries were observed: "San," "CliDeck," "pi-reflect," "Data Olympus,"
"grite"), so treat any single entry's claimed feature set or star count as a
starting point for verification, not a finding, before it informs a real decision
— exactly the standard applied to macro-agent above.

## 9. Addendum (2026-08-25): what agent-orchestrator (AO) could and couldn't close

Follow-up question: what parts of ai-software-factory could `Untrivial-ai/agent-orchestrator`
(AO) actually replace, why couldn't AO + the Governance Controller close the entire
project alone, and what does the combined resource footprint of Plane + Controller
+ AO look like? Answered by reading AO's actual README, `docs/STATUS.md`, and by
measuring the currently-running Plane deployment directly (not estimating).

### 9.1 What AO could plausibly close

AO's shipped feature set (`docs/STATUS.md`, verified 2026-08-25) maps closely onto
**macro-agent's execution-orchestration role** specifically:

- **Worker lifecycle** (spawn/kill/restore/rename/rollback/cleanup) over a Go
  daemon with a durable SQLite-backed session store — comparable to macro-agent's
  `AgentManager`/`YamlDrivenTopology`.
- **Per-task git isolation**: every Git-backed worker gets its own branch and
  worktree (non-git work gets an "AO-managed branchless directory") — comparable
  to `git-cascade`'s worktree/stream role, though without the "cascade rebase for
  stacked streams" concept SPEC-05 describes.
- **PR/CI/review feedback loop**: an SCM observer polls GitHub, feeds PR facts
  into a lifecycle reducer, and "sends agent nudges for CI failures, review
  feedback, and merge conflicts" — a real, shipped equivalent of the
  conflict-recovery/event-translation role that `SPEC-05`'s Event Bridge plus
  macro-agent's `ConflictRecoveryStrategy` cover today.
- **26 harness adapters** (Claude Code, Codex, Cursor, OpenCode, Aider, Copilot,
  Goose, and 19 more) via a registry-based adapter platform — a broader,
  already-built version of the Harness Provider Registry this project is building
  incrementally.
- **A built-in "project orchestrator" agent** that can decompose a plan into
  tasks and spawn/redirect workers — a partial, AI-driven analogue of the planned
  Meta Orchestrator's decomposition role (though not the deterministic
  BMAD/OpenSpec pipeline SPEC-01/02 describe).
- **A live Kanban** deriving card position from session + PR + CI + review state —
  a real-time *technical* execution view.

If the team ever runs the bake-off flagged in §8.3, this is the concrete shape it
would take: AO in place of macro-agent, with the Controller still owning
everything in §9.2.

### 9.2 Why AO + Controller alone could not close the whole project

`docs/STATUS.md`'s own framing is the clearest evidence: *"Current `main` ships a
working **single-user local loop**... Loopback-only HTTP daemon."* This is a
structural mismatch, not a missing-feature gap that a future release fixes:

1. **No pre-execution approval gate.** AO's workers start the moment a human (or
   AO's own orchestrator agent) creates them — there is no concept of a task
   sitting in a `PLAN_APPROVED`/`EXEC_APPROVED`-equivalent state awaiting a
   recorded human decision before code starts changing. "Approvals" in AO's shipped
   feature list refers to per-tool-call permission prompts inside a single agent
   session (e.g., "allow this shell command?"), not an organizational
   approve-before-work-starts gate. Grafting the Controller's state machine onto
   it would mean building the entire `POST /approvals` → policy-check → "only then
   tell AO to spawn a worker" integration from scratch — which is most of what the
   Controller already does today; AO doesn't reduce that work; it just relocates
   where execution happens.
2. **Single-user, loopback-only, desktop deployment model.** AO is explicitly "a
   local desktop workspace" (Electron app) with a daemon that binds to loopback
   only, built for one developer supervising their own machine. Ai-software-factory
   needs an always-on, unattended, server-side component that reacts to tasks
   arriving at any hour via Telegram/Email intake with no human present — a
   fundamentally different deployment shape than "a developer has AO open on their
   laptop."
3. **No durable, policy-aware task DAG.** AO's Kanban positions derive from live
   git/PR/CI facts per worker, not from a materialized, dependency-validated
   graph the way `opentasks` (fed by the Controller from an approved Plane
   subgraph) provides. Sequencing across many tasks lives in the ad-hoc planning
   conversation of AO's project-orchestrator agent, not in a deterministic,
   replayable structure.
4. **No human-facing PM/idea board.** AO's Kanban is a technical, developer-facing
   execution view. It has no equivalent of Plane's role as the cross-project,
   non-technical-stakeholder-visible backlog/epic system, and no Intake Adapter
   equivalent for Telegram/Email idea ingestion.
5. **No policy engine.** Nothing in AO's shipped feature set enforces
   `forbidden_paths`, per-project sandbox levels, allowed-harness lists, or a
   Completion Contract's deterministic acceptance checklist. It supervises agents
   operationally; it does not gate what they're allowed to do per-project policy.
6. **No tamper-evident audit log.** AO's SQLite store plus change-data-capture
   (`change_log`) is an operational event stream for driving its own UI, not an
   append-only, actor-attributed compliance record — the same category of gap
   ADR-001 already identified in Temporal's Event History and this memo's §8.3
   identified in Hatchet's audit logs.
7. **Outbound telemetry by default.** AO's README discloses it "record[s] the
   GitHub organization or account that owns a project" for product analytics.
   For a project whose Project Profile schema explicitly models per-project
   network egress policy (`SPEC-08 §8.5`), this is a concrete data-egress channel
   that would need review/disabling before adoption, not a blocker but a real
   checklist item.

In short: AO is a strong, already-built candidate for the **execution** half of
the stack, but the entire reason ai-software-factory has a Governance Controller
at all — durable pre-execution approval, policy enforcement, tamper-evident audit,
and a task graph independent of any one tool's UI — is precisely the half AO does
not attempt to solve, by its own stated design ("single-user local loop").
Replacing macro-agent with AO would not shrink the project down to "AO + Controller
already covers it"; it would still leave Plane (human PM layer), the Meta
Orchestrator (deterministic decomposition), the Intake Adapter, and the Policy
Engine's project-level rules to build exactly as planned.

### 9.3 Resource footprint: Plane + Controller + AO

Measured directly on the machine this session is running on, not estimated,
wherever a live instance was available:

| Component | Footprint | How measured |
|---|---|---|
| **Plane CE** (full stack: web, admin, space, api, worker, beat-worker, live, proxy, postgres, redis, rabbitmq, minio — 12 containers) | **~998 MiB RAM total, ~0% CPU idle** | `docker stats --no-stream` against the live `plane` docker-compose project already running on this host (see `docker ps`/`docker compose ls` output from earlier in this session) |
| **Governance Controller — Postgres** | **~69.5 MiB RAM** (measured while under incidental load from a concurrent session's test run, hence 45% CPU at that instant — otherwise idle load is lower) | `docker stats` against `gc-test-postgres`, the project's own `postgres:16-alpine` container, already running |
| **Governance Controller — API process** | **~50-100 MiB RAM (estimated)** | Not currently running as a process to measure directly; estimate based on a single-worker `uvicorn` + FastAPI + `asyncpg` process with the project's actual minimal dependency set (`pyproject.toml`: fastapi, pydantic, sqlmodel, asyncpg, httpx, structlog, typer, uvicorn — no ORM-heavy or ML dependencies) |
| **agent-orchestrator (AO)** | **Not measurable here** (Electron desktop app; this session has no GUI) — reasoned estimate only | Electron apps typically carry a baseline of roughly 150-300 MiB RAM for the shell process alone (bundled Chromium + Node runtime), *on top of* which AO explicitly gives **each active worker its own isolated embedded browser** for live preview — a Chromium `WebContentsView` per worker, not a shared one. With, say, 3-5 concurrently active workers with preview open, total AO memory could plausibly reach several hundred MiB to 1+ GiB, scaling roughly linearly with the number of simultaneously open workers, not with a fixed platform cost |

**The important structural difference, not just the numbers:** Plane and the
Controller are **shared, server-side costs** — one instance serves the whole team/
organization, paid once regardless of how many humans or tasks use it. AO's cost is
**per-developer-desktop** — it is a local app that would need to run on every
individual contributor's machine who wants to supervise agents through it, and its
footprint scales with how many workers *that one person* has open, not with team
size. That makes "Plane + Controller + AO" a mixed bag to size as a single number:
Plane + Controller together is a modest, fixed ~1.1-1.3 GiB shared-infrastructure
cost that fits comfortably even on the constrained Hermes VM profiled in
`docs/research-self-hosted-deployment-constraints-2026-08-24.md` (which had ~5.2
GiB free); AO's cost is a separate, variable, client-side cost that does not
consolidate onto shared infrastructure the way the rest of this stack does — a
relevant factor if the goal is a fully unattended, server-driven pipeline rather
than a human-supervised desktop workflow.
