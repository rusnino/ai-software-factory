# Research Note: 5 OSS projects surveyed for architectural fit

Date: 2026-08-23
Status: Evaluated, not adopted. Recorded per the ADR-001/RISK-13 precedent (document, defer, don't forget).

## 0. Org-identity correction (read this first)

The URL given for the second project, `https://github.com/ComposioHQ/agent-orchestrator`, does not
resolve to a Composio-owned repository — GitHub search confirms Composio has no such repo under its
org, and the URL redirects to `Untrivial-ai/agent-orchestrator`, an unrelated org created July 2026.
Everything below about "agent-orchestrator" refers to `Untrivial-ai/agent-orchestrator`. The
"Composio" attribution in the original URL appears to be simply wrong (a stale link, or a
rename/transfer that left no trace) and should not be read as any kind of maintainer or backing
signal — Composio is not connected to this project as far as this research could confirm.

## 1. Why these five, together

The user asked for an architectural-fit read on five otherwise-unrelated repos. They split into two
clusters relative to this project's own layering (Governance Controller = authoritative approvals/
policy/audit; macro-agent = execution orchestration, an external pre-1.0 dependency per RISK-02/08/15):

- **Coding-agent orchestration/execution engines** — competes with or is adjacent to the macro-agent
  layer: `fabro-sh/fabro`, `Untrivial-ai/agent-orchestrator`, `GantisStorm/autonomous-coding-harness`.
- **Agent governance/policy tooling** — adjacent to or potentially complementary with the Governance
  Controller: `humanlayer/agentcontrolplane`, `microsoft/agent-governance-toolkit`.

None of the five is a drop-in fit for either layer. Two are worth a formal note for different reasons
(one as a genuine macro-agent-alternative candidate, one as a legitimacy-flagged watch item); the
other three are recorded here for completeness and not carried forward anywhere else.

## 2. What each one actually is

### fabro-sh/fabro

Rust, MIT, ~1.5k stars, pre-1.0 (nightly-only releases). A full self-contained agent execution engine:
DOT-graph-defined workflows, its own model-routing "stylesheets" for picking which model/provider runs
which node, Daytona-hosted cloud sandboxes for isolation, and built-in git checkpointing per step. Its
"gates" are workflow-level checks (lint, test, LLM-as-judge) gating progression from one graph node to
the next — a CI-style quality gate, not an authoritative human-approval/policy/audit system in the
SPEC-03 sense.

**Fit**: not a fit. Adopting Fabro as an execution engine means reimplementing agent lifecycle,
worktree/sandbox management, and merge/checkpoint logic inside Fabro's own graph runtime — exactly what
AGENTS.md's "do not reimplement macro-agent internals" already forbids doing ourselves, and adopting a
second engine that does the same job as macro-agent doesn't avoid that problem, it just moves it.
Recorded here for completeness; not tied to any existing RISK row.

### Untrivial-ai/agent-orchestrator (linked as ComposioHQ — see §0 correction)

Go + Electron, Apache-2.0, ~9.9k stars, 84 contributors, very active (commits at roughly hourly
cadence). A fleet manager for running many coding-agent CLI sessions concurrently: one git worktree per
task, pluggable adapters for agent (which CLI/harness runs), runtime (where it executes), and SCM (which
git host/PR flow it targets), an orchestrator process that delegates work out to worker sessions, and a
Kanban-style UI for watching/steering the fleet.

It has no governance, policy, or audit layer of its own — it decides *how and where* work runs, not
*whether* it's allowed to. That maps cleanly onto exactly the slice of responsibility SPEC-05 assigns to
macro-agent (execution orchestration only), which is what makes it worth taking seriously as a concrete
answer to "what if macro-agent's pre-1.0 status becomes a real blocker" (RISK-02/08, already tracked in
RISK-15 alongside alexngai/openswarm, alexngai/openhive, and sudocode-ai/sudocode).

**Fit**: worth a real entry as a macro-agent-alternative candidate. Two caveats before it could go
further than "candidate": (1) it can't replace the Governance Controller — no policy/approval/audit
concept exists in it at all, so the Controller-is-authoritative boundary would still need to sit above
it exactly as it does above macro-agent today; (2) its Kanban UI would compete with Plane's role as the
human-facing projection (ADR-001/SPEC-04) if adopted wholesale rather than studied as a reference for
its worktree/adapter pattern specifically. Its programmatic/API surface (can an external controller
drive it non-interactively, the same open question already flagged for openswarm in the prior research
note) is unconfirmed and would need direct verification before any real evaluation moves forward.

### humanlayer/agentcontrolplane

Go, packaged as a Kubernetes operator, Apache-2.0, 461 stars, but **stale — last push roughly 13
months ago** despite 4 still-open PRs, which reads as a maintenance slowdown or effective abandonment
rather than active development. Requires adopting Kubernetes as the agent runtime itself (agents run as
CRDs/pods under the operator), plus HumanLayer's own external hosted SaaS for routing human-in-the-loop
approvals (Slack/email/etc.) back to a paused agent run. Solves generic agent-runtime durability and
checkpoint/resume plus tool-call-level human gates — a different problem from a development-lifecycle
state machine, a policy engine over task contracts, or an audit log of governance decisions.

**Fit**: not a fit. Wrong deployment model (Kubernetes-as-runtime is a much larger commitment than this
project makes anywhere in its current specs), adjacent rather than overlapping problem, and apparent
abandonment on top of both. Recorded for completeness; no RISK row.

### microsoft/agent-governance-toolkit

Multi-language, MIT. Tool-call-level policy middleware: a `govern()` decorator wraps individual tool
calls, evaluated by a PDP (policy decision point) that returns allow/warn/deny/escalate verdicts, with
supporting identity, sandboxing, and Merkle-tree-backed audit layers, and adapters for LangGraph, CrewAI,
AutoGen, Claude Code, and others.

If legitimate, this is genuinely **complementary rather than competing** with the Governance
Controller — it answers "can this agent call this specific API/tool right now," a different and
lower-level question than "should this plan/execution/merge be approved," and could in principle sit
underneath as a tool-call firewall for what a coding agent does *inside* its own already-approved
session, without touching the Controller's approval-chain authority at all.

**However**: the repository was created roughly March 2026 (about 5-6 months old at the time of this
survey) yet already shows 6,091 stars and version labeling ("Public Preview") that reads as more
mature than a repo that age would typically support — a mismatch worth independently verifying before
trusting any of its other maturity signals (star count, contributor count, adapter breadth) at face
value.

**Fit**: flag as a watch item only — do not adopt or formally risk-track yet. The legitimacy concern is
the headline finding here, not a footnote; revisit if/when it's independently corroborated by something
beyond GitHub's own counters (real-world adopters, independent press or blog coverage, a second
data point).

### GantisStorm/autonomous-coding-harness

Python, built on the Claude Agent SDK, single-branch-only by explicit design (no worktrees, no
merge-conflict recovery of any kind), unlicensed, 7 stars, 12 commits, single author.

**Fit**: not a fit. Too immature (single author, low commit count, no license at all — a blocker on its
own for any kind of adoption), and architecturally misaligned regardless of maturity: hardwired to one
SDK/provider and with no worktree-isolation story, where this project's whole execution model (SPEC-05)
assumes per-task worktree isolation as a baseline. Recorded for completeness; no RISK row.

## 3. Recommendation

Do not adopt any of the five for Phase 1 or Phase 2.

- `Untrivial-ai/agent-orchestrator` is worth remembering specifically as a second concrete answer to
  "what if macro-agent doesn't pan out" (RISK-02/08), alongside alexngai/openswarm — revisit if that
  risk ever materializes into an actual blocker, and verify its programmatic/API surface first, exactly
  as already flagged for openswarm in the prior research note.
- `microsoft/agent-governance-toolkit` is a watch item, not a candidate — its tool-call-level governance
  model is architecturally interesting and genuinely complementary in theory, but its maturity signals
  don't hold up to a first pass of scrutiny and it should not be treated as a serious option until that's
  resolved independently.
- `fabro-sh/fabro`, `humanlayer/agentcontrolplane`, and `GantisStorm/autonomous-coding-harness` are
  rejected outright (wrong layer, wrong deployment model/abandoned, or too immature respectively) and
  need no further tracking.

## Sources

- https://github.com/fabro-sh/fabro (README)
- https://github.com/ComposioHQ/agent-orchestrator → redirects to https://github.com/Untrivial-ai/agent-orchestrator (README, org/repo metadata)
- https://github.com/humanlayer/agentcontrolplane (README, repo activity/PR state)
- https://github.com/microsoft/agent-governance-toolkit (README, repo creation date vs. star/version signals)
- https://github.com/GantisStorm/autonomous-coding-harness (README, repo metadata)
