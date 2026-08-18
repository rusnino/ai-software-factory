# REVIEW-002: Verification of REVIEW-001 gap closures

Date: 2026-08-18
Scope: every commit that claimed to close a `GAP-NNN` row from `reviews/REVIEW-001-phase-1-governance-controller.md`
between the closing summary commit `5f43f25` and the fix commits it references (`08f1171` .. `31b6dfa`).
Method: independently re-read each fix's diff and the current (HEAD) state of the affected files, ran the
relevant tests live, and actively tried to construct failure scenarios rather than trusting that a commit +
passing test implies a correct fix. This review exists because `reviews/GAPS.md` marked several `CRITICAL`/
`HIGH` gaps `CLOSED` that, on inspection, are not — which directly violates the gate rule `AGENTS.md` and
`reviews/GAPS.md` themselves define. Tracking rows for this review's findings live in the *same*
`reviews/GAPS.md` ledger (existing `GAP-NNN` ids are reopened rather than duplicated, since these are the
same underlying gaps, not new ones).

## Verdict summary

| Gap | REVIEW-001 severity | Verdict | New status |
|---|---|---|---|
| GAP-001 | CRITICAL | **FULLY FIXED** | CLOSED (unchanged) |
| GAP-002 | CRITICAL | **PARTIALLY FIXED** | reopened → OPEN |
| GAP-003 | HIGH | FULLY FIXED | CLOSED (unchanged) |
| GAP-004 | HIGH | FULLY FIXED | CLOSED (unchanged) |
| GAP-005 | HIGH | FULLY FIXED | CLOSED (unchanged) |
| GAP-006 | HIGH | **PARTIALLY FIXED** | reopened → OPEN |
| GAP-007 | HIGH | FULLY FIXED | CLOSED (unchanged) |
| GAP-008 | HIGH | **NOT ACTUALLY FIXED** | reopened → OPEN |
| GAP-009 | MEDIUM | **PARTIALLY FIXED** (fabricated rationale) | reopened → OPEN |
| GAP-010 | MEDIUM | FULLY FIXED | CLOSED (unchanged) |
| GAP-011 | MEDIUM | FULLY FIXED | CLOSED (unchanged) |
| GAP-012 | MEDIUM | PARTIALLY FIXED (real but unconditional, untested) | → IN_PROGRESS |
| GAP-013 | MEDIUM | PARTIALLY FIXED (one whitewashed line) | fixed directly in this review → CLOSED |
| GAP-014 | MEDIUM | FULLY FIXED | CLOSED (unchanged) |
| GAP-015 | LOW | FULLY FIXED | CLOSED (unchanged) |
| GAP-016 | LOW | PARTIALLY FIXED (files committed, ledger still stale) | → IN_PROGRESS |
| GAP-017 | LOW | FULLY FIXED (confirmed in a prior turn) | CLOSED (unchanged) |
| GAP-018 | LOW | FULLY FIXED | CLOSED (unchanged) |
| GAP-019 | LOW | FULLY FIXED | CLOSED (unchanged) |

**Per `AGENTS.md`'s gate rule, Phase 1 is still not done**: `GAP-002` (CRITICAL) and `GAP-006`/`GAP-008` (HIGH)
are reopened. `docs/NEXT_STEPS.md`'s "complete... review gaps are closed" claim is corrected again as part of
this review.

Test/lint claims from the closing commit were spot-checked and are accurate: `uv run pytest -q` → 130 passed;
`uv run ruff check .` → clean.

## Findings requiring reopening

### GAP-002 (CRITICAL, reopened) — forbidden-path check only catches exact string matches, not subpaths

The inverted-boolean bug from REVIEW-001 is genuinely fixed: `services/policy_engine.py` now checks
`touched_paths & profile_forbidden` (task's real `inputs`/`deliverables` against the profile's forbidden
list) instead of `contract_forbidden & profile_forbidden`, and SPEC-03's own canonical example now correctly
approves.

But the new check is a plain set intersection over exact string equality, with no path-prefix/containment
logic. Traced concretely:
```python
# forbidden_paths = ["~/.ssh", "/srv/production"]
inputs = ["~/.ssh/id_rsa"]                      # → allowed: True  (should be rejected)
deliverables = ["/srv/production/db_dump.sql"]  # → allowed: True  (should be rejected)
inputs = ["~/.ssh"]                             # → allowed: False (correct — exact match only)
```
Since SPEC-03's own `forbidden_paths` examples (`~/.ssh`, `/srv/production`) are directories, any task
touching a specific file *under* one of them sails through undetected. `tests/test_policy_engine.py` only
tests exact-match rejection, not a nested-path case — the gap is real and untested.

**Needed to actually close this**: forbidden-path membership should be a prefix/containment check (e.g.
normalize and check `touched.startswith(forbidden.rstrip("/") + "/")` or equivalent path-aware comparison),
not literal set equality.

### GAP-006 (HIGH, reopened) — Completion Contract `required`/`optional` checks never execute their commands

`VerificationService.verify()` does real work for `forbidden_path_check` and `scope_check`, but for
`required`/`optional` checks it hardcodes `"status": "passed"` for every check regardless of `Check.command`
or `Check.expect_exit` — the commands are never run as a subprocess and no exit code is ever compared. This
is the part of SPEC-03 §3.7 that actually matters (gating completion on real command success); it's
decorative. Additionally, `VerificationService` is still not called from `ApprovalService`, any API route, or
any state transition — it exists in isolation, exercised only by its own unit test.

**Needed to actually close this**: run each `Check.command` (subprocess, capture exit code), compare to
`expect_exit`, and set `status` accordingly; then wire the service into the `AGENT_REVIEW -> HUMAN_REVIEW`
path so it actually gates something.

### GAP-008 (HIGH, reopened) — Reconciliation service is an orphaned stub, not a real implementation

`ReconciliationService.check(task_id)` is a single-task comparison against `MemoryPlaneAdapter.updates`, a
list that **no application code path ever populates** (`sync_task_state` is called only by the service's own
tests). It is not invoked anywhere in the running application (no scheduler, no CLI command, no API route).
Against SPEC-03 §3.9's five requirements: (1) Plane-vs-DB comparison — implemented only against inert
never-populated data; (2) DB-vs-opentasks comparison — not implemented at all, `OpenTasksService` is never
referenced; (3) divergence detection — only for (1); (4) human alert on divergence — not implemented, just an
ordinary audit log write; (5) projection-field-only auto-correct — not implemented, no correction logic
exists. This is functionally equivalent to the original "zero implementation" finding.

**Needed to actually close this**: either build a real periodic job that compares live Plane/opentasks state
(once those integrations exist), or — more honestly, given Plane/opentasks are themselves still stubs —
explicitly defer Reconciliation to Phase 2/3 in `docs/NEXT_STEPS.md` with a footnote, the same way GAP-013's
other criteria were honestly deferred, rather than claiming it's closed.

### GAP-009 (MEDIUM, reopened) — dependency rationale doc contains a factually false claim

`docs/DEPENDENCIES.md` claims `structlog` and `python-json-logger` are "intentionally paired," with
`python-json-logger` providing "the JSON formatter consumed by the `structlog` console processor." Neither
half of that claim is true: `python-json-logger` is imported nowhere in the application
(`grep -rn "python_json_logger\|pythonjsonlogger"` → zero hits outside `pyproject.toml`/`uv.lock`/the doc
itself), and no `structlog.configure(...)` call exists anywhere to have a "console processor" in the first
place. The documentation obligation was met in form but not in substance.

**Needed to actually close this**: either wire up real structured JSON logging (making the doc's claim true)
or drop the unused `python-json-logger` dependency and correct the doc.

## Findings adjusted but not reopened as CRITICAL/HIGH blockers

### GAP-012 (MEDIUM → IN_PROGRESS) — audit logging added but unconditional and untested

`TaskService` now logs `project_profile_created`/`project_profile_updated`/`task_created` events — genuine
code, not doc-only — but `project_profile_updated` fires unconditionally on every call against an existing
project (no old-vs-new content comparison), and there is zero test coverage for any of these three event
types. Real improvement over "silent," but not solid enough to call fully closed without at least a test.

### GAP-013 (MEDIUM → fixed directly in this review) — one checklist line was ticked without disclosure

Five of the six deferred `SPEC-10` §10.1 criteria have honest footnotes explaining exactly what's missing.
The sixth, `Failed verification never produces DONE`, was ticked `[x]` with no footnote, even though
`VerificationService` (see GAP-006) is never invoked anywhere in the live approval/state-transition path — the
criterion held only in the vacuous sense that verification is never consulted at all, not because it actively
gates anything. This review corrects that line in `specs/SPEC-10-phase-plan.md` directly (unchecked +
footnote added, consistent with the other five), so no separate code fix is required to close this one — it's
closed by the documentation correction itself.

### GAP-016 (LOW → IN_PROGRESS) — files committed, but the ledger they describe is still stale

`.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/` now has no untracked files (the 23
brief/report files were committed). But `progress.md` inside that directory still shows only Task 1-2 checked
and doesn't list Tasks 17-20 at all, despite report files existing through Task 20. Cosmetic, not
governance-relevant, left `IN_PROGRESS` rather than reopened as blocking.

## Confirmed correct (no action needed)

GAP-001, GAP-003, GAP-004, GAP-005, GAP-007, GAP-010, GAP-011, GAP-014, GAP-015, GAP-017, GAP-018, GAP-019 —
each independently re-verified against its original finding, with tests actually executed where applicable.
Full detail available on request; summarized in the table above to keep this file scannable.

## Verification

`cd src/governance_controller && uv run pytest -q` → 130 passed (unchanged by this review, no code touched).
`uv run ruff check .` → clean. This review only touched `specs/SPEC-10-phase-plan.md`,
`reviews/GAPS.md`, `docs/NEXT_STEPS.md`, and this file — no `governance_controller` application code was
modified.
