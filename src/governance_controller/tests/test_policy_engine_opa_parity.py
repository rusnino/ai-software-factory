"""Differential parity tests between the embedded PolicyEngine and OPA.

OPA is intended to be a parity layer, not a downgrade. These tests run real
`opa eval` invocations against the checked-in `governance.rego` and assert that
its decisions match the embedded engine's decisions for the same input.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest

from governance_controller.constants import ApprovalType
from governance_controller.schemas import (
    Check,
    CompletionContract,
    ProjectProfile,
    RepositoryConfig,
    ScopeCheck,
)
from governance_controller.schemas.project_profile import (
    ProjectExecutionConfig,
    SecurityConfig,
)
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.policy_engine import PolicyEngine
from governance_controller.services.policy_engine_backend import PolicyEngineBackend

REPO_ROOT = Path(__file__).resolve().parents[1]
REGO_PATH = REPO_ROOT / "policies" / "opa" / "governance.rego"


def _find_opa() -> str | None:
    """Return the path to a usable `opa` binary, or None."""
    for candidate in ["/tmp/opencode/opa", shutil.which("opa")]:
        if candidate and Path(candidate).exists():
            return candidate
    return None


OPA_PATH = _find_opa()


@pytest.fixture
def opa_path() -> str:
    if OPA_PATH is None:
        pytest.skip("opa binary not available")
    return OPA_PATH


def _make_contract(command: str) -> TaskContract:
    return TaskContract(
        task_id="T-1",
        project_id="P-1",
        proposed_by="agent",
        objective="Verify OPA parity",
        acceptance=["parity holds"],
        execution=ExecutionConfig(harness="opencode", role="worker"),
        completion_contract=CompletionContract(
            task_id="T-1",
            required=[Check(type="command", command=command)],
            scope_check=ScopeCheck(description="OPA parity"),
        ),
    )


def _make_profile() -> ProjectProfile:
    return ProjectProfile(
        project_id="P-1",
        repository=RepositoryConfig(path="/repo"),
        execution=ProjectExecutionConfig(allowed_harnesses=["opencode"]),
        security=SecurityConfig(forbidden_paths=[]),
    )


def _opa_decision(opa_path: str, opa_input: dict[str, Any]) -> dict[str, Any]:
    """Run `opa eval` and return the approve decision document."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
        json.dump(opa_input, fh)
        input_path = fh.name
    try:
        proc = subprocess.run(
            [
                opa_path,
                "eval",
                "-d",
                str(REGO_PATH),
                "data.governance.approve",
                "--input",
                input_path,
                "--format",
                "raw",
            ],
            capture_output=True,
            check=False,
        )
    finally:
        Path(input_path).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"opa eval failed: {proc.stderr.decode(errors='replace')}\n"
            f"input: {json.dumps(opa_input)}"
        )
    return cast(dict[str, Any], json.loads(proc.stdout.decode()))


def _evaluate_both(opa_path: str, command: str) -> tuple[bool, bool]:
    """Return (embedded_allowed, opa_allowed) for a single command."""
    contract = _make_contract(command)
    profile = _make_profile()
    embedded = PolicyEngine.evaluate(
        contract, profile, ApprovalType.EXECUTION
    )
    opa_input = PolicyEngineBackend._minimal_opa_input(
        contract, profile, ApprovalType.EXECUTION, actor="human"
    )
    opa = _opa_decision(opa_path, opa_input)
    return embedded.allowed, opa.get("allow", False)


@pytest.mark.parametrize(
    "command",
    [
        "sed 's/foo/bar/w /tmp/data/dump.bin' input.txt",
        "sed 's/foo/bar/W /var/lib/out.dat' input.txt",
        "sed -n 's@foo@bar@w/tmp/x' input.txt",
        "sed 's/foo/bar/e' input.txt",
        "sed -n 's/foo/w bar/g' input.txt",
        "sed -e 's/foo/bar/g' file.txt",
        "sed '/skip/s/foo/bar/w /tmp/a/b/out.bin' file.txt",
        "sed '/season/s/foo/bar/w /tmp/a/b/out.bin' file.txt",
        "sed '/skip/s/foo/bar/W /tmp/a/b/out.bin' file.txt",
        "sed '/skip/s/foo/bar/ep' file.txt",
        "sed '/miss/s/foo/bar/w /tmp/a/b/out.bin' file.txt",
        "sed -n '/skip/s/foo/bar/w /tmp/a/b/out.bin' file.txt",
        "sed '/skip/,/end/s/foo/bar/w /tmp/a/b/out.bin' file.txt",
    ],
)
def test_opa_matches_embedded_on_sed_substitution_flags(
    opa_path: str, command: str
) -> None:
    """#340/#345: OPA must not silently allow sed write/RCE paths."""
    embedded_allowed, opa_allowed = _evaluate_both(opa_path, command)
    assert opa_allowed == embedded_allowed, (
        f"OPA/embedded divergence for {command!r}: "
        f"embedded={embedded_allowed}, opa={opa_allowed}"
    )


def test_opa_denies_multi_segment_sed_write_path(opa_path: str) -> None:
    """Regression: OPA previously allowed sed 's/x/y/w /a/b/c'."""
    command = "sed 's/x/y/w /a/b/c' input.txt"
    embedded_allowed, opa_allowed = _evaluate_both(opa_path, command)
    assert embedded_allowed is False
    assert opa_allowed is False


@pytest.mark.parametrize(
    "command",
    [
        "uv run --quiet rm -rf /",
        "uv run --python /tmp/evil pytest",
        "uv run --quiet totally-arbitrary-binary",
        "uv run --no-project pytest",
        "uv run --with requests arbitrary-binary",
        "uv run --directory /tmp arbitrary-binary",
        "uv run --active arbitrary-binary",
        "uv run --frozen arbitrary-binary",
        "uv run -q -- pytest",
    ],
)
def test_opa_matches_embedded_on_uv_run_flag_prefixed_child(
    opa_path: str, command: str
) -> None:
    """#344: OPA must not allow uv run <flag> <unlisted child> bypass."""
    embedded_allowed, opa_allowed = _evaluate_both(opa_path, command)
    assert opa_allowed == embedded_allowed, (
        f"OPA/embedded divergence for {command!r}: "
        f"embedded={embedded_allowed}, opa={opa_allowed}"
    )


def test_opa_denies_uv_run_flag_prefixed_unlisted_child(
    opa_path: str,
) -> None:
    """Regression: OPA previously allowed uv run --quiet rm -rf /."""
    command = "uv run --quiet rm -rf /"
    embedded_allowed, opa_allowed = _evaluate_both(opa_path, command)
    assert embedded_allowed is False
    assert opa_allowed is False


@pytest.mark.parametrize(
    "command",
    [
        "sed '/start/,/end/w /tmp/a/b/out.bin' file.txt",
        "sed '10,/end/w /tmp/out.bin' file.txt",
        "sed '0,/end/w /tmp/out.bin' file.txt",
        "sed '/start/,/end/r /etc/passwd' file.txt",
        "sed '/start/,/end/W /tmp/out.bin' file.txt",
        "sed '/start/,/end/!w /tmp/out.bin' file.txt",
    ],
)
def test_opa_matches_embedded_on_sed_direct_two_address_range(
    opa_path: str, command: str
) -> None:
    """#347: OPA must not allow two-address-range sed r/w/R/W direct commands."""
    embedded_allowed, opa_allowed = _evaluate_both(opa_path, command)
    assert opa_allowed == embedded_allowed, (
        f"OPA/embedded divergence for {command!r}: "
        f"embedded={embedded_allowed}, opa={opa_allowed}"
    )


@pytest.mark.parametrize(
    "command",
    [
        "sed '3r /tmp/2024/out.bin' file.txt",
        "sed '3w /tmp/2024/out.bin' file.txt",
        "sed '3s/foo/bar/w /tmp/2024/out.bin' file.txt",
        "sed '1w /tmp/file1.txt' file.txt",
        "sed '3,5w /tmp/2024/out.bin' file.txt",
        "sed '3,/end/w /tmp/2024/out.bin' file.txt",
        "sed '3s/a1/b/w /tmp/out.bin' file.txt",
    ],
)
def test_opa_matches_embedded_on_sed_numeric_address_with_later_digit(
    opa_path: str, command: str
) -> None:
    """#351: _sed_skip_digits must stop at the first non-digit, not jump ahead."""
    embedded_allowed, opa_allowed = _evaluate_both(opa_path, command)
    assert opa_allowed == embedded_allowed, (
        f"OPA/embedded divergence for {command!r}: "
        f"embedded={embedded_allowed}, opa={opa_allowed}"
    )


def test_opa_denies_sed_direct_two_address_range_write(opa_path: str) -> None:
    """Regression: OPA previously allowed sed '/start/,/end/w /a/b/c'."""
    command = "sed '/start/,/end/w /tmp/a/b/out.bin' file.txt"
    embedded_allowed, opa_allowed = _evaluate_both(opa_path, command)
    assert embedded_allowed is False
    assert opa_allowed is False
