# REVIEW-014: Verification of REVIEW-013's 15 gap fixes

Date: 2026-08-18
Scope: all 15 gaps opened by `REVIEW-013` (`GAP-057` through `GAP-071`). Method: maximum-skepticism
live reproduction against the real, current code for every technical fix — not trusting the diff,
commit message, or checked-in test suite alone, consistent with this series' established practice.
I personally re-verified the two most severe claims (`GAP-057` CRITICAL, `GAP-058` HIGH) directly;
three parallel agents verified the remaining twelve (one agent's first run failed mid-response on a
connection error and was relaunched from scratch — its retry is what's reported here).

## Verdict: 14 of 15 GENUINELY FIXED; 1 (`GAP-062`) PARTIALLY FIXED — reopened

All `CRITICAL`/`HIGH` gaps are closed. The phase-gate rule (`reviews/GAPS.md`'s own rule: no
`CRITICAL`/`HIGH` may stay `OPEN` while a phase is declared complete) is satisfied for the first
time since `REVIEW-013` reopened it. One `MEDIUM` finding, `GAP-062`, is not fully fixed and has been
reopened to `IN_PROGRESS`.

### CRITICAL — confirmed fixed

**GAP-057** — personally re-verified. `governance_controller/utils/paths.py::normalize_path()` now
expands `~`, anchors relative paths at `/`, and runs `os.path.normpath()` before every prefix/equality
comparison in both `policy_engine.py::_is_inside` and `verification_service.py::_is_prefixed_by`.
Live-reproduced the exact original bypass against the current code:
```
contract.inputs=['src/../../srv/production/secrets.env']  → PolicyEngine.evaluate(): allowed=False,
  violations=['Task touches forbidden path: src/../../srv/production/secrets.env', ...]
```
and the same input against `VerificationService.verify_execution()` with a `CompletionContract`
(`forbidden_path_check.paths=['/srv/production','~/.ssh']`) → `passed=False`,
`forbidden_paths: failed`. Both paths that previously let the traversal through now correctly reject
it. Genuinely fixed.

### HIGH — all confirmed fixed

**GAP-058** — personally re-verified by reading `verification_service.py:236-258` directly: `await
db.commit()` now sits between the `AuditService.log(...)` call and the `raise ValueError(...)`,
mirroring `_trigger_execution`'s pattern exactly, with an explanatory comment.

**GAP-059** — verified live: a `TaskContract.execution.timeout_minutes=60`/`max_retries=5` against a
`ProjectProfile.execution` cap of `30`/`1` both correctly produced `allowed=False` with specific
violation messages (`"...exceeds project cap..."`); equal/under values passed. Both fields are
covered (a new "execution resource caps" section in `PolicyEngine.evaluate()`), not just one.

**GAP-060** — verified live against the real ASGI app with a faithful per-request session lifecycle
(matching production's `get_db()`): first `POST /tasks` for a given `task_id` → 201, second → 409
with a sensible detail message, and a follow-up `GET` confirmed the original row survived untouched.
One test-suite fidelity note (not a production bug): the checked-in test's DB fixture shares one
uncommitted session across the whole test, which the verifying agent found rolls back the *first*
task's flush too when the duplicate's `IntegrityError` triggers a rollback — a blind spot in the test
harness's realism, not in the fix itself, and the checked-in test never re-asserts the original task
is still fetchable afterward. Worth a future test-fixture hardening pass, not gap-worthy on its own
given the fix is independently confirmed correct against the real session lifecycle.

**GAP-061** — verified by actually building and running the Docker image: `docker build -t gc-verify
-f Dockerfile .` succeeded with `uvicorn==0.52.3` now installed, and `docker run --rm gc-verify`
started uvicorn normally, failing only on the expected `OSError` from asyncpg unable to reach a
nonexistent Postgres in the sandbox — the original "executable file not found" failure mode is gone.

### MEDIUM

**GAP-062 — REOPENED (`IN_PROGRESS`), only partially fixed.** `EventBodySizeLimitMiddleware`
(`api/events.py`) checks `request.headers.get("content-length")` against 64 KiB, but never caps
actual bytes read from the ASGI stream. Live-reproduced three ways against the real app: (1) an
honest large `Content-Length` header → correctly `413`; (2) the identical ~100 KB body sent via a
streamed/chunked request with **no** `Content-Length` header → `204`, fully parsed and handed to
`EventBridge.handle()`; (3) the same body sent with a **dishonest** `Content-Length: 10` → also
`204`, fully processed. Any client that doesn't cooperate with the header check sails through
untouched — the resource-exhaustion risk `GAP-062` was meant to close is still live. There's also no
server-level backstop (`Dockerfile` runs plain `uvicorn` with no request-size limit flags), and the
checked-in regression test only exercises the honest-header path (httpx's `json=` sets
`Content-Length` automatically), so it structurally cannot catch this. Needs either a real
byte-counting read of the stream (reject once bytes-read exceeds the cap, regardless of what the
header claims) or a uvicorn/reverse-proxy-level limit instead of an app-level header check.

**GAP-063** — genuinely fixed. `opentasks_service.py` and its test are deleted from disk; a
whole-repo `grep` for any reference returns nothing; `pytest`/`mypy` both clean after removal.

**GAP-064** — genuinely fixed. `api/approvals.py` now maps `RuntimeError` to `503`. Live-reproduced
end-to-end: a task driven through approval with a mocked macro-agent-start `RuntimeError` returns
HTTP `503` with a sensible detail, and the DB afterward shows `Task.state=FAILED`, the `Execution`
row correctly marked `FAILED`/`ended_at`, and 8 durable audit rows — confirming this fix doesn't
regress the GAP-031/GAP-044 durability guarantees it sits next to.

### LOW

**GAP-066** — genuinely fixed. `Settings.log_level` plus a module-import-time
`structlog.configure(...)` call. Live-verified with `GC_LOG_LEVEL=warning` vs `=debug` env vars in
separate fresh processes: `structlog.get_config()`'s `wrapper_class` correctly reflects each level,
and debug logs are filtered/emitted accordingly. No double-configuration risk (module-cache
singleton behavior, confirmed correct not buggy).

**GAP-067** — genuinely fixed, and unusually thoroughly checked: the verifying agent read SPEC-05
§5.4's actual 9-event table (not just trusting the gap description), confirmed `_EVENT_TO_STATE` now
has all 9 mapped exactly per spec, and live-drove `EventBridge.handle()` through scenarios the
checked-in suite doesn't cover (a same-state no-op, an out-of-order/invalid predecessor correctly
rejected with an audit trail, a legitimate predecessor correctly transitioning, and a terminal-state
event correctly rejected) — no crashes, no silently-wrong transitions in any case.

**GAP-071** — genuinely fixed. All 13 newly-cited commit hashes in the backfilled `progress.md`
verified to be real commits (not fabricated), with prose summaries cross-checked against the actual
`REVIEW-004` through `REVIEW-013` files and matched 1:1, including `REVIEW-013`'s own 11-gap fix
list. No fabrication or mismatch found.

**GAP-065/068/069/070** — not re-verified this round; these were direct documentation/ledger fixes I
made myself in `REVIEW-013` (`REQUIREMENTS.md`, `SPEC-10` footnote, `NEXT_STEPS.md` endpoint bullet,
and the accepted git-hygiene note), untouched by the neighboring agent's fixes, so there is nothing
new to verify.

## Verification

`cd src/governance_controller && uv run pytest -q` → 185 passed (confirmed by two of the three
verifying agents independently). `uv run ruff check .` and `uv run mypy governance_controller` both
clean (confirmed by the GAP-063 verification, which specifically re-ran both after a file deletion).
No application or test code was modified by this review — all verification was live reproduction via
throwaway scripts/requests against the real ASGI app, real DB sessions, and a real Docker build;
every verifying agent confirmed a clean `git status` after finishing.

## Milestone

With `GAP-057` through `GAP-061` (the only `CRITICAL`/`HIGH` gaps `REVIEW-013` opened) all confirmed
genuinely fixed, `reviews/GAPS.md`'s gate rule is satisfied again: no `CRITICAL`/`HIGH` row is
currently `OPEN`. `GAP-062` (`MEDIUM`) remains open and is reflected in `docs/NEXT_STEPS.md`'s current
state summary — Phase 1 should not be described as having "zero open gaps" until it closes too.
