# AGENTS.md — AI Software Factory

This project is designed to be implemented by AI coding agents (OpenCode, Claude Code, Codex, and others) under explicit human governance.

## Read Order for First Contact

Read in this order before writing application code:

1. `README.md`
2. `specs/SPEC-01-vision.md`
3. `specs/SPEC-02-architecture.md`
4. `specs/SPEC-03-governance.md`
5. `decisions/ADR-001-governance-controller-implementation.md`
6. `specs/SPEC-04-plane-integration.md`
7. `specs/SPEC-05-macro-agent-integration.md`
8. `specs/SPEC-06-harnesses.md`
9. `specs/SPEC-07-intake.md`
10. `specs/SPEC-08-security.md`
11. `specs/SPEC-09-verification.md`
12. `specs/SPEC-10-phase-plan.md`
13. `requirements/REQUIREMENTS.md`

## Non-Negotiable Architecture Rules

- **Governance Controller is authoritative** for approvals, state machine, policy, audit, task DAG, execution records.
- **Plane CE is a replaceable projection** for humans. Do not rely on Plane to enforce workflow transitions or approvals.
- **macro-agent** is execution orchestration only. It decides *who / when / how* approved work runs, not *whether* it may run.
- **Do not reimplement macro-agent internals**: agent lifecycle, worktrees, messaging, merge, conflict recovery.
- **Plane dependencies → runtime DAG**: Plane holds project-level dependencies; Controller materializes the approved subgraph into `opentasks`; agents claim work only from `opentasks`.
- **Event Bridge** translates macro-agent workspace events into Controller state updates. macro-agent has no outgoing webhooks.
- **All approval paths converge to `POST /approvals`** in the Controller, regardless of source (Plane, Telegram, CLI, dashboard).
- **Human approval must be explicit and machine-recorded** before sensitive transitions.
- **Worktree is not a security sandbox**. Progressive isolation: git worktree → Docker → Firecracker/Kata.
- **Secrets stay outside prompts, team YAML, and task descriptions**.
- **Python projects and utilities use `uv` / `uvx`**.

## Domain Model Separation

Keep these concepts separate in code and configuration:

- **agent harness** (OpenCode / Claude Code / Codex / Aider)
- **role** (planner / worker / reviewer)
- **provider / account**
- **model policy**
- **skills / behavior**
- **permissions**
- **execution backend**

## Current Implementation Priority

Build and prove this path first:

```text
Controller API → durable human approval → READY → Agent Registry → macro-agent
  → Direct CLI / ACP → OpenCode (+ optional harness) → worktree
  → git-cascade landing → verification → HUMAN_REVIEW → DONE
```

Do this **before** adding Plane UI, OPA, Temporal, advanced sandboxing, or more than two harnesses.

## Phase 1 Stop Conditions

Stop Phase 1 and document a blocker if:

- OpenCode cannot receive required macro-agent MCP tools without invasive changes.
- Per-role harness selection requires deep changes to macro-agent that break upstream compatibility.
- Durable approval or state machine cannot be guaranteed without Plane.

## Security Constraints for AI Agents

- Do not add new external dependencies without documenting why.
- Do not put secrets, API keys, or private credentials in any markdown/yaml/config file committed to git.
- Do not treat a git worktree as a sandbox.
- Do not allow agents to approve their own work.
- Do not make Plane, UI, or macro-agent authoritative for approvals.

## Skills and Methodology

- Use OpenCode skills / Superpowers where appropriate.
- Keep skills as methodology, not security boundaries.
- Prefer deterministic verification over LLM-based claims of correctness.

## Communication Rules

- When changing a SPEC, ADR, or REQUIREMENTS file, update this index and commit with a clear message.
- When adding risk items, keep `requirements/REQUIREMENTS.md` synchronized.
- Update `docs/NEXT_STEPS.md` when finishing or reprioritizing work.

## Review Findings and Gap Tracking

**As of 2026-08-19, gap tracking lives in GitHub Issues on `rusnino/ai-software-factory`, not in
`reviews/GAPS.md`/`reviews/REVIEW-NNN-*.md`.** Those files are a frozen historical record (106 gaps,
`GAP-001` through `GAP-106`, all migrated as closed/resolved Issues — see the banner at the top of
`reviews/GAPS.md`) — do not add new rows or new `REVIEW-NNN` files to them. Any agent that reviews
another agent's work (code review, spec-conformance review, security review) — whether asked to by a
human or as a self-check before declaring something done — follows this convention instead, so
findings survive across agent sessions and are visible/searchable in the same place the code lives:

- **Filing a finding**: `gh issue create --repo rusnino/ai-software-factory --title "<short, specific
  summary>" --body "<the full finding — failure scenario, exact file:line, reproduction evidence, same
  level of detail a `GAPS.md` row used to carry>" --label "severity:<critical|high|medium|low>" --label
  "phase-1"`. The GitHub issue number is the identifier from now on — reference it as `#123` in commit
  messages, other issues, spec footnotes, and `requirements/REQUIREMENTS.md` RISK rows. There is no more
  manual `GAP-NNN` numbering; don't invent one.
- **Closing a finding**: whichever agent fixes it includes `Fixes #123` (or `Closes #123`) in the
  commit message. Pushing that commit to `main` auto-closes the issue — no separate "mark it closed"
  step needed. If a claimed fix later turns out incomplete on re-verification, `gh issue reopen 123`
  with a comment explaining exactly what's still broken (mirroring the old "reopened, here's why" note
  a `GAPS.md` row used to carry) — don't file a duplicate issue for the same underlying defect.
- **Review rounds**: no more `REVIEW-NNN.md` files. A review round's findings are: a new issue per new
  problem found, and a comment on each existing issue being re-verified — state what was checked, the
  live-reproduction evidence, and the verdict, exactly what a `REVIEW-NNN.md` entry used to say, just on
  that issue's timeline instead of in a separate file. `gh issue comment 123 --body "..."`.
- **The gate rule** (unchanged in substance, restated for the new location): **a phase may not be
  declared "complete" in `docs/NEXT_STEPS.md` or have its checklist ticked in
  `specs/SPEC-10-phase-plan.md` while any issue labeled `severity:critical` or `severity:high` is still
  open.** Check both severities before declaring anything complete — `gh issue list --repo
  rusnino/ai-software-factory --label "severity:critical" --state open` and the same with
  `severity:high` (run as two separate queries: `gh issue list`'s multiple `--label` flags are ANDed
  together, not ORed, so a single call with both labels would never match anything). This is the
  Verification Rule below, applied to review findings specifically, not a separate exception to it.
- GitHub Issues here are an inter-agent workflow tracker, not the Governance Controller's own audit log
  (SPEC-03 §3.8) — do not conflate the two. It tracks the humans'/agents' review-and-fix workflow around
  this repo; it has no runtime effect on the Controller itself.

## Verification Rule

Every significant code change must be accompanied by tests or a clear explanation why tests are not yet feasible. Failed verification must never produce `DONE`.
