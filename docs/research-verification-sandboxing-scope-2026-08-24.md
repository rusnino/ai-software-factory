# Research Note: Scoping Docker Sandboxing for Verification Execution

Date: 2026-08-24
Status: Scoping estimate only. Not started, not adopted for any phase. Recorded per the
ADR-001/RISK-13 precedent (document, defer, don't forget).

## 1. Why this is being scoped now

Five consecutive full-Phase-1 review rounds (issues #107-#150) found a recurring defect class in
`governance_controller/services/policy_engine.py`: an allowlisted verification-command binary
(`bash`/`env`/`xargs` wrappers, `find -delete`, `docker run -v /:/host`, `git -c core.sshCommand`,
`tar --to-command`, `sed 1e ...`, `curl -K`, `dpkg -i`) turned out to have its own embedded
execution/indirection primitive that argv-string inspection couldn't see. Each round's fix closed
that specific instance; the next round found a new one on a different binary. Issue #147 named this
explicitly: **build-tool invocation (`make`, `npm install`, `pip install`, `cargo`, `mvn`, `gradle`)
is an inherent, argv-level-unmitigable risk** — the danger lives in file content the task's own
worktree supplies, not in the command line, so no denylist/allowlist refinement can ever close it.
`specs/SPEC-08-security.md §8.7` now carries this as a documented Phase 1 limitation.

The user asked, in conversation, what sandboxing is actually for and asked for a work estimate to
scope it as a candidate task — this note is that estimate, not a decision to build it.

## 2. What sandboxing would change

`policy_engine.py`'s current approach tries to prove a command *can't* be malicious before it runs
(argv-string allow/deny). Sandboxing accepts that some command *will* eventually be malicious,
buggy, or bypass a check, and bounds what it can do: no network by default, no filesystem access
outside the task's own worktree, non-root, resource/time limits. The two approaches are
complementary, not alternatives — sandboxing does not replace `policy_engine.py`, which still cheaply
rejects obviously-bad commands before a container is even started, and still governs concerns
unrelated to execution isolation (self-approval, forbidden paths in *declared* `inputs`/`deliverables`,
harness/role allowlists).

## 3. Current baseline

- `VerificationService._run_check` (`services/verification_service.py`) runs
  `Check.command` via `asyncio.create_subprocess_shell` **directly inside the Governance Controller's
  own process**, in the task's worktree `cwd` when one can be found, with a filtered env
  (`_SAFE_ENV_KEYS`). No isolation beyond that filtering exists today.
- `specs/SPEC-08-security.md §8.2` already has an isolation-level table: Level 1 (git worktree) is
  Phase 1 baseline, Level 3 (Docker container) is assigned to **Phase 3**, Level 4
  (Firecracker/Kata/gVisor) to Phase 4. Docker sandboxing for verification is therefore already
  planned, just not yet scoped or scheduled — this note provides the scoping.
- The Governance Controller's own `Dockerfile`/`docker-compose.yml` run it as a plain, non-root
  container with **no Docker socket** — consistent with `SPEC-08 §8.4`'s "no Docker socket unless
  project explicitly allows." This matters directly for the architecture decision below.
- `adapters/macro_agent/executor.py::MacroAgentExecutor.start()` already forwards a `sandbox`
  parameter (default `"worktree"`) to macro-agent for the *task execution* itself — but this is a
  separate lifecycle from *verification*. Verification's `CompletionContract` checks are not routed
  through macro-agent at all today; whether they should be is architecture-decision option C below.

## 4. Architecture decision required before implementation

This determines the estimate more than any other single factor and should be resolved (via a short
spike, not full implementation) before committing to a number.

| Option | Description | Trade-off |
|---|---|---|
| **A. Docker socket in the Controller (DooD)** | Mount `/var/run/docker.sock` into the Controller's own container; it launches sibling containers directly. | Simplest to implement, but the component whose entire job is enforcing "no Docker socket access by default" (`SPEC-08 §8.4/8.7`) would itself require exactly that capability — an uncomfortable self-exception worth avoiding if a cleaner option exists. |
| **B. Dedicated sandbox-executor sidecar** | A separate service holds the only Docker-socket-bearing process; the Controller calls it over HTTP/gRPC to run a check and get back exit code/stdout/stderr. | Cleanest privilege separation, matches the project's existing adapter pattern (Plane/macro-agent are already separate services the Controller talks to). New service to design, deploy, and secure. |
| **C. Delegate to macro-agent's own sandbox** | Ask macro-agent (which already accepts a `sandbox` param for task execution) to also run verification commands in its existing isolation. | Smallest Controller-side footprint if macro-agent already exposes a suitable "run one command, return result" API — **unverified**; needs a spike against macro-agent's actual API surface before this option can be sized at all. Couples verification availability to macro-agent's uptime/API stability (already an accepted dependency per RISK-02/08/15). |

Option B is the most consistent with this project's existing separation-of-concerns pattern (Plane is
a separate adapter, macro-agent is a separate adapter) and with `AGENTS.md`'s "do not reimplement
macro-agent internals" — a sidecar is not macro-agent internals, it's a new, narrowly-scoped service.
Option A is fastest but contradicts the project's own default-deny stance on the exact capability
being granted. Option C could be cheapest of all, or could be blocked entirely, depending on
macro-agent's actual API — this is the first thing to check.

## 5. Work breakdown and estimate (assuming option B or C)

| Block | Estimate |
|---|---|
| Spike + architecture decision (confirm option A/B/C, check macro-agent's API for option C), update `SPEC-08`/`SPEC-10` | 0.5-1 day |
| Sandbox container image (minimal set of the binaries that survived #107-#150's allowlist: `uv`, `npm`, `pytest`, `ruff`, `git`, etc.) + run wrapper | 1-2 days |
| `VerificationService` integration: replace `create_subprocess_shell` with a container-run call — bind-mount the worktree, non-root, read-only rootfs, resource/pids limits, timeout enforcement via container stop/kill (replacing the current `os.killpg` process-group logic) | 2-4 days |
| Network policy: `--network=none` by default is straightforward; a real domain-allowlist egress proxy (closing the already-tracked `SPEC-08 §8.5`/#143 gap) is a separate, larger task | 0.5-1 day (none) / +2-3 days (allowlist proxy) |
| Deployment/CI: Docker-daemon access for the Controller or the new sidecar, image build/publish pipeline, a third test-matrix dimension (sandboxed vs. direct execution, alongside the existing SQLite-vs-Postgres dimension) | 1-2 days |
| Buffer for edge cases in exactly this code path — `_run_check`'s cwd/env/timeout logic has been the subject of real bugs across #109/#118/#122/#128/#132 | 2-3 days |
| **Total** | **~1.5-3 weeks**, solo engineer, for a solid Docker-level v1 (no network by default) |

Firecracker/gVisor (Phase 4) and a full domain-allowlist egress proxy are explicitly out of scope for
this estimate and would each add their own separate scoping pass later.

## 6. Recommendation

- Do not schedule this for Phase 2. `SPEC-10 §10.3` already places Docker sandboxing in Phase 3;
  pulling it forward would be a deliberate phase-plan change, not a default.
- Before writing any implementation code, run the architecture spike (option A vs. B vs. C) — it's
  the single biggest lever on the estimate and should be resolved cheaply first.
- Keep `policy_engine.py`'s checks regardless of when/whether sandboxing lands — they remain the
  cheap first filter and continue to cover concerns sandboxing doesn't touch.

## Sources

- `specs/SPEC-08-security.md` §8.2, §8.4, §8.5, §8.7
- `specs/SPEC-10-phase-plan.md` §10.3
- `src/governance_controller/governance_controller/services/verification_service.py`
- `src/governance_controller/governance_controller/adapters/macro_agent/executor.py`
- `src/governance_controller/Dockerfile`, `docker-compose.yml`
- GitHub Issues #107-#150 (five review rounds' worth of allowlist-bypass findings, `rusnino/ai-software-factory`)
