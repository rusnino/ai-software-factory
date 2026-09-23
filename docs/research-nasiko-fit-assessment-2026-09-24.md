# Research: Nasiko — Fit Assessment Against AI Software Factory

Date: 2026-09-24
Status: research memo (fit assessment for a specific project). No architectural
decisions made; no code/spec changes.

## 1. Scope

The user asked whether `Nasiko-Labs/nasiko` (https://github.com/Nasiko-Labs/nasiko)
could replace macro-agent, or otherwise integrate well into ai-software-factory.
Method as with every prior comparison in this thread: read the project's own
README, source (not just descriptions), license, and commit/contributor history
directly via the GitHub API, not summaries.

`Nasiko-Labs/nasiko` — "Developer Control Plane for your AI Agents," Rust,
Apache-2.0 (plain, verified by reading the full `LICENSE` file — GitHub's API
mis-flagged it as `NOASSERTION`), 8,655 stars, created 2026-02-12, last pushed
2026-09-14 (10 days before this writing), 9-10 distinct contributors under
company-branded handles (`nasiko-neeraj`, `nasiko-chamansinghal`, etc.) — a real
small team, not a single maintainer.

## 2. What Nasiko actually is

A single control-plane process that deploys, routes, secures, and observes any
**A2A-speaking agent** (the Agent2Agent protocol — a real, standards-track
protocol maintained by the Linux Foundation, spec v1.0 hardcoded and enforced
via an `A2A-Version: 1.0` header; older/non-compliant agents are rejected with
`-32009 VersionNotSupported`). An "agent" in Nasiko's model is a long-lived OCI
container with an `AgentCard.json` manifest, deployed to a self-hosted registry
and cluster — a microservice-deployment model, not an ephemeral per-task coding
session.

Core components (from `README.md`'s architecture diagram and
`docs/AGENT_LIFECYCLE.md`):

- **Routing engine** — 3-stage pipeline (embedding-similarity shortlist →
  context rerank → LLM final pick) so callers don't need to know the fleet.
- **Single ingress, always proxied** — agents are never publicly reachable;
  every agent-to-agent call is proxied through the server, which is the one
  chokepoint for rate limits, ACLs, and tracing.
- **MCP Gateway** — one permanent URL gives every agent a merged,
  permission-filtered view of Composio toolkits and custom MCP servers,
  without the agent holding the credentials.
- **LLM Router** — agents receive an `OPENAI_BASE_URL` and a short-lived
  identity token instead of a real API key; the router resolves
  provider/model/key server-side. No agent or log ever sees a real key.
- **Flow guards** — Redis-backed cascade limits (depth, fan-out, token budget,
  timeout, cycle detection) to stop runaway agent loops.
- **Encrypted secrets** — per-agent secrets AES-256-GCM encrypted at rest,
  injected only at deploy time.
- **Access control** — user-to-agent ownership/grants and an agent-to-agent
  allowlist gate every proxy call.
- **Embedded OCI registry** — self-hosted, S3-backed (RustFS), so
  `nasiko push`/`nasiko deploy` need nothing external.
- **Full observability** — every dispatch/proxy hop emits a real OTel span;
  token usage and cost auto-collected from `gen_ai.*` attributes.

Fully self-hostable: `docker compose up -d` brings up Postgres, Redis, RustFS
(S3), an OTel Collector, Tempo, Loki, and the server itself. No Rust toolchain
needed for the Docker-only path.

## 3. Direct answer: not a macro-agent replacement

Nasiko has none of the functional requirements this project's research thread
has consistently checked candidates against (see
`docs/research-macro-agent-alternatives-survey-2026-09-11.md` §2):

- No git worktree or per-task workspace isolation concept at all.
- No task-claiming DAG (`opentasks` equivalent).
- No landing/merge strategy.
- No conflict-recovery mechanism.

This is not a gap or an immaturity — Nasiko's unit of work is a persistent
agent *service*, not a one-shot coding-task session against a specific repo.
It solves a genuinely different problem (agent-to-agent traffic security,
credential isolation, and observability for a fleet of long-lived agent
microservices) than macro-agent solves (spin up a coding harness in an
isolated workspace for one approved task, land the result, recover from
conflicts). Recommending Nasiko as a macro-agent substitute would be a category
error.

## 4. Where Nasiko is a genuinely strong fit — three separate cubes

### 4.1 LLM Router → a stronger implementation of the `llm.gateway: litellm` placeholder

`specs/SPEC-03-governance.md`'s Project Profile schema has carried
`llm.gateway: litellm` as an unbuilt configuration placeholder since the very
first research pass in this thread
(`docs/research-proposed-orchestration-stack-2026-08-23.md` §4.4) — no LiteLLM
service, deployment, or routing code exists in this repository today. Nasiko's
LLM Router is a real, shipped implementation of exactly this role, with a
security property plain LiteLLM does not provide by default: agents never hold
a real provider API key at all, only a short-lived, per-request JWT the router
exchanges server-side. `AGENT_JWT_SECRET` unset fails closed (every router
request rejected with 401) rather than failing open.

Critically, Nasiko ships **first-class integration with the exact harnesses
this project already targets**: `nasiko connect claude|codex|opencode --config
<llm-config-name>` rewrites the harness's own `apiKeyHelper`/`*_BASE_URL`
settings to route through Nasiko, with the inbound wire protocol and outbound
provider fully decoupled (e.g., Claude Code's Anthropic-format traffic can be
routed to an OpenAI-compatible backend transparently). This is a closer match
to this project's actual harness set (OpenCode, Claude Code, Codex) than any
other model-gateway candidate surveyed so far.

### 4.2 Session reporting → a ready-made audit-trail input for coding-agent harnesses

`nasiko agents install claude|codex|opencode` installs a Stop/session-idle hook
per harness that queues completed-turn events locally
(`~/.nasiko/integrations/queue/`) and delivers them in batches to
`POST /telemetry/coding-agent/events/batch` — offline-safe (a briefly
unreachable control plane costs nothing) with a separate
`~/.nasiko/integrations/rejected/` path for permanently-failed deliveries.
This is a real, already-built data source for exactly the kind of
"what did the harness actually do" signal this project's own `AuditLog` and
verification pipeline would want to consume, without building the
hook-installation and offline-queue machinery from scratch.

### 4.3 The `hitl` crate → a well-engineered reference pattern (not a governance-gate replacement)

Source read directly (`hitl/src/types.rs`), not just the README. This is a
genuinely mature implementation of durable human-in-the-loop pause/resume:

- `HitlKind`: `InputRequired`, `AuthRequired`, `ToolApproval`.
- `HitlStatus`: `Pending`, `Resolved`, `Rejected`, `Expired`, `Canceled`.
- A separate `ResumeStatus` (`NotStarted`, `Completed`, `Failed`,
  `DeliveryOutcomeUnknown`) tracks *delivery of the human's decision back to
  the paused execution* as its own state machine, with lease-based recovery
  explicitly modeled on the same pattern as `build_jobs.picked_at` (per an
  in-code comment) — i.e., a real answer to "what happens if the resume
  attempt dies mid-flight," not a naive fire-and-forget callback.
- The row type is deliberately not `Serialize` so `resume_state` can never leak
  through a stray `Json(row)` response — a small but telling sign of security
  discipline.

**Important distinction**: this is a **mid-execution, tool-call-level**
approval primitive (closer to Claude Code's own permission prompts, or to
Paperclip's/Multica's per-tool-call gates — see
`docs/research-paperclip-vs-ai-software-factory-comparison-2026-09-07.md` §4)
— it pauses an *already-running* agent at a specific decision point. It is
**not** a substitute for this project's `PLAN_APPROVED`/`EXEC_APPROVED`
pre-execution gate, which blocks a task from starting at all. The two are
complementary, not overlapping: if this project's harnesses ever need a
"pause mid-task and durably wait for a human" capability distinct from the
existing pre-execution approval chain, the `hitl` crate's state model (event
kind, resolution status, and a separately-tracked delivery/resume status with
lease recovery) is a solid pattern to study or adapt, not a component to
depend on directly (it is not exposed as a standalone library outside the
Nasiko monorepo).

### 4.4 Secondary hardening patterns worth noting

- **Flow guards** (`NASIKO_FLOW_MAX_DEPTH`/`MAX_FAN_OUT`/`MAX_TOKENS`,
  Redis-backed, with cycle detection) — a runaway-loop safety net that
  complements rather than replaces the Policy Engine's per-task rules.
- **Secrets engine** (AES-256-GCM at rest, injected only at deploy time, never
  passed through to logs) — a more complete secrets-handling story than this
  project's current "do not put secrets in prompts/YAML" rule alone provides;
  worth referencing if/when a dedicated secrets-management piece is built.

## 5. License and maturity

- **License**: plain Apache-2.0, verified by reading the full `LICENSE` file —
  no additional conditions, no anti-SaaS-resale clause, no BUSL-style
  restriction. Cleanest license category already established as preferable in
  this research thread (matching Paperclip's plain MIT, better than Multica's
  Apache-plus-clause or Superset's/nodeterm's/loki-mode's source-available
  licenses).
- **Team**: at least 9-10 distinct, sustained contributors under
  company-branded GitHub handles — a real small team, not a single-maintainer
  risk like `alexngai`'s macro-agent ecosystem
  (`docs/research-proposed-orchestration-stack-2026-08-23.md` §8.3).
- **Activity**: commits as recent as 10 days before this writing, real
  feature work (`feat(llm-router): add OpenRouter provider support`,
  `feat(coding-agent): autoscan`), PR-based workflow. 8,655 stars over roughly
  7 months of existence — a comparatively modest, believable growth rate next
  to some of the more extreme star counts encountered elsewhere in this
  research thread (e.g., `openclaw/openclaw` at 387k, `NousResearch/hermes-agent`
  at 235k).

## 6. Bottom line

Do not evaluate Nasiko as a macro-agent alternative — it solves a different
problem entirely (long-lived agent-service deployment and traffic security,
not per-task coding-session orchestration). It is, however, the strongest
candidate surveyed so far in this research thread for actually building the
long-standing `llm.gateway: litellm` placeholder, with the added benefit of
first-class integration with this project's exact harness set (Claude Code,
Codex, OpenCode) and a ready-made coding-agent session-reporting pipeline that
could feed the audit trail. Its `hitl` crate is worth studying as a reference
pattern for mid-execution human-pause semantics, distinct from and
complementary to this project's existing pre-execution approval chain — not
something to adopt as a dependency, but a design worth learning from.

## 7. Sources

- https://github.com/Nasiko-Labs/nasiko (`README.md`, `LICENSE`,
  `docs/AGENT_LIFECYCLE.md`, `hitl/src/types.rs`)
- Companion memos:
  `docs/research-proposed-orchestration-stack-2026-08-23.md` (the
  `llm.gateway: litellm` placeholder and macro-agent vendor-concentration
  findings this assessment follows up on),
  `docs/research-macro-agent-alternatives-survey-2026-09-11.md` (the
  functional-requirements checklist this assessment was checked against),
  `docs/research-paperclip-vs-ai-software-factory-comparison-2026-09-07.md`
  (for the mid-execution vs. pre-execution approval distinction)
