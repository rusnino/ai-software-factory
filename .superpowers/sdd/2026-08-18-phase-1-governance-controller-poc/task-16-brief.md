# Task 16 Brief: CLI / Telegram Approval Stub

**Goal:** Add a minimal CLI command and a no-op Telegram approval stub so the Controller has multiple inbound approval paths converging on `POST /approvals`.

**Files to create:**
- `src/governance_controller/governance_controller/cli.py`
- `src/governance_controller/tests/test_cli.py`
- `src/governance_controller/governance_controller/adapters/telegram.py`

**Exact values to use verbatim:**

CLI tool: `typer` app in `governance_controller.cli`.
Command: `approve`
Arguments/options:
- `task_id: str`
- `--type`: choice `["plan", "execution", "merge"]`, default `"execution"`
- `--actor`: default `"cli-user"`
- `--source`: default `"cli"`
- `--base-url`: default `"http://localhost:8000"`
- `--comment`: optional

CLI behavior:
- Build `ApprovalRequest` payload.
- POST to `{base_url}/approvals` using `httpx`.
- Print `Approved {task_id}: {state}`.
- Exit with code 1 on HTTP error.

Telegram adapter:
- `TelegramAdapter` class with `async def process_update(update: dict) -> dict`.
- If `update["message"]["text"]` starts with `/approve {task_id} {type}`, POST to Controller `/approvals`.
- Otherwise return `{"status": "ignored"}`.
- Use `httpx.AsyncClient`.

**Constraints:**
- No real Telegram bot polling/long-polling in Phase 1; adapter only.
- No secrets in code; token should come from env (not used in stub).

**Verification steps:**
1. Write `tests/test_cli.py` covering:
   - CLI approve invokes httpx post with correct payload.
   - CLI handles HTTP error.
2. Write `tests/test_telegram_adapter.py` (or add to `test_cli.py`) covering:
   - `/approve TASK-1 execution` sends request.
   - unrelated message returns ignored.
3. Run `uv run pytest tests/test_cli.py tests/test_telegram_adapter.py -v`.
4. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add CLI and Telegram approval stubs`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-16-report.md`
