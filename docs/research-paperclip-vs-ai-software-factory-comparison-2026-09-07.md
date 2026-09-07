# Research: Paperclip vs. AI Software Factory — Where the Real Differences Are

Date: 2026-09-07
Status: research memo (comparative analysis). No architectural decisions made; no
code/spec changes.

## 1. Scope

Companion to `docs/research-multica-vs-ai-software-factory-comparison-2026-09-07.md`,
using the same method: read the project's own documentation and source tree
directly (GitHub API, not marketing copy or web search), then compare against
ai-software-factory's actual current architecture and status.

`paperclipai/paperclip` — "The open-source app everyone uses to manage agents at
work" — 80,158 stars, 14,713 forks, MIT license, TypeScript, created 2026-03-02,
pushed today. Larger by star count than Multica and cleaner-licensed (plain MIT,
no additional-conditions clause). Sources read in full: `README.md`,
`SECURITY.md`, `docs/start/architecture.md`, `docs/start/core-concepts.md`,
`docs/guides/execution-policy.md`, `docs/guides/board-operator/approvals.md`,
`docs/guides/agent-developer/handling-approvals.md`,
`docs/guides/board-operator/execution-workspaces-and-runtime-services.md`,
`docs/guides/board-operator/activity-log.md`, `docs/adapters/claude-local.md`,
`docs/adapters/codex-local.md`, and
`packages/plugins/sandbox-providers/SANDBOX-REQUIREMENTS.md`, plus a repository
tree scan for every sandbox/execution-target-related file.

## 2. What Paperclip is, concretely

An org-chart simulation for AI agents, not a Kanban-for-agents like Multica:

- **Company / org structure.** A company has a goal, a strict management tree
  (every agent reports to exactly one manager except the CEO), and a monthly
  budget. One Paperclip instance can run multiple companies.
- **Delegation.** The CEO agent turns company goals into a strategy (submitted
  for human approval), breaks approved goals into tasks, assigns them by role/
  capability, and can request to hire subordinate agents (gated by an optional
  hire-approval setting).
- **Heartbeats.** Agents are not continuously running processes; they wake in
  short execution windows triggered by schedule, assignment, @mention, manual
  invoke, or approval resolution.
- **Issues.** The unit of work, with a status lifecycle
  (`backlog → todo → in_progress → in_review → done`, with `blocked` and
  `cancelled`), single-assignee atomic checkout (`409 Conflict` on a race).
- **Execution Policy** (§4 below) — a genuinely structural review/approval
  system, the most sophisticated of anything found across this project's whole
  comparative survey so far (Multica, AO, nodeterm, Symphony, zeroshot, etc.).
- Adapters (`claude_local`, `codex_local`, `process`, `http`) bridge Paperclip to
  actual agent CLIs, plus a real plugin ecosystem of remote sandbox providers
  (§5).
- Self-hostable: Node.js/Express backend, PostgreSQL 17 (or embedded PGlite for
  zero-config local mode), React/Vite frontend, Drizzle ORM, Docker deployment
  documented (`docs/deploy/docker.md`, `docs/deploy/aws-ecs.md`). License: plain
  MIT, no additional conditions — cleaner than Multica's Apache-plus-anti-resale
  clause.

## 3. Governance, as Paperclip's own docs define it

`docs/start/core-concepts.md` states this narrowly: "Some actions require board
(human) approval: hiring agents, CEO strategy, board overrides (pause/resume/
terminate any agent, reassign any task)." This is a coarse, org-simulation
governance model — approval gates specific *organizational* decisions, not
individual code changes. It does not gate whether a given coding task is allowed
to start.

## 4. Execution Policy — the most sophisticated review/approval system surveyed so far

`docs/guides/execution-policy.md` documents a real, runtime-enforced,
per-issue review/approval workflow (`executionPolicy` field), with genuine
engineering depth:

- **Runtime-enforced, not prompt-dependent.** When an executor transitions an
  issue to `done`, the runtime *intercepts* the transition: status becomes
  `in_review`, the issue is reassigned to the first eligible reviewer, and
  `executionState` tracks exactly where the issue sits in its policy pipeline.
- **Composable stages** — an issue can have a review stage, an approval stage,
  both in sequence, or neither (a comment-required backstop always applies).
  Participants can be agents or human board users.
- **Self-review prevention** — the runtime "excludes the original executor" when
  selecting an eligible reviewer/approver. This is a genuine structural parallel
  to ai-software-factory's own self-approval-rejection rule and is notably
  something **Multica has no equivalent of**.
- **A real decision audit table** (`issue_execution_decisions`): actor
  (agent or user), stage, outcome (`approved`/`changes_requested`), a
  *required* explanatory comment, and the run ID that produced it — access
  control enforces that only the current active participant can record a
  decision (others get `422`).
- **Iterative, not terminal** — "changes requested" routes back to the *same*
  review stage with the *same* reviewer, not to the start of the pipeline.

**The critical distinction from ai-software-factory:** every one of these stages
fires *after* the executor has already produced a result — "Executor transitions
to `done` — the runtime intercepts this." There is no equivalent of
ai-software-factory's `PLAN_APPROVED`/`EXEC_APPROVED` gates, which block
execution from starting at all until a human has approved the plan. Paperclip's
model is squarely in the same family as `openai/symphony`'s "review is a
workflow state, not a gate" philosophy (see
`docs/research-alternative-autonomous-pipeline-stack-2026-08-30.md` §2.2) —
just implemented with materially more engineering rigor than anything else
surveyed in that family so far.

## 5. Sandboxing — genuinely more advanced than Multica, with an important caveat

This is where Paperclip meaningfully differs from Multica. Multica's own
security-model doc stated plainly that it does not attempt sandboxing at all.
Paperclip has a real plugin ecosystem of remote sandbox providers in its
monorepo: **Cloudflare, Daytona, E2B, Modal, Novita, and Kubernetes** — the
Kubernetes provider alone includes `cilium-network-policy.ts`,
`image-allowlist.ts`, `scoped-network-egress.ts`, `secret-manager.ts`,
`tenant-orchestrator.ts`, and a real `kind`-cluster integration test suite
(`test/integration/end-to-end-run.test.ts`). This is genuinely more
sophisticated *shipped* isolation infrastructure than ai-software-factory has
built to date (whose own roadmap — worktree → Docker → Firecracker/Kata/gVisor —
is largely still aspirational per `SPEC-08`/`SPEC-10`, with Phase 1 still
worktree-only).

But two things temper this:

1. **Local execution mode — the default and simplest path — is unsandboxed and
   permission-bypassing by default**, exactly like Multica. `docs/adapters/claude-local.md`:
   `dangerouslySkipPermissions` defaults to **`true`** ("required for headless
   runs where interactive approval is impossible"). `docs/adapters/codex-local.md`
   documents an equivalent `dangerouslyBypassApprovalsAndSandbox` flag (labeled
   dev-only, not stated as defaulting true, so slightly more conservative than
   the Claude adapter's default). A user running Paperclip the simple way gets
   the same "agent has full permissions of the process it's spawned as" posture
   Multica discloses.
2. **Paperclip does not itself enforce or verify the sandbox contract.**
   `packages/plugins/sandbox-providers/SANDBOX-REQUIREMENTS.md` is a genuinely
   well-reasoned, authority-based security boundary contract (it protects
   exactly two authorities — the ability to write host files and the ability to
   call the Paperclip API — through exactly two surfaces: outbound workspace
   sync and the HTTP bridge). But the document says outright: **"This repository
   does not enforce these provider rules today, and Paperclip cannot verify them
   for an externally supplied sandbox,"** and **"The provider and the operator
   set the policy for general internet access. Paperclip does not enforce this
   policy inside the sandbox."** The contract exists and is well-specified;
   compliance is delegated entirely to whichever third-party sandbox provider
   plugin is in use, unverified by Paperclip itself.

Net: Paperclip's *advanced* path (a configured remote sandbox provider) is a
real, substantially engineered isolation story that currently exceeds
ai-software-factory's shipped isolation state. Paperclip's *default* path is the
same "no sandbox, bypass permissions" posture as Multica.

## 6. Audit trail — same category as Multica, not ai-software-factory's

`docs/guides/board-operator/activity-log.md`: "Every mutation in Paperclip is
recorded in the activity log," covering agent lifecycle, issue changes,
approval decisions, budget changes, and company config changes — described
explicitly as a debugging tool ("When something goes wrong, the activity log is
your first stop"). Nothing in the docs or schema references append-only
enforcement, hash-chaining, or DB-level immutability. This sits in the same
category as Multica's execution log: real, queryable, actor-attributed —
but an observability feed, not the tamper-evident compliance record
ai-software-factory's hash-chained (`previous_hash`/`row_hash`,
UPDATE/DELETE rejected at the DB level) `AuditLog` is designed to be.

## 7. No equivalent of a pre-execution declarative Policy Engine

Nothing in Paperclip corresponds to ai-software-factory's Task Contract /
Project Profile / Completion Contract triad — a set of rules that apply
*before* and *during* execution regardless of where it runs (forbidden paths,
allowed harnesses, network egress, deterministic completion checks). The
closest analogues found are scoped narrowly to one execution path each: the
Kubernetes sandbox provider's own `image-allowlist.ts`/`scoped-network-egress.ts`
(real, but specific to that one provider plugin, not a universal per-project
policy the runtime enforces regardless of adapter) and the Execution Policy
system (§4), which governs *who reviews what after the fact*, not *what an
agent is permitted to touch while working*.

## 8. Comparison table

| Dimension | Paperclip | Multica *(for reference)* | ai-software-factory |
|---|---|---|---|
| **Approval model** | Runtime-enforced review/approval **after** the executor finishes; self-review excluded | PR review after work is done; no runtime-enforced stages | Durable **pre-execution** gate (`PLAN_APPROVED → EXEC_APPROVED → READY`) before any work starts |
| **Isolation, default path** | Unsandboxed, `dangerouslySkipPermissions: true` by default (Claude adapter) | Unsandboxed, `bypassPermissions`/`danger-full-access` by design | Worktree only in Phase 1 (explicitly documented as not a security boundary) |
| **Isolation, advanced path** | Real plugin ecosystem: Cloudflare, Daytona, E2B, Modal, Kubernetes w/ Cilium network policy, image allowlisting, tenant isolation — genuinely shipped and tested | None — operator builds their own boundary | Staged roadmap (Docker → Firecracker/Kata/gVisor), not yet built |
| **Sandbox contract enforcement** | A real, well-reasoned authority-based contract exists, but Paperclip states it does not enforce or verify provider compliance | N/A (no contract attempted) | Enforcement intended to live in the Controller's Policy Engine, itself still hardening (open critical findings per `NEXT_STEPS.md`) |
| **Self-review prevention** | Yes — explicit runtime rule | Not found | Yes — explicit invariant (`AGENTS.md`, `PermissionService`) |
| **Audit trail** | Mutation log for debugging, actor-attributed, not tamper-evident | Execution/cost log for debugging | Append-only, hash-chained, DB-level immutability |
| **Pre-execution policy engine** | None (governance is org-decision-scoped: hiring/strategy/overrides) | None | Task Contract / Project Profile / Completion Contract |
| **License** | Plain MIT, no additional conditions | Apache-2.0 + anti-SaaS-resale clause | Fully owned, no vendor |
| **Harness breadth** | 4 built-in adapters + a real plugin/adapter-authoring model | 26 CLIs in production | 2 declared, execution still stubbed |
| **Maturity** | 80k★, pushed today, large engineering team, real K8s e2e test suite | 47.6k★, released near-daily | Not gate-clean as of 2026-09-07 (open `severity:critical` findings) |

## 9. Where ai-software-factory still has a genuine advantage

Narrower than the gap over Multica, because Paperclip is a materially more
sophisticated project than Multica on almost every axis checked. What survives:

1. **Pre-execution gating is still unique to ai-software-factory.** Paperclip's
   Execution Policy — its best-engineered feature — governs review *after* the
   executor already produced a diff. Nothing in Paperclip stops an agent from
   starting work on a task at all pending human sign-off on the plan; that is
   ai-software-factory's entire founding design choice (see
   `docs/PROJECT_CONTEXT.md` and `decisions/ADR-001-governance-controller-implementation.md`
   for why this was made a hard requirement rather than a nice-to-have).
2. **A tamper-evident audit log, not a mutation feed.** Both Paperclip and
   Multica have real, actor-attributed activity logs built for debugging. Only
   ai-software-factory's is designed to survive an adversarial actor tampering
   with the record itself (hash-chained, DB-enforced immutability).
2. **A universal, declarative pre-execution policy** (Task Contract / Project
   Profile / Completion Contract) that applies regardless of which harness or
   execution target is used — Paperclip's closest equivalents (K8s provider
   image allowlist, egress scoping) are real but scoped to one sandbox provider
   plugin, not a project-wide invariant enforced by the orchestration core
   itself.
4. **No vendor.** Paperclip is a well-funded, fast-moving company's product
   (80k★, daily pushes, presumably a large team given the K8s/Cloudflare/Daytona/
   E2B/Modal provider breadth) with its own roadmap and incentives.
   ai-software-factory's governance layer answers to no one else's business
   model.

## 10. Where Paperclip is ahead of ai-software-factory today — stated plainly

This is a stronger competitor than Multica on engineering merits, and the gap
should be acknowledged honestly:

- **Real, shipped, multi-provider sandbox infrastructure** — Kubernetes with
  Cilium network policies, image allowlisting, and per-tenant orchestration is
  further along than anything in ai-software-factory's own security roadmap
  today, even though ai-software-factory's *destination* design (Project
  Profile-declared sandbox levels) is architecturally comparable in spirit.
- **A genuinely well-engineered post-hoc review/approval system**, with
  self-review exclusion, iterative changes-requested loops, and a structured
  decision audit trail — closer in spirit to what a mature version of
  ai-software-factory's `AGENT_REVIEW`/`HUMAN_REVIEW` states could look like
  than anything else surveyed so far.
- **Cleaner license** (plain MIT vs. ai-software-factory's own dependency risk
  on `macro-agent`'s single-maintainer ecosystem, per
  `docs/research-proposed-orchestration-stack-2026-08-23.md` §8.3) — not a
  criticism of ai-software-factory's own code, but a reminder that Paperclip's
  *execution* layer is more independently de-risked than the execution layer
  ai-software-factory currently depends on.
- **Far broader adoption and battle-testing** at 80k stars with real production
  usage implied by the depth of its sandbox-provider integrations.

## 11. Bottom line

Paperclip is the strongest comparison project surveyed in this whole research
thread — more sophisticated than Multica on sandboxing, review workflow
engineering, and audit structure, and cleaner-licensed. It still does not
solve the one problem that is ai-software-factory's entire reason to exist:
gating *whether execution may start at all* behind a durable, human-attributed,
pre-execution decision. Every review/approval mechanism found in Paperclip
operates on work that has already happened. If ai-software-factory's own
pre-execution governance model is ever fully hardened (closing the gaps its
adversarial review process currently keeps finding, per `docs/NEXT_STEPS.md`),
that remains its one durable differentiator against a project that is, on
every other axis checked, currently more mature.

## 12. Sources

- https://github.com/paperclipai/paperclip (`README.md`, `SECURITY.md`, `LICENSE`)
- `docs/start/architecture.md`, `docs/start/core-concepts.md`
- `docs/guides/execution-policy.md`
- `docs/guides/board-operator/approvals.md`,
  `docs/guides/agent-developer/handling-approvals.md`
- `docs/guides/board-operator/execution-workspaces-and-runtime-services.md`
- `docs/guides/board-operator/activity-log.md`
- `docs/adapters/claude-local.md`, `docs/adapters/codex-local.md`
- `packages/plugins/sandbox-providers/SANDBOX-REQUIREMENTS.md` and the
  Cloudflare/Daytona/E2B/Modal/Novita/Kubernetes provider plugin directories
- Companion memo:
  `docs/research-multica-vs-ai-software-factory-comparison-2026-09-07.md`
- `docs/PROJECT_CONTEXT.md`,
  `decisions/ADR-001-governance-controller-implementation.md`,
  `docs/NEXT_STEPS.md` (this repository, as of 2026-09-07)
