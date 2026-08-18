# REVIEW-003: Verification of REVIEW-002 gap re-fixes

Date: 2026-08-18
Scope: the six commits that claimed to close the gaps `REVIEW-002` reopened or downgraded
(`GAP-002`, `GAP-006`, `GAP-008`, `GAP-009`, `GAP-012`, `GAP-016`).
Method: same as `REVIEW-002` — independently re-read diffs and current file state, ran the relevant tests
live, and additionally executed hand-constructed reproduction scripts against the real code (not just the
checked-in test suite) for the two highest-stakes fixes (`GAP-002`, `GAP-006`).

## Verdict summary

| Gap | Verdict |
|---|---|
| GAP-002 | **FULLY FIXED.** Real path-prefix/containment matching (`_is_inside`, `policy_engine.py`), verified against all required cases including the SPEC-03 regression case and a `~/.ssh2` false-positive guard, by hand and by live execution. New tests are genuine. No caveats. |
| GAP-006 | **FULLY FIXED as literally claimed.** `VerificationService` now runs `Check.command` via `asyncio.create_subprocess_shell` (async-safe, not blocking), compares real exit codes, and `EventBridge.handle()` genuinely gates `AGENT_REVIEW -> HUMAN_REVIEW` on the result — confirmed by live reproduction reaching both `FAILED` and `HUMAN_REVIEW` correctly for failing/passing required checks. Two new residual issues found during this verification are logged below as `GAP-020`/`GAP-021` rather than counted against this gap's literal scope. |
| GAP-008 | **FULLY FIXED.** `reconciliation_service.py` and its test were deleted outright; zero dangling references; `docs/NEXT_STEPS.md` carries an honest, explicit Phase 2 deferral with rationale. Clean removal, not a partial fix. |
| GAP-009 | **FULLY FIXED.** `python-json-logger` removed from `pyproject.toml`, `uv.lock`, and the dependency doc; `uv sync` actually uninstalled it from the venv, confirming the edit is effective, not cosmetic. |
| GAP-012 | **FULLY FIXED.** `task_service.py` now does a genuine content-diff (`existing.profile_json != project_profile_json`) before logging `project_profile_updated`; new test file covers creation, no-op reuse, and genuine change. |
| GAP-016 | **FULLY FIXED.** `progress.md` now lists and checks off all 20 original tasks (commit hashes spot-checked against real commit subjects) plus both review gap-closure rounds as follow-up notes. |

`docs/NEXT_STEPS.md`'s "134 passed, ruff clean" claim was independently re-run and confirmed exact.

## New findings surfaced during this verification (not part of any prior REVIEW-NNN)

While verifying `GAP-006`, two issues turned up that weren't in scope for that gap's literal claim but are real
and worth tracking — logged as `GAP-020` and `GAP-021` in `reviews/GAPS.md`.

### GAP-020 (HIGH, new) — `VerificationService.forbidden_path_check` has the same subpath-bypass bug GAP-002 just fixed, in a sibling location

`services/verification_service.py`'s own `forbidden_path_check` block still does:
```python
touched_paths = set(contract.inputs + contract.deliverables)
forbidden_touches = touched_paths & set(completion.forbidden_path_check.paths)
```
— plain set intersection over exact string equality. Reproduced live: `inputs=["~/.ssh/id_rsa"]` against
`forbidden_path_check.paths=["~/.ssh"]` → `passed=True`, not caught. What makes this notable: the *same
commit* (`116d95a`) added a correct `_is_prefixed_by()` helper and used it correctly for the neighboring
`scope_check.forbidden_paths` comparison a few lines below in the same function — but never applied it to
`forbidden_path_check.paths` just above. This is lower stakes than the original GAP-002 (PolicyEngine's check
runs pre-execution and is the primary gate; this one is a post-execution completion check that would, in
practice, usually be defense-in-depth once GAP-002's fix is in place for the same declared `inputs`/
`deliverables` fields) — hence `HIGH` rather than `CRITICAL` — but it's a real, live, currently-unfixed
instance of the exact vulnerability class both prior reviews were chasing.

### GAP-021 (MEDIUM, new) — the verification failure-gating path has no automated regression test

The behavior itself is correct (confirmed by live reproduction in this review): a failing required check
correctly routes the task to `FAILED` rather than `HUMAN_REVIEW`. But no test in `tests/test_event_bridge.py`
or `tests/test_phase1_smoke.py` ever attaches a real `completion_contract` before triggering `landing:completed`
— every existing `EventBridge` test has an empty/absent contract, so the verification-gating branch is always
skipped by the checked-in suite. `tests/test_verification_service.py` tests the failing-check logic at the
`VerificationService` unit level but never through `EventBridge.handle()`, so it never asserts on the resulting
`Task.state`. A future refactor could silently break this gating and nothing in CI would catch it.

## Recommendation

Per `AGENTS.md`'s gate rule, `GAP-020` (HIGH) keeps Phase 1 not-yet-done until closed. `GAP-021` (MEDIUM) is a
test-debt item, not itself a gate blocker, but should not be left indefinitely given it guards a governance-
relevant behavior (verification actually gating HUMAN_REVIEW).

## Verification

`cd src/governance_controller && uv run pytest -q` → 134 passed. `uv run ruff check .` → clean. This review
did not modify any `governance_controller` application code — only `reviews/GAPS.md` and this file.
