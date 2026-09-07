# Research: Multica vs. AI Software Factory — Where the Real Differences Are

Date: 2026-09-07
Status: research memo (comparative analysis). No architectural decisions made; no
code/spec changes.

## 1. Scope

`multica-ai/multica` was already surfaced twice in this project's research
(`docs/research-proposed-orchestration-stack-2026-08-23.md` §9 as a macro-agent
execution-orchestration candidate, and `docs/research-alternative-autonomous-pipeline-stack-2026-08-30.md`
§2.1 as a "meta-analyzer + memory" candidate for a hypothetical alternative stack).
This memo goes further: a direct, product-level comparison between Multica and
ai-software-factory as a whole, to answer "where does ai-software-factory actually
have an advantage over Multica, honestly."

Sources read in full for this memo (not summaries): Multica's `README.md`,
`VISION.md`, `SELF_HOSTING.md`, `LICENSE`, and — most importantly —
`apps/docs/content/docs/security-model.mdx`, which is the one place Multica's own
documentation states its real security posture plainly, rather than in marketing
language. On the ai-software-factory side: `docs/NEXT_STEPS.md` as of 2026-09-07
(current, not a historical snapshot), which documents this project's own
not-gate-clean status honestly, including open critical-severity findings.

## 2. What Multica is, concretely

A commercial, VC-style open-core product (Multica AI; the repo notes "we release
most weekdays," 47.6k★ at time of writing) that turns AI coding-agent CLIs into
Kanban-board teammates:

- Assign an issue to an agent the way you'd assign it to a colleague; the agent
  picks it up, works on a machine you control (a "runtime," typically your own
  laptop via a local daemon), comments as it goes, and moves the issue to review
  when done.
- 26 supported agent-CLI runtimes (Claude Code, Codex, Cursor, Copilot, OpenCode,
  Hermes, and 20 more) — Multica does not ship a model, it drives CLIs you already
  have installed and authenticated.
- Self-hostable in full: Go backend (Chi + WebSocket) + Next.js frontend +
  PostgreSQL 17, official Docker Compose / Helm deployment, no functional gating
  between the free self-hosted path and the hosted Multica Cloud offering.
- Web, desktop (Electron, macOS/Windows/Linux), and iOS clients; Slack/Lark/
  DingTalk/WeCom/Telegram channel integrations; a scriptable CLI/API surface.
- License: Apache License 2.0 plus additional conditions restricting *hosting
  Multica itself as a service to third parties* — internal single-organization
  use, including multiple workspaces, is explicitly unrestricted.

## 3. The one finding that matters most: Multica's own security model

Buried in `apps/docs/content/docs/security-model.mdx` (not the README) is a
directly stated, self-critical security posture:

> "By default a run executes with the full permissions of the operating-system
> user running the daemon... Multica makes no filesystem-sandbox guarantee...
> Codex runs with `sandbox_mode = "danger-full-access"` and Claude Code with
> `--permission-mode bypassPermissions`... Multica runs agents unattended, so
> approval prompts are answered automatically."

Multica explicitly does not attempt sandboxing or tool-call approval gating as a
product feature. It states this is a deliberate choice (a partial sandbox breaks
legitimate tool use, like cloud CLIs needing real credentials, while not actually
stopping credential exfiltration) and tells operators to build the isolation
boundary themselves — a dedicated Unix user, a container, or a VM, "listed from
lightest to strongest."

This is the single fact that most differentiates the two projects' philosophies:
Multica treats sandboxing/governance as **out of scope by design**, an operator
responsibility; ai-software-factory treats it as **the core problem the product
exists to solve**, with a Governance Controller, a declarative Policy Engine
(Task Contract / Project Profile / Completion Contract), and a staged isolation
roadmap (worktree → Docker → Firecracker/Kata/gVisor) as first-class
architecture, not an operator afterthought.

## 4. Comparison table

| Dimension | Multica | ai-software-factory |
|---|---|---|
| **Approval model** | Review happens *after* work is done — "work lands in review, not in main. You decide what ships." No pre-execution gate; per-tool-call permission prompts are auto-bypassed by design. | Durable, pre-execution approval chain (`PROPOSED → PLAN_APPROVED → EXEC_APPROVED → READY`) before any execution starts; every transition is attributed to an actor and recorded. |
| **Isolation / sandboxing** | Explicitly none by default (`sandbox_mode = "danger-full-access"`, `--permission-mode bypassPermissions`); the daemon documentation tells operators to build a boundary themselves. | A staged model is architecturally defined (worktree → Docker → Firecracker/Kata/gVisor) with per-project `sandbox_level` declared in the Project Profile — not fully enforced yet, but the destination state is a real sandbox, not "not our job." |
| **Policy engine** | None as a product concept. Access control is role-based only (`owner`/`admin`/`member`, and which agents each member may run). | Task Contract / Project Profile / Completion Contract: declarative, per-task and per-project policy (forbidden paths, allowed harnesses, network egress, deterministic acceptance checks). |
| **Audit trail** | An "execution log" — every tool call, command, and error, timestamped, plus token cost per run. Built for debugging and cost visibility. | Append-only, hash-chained (`previous_hash`/`row_hash`); `UPDATE`/`DELETE` rejected at the database level. Built for tamper-evident, actor-attributed compliance, not just observability. |
| **Self-approval rule** | Not stated as an invariant anywhere found. | Explicit rule: an agent/system actor cannot approve its own work (`AGENTS.md`, enforced in `PermissionService`). |
| **PM/task-board layer** | Built into the product itself — one vendor, one Kanban implementation, not swappable. | Deliberately kept swappable and non-authoritative (Plane = projection only) — a lesson drawn explicitly from an earlier failed design (`docs/PROJECT_CONTEXT.md`), where Plane/Windmill were originally meant to be authoritative and that was reversed. |
| **Harness breadth** | 26 agent CLIs, in production, real adoption. | 2 declared (OpenCode, Claude Code) at the metadata/registry level; execution invocation is still stubbed as of this writing. |
| **License / vendor risk** | Apache-2.0 plus an anti-SaaS-resale clause; a company with its own roadmap, business model, and weekday release cadence sits behind it. | Fully owned by this project; no vendor lock-in at the governance layer (though `macro-agent`'s own single-maintainer dependency chain carries its own risk, already documented in `docs/research-proposed-orchestration-stack-2026-08-23.md` §8.3). |
| **Current maturity** | Shipped, released, updated near-daily. | **Not gate-clean as of 2026-09-07** — `docs/NEXT_STEPS.md` documents open `severity:critical` findings in the policy-parser (a fifth consecutive round finding a new instance of the same "enumerate the safe command/flag forms, miss one" bypass class) and a non-functional OPA Docker Compose deployment (image `0.68.0` cannot parse this project's own `governance.rego`, 128 parse errors). |

## 5. Where ai-software-factory has a genuine advantage

Not "we are already safer" — as of this writing ai-software-factory has open
critical-severity vulnerabilities in exactly the enforcement layer this section
is about, and Multica's stance ("we don't attempt this, you build the boundary")
is at least an honestly-disclosed risk rather than a silent one. The genuine
advantage is architectural intent and process, not current guarantees:

1. **It is attempting the problem Multica scopes out entirely.** Pre-execution,
   durable, human-attributable governance is this project's core reason to
   exist; for Multica it's explicitly not the product's job.
2. **A compliance-grade audit trail, not a debug log.** Hash-chained
   append-only storage with DB-level immutability is a different category of
   guarantee than "replay what tools ran," which is what Multica's execution log
   provides.
3. **A real, if still-hardening, policy engine.** Declarative per-project/
   per-task rules (forbidden paths, allowed harnesses, network egress,
   deterministic completion checks) — Multica has nothing comparable; its
   closest analogue is coarse role-based access to *which agent a person may
   run*, not *what an agent may do once running*.
4. **A documented adversarial-review discipline that is actually finding real
   bugs.** The opencode-vs-Claude review rounds tracked in `docs/NEXT_STEPS.md`
   have caught genuine RCE-class policy-parser bypasses before they'd ship,
   repeatedly, across multiple rounds — a level of scrutiny not evidenced in
   Multica's public process.
5. **No vendor dependency on the governance layer itself.** Multica is a
   company's product with its own commercial license conditions and release
   cadence; the equivalent layer here belongs entirely to this project (the
   execution layer's own vendor-concentration risk, `macro-agent`, is a separate,
   already-documented concern — see §8.3 of the orchestration-stack memo).

## 6. Where Multica genuinely wins today — stated plainly

- **It works, today.** 26 production-ready harnesses, real desktop/mobile
  clients, real chat-channel integrations, near-daily releases from a funded
  team.
- **Time to first value is minutes** ("assign an issue, get a PR"), versus
  ai-software-factory's current state, which is not yet gate-clean even for
  Phase 2 and has open critical findings in its core enforcement path.
- **Breadth and polish** that a small, from-scratch governance-first project
  cannot match yet, by design — Multica optimized for adoption speed and
  ecosystem breadth; ai-software-factory optimized for a governance guarantee
  that doesn't fully exist yet.

## 7. Bottom line

The two projects are not really competing on the same axis. Multica is further
along as a *working product* and made an explicit, disclosed trade-off to not
solve governance/sandboxing at all. ai-software-factory is earlier as a working
product but is the only one of the two actually attempting to solve the
governance problem as a first-class architectural concern — a bet that only pays
off once the enforcement layer itself is proven correct, which, per this
project's own current status, it is not yet. The honest framing: Multica's
advantage is real and current; ai-software-factory's advantage is real but
future-conditional on closing the gaps its own adversarial review process keeps
finding.

## 8. Sources

- https://github.com/multica-ai/multica (`README.md`, `VISION.md`,
  `SELF_HOSTING.md`, `LICENSE`)
- `apps/docs/content/docs/security-model.mdx` within the Multica repository
  (fetched via GitHub API, not the marketing site)
- `docs/NEXT_STEPS.md` (this repository, as of 2026-09-07)
- `docs/PROJECT_CONTEXT.md` (this repository — Plane/Windmill precedent)
- `docs/research-proposed-orchestration-stack-2026-08-23.md` §8.3, §9 (this
  repository — prior Multica mentions and the macro-agent vendor-concentration
  finding)
- `docs/research-alternative-autonomous-pipeline-stack-2026-08-30.md` §2.1
  (this repository — prior Multica mention as a memory/meta-analyzer candidate)
