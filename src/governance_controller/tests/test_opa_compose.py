import json
import subprocess
from pathlib import Path

COMPOSE_FILE = Path(__file__).parents[1] / "docker-compose.yml"


def _compose_config() -> dict[str, object]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "opa",
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_opa_compose_service_is_runnable_and_opt_in() -> None:
    config = _compose_config()
    service = config["services"]["opa"]

    assert service["image"] == "openpolicyagent/opa:1.19.1"
    assert service["profiles"] == ["opa"]
    assert "/policy" in service["command"]

    policy_mounts = [
        volume
        for volume in service["volumes"]
        if (volume.get("target") == "/policy")
    ]
    assert policy_mounts
    assert policy_mounts[0]["read_only"] is True

    healthcheck = service["healthcheck"]["test"]
    assert healthcheck[0] == "CMD"
    assert "CMD-SHELL" not in healthcheck
    assert all(token not in {"sh", "wget", "curl"} for token in healthcheck)
