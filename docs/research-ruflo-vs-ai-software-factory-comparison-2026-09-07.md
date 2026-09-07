# Research: Ruflo vs. AI Software Factory — Where the Real Differences Are

Date: 2026-09-07
Status: research memo (comparative analysis). No architectural decisions made; no
code/spec changes.

## 1. Scope and an important framing correction upfront

Third in this series, after Multica
(`docs/research-multica-vs-ai-software-factory-comparison-2026-09-07.md`) and
Paperclip (`docs/research-paperclip-vs-ai-software-factory-comparison-2026-09-07.md`).
Same method: read the project's own docs and source tree directly (GitHub API),
not marketing copy.

**`ruvnet/ruflo`** — MIT, TypeScript, 71,230 stars, 8,448 forks, 943 open issues,
created 2025-06-02, pushed today — is the renamed successor to
`ruvnet/claude-flow` (the README states this outright: "Claude Flow is now
Ruflo"). Sources read: `README.md`, `SECURITY.md`, `AGENTS.md`, `docs/STATUS.md`,
and `plugins/ruflo-agent/README.md`.

**Unlike Multica and Paperclip, Ruflo is not the same category of product.** It
is not a standalone platform with its own web UI, issue board, or org
simulation. It is a **harness/coordination layer that plugs into an already-running
Claude Code or Codex session** via hooks and an MCP server (323 tools, 45 CLI
commands, 32-35 installable plugins), adding swarm coordination, persistent
memory/RAG, cross-machine federation, and self-learning routing. Its own
framing: "**Agent = Model + Harness.** The model writes; the harness gives it
tools, memory, loops, sandboxes, and controls." This comparison is adjusted
accordingly — several axes that mattered for Multica/Paperclip (issue boards,
org structure, PR review UI) simply do not apply, because Ruflo does not attempt
them.

## 2. What Ruflo is, concretely

- **Installed alongside Claude Code/Codex**, not instead of them. `npx ruflo init`
  writes a `CLAUDE.md` with hooks/routing rules and registers an MCP server;
  after that, the developer keeps using Claude Code normally while Ruflo's hooks
  route tasks, retrieve memory, and coordinate background agents transparently.
- **Swarm coordination** (`ruflo-swarm`): `ruflo swarm init --topology hierarchical
  --max-agents 8` — comparable in spirit to macro-agent's team/topology role, but
  scoped to coordinating sub-agents *within* one developer's Claude Code
  session/machine, not a fleet-wide execution-orchestration engine serving many
  independent tasks across an organization.
- **Persistent memory/RAG** (`ruflo-agentdb`, `ruflo-rag-memory`, `ruflo-rvf`,
  `ruflo-knowledge-graph`) and **self-learning routing**
  (`ruflo-intelligence`, `ruflo-daa`) — an ambitious, actively-iterating research
  program (the repo's own `docs/dream-cycle/` and `docs/darwin*/` directories
  document repeated self-improvement/evolutionary-tuning passes).
- **Federation** (`ruflo-federation`): agents on different machines exchange
  work over mTLS + ed25519-verified channels, with PII stripped before anything
  leaves a node.
- **Security-adjacent plugins**: `ruflo-security-audit` (vulnerability/CVE
  scanning) and `ruflo-aidefence` (prompt-injection blocking, PII detection) —
  scanning/defensive tools, not a governance or approval layer.
- **Isolated sub-agent execution** (`ruflo-agent`): a WASM sandbox runtime
  (`wasm_agent_*`, built on `@ruvector/rvagent-wasm`) that runs with **no host
  filesystem access by default**, plus an option to run sub-agents in
  Anthropic's cloud-isolated Managed Agents container. A third mode — running a
  sub-agent in-process with full host trust — is explicitly documented as
  planned-but-not-yet-built, and will be opt-in specifically because it breaks
  the sandboxed default.
- License: plain MIT, same as Paperclip and cleaner than Multica.

## 3. The governance/approval axis: essentially absent by design, not by gap

This is the most consequential finding, and it is stated plainly by the
project's own README, in the context of its federation trust model:

> "Untrusted agents can still participate at lower privilege... As they prove
> reliable, trust upgrades. If they misbehave, they get downgraded instantly —
> **no human in the loop required.**"

Searching `README.md` and the 707-line `AGENTS.md` for "approval," "governance,"
"permission," or "human-in-the-loop" turns up nothing beyond this one sentence
and Claude Code's own native hooks (`pre-task`/`post-task`, unrelated to human
sign-off). There is no issue-board review stage (Paperclip), no PR-review
convention baked into the product (Multica), and no pre-execution approval gate
(ai-software-factory). Ruflo's entire design intent is to *reduce* how often a
human needs to be consulted — trust/privilege decisions for federated peers are
resolved automatically by a reputation mechanism, explicitly to avoid needing a
human at all.

This is not a gap relative to its goals — Ruflo was never trying to be a
governance product. But it means Ruflo sits at the opposite end of the spectrum
from ai-software-factory on this specific axis, more so than either Multica or
Paperclip.

## 4. Sandboxing: genuinely good for the one thing it controls, irrelevant for the thing that matters most

Ruflo's WASM sandbox default (`wasm_agent_*`, no host filesystem access) is
actually a *better* default posture than either Multica's or Paperclip's local
adapters, both of which default to bypassing permissions/sandboxing entirely.
Credit where due.

But this sandbox only covers sub-agents Ruflo itself spawns via the
`ruflo-agent` plugin — an optional, secondary capability. **It has no bearing
on, and does not modify, the sandbox posture of the primary Claude Code or
Codex session Ruflo is a harness for.** That session's tool-approval and
filesystem-access behavior is entirely Claude Code's or Codex's own
configuration, outside anything Ruflo's docs claim to control. Ruflo augments
an already-running agent's capabilities (memory, coordination, routing); it
does not wrap or gate that agent's execution the way Multica's daemon or
Paperclip's adapters do.

## 5. Audit trail

Ruflo's federation model states "every message auditable," and `ruflo doctor`/
`ruflo verify` can confirm installed bytes match a "signed witness" (a
supply-chain integrity check on the Ruflo installation itself, not an audit log
of agent decisions). No evidence was found of a tamper-evident, hash-chained,
actor-attributed decision log comparable to ai-software-factory's `AuditLog`,
Paperclip's `issue_execution_decisions`, or even Multica's execution log. Ruflo
is not built around a durable record of "who decided what, when" in the sense
any of the other three projects are — it is built around agents coordinating
and learning, with observability plugins (`ruflo-observability`,
`ruflo-cost-tracker`) for operational visibility, not decision provenance.

## 6. Comparison table

| Dimension | Ruflo | Multica / Paperclip *(for reference)* | ai-software-factory |
|---|---|---|---|
| **Product category** | Harness/coordination plugin layer for an existing Claude Code/Codex session | Standalone platform (own UI, own agent-spawning) | Standalone governance platform |
| **Approval model** | None — federation trust adjusts automatically, explicitly "no human in the loop required" | Post-hoc review (PR review / runtime-enforced review-approval stages) | Durable pre-execution gate before work starts |
| **Sandbox, agents it spawns itself** | WASM sandbox, no host FS access, is the *default* | Unsandboxed by default | Worktree only (Phase 1), staged roadmap toward stronger isolation |
| **Sandbox, primary session** | Not controlled by Ruflo at all (Claude Code's/Codex's own config) | Controlled by the product (and defaults unsafe) | Controlled by the product (worktree; not yet a real boundary) |
| **Audit trail** | Supply-chain integrity check (`ruflo verify`) + federation message logging; no decision-provenance record | Mutation/execution log for debugging | Append-only, hash-chained, DB-enforced immutability |
| **Multi-tenant/org concept** | None — single-developer/single-machine-oriented, federation is peer-to-peer | Multi-workspace (Multica), multi-company (Paperclip) | Multi-project via Project Profile |
| **License** | Plain MIT | Apache-2.0+clause (Multica) / plain MIT (Paperclip) | Fully owned |
| **Scale/growth claims** | Self-published "8.1M+ ecosystem downloads" and "106k git clones/14d" via in-repo JSON "proof"/"ledger" files, not GitHub's own traffic API | Standard GitHub star/fork counts | N/A |
| **Maturity** | 71.2k★, pushed today, 1999/1999 + 366/366 green test suites per its own `STATUS.md`, but an actively-iterating research program (dream-cycle/darwin self-tuning docs suggest ongoing architectural churn) | 80k★ (Paperclip) / 47.6k★ (Multica), both released products | Not gate-clean as of 2026-09-07 |

One methodological note on the growth numbers: Ruflo's download/clone claims
are backed by files it publishes itself (`data/clone-data.proof.json`,
`data/clone-data.ledger.json`) rather than a third-party or platform-verified
source. This deserves the same independent-verification caveat already applied
elsewhere in this research thread to self-reported growth metrics — it is not
evidence of fabrication, but it is not independently verified either.

## 7. Where ai-software-factory has a genuine advantage

Larger than the gap over either Multica or Paperclip, because Ruflo is not
competing on this axis at all:

1. **Governance exists as a concept.** Ruflo has none — not a weak version, an
   absent one, by explicit design ("no human in the loop required"). Any
   comparison on approval workflows, policy enforcement, or audit
   trails is a comparison against something that isn't there.
2. **A real decision-provenance record**, vs. Ruflo's supply-chain-integrity
   and message-logging focus, which answers "is my install untampered" and "did
   this message get delivered," not "who approved this change and why."
3. **Ruflo does not touch the primary agent's sandbox posture at all** — it is
   additive to whatever Claude Code/Codex already does. ai-software-factory (like
   Multica and Paperclip) at least attempts to own that boundary, even if
   imperfectly today.
4. **A multi-project, multi-stakeholder model** (Project Profile per project,
   Plane as a shared human-facing board) — Ruflo's federation is peer-to-peer
   coordination between machines/agents, not a shared organizational system of
   record the way Plane or Paperclip's company board are.

## 8. Where Ruflo is ahead of ai-software-factory today — stated plainly

- **A genuinely useful, narrower thing, done deeply.** If the actual need is
  "make my existing Claude Code session smarter across long sessions, coordinate
  a few sub-agents, and remember things between runs," Ruflo does that with far
  more engineering investment (323 MCP tools, persistent vector memory, learned
  routing) than anything in ai-software-factory's scope — because it isn't
  ai-software-factory's scope. This is a different, real capability gap, not a
  weakness of ai-software-factory's design.
- **A safer default for sub-agent spawning specifically** (WASM, no host FS
  access) than either Multica's or Paperclip's default local execution path —
  worth noting as a pattern (default-sandboxed sub-agent execution, upgrade
  path is explicit opt-in) that ai-software-factory's own harness/execution
  layer could learn from once it builds real sandboxing.
- **Enormous plugin/ecosystem breadth** (35 plugins spanning memory, testing,
  DevOps, architecture methodology) that a small, governance-focused project
  cannot and should not try to match.

## 9. Bottom line

Ruflo is not really a competitor to ai-software-factory — it solves a different
problem (making a single agent session smarter, longer-lived, and
better-coordinated) and explicitly does not attempt the problem
ai-software-factory exists to solve (gating whether work is allowed to happen
at all, with a durable, attributable, human-controlled decision trail). The
comparison is useful mainly as a boundary case: it shows what "agent tooling
with zero governance ambition" looks like at its most technically ambitious,
which sharpens what ai-software-factory is actually for by contrast. Where
Ruflo genuinely teaches a lesson worth carrying over is narrow but real:
default-sandboxed sub-agent spawning (WASM, no host access unless explicitly
upgraded) is a pattern worth matching once ai-software-factory's own execution
layer builds real sandboxing, rather than defaulting to trust the way Multica's
and Paperclip's local paths currently do.

## 10. Sources

- https://github.com/ruvnet/ruflo (`README.md`, `SECURITY.md`, `AGENTS.md`, `LICENSE`)
- `docs/STATUS.md`
- `plugins/ruflo-agent/README.md`
- Companion memos:
  `docs/research-multica-vs-ai-software-factory-comparison-2026-09-07.md`,
  `docs/research-paperclip-vs-ai-software-factory-comparison-2026-09-07.md`
