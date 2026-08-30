---
name: Review finding (live-reproduced gap)
about: A defect found by adversarial live-reproduction review — not a feature request
title: "[SEVERITY]: one-line statement of the defect"
labels: bug, phase-2
---

<!--
This template matches the structure used for every issue filed by this project's review process
(github.com/rusnino/ai-software-factory/issues/151-279 and onward). Fill in every section — an
issue without live reproduction or a severity justification will not be trusted by the next review
round, per this project's own history of premature "gate-clean" declarations.
-->

## Summary

One or two sentences: what's broken, and in one clause, why it matters.

## Where

File:line of the defect. Quote the exact code if it clarifies the mechanism.

## Failure scenario

Concrete inputs/state → wrong output, crash, or security bypass. If this is a concurrency bug,
state the exact race window (which two operations overlap, and what state each assumes).

## Live reproduction

What you actually ran and what it actually showed — not what the code "should" do on inspection.
For concurrency-shaped bugs: real Postgres, genuinely overlapping requests/sessions (confirmed via
timing evidence), not a mock and not a sequential simulation. Paste the relevant output.

## Severity

CRITICAL / HIGH / MEDIUM / LOW, calibrated against this project's existing bar (see `docs/NEXT_STEPS.md`):
CRITICAL = live data-loss/permanent-stuck-state/cross-tenant-breach/duplicate execution of a
side-effecting action; HIGH = live security bypass or a serious correctness bug with a bounded blast
radius; MEDIUM = real but narrower; LOW = hygiene/observability/documentation.

## Suggested fix

A direction, not necessarily a full patch. Note if this is a known recurring defect class
(`RISK-16`/`RISK-19` in `requirements/REQUIREMENTS.md`) — if so, say so explicitly so the fix sweeps
every sibling call site, not just this one.

## Regression test

<!-- Required for severity:high and severity:critical. Fill in when the fix lands, or explain why an
     automated test genuinely isn't possible — see CLAUDE.md's "Regression Coverage Policy". -->

- [ ] A test exists that fails against the pre-fix code and passes against the post-fix code.
- [ ] If this is a concurrency/CAS/race-condition bug: the test uses real Postgres and genuine
      concurrent sessions/requests (`asyncio.gather` or equivalent), not a mock.
- [ ] If this is an instance of a known recurring defect class: sibling call sites with the same
      shape were swept as part of this fix, not just the reported one.
