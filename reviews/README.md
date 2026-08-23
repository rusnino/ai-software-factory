# Archived — this directory is historical, frozen 2026-08-23

Everything in this directory (`GAPS.md`, all `REVIEW-NNN-*.md` files, and the handful of early
`GAP-NNN-report.md` closure reports) documents the inter-agent review-and-fix workflow for the Phase 1
Governance Controller **before** gap tracking moved to GitHub Issues. See `AGENTS.md` §"Review
Findings and Gap Tracking" for the current convention.

Nothing here is edited or added to going forward:

- **`GAPS.md`** — the running ledger of all 106 gaps found across 25 review rounds. All 106 were
  migrated to closed/resolved GitHub Issues (search `is:issue label:phase-1`, or an issue's title
  `[GAP-NNN]` to find a specific row's migrated Issue). Kept as-is below its own frozen banner.
- **`REVIEW-NNN-*.md`** (25 files) — one point-in-time narrative per review round: what was checked,
  live-reproduction evidence, and verdicts. `AGENTS.md`'s old convention already treated each of these
  as immutable once written (a later review that revisited the same code wrote a new `REVIEW-NNN` file
  rather than editing an old one), so nothing changes about them now except that no `REVIEW-026` (or
  later) will ever be added here; any future review round's findings become GitHub Issue comments
  instead.
- **`GAP-NNN-report.md`** (`GAP-021`, `022`, `023`, `024`, `045`) — a handful of even earlier,
  per-gap closure reports from before the `REVIEW-NNN.md` convention was established. Superseded by
  both `GAPS.md`'s own row for that gap and the review round that verified it; kept for completeness,
  not actively referenced.

If you're looking for the current, open state of known issues: it's the GitHub Issues tracker, not
this directory.
