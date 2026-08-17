# SPEC-09: Verification and Review

## 9.1 Verification Pipeline

After macro-agent landing completes, Controller triggers verification:

1. **Deterministic checks** (Completion Contract):
   - tests
   - lint
   - type check
   - forbidden path check
   - secret scan
   - scope check

2. **Reviewer agent**:
   - Read-only evaluation of intent/correctness.
   - Verdict: PASS / FAIL with reasoning.

3. **Human review**:
   - Final gate before merge.
   - UI: Plane task in `HUMAN_REVIEW` state.

## 9.2 Completion Contract Execution

Controller runs each check in isolated environment:

```python
class CompletionContractRunner:
    async def run(task_contract: TaskContract, project_profile: ProjectProfile) -> VerificationResult:
        ...
```

Result:

```yaml
task_id: TASK-42
execution_id: EXEC-123
status: pass | fail
 checks:
  - type: test
    command: uv run pytest
    status: pass
    output: "..."
  - type: lint
    command: uv run ruff check .
    status: fail
    output: "..."
summary: "lint failed"
```

## 9.3 Reviewer Agent

- Read-only access to repository, diff, test results, Task Contract.
- Cannot edit source or merge.
- Returns verdict with evidence.
- Triggered only after Completion Contract passes.

## 9.4 Human Review

Plane task transitions to `HUMAN_REVIEW`:

- Human sees: description, diff, PR, CI results, reviewer verdict.
- Human approves merge via Plane UI or direct Controller endpoint.
- Controller records merge approval and authorizes merge.

## 9.5 Merge Authorization

Controller performs merge:

- only after `HUMAN_REVIEW` approval;
- only if Project Profile allows automated merge;
- otherwise creates PR and leaves merge to human.

## 9.6 Failure Handling

If verification fails:

1. Controller transitions task to retry if retries remain.
2. Macro-agent receives failure feedback.
3. After retry limit, mark `FAILED` and alert human.

## 9.7 CI Integration

Preferred CI for self-host:

- Woodpecker CI
- Gitea Actions
- GitHub Actions (if already on GitHub)

CI triggered by PR/push from macro-agent.
