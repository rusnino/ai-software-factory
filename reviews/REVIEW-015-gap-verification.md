# REVIEW-015: Verification of REVIEW-014's GAP-062 fix

Date: 2026-08-18
Scope: `GAP-062` — the sole gap left open after `REVIEW-014` (`POST /events`'s body-size limit only
checked the declared `Content-Length` header, not actual bytes read from the stream). Method: same
maximum-skepticism live reproduction as prior rounds, driving the real ASGI application directly
(constructing `scope`/`receive`/`send` by hand, not going through `httpx`) so the exact three bypass
angles `REVIEW-014` demonstrated could be replayed against the current code without any test-harness
convenience masking the result.

## Verdict: GENUINELY FIXED

`e6553e2` replaced the header-only `BaseHTTPMiddleware` with a raw ASGI middleware
(`EventBodySizeLimitMiddleware` in `governance_controller/api/events.py`) that reads and buffers the
actual `http.request` messages itself, accumulating `total_bytes` from the real message bodies (not
the header), and rejects with `413` the moment that running total exceeds 64 KiB — regardless of what
`Content-Length` claims or whether the header is present at all. Only after buffering completes does
it replay the buffered messages to the FastAPI app via a `_replay_receive` closure.

Replayed all three of `REVIEW-014`'s bypass scenarios directly against `governance_controller.main.app`
(hand-built ASGI scope, no `Content-Length` shortcuts from a test client):

| Scenario | REVIEW-014 result (before fix) | REVIEW-015 result (after fix) |
|---|---|---|
| ~100 KB body, no `Content-Length` header, sent as 2 chunks | `204` (bypass) | **`413`** |
| ~100 KB body, dishonest `Content-Length: 10` | `204` (bypass) | **`413`** |
| ~100 KB body, honest large `Content-Length` | `413` (already worked) | **`413`** (still works) |
| normal small event (sanity check, no regression) | `204` | **`204`** (against a real SQLite-backed `get_db()`, audit row confirmed written) |

No regression on the happy path, and all three previously-demonstrated bypasses are now closed.
`uv run pytest -q` → 186 passed (includes a new
`test_post_event_rejects_oversized_body_without_content_length` test). `uv run ruff check .` → clean.
`uv run mypy governance_controller` → 0 errors.

## New, minor finding: `GAP-072` (LOW, ACCEPTED)

The fix's inline comment contains a marker: `# ponytail: buffers the entire body in memory up to the
cap; streaming handling would need a different approach...`. Initially flagged as an unexplained
artifact (no other occurrence anywhere in the repo, no linked issue). The user clarified this is a
known harness skill applied in the neighboring agent's session, not a stray/unintentional marker —
logged as `GAP-072`, `ACCEPTED`, for the record only.

## Milestone

With `GAP-062` closed, every gap opened by `REVIEW-013` (`GAP-057` through `GAP-071`) is now
genuinely fixed and verified by live reproduction, not just diff review. `reviews/GAPS.md`'s gate
rule is satisfied — no `CRITICAL`/`HIGH` row is `OPEN`, and no row is `OPEN` at any severity;
`GAP-072` is `ACCEPTED`, not an outstanding task.

## Verification

All reproduction was done via throwaway Python scripts driving the real ASGI app object directly
(not the checked-in test suite, though the suite was also run for its own sake) — no application or
test code was modified by this review. `git status` confirmed clean before and after.
