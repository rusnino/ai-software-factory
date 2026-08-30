# Research: Alternative "Kickoff-Once-Then-Autonomous" OSS Stack

Date: 2026-08-30
Status: exploratory research memo — a hypothetical alternative architecture, not a
proposal to change ai-software-factory's current plan. No code/spec changes.

## 1. What this explores

A different product philosophy from the one ai-software-factory implements today.
The prompt was: suppose a person voices an idea, a meta-analyzer records it into
some memory (a future-project backlog), and once the human says "start," a single
orchestrator/main-agent runs a **fully autonomous** process — coding, tests,
cross-review, fixes after review — through to a landed result, with the human only
present at the start and at rare judgment calls.

This is a genuinely different governance model than ai-software-factory's, not a
missing feature of it:

| | ai-software-factory (current) | This alternative |
|---|---|---|
| Approval model | Durable, pre-execution human gate before every phase (`PROPOSED → PLAN_APPROVED → EXEC_APPROVED → READY`) | Human kicks off once; approval (if any) is just one workflow state the loop can land in, not a mandatory gate before each phase |
| Safety net | Governance Controller + Policy Engine + audit log, enforced externally | Executor–verifier loops, quality gates, and review councils, enforced internally by the pipeline itself |
| Risk appetite | Lower friction risk, higher governance overhead | Lower governance overhead, higher trust placed in automated verification |

Neither is objectively better — they're different trade-offs for a different set of
constraints. This memo maps real OSS projects onto the alternative model and
answers, separately, which of them are genuinely self-hostable without hidden
functional blockers.

## 2. The four stages, mapped to real projects

```
Voice/text idea → Beads (memory/graph of tasks, in place of opentasks)
                → Symphony (daemon: reads backlog, spawns an agent into an
                            isolated workspace per item)
                      → zeroshot (executor–verifier loop *inside* each run:
                                  cross-review + fix-after-review, escalating
                                  validator count with task complexity)
                      → h5i (inter-agent messaging + real sandbox isolation,
                              if a run needs more than one agent talking to
                              each other)
                → human: a judgment call surfaces only at a terminal workflow
                  state (e.g. "Human Review"), not before every phase
```

### 2.1 Stage A — Idea capture → meta-analyzer → durable memory

- **`gastownhall/beads`** (26.5k★, MIT, Go, Dolt-backed) — "distributed graph
  issue tracker and persistent memory for coding agents." A genuinely popular,
  independently-maintained open alternative to the role `opentasks` plays in
  ai-software-factory today (which, per `docs/research-proposed-orchestration-stack-2026-08-23.md`
  §8.3, is a single-maintainer, near-zero-visibility package). `bd create` →
  `bd ready` → `bd update --claim` → `bd close`, with `bd dolt push/pull`
  syncing across machines/agents.
- **`multica-ai/multica`** (47.6k★, Go/Next.js) — "Squads: put agents and
  people on one team; the leader routes the work," with intent/run/decisions/
  diff kept attached to the same issue — a combined meta-analyzer-plus-memory
  candidate, not just a tracker.

### 2.2 Stage B — Human says "go" → orchestrator takes over

- **`openai/symphony`** (26.8k★, Apache-2.0) — a formal `SPEC.md` (RFC-2119
  style) for a long-running daemon that polls an issue tracker on a fixed
  cadence, creates a deterministic per-issue workspace, and runs a coding-agent
  session inside it. Explicitly: **"This specification does not require a
  single approval, sandbox, or operator-confirmation policy... A successful run
  can end at a workflow-defined handoff state (for example `Human Review`), not
  necessarily `Done`."** This is the clearest real match for "human sets
  direction once, then the loop is autonomous, and review is just a workflow
  state, not a gate."

### 2.3 Stages C+D — Autonomous execution, cross-review, fix-after-review

- **`the-open-engine/zeroshot`** (1.7k★, MIT) — an **executor–verifier loop**:
  a conductor classifies task complexity (TRIVIAL → CRITICAL) and routes to a
  matching team shape, from a single worker with no validator (TRIVIAL) up to
  planner + worker + meta-coordinator + 4 validators in two stages (CRITICAL).
  Validators do not share the executor's session/context and must reproduce
  reported failures themselves — a real structural safeguard against an agent
  marking its own work "done." The loop repeats until verified or returns a
  concrete failure reason.
- **`asklokesh/loki-mode`** (1k★, BUSL-1.1, by Autonomi) — closest to a literal
  one-line match for the prompt: `loki quickstart "a todo app with user
  accounts" --yes` quotes cost/time, then builds fully autonomously with "8
  quality gates," refusing to call a run "done" on an empty diff or failing
  tests.
- **`h5i-dev/h5i`** (541★, Apache-2.0, Rust) — needed only if a run involves
  more than one agent that must talk to each other: a Git-backed "message
  forum" with each agent isolated in its own disposable sandbox (including real
  microVM isolation via `microsandbox`, not just a worktree).

## 3. Is there already a single product that does all of this?

No single product covers the full loop (voice idea → memory → autonomous
build → cross-review) end to end. The two closest single answers:

- **`loki-mode`** for the tightest end-user UX match (one-line idea → estimate
  → autonomous build with quality gates) — but it's BUSL-1.1, not OSI, a
  licensing category this project has consistently avoided.
- **`openai/symphony` + `zeroshot`** together for the more composable,
  Apache/MIT match: Symphony supplies the always-on "backlog → isolated run"
  daemon, zeroshot supplies the cross-review/fix-loop inside each run. Both are
  genuinely early (Symphony calls itself "a low-key engineering preview").

## 4. Self-hosting reality check

Marketing descriptions and one-line catalog summaries were not trusted here —
each project's actual `SELF_HOSTING.md`/`LICENSE`/architecture doc was read
directly (GitHub API, not web search) before drawing a conclusion.

| Project | Self-hosted? | License | Functional blocker? |
|---|---|---|---|
| **Beads** | Yes, fully (CLI + optional external `dolt sql-server`) | MIT | **None found.** No cloud dependency anywhere in the docs. |
| **Symphony** | Yes, by design — no cloud tier exists at all (explicit non-goal: "rich web UI or multi-tenant control plane") | Apache-2.0 | **None**, but it is literally "a spec — implement it yourself" or an experimental Elixir reference implementation. The limitation is maturity, not licensing or feature-gating. |
| **zeroshot** | Yes — a dedicated `zeroshot-rust` self-hosted target image exists | MIT | **No functional blocker.** Self-hosted "direct target" mode simply never reports one cosmetic status value (`queued`, which belongs to their hosted dashboard) — the executor–verifier loop itself runs identically. Real (not blocking) limitation: the self-host target explicitly states it "is not a tenant-isolation boundary and does not include a queue or scheduler" — multi-user scheduling would need to be supplied externally, which ai-software-factory's own Controller/opentasks-equivalent already would. |
| **h5i** | Yes, fully, including genuine microVM isolation (`microsandbox`) | Apache-2.0 | **None.** The only candidate in this set offering real hardware-level isolation out of the box rather than worktree-only. |
| **loki-mode** | Yes | **BUSL-1.1** (not OSI) | **No functional gap** — the README states directly: "Same Loki CLI, SDK, and MCP for everyone"; Autonomi Cloud/Enterprise are hosting convenience tiers of the identical tool, not a crippled free edition. The blocker here is purely the license category, not missing features. |
| **Multica** | Yes, full Docker Compose stack (Go backend + Next.js frontend + Postgres, official GHCR images) | Apache-2.0 + an anti-SaaS-resale clause | **None for internal use.** The license explicitly states: "Internal use within a single organization (including multiple workspaces) does not require a commercial license." The restriction only triggers if you resell Multica as a hosted service to third parties. |
| **AgentsMesh** *(evaluated alongside these, not part of the four-stage kit above)* | Technically yes | **BSL-1.1 with "Additional Use Grant: None"** | ⚠️ **Real blocker.** Unlike the other BSL projects checked, the use grant here is empty — production self-hosted use requires a paid commercial license until the license's change date (GPL-2.0-or-later afterward). Free only for non-production use. |
| **LoopTroop** *(evaluated alongside these, not part of the four-stage kit above)* | Yes, fully local | MIT | ⚠️ **Real blocker, but security not licensing.** Runs OpenCode in `dangerously-skip-permissions` (YOLO) mode by default. Its own README: "git worktrees isolate your code changes, but they do not sandbox the command execution process... the agent runs with your local user privileges" — and recommends running only inside a disposable VM. This directly conflicts with this project's own stated principle that a worktree is not a security boundary; LoopTroop is aware of the gap but ships live-fire by default anyway. |

### 4.1 Headline conclusions

1. Only two of the eight checked have a **real** blocker: `AgentsMesh`
   (licensing — production self-host is paid) and `LoopTroop` (security — unsafe
   by default without an external VM wrapper).
2. `loki-mode` is functionally complete self-hosted, but its license (BUSL-1.1)
   sits outside this project's consistent MIT/Apache preference — the same
   caveat already raised for `nodeterm` in
   `docs/research-proposed-orchestration-stack-2026-08-23.md` §9.2.
3. `Beads`, `Symphony`, `zeroshot`, `h5i`, and `Multica` are all genuinely
   feature-complete when self-hosted, with no hidden cloud-tier gating found in
   their actual documentation.
4. `h5i` stands out on security specifically: it is the only project in this
   whole survey (including everything evaluated in the companion
   orchestration-stack memo) offering real microVM-level isolation as a
   built-in option rather than relying on git worktrees alone.

## 5. Relationship to ai-software-factory's actual plan

This is not a recommendation to adopt any of these in place of the
Governance Controller / macro-agent / Plane design already decided (see
`docs/research-proposed-orchestration-stack-2026-08-23.md` §3 for why that
governance-first precedent exists — the same reasoning that rejected Plane/
Windmill-as-authority and Temporal-as-governance-core applies here too: none of
these six projects offer a pre-execution, durable, actor-attributed approval
gate, because that isn't the problem any of them are trying to solve). It is
recorded here purely as an answer to "what would a different product, built
for a different risk appetite, look like with today's OSS landscape" — useful
context if that trade-off is ever revisited, not a pending decision.

## 6. Sources

All metadata verified via GitHub API (`gh api repos/<owner>/<repo>`) on
2026-08-25/30, not web search. READMEs, `SPEC.md`, `SELF_HOSTING.md`, and
`LICENSE` files fetched and read directly from each repository's default
branch:

- https://github.com/gastownhall/beads
- https://github.com/openai/symphony (`SPEC.md`)
- https://github.com/the-open-engine/zeroshot (`docker/zeroshot-rust-target/README.md`)
- https://github.com/h5i-dev/h5i
- https://github.com/asklokesh/loki-mode
- https://github.com/multica-ai/multica (`SELF_HOSTING.md`, `LICENSE`)
- https://github.com/AgentsMesh/AgentsMesh (`LICENSE`)
- https://github.com/looptroop-ai/LoopTroop
- Companion memo: `docs/research-proposed-orchestration-stack-2026-08-23.md`
