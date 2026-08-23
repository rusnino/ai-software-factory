# SDD ledger — plan: 2026-08-18-phase-1-governance-controller-poc

## Task Status

- [x] Task 1: Project Scaffold and Dependencies (commits b798ba5..aea6bd7, review clean)
- [x] Task 2: Configuration and Database Setup (commit 00d6ed5)
- [x] Task 3: Task and State Machine Models (commit 63bc63e)
- [x] Task 4: Task Contract / Project Profile Schemas (commit e807da8)
- [x] Task 5: Approval Service and Policy Engine (commit 97f2757)
- [x] Task 6: Audit Log (commit 401cf62)
- [x] Task 7: REST API Endpoints (commit 0f9ee9a)
- [x] Task 8: Plane Adapter Interface + Stub (commit 7c8943a)
- [x] Task 9: Harness Provider Registry (commit 7195140)
- [x] Task 10: macro-agent Executor Abstraction (commit 9f352fb)
- [x] Task 11: Trigger Execution After Approval (commit da81276)
- [x] Task 12: opentasks Materialization Stub (commit 5aa5fa5)
- [x] Task 13: Minimal Event Bridge Listener (commit 409460b)
- [x] Task 14: Docker Compose for Local Dev (commit aaeb52d)
- [x] Task 15: Health Check Endpoint (commit 8fba214)
- [x] Task 16: CLI / Telegram Approval Stubs (commit cbed263)
- [x] Task 17: Verification Stub (commit eae0462)
- [x] Task 18: Permission Model Stub (commit 86cfb6a)
- [x] Task 19: Git-Cascade Landing Stub (commit 8d62030)
- [x] Task 20: End-to-End Smoke Test (commit 4902944)
- [x] Review 1 gap closure (commits 08f1171..31b6dfa)
- [x] Review 2 gap closure (commits 14d11da..fa94ad5)
- [x] Review 3 (REVIEW-003) gap closure — round-2 verification, durable commit fix (commit cb7e0ce)
- [x] Review 4 (REVIEW-004) full fresh review — atomic row lock / version guard for approvals (commits b411b58..e96dedf)
- [x] Review 5 (REVIEW-005) gap re-fixes — command allowlist, event deduplication, Telegram auth (commits aff57f6..1887c4c)
- [x] Review 6/7 (REVIEW-006/007) gap fixes — verification commands, downstream atomic CAS, EventBridge route, docker-compose, harness role deny (commits 8c1b8a1..5fce7c4)
- [x] Review 8/9 (REVIEW-008/009) gap re-fixes — durable rejection audit commit, _trigger_execution commit-before-raise (commits 983c82c..20321f3)
- [x] Review 10/11/12 (REVIEW-010/011/012) gap re-fix verification — regression tests for _trigger_execution commit-before-raise sites (commits 3f45649..345a6e4)
- [x] Review 13 (REVIEW-013) full fresh review — path normalization, verification commit, timeout caps, 409/503 mapping, uvicorn dependency, event body limit, remove opentasks stub, log level, missing event types (commits f75c33d..9471d51)
- [x] Review 14 (REVIEW-014) verification — 14/15 REVIEW-013 gaps closed, GAP-062 body-size limit reopened (docs commit 749b898)
- [x] Review 15 (REVIEW-015) verification — GAP-062's second fix (byte-counting middleware) closed for real (docs commit 259da97)
- [x] Review 16 (REVIEW-016) full fresh review — CRITICAL Postgres timezone defect (GAP-078), GAP-079/080 concurrency bugs found (docs commit 3fb8628, gap closures 0222ce7)
- [x] Review 17 (REVIEW-017) verification — GAP-078/079/073/074 confirmed fixed against live Postgres; GAP-080's first fix attempt found NOT genuinely fixed, reopened (docs commit 17a76b0)
- [x] Review 18 (REVIEW-018) verification — GAP-080's second fix, GAP-082/083 confirmed genuinely fixed; audit found dual-DB test-coverage bypass (GAP-084) (bundled with fix commit ee80cd6)
- [x] Review 19 (REVIEW-019) full fresh review — GAP-077's fix found incomplete (3 bugs); GAP-085/086/087 new HIGH findings (idempotency-key collision, middleware DoS, retry never restarting execution) (docs commit 74d2218)
- [x] Review 20 (REVIEW-020) verification — 5 of 6 REVIEW-019 gaps closed; GAP-077 narrowed to event-replay bug, found worse than originally reported (docs commit 85679df)
- [x] Review 21 (REVIEW-021) verification — GAP-077/091 closed; GAP-093 concurrency race analyzed as not exploitable; accidentally found GAP-094 (SQLite pool-kwargs regression) while verifying (docs commit 541837d)
- [x] Review 22 (REVIEW-022) verification — GAP-094 closed; gap ledger reached zero OPEN/IN_PROGRESS rows for the first time (docs commit b79ac5a)
- [x] Review 23 (REVIEW-023) full fresh review — gate reopened: GAP-095/096/097 new HIGH (verification-lock/timeout, permission-blocklist bypass, retry dedup-on-exception); GAP-098-101/103/104/106 also found and closed; GAP-105 (SDD ledger staleness) initially marked ACCEPTED, later backfilled here instead per REVIEW-024's re-examination (docs commit ccf3617, gap closures 33ebf6b/59be68c)
- [x] GitHub Issues migration — gap tracking moved from `reviews/GAPS.md`/`REVIEW-NNN-*.md` to GitHub Issues on `rusnino/ai-software-factory` (commit c857b92); frozen `reviews/` banners corrected to actual freeze date 2026-08-23
- [x] Issues #107-#115 round — shell-command newline bypass, approval idempotency scope, verification isolation, scope check enforcement, 403 mapping, security-flag forwarding, review banner dates, hash-chain/mypy fix (commits 5ab5ea1..25dfeb6)
- [x] Issues #116-#125 round — shlex-based argv policy validation, audit hash-chain FOR UPDATE locking, missing-worktree fallback + conditional event dedup, AuditLog startup migration + DB-level immutability triggers, Check.command NUL/control-char validation, verification env HOME scoping, Telegram constant-time secret comparison, NEXT_STEPS/progress ledger updates

## Preflight Rulings

Ruling: Task 1 was completed in an isolated git worktree; merged manually via fast-forward to main to avoid worktree/merge conflicts. Cost if wrong: history could diverge; mitigated by verifying commit chain before merge.
Ruling: For subsequent tasks, work directly in main without per-task branches to avoid worktree merge friction and increase velocity. Cost if wrong: less isolation between tasks; mitigated by small task scope and per-task review via subagent self-test + final whole-branch review.

## Notes

Phase 1 scope: OpenCode + Claude Code harnesses, Plane adapter as memory stub, no runtime Plane dependency.
