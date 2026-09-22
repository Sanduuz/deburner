"""Exercise configuration validation with isolated positive and negative cases."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ConfigCase:
    name: str
    values: dict[str, Any]
    succeeds: bool
    marker: str


CASES = (
    ConfigCase(
        name="default configuration",
        values={},
        succeeds=True,
        marker="Configuration from local.yml is valid.",
    ),
    ConfigCase(
        name="enabled optional profiles",
        values={
            "customization_user": "ctfuser",
            "docker_group_users": ["ctfuser"],
            "hardening_allowed_tcp_ports": [443, 8080],
            "hardening_allowed_udp_ports": [51820],
            "tooling_bloodhound_enabled": True,
            "tooling_mobile_android_studio_enabled": True,
            "tooling_mobile_android_licenses_accepted": True,
            "offline_mirror_enabled": True,
            "offline_mirror_root": "/srv/deburner/test-mirror",
            "tooling_core_packages": ["curl", "git"],
        },
        succeeds=True,
        marker="BloodHound=True, Android Studio=True, offline mirror=True.",
    ),
    ConfigCase(
        name="unknown variable",
        values={"offline_miror_enabled": True},
        succeeds=False,
        marker="unsupported or misspelled variables: offline_miror_enabled",
    ),
    ConfigCase(
        name="quoted Boolean",
        values={"offline_mirror_enabled": "true"},
        succeeds=False,
        marker="offline_mirror_enabled must be the YAML Boolean true or false.",
    ),
    ConfigCase(
        name="invalid firewall port",
        values={"hardening_allowed_tcp_ports": [0]},
        succeeds=False,
        marker="Firewall ports must be YAML integers between 1 and 65535.",
    ),
    ConfigCase(
        name="root Docker group user",
        values={"docker_group_users": ["root"]},
        succeeds=False,
        marker="Docker group users must be unique, valid, non-root local account names.",
    ),
    ConfigCase(
        name="Android license not accepted",
        values={
            "tooling_mobile_android_studio_enabled": True,
            "tooling_mobile_android_licenses_accepted": False,
        },
        succeeds=False,
        marker="Android Studio is enabled",
    ),
    ConfigCase(
        name="invalid mirror path",
        values={"offline_mirror_root": "/opt/deburner/mirror"},
        succeeds=False,
        marker="The offline mirror must use a valid path below /srv",
    ),
    ConfigCase(
        name="empty package override",
        values={"tooling_core_packages": []},
        succeeds=False,
        marker="tooling_core_packages must be a non-empty list",
    ),
)


def run_case(
    repository: Path,
    test_repository: Path,
    ansible_playbook: str,
    temporary_root: Path,
    case: ConfigCase,
) -> tuple[bool, str]:
    config_path = test_repository / "local.yml"
    config_path.write_text(json.dumps(case.values, indent=2, sort_keys=True) + "\n")

    environment = os.environ.copy()
    environment.update(
        {
            "ANSIBLE_LOCAL_TEMP": str(temporary_root / "ansible-local"),
            "ANSIBLE_REMOTE_TEMP": str(temporary_root / "ansible-remote"),
            "ANSIBLE_NOCOLOR": "1",
        }
    )
    result = subprocess.run(
        [
            ansible_playbook,
            "--inventory",
            str(repository / "inventory.ini"),
            str(test_repository / "validate-config.yml"),
        ],
        cwd=repository,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=60,
    )

    expected_return = result.returncode == 0 if case.succeeds else result.returncode != 0
    marker_present = case.marker in result.stdout
    if expected_return and marker_present:
        return True, ""

    expectation = "success" if case.succeeds else "validation failure"
    details = (
        f"expected {expectation} containing {case.marker!r}, "
        f"received exit code {result.returncode}\n{result.stdout}"
    )
    return False, details


def create_test_repository(repository: Path, destination: Path) -> None:
    destination.mkdir()
    shutil.copy2(repository / "validate-config.yml", destination / "validate-config.yml")
    for source in sorted(repository.glob("roles/*/defaults/main.yml")):
        target = destination / source.relative_to(repository)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def main() -> int:
    repository = Path(__file__).resolve().parent.parent
    ansible_playbook = shutil.which("ansible-playbook")
    if ansible_playbook is None:
        print("ansible-playbook is required; run this target through the locked uv environment.")
        return 2

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="deburner-config-tests-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        test_repository = temporary_root / "repository"
        create_test_repository(repository, test_repository)
        for case in CASES:
            try:
                passed, details = run_case(
                    repository,
                    test_repository,
                    ansible_playbook,
                    temporary_root,
                    case,
                )
            except subprocess.TimeoutExpired:
                passed = False
                details = "configuration validation exceeded the 60-second case timeout"
            if passed:
                print(f"PASS: {case.name}")
            else:
                print(f"FAIL: {case.name}")
                failures.append(f"{case.name}: {details}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"All {len(CASES)} configuration validation cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
