# Research: google/ax — Fit Assessment Against AI Software Factory

Date: 2026-09-24
Status: research memo (fit assessment for a specific project, superseding an
earlier finding). No architectural decisions made; no code/spec changes.

## 1. Scope and why this supersedes earlier findings

`google/ax` was already researched twice in this thread:
`docs/research-proposed-orchestration-stack-2026-08-23.md` §8.2 (initial
verification, 2026-08-24) and its own follow-up correction (2026-08-25),
which concluded AX had three deployment modes and that a Phase 1/2-scale
bake-off against `macro-agent` would not require adopting Kubernetes at all.

**That conclusion is now stale.** A commit on 2026-09-20 — "Restructure AX
into a general-purpose orchestration layer for agentic tasks" — removed the
local-binary and standalone-server modes the earlier research relied on. This
memo re-reads the current README, `docs/concepts.md`, and `docs/manifests.md`
directly (not the August notes) and replaces the prior conclusion. The
2026-08-23 memo's §8.2 has been annotated with a pointer to this document
rather than rewritten in place, so the history of what changed and when stays
legible.

Metadata refreshed 2026-09-24: 8,865 stars (up from 1,967 a month earlier —
a real ~4.5x jump, plausible given Google's backing and a public restructuring
announcement, not flagged as suspicious), 415 forks, 39 open issues, Apache-2.0
(unchanged, plain, no additional conditions), pushed 2026-09-23. Contributors
include `rakyll` (472 commits — a well-known senior Google engineer, JBD) plus
several others under real handles — a genuine small team, not a single
maintainer.

## 2. What AX is now, concretely

> "Declare an agentic task with workspaces and gateway specifications. AX
> sandboxes it, wires up its workspace, fences its network, and helps running
> it at scale... If you have used Kubernetes, `ax` will feel similar."

AX is now an explicitly Kubernetes-native, declarative orchestrator, full
stop. The CLI is deliberately `kubectl`-shaped: `ax apply -f task.yaml`,
`ax get tasks`, `ax describe`, `ax watch`, `ax delete`, and it follows the
active `kubectx` context automatically (with `--context`/`-n`/`--server`
overrides). Deployment requires "a Kubernetes cluster, `ko`, a container
registry your cluster can pull from, and a reachable Agent Substrate Control
API" — there is no lighter-weight path documented anywhere in the current
README, unlike the August version.

Four declarative primitives, expressed as `ax.io/v1alpha1` YAML manifests:

| Primitive | What it does |
|---|---|
| `Task` | An isolated sandboxed agent workload with CPU/memory limits; supports `ax suspend`/`ax resume` (checkpoint and pick up exactly where it left off) and `ax ssh` (shell into the running sandbox, requires `spec.debug: true`). |
| `Workspace` | Pre-wires Git repositories (cloned into subdirectories of the workspace path), MCP servers, and skill packages so every agent starts "warm." |
| `Gateway` | Locks outbound network traffic to an explicit host allowlist. |
| `Model` | Configures which LLM the platform uses, with credentials sourced from a Kubernetes Secret. |

Runners are pluggable: "build your own runner image to replace the default"
(`docs/runner.md`) — a documented contract between the control plane and the
task container, conceptually similar to swapping a harness, but at the
container-image level rather than a CLI-process level.

## 3. Direct answer: still not a governance layer; now a thinner match for macro-agent's actual scope than the August finding suggested

### 3.1 No governance/approval concept — unchanged from August

`docs/concepts.md` was searched directly for "approval," "human," and
"governance": zero matches. AX remains purely an execution/scheduling
substrate, with no notion of a pre-execution gate, policy engine, or audit
trail beyond whatever Kubernetes/OTel-level observability the cluster
provides. This part of the prior assessment still holds.

### 3.2 Git handling is materially thinner than macro-agent's actual scope — a new, more critical finding

`docs/manifests.md`, read directly: Git repositories are "cloned into
subdirectories of the workspace path." That is the entire git story. There is
no worktree-per-task isolation, no landing strategy (merge-to-parent,
queue-to-branch, or otherwise), and no conflict-recovery mechanism of any
kind — none of the concepts `SPEC-05-macro-agent-integration.md` requires
`macro-agent` to own are present in AX at all. Committing, pushing, and
opening a PR would be entirely up to whatever the agent does inside the
sandbox on its own, using whatever credentials the `Workspace`/`Gateway`
config exposes to it — not a platform-level guarantee.

This is a materially weaker match than even the lighter session-manager tools
already surveyed (`stablyai/orca`, `superset-sh/superset`), which at least
implement one worktree per agent and a compare/merge-the-winner UI flow. AX
does not attempt that layer at all — it stops at "the sandbox exists, the repo
is cloned, go do work."

### 3.3 Kubernetes is now a hard, unconditional requirement

The August correction's headline claim — that local and standalone-server
modes made a Kubernetes-free bake-off possible — described modes that no
longer exist in the current codebase. Every path documented today assumes a
live Kubernetes cluster and a running Agent Substrate control plane. This
directly and now unconditionally conflicts with `SPEC-10 §10.5`'s
"Explicitly Postponed: Kubernetes" stance — there is no lighter alternative to
fall back to if that constraint is not lifted.

## 4. Where AX's model is still conceptually interesting, independent of adoption

Even though AX is not a viable macro-agent substitute today, its declarative
primitives map unusually cleanly onto concepts this project already has on
paper but hasn't built:

- `Gateway`'s explicit outbound-host allowlist is close to a literal
  implementation of the network-egress policy field already described in
  `specs/SPEC-08-security.md` §8.5's Project Profile.
- `Model`'s "which LLM the platform uses, credentials from a Kubernetes
  Secret" is the same shape as the still-unbuilt `llm.gateway: litellm`
  placeholder in `specs/SPEC-03-governance.md` (see
  `docs/research-nasiko-fit-assessment-2026-09-24.md` for a more directly
  usable candidate for that specific placeholder, since Nasiko's LLM Router
  ships without requiring Kubernetes and integrates with this project's
  actual harnesses today).
- `Task`'s suspend/resume is a genuine, real answer to the same cost problem
  ADR-001 and the earlier orchestration-stack memo raised about idle
  processes holding memory while waiting on human approval — worth
  remembering as a pattern if/when this project's own execution layer needs
  it, independent of whether AX itself is ever adopted.

None of this changes the bottom line: these are ideas worth borrowing, not
components this project is in a position to depend on, given the unconditional
Kubernetes requirement and the missing landing/merge/conflict-recovery layer.

## 5. Bottom line

Do not adopt `google/ax` as a macro-agent replacement, and treat the earlier
(2026-08-25) framing that a Kubernetes-free evaluation was possible as
withdrawn — the project restructured on 2026-09-20 and no longer offers that
path. Even setting the Kubernetes question aside entirely, AX's git handling
(a plain clone into a workspace directory, no worktree/landing/conflict-
recovery concept) is a thinner match for what `SPEC-05` requires of
`macro-agent` than most other candidates already surveyed in this thread. Its
`Gateway` and `Model` primitives remain worth studying as clean reference
designs for this project's still-unbuilt network-egress-policy and
`llm.gateway` placeholders, independent of any decision about AX itself.

## 6. Sources

- https://github.com/google/ax (`README.md`, `docs/concepts.md`,
  `docs/manifests.md`, `LICENSE`)
- https://github.com/agent-substrate/substrate (the Kubernetes control
  service AX now unconditionally depends on)
- Prior memo this supersedes:
  `docs/research-proposed-orchestration-stack-2026-08-23.md` §8.2
- Companion memos: `docs/research-macro-agent-alternatives-survey-2026-09-11.md`
  (the functional-requirements checklist this assessment was checked
  against), `docs/research-nasiko-fit-assessment-2026-09-24.md` (the more
  directly usable candidate for the `llm.gateway` placeholder)
