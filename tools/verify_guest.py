#!/usr/bin/env python3
"""Verify a provisioned deburner integration-test guest."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

FAILURES: list[str] = []
PASSES: list[str] = []


def record(condition: bool, description: str, details: str = "") -> None:
    if condition:
        PASSES.append(description)
        print(f"PASS: {description}")
        return
    suffix = f": {details}" if details else ""
    FAILURES.append(f"{description}{suffix}")
    print(f"FAIL: {description}{suffix}")


def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=False, capture_output=True, text=True, timeout=60)


def command_succeeds(arguments: list[str], description: str) -> None:
    result = run(arguments)
    details = (result.stderr or result.stdout).strip()
    record(result.returncode == 0, description, details)


def command_exists(command: str) -> None:
    record(shutil.which(command) is not None, f"command is installed: {command}")


def file_exists(path: str, *, executable: bool = False) -> None:
    candidate = Path(path)
    condition = candidate.exists()
    if executable:
        condition = condition and os.access(candidate, os.X_OK)
    record(condition, f"{'executable' if executable else 'file'} exists: {path}")


def service_state(unit: str, *, active: str, enabled: set[str]) -> None:
    active_result = run(["systemctl", "is-active", unit])
    enabled_result = run(["systemctl", "is-enabled", unit])
    actual_active = active_result.stdout.strip()
    actual_enabled = enabled_result.stdout.strip()
    record(actual_active == active, f"{unit} is {active}", actual_active)
    record(actual_enabled in enabled, f"{unit} enablement is {sorted(enabled)}", actual_enabled)


def verify_platform() -> None:
    os_release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", maxsplit=1)
            os_release[key] = value.strip('"')
    record(os_release.get("ID") == "debian", "guest operating system is Debian")
    record(os_release.get("VERSION_ID") == "13", "guest major version is 13")
    record(platform.machine() == "x86_64", "guest architecture is x86_64")
    record(Path("/usr/bin/gnome-shell").is_file(), "GNOME desktop baseline is installed")


def verify_hardening() -> None:
    service_state("apparmor.service", active="active", enabled={"enabled"})
    service_state("deburner-firewall.service", active="active", enabled={"enabled"})
    service_state("ssh.service", active="inactive", enabled={"masked"})
    service_state("ssh.socket", active="inactive", enabled={"masked"})
    command_succeeds(
        ["nft", "list", "table", "inet", "deburner"], "deburner nftables table is loaded"
    )

    expected_sysctls = {
        "kernel.kptr_restrict": "2",
        "kernel.dmesg_restrict": "1",
        "fs.protected_hardlinks": "1",
        "fs.protected_symlinks": "1",
        "fs.protected_fifos": "1",
        "fs.protected_regular": "2",
        "net.ipv4.conf.all.accept_redirects": "0",
        "net.ipv4.conf.default.accept_redirects": "0",
        "net.ipv4.conf.all.send_redirects": "0",
        "net.ipv4.conf.default.send_redirects": "0",
        "net.ipv6.conf.all.accept_redirects": "0",
        "net.ipv6.conf.default.accept_redirects": "0",
        "net.ipv4.tcp_syncookies": "1",
    }
    for key, expected in expected_sysctls.items():
        result = run(["sysctl", "-n", key])
        record(
            result.returncode == 0 and result.stdout.strip() == expected, f"sysctl {key}={expected}"
        )

    sockets = run(["ss", "-H", "-ltn"]).stdout.splitlines()
    record(
        not any(line.split()[3].rsplit(":", maxsplit=1)[-1] == "22" for line in sockets),
        "TCP port 22 is not listening",
    )
    auditd = run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}", "auditd"])
    record(not auditd.stdout.startswith("ii "), "auditd is not installed")


def verify_docker() -> None:
    service_state("docker.service", active="active", enabled={"enabled"})
    command_succeeds(["docker", "version"], "Docker client can reach the daemon")
    command_succeeds(["docker", "compose", "version"], "Docker Compose plugin works")
    try:
        configuration = json.loads(Path("/etc/docker/daemon.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        record(False, "Docker daemon configuration is valid JSON", str(error))
    else:
        record(configuration.get("log-driver") == "local", "Docker uses the local log driver")
        record(configuration.get("live-restore") is True, "Docker live restore is enabled")


def verify_customization() -> None:
    passwd = run(["getent", "passwd", "debian"])
    record(passwd.returncode == 0, "customization user exists", passwd.stderr.strip())
    groups = run(["id", "-nG", "debian"])
    record(
        groups.returncode == 0 and "sudo" in groups.stdout.split(),
        "customization user belongs to sudo",
        groups.stdout.strip(),
    )
    command_succeeds(
        ["runuser", "-u", "debian", "--", "sudo", "-n", "true"],
        "customization user has passwordless sudo",
    )
    file_exists("/etc/systemd/logind.conf.d/90-deburner-lid-switch.conf")
    file_exists("/home/debian/.vimrc")
    file_exists("/home/debian/.vim/colors/monokai.vim")
    file_exists("/home/debian/.bash_aliases")
    file_exists("/home/debian/bin/fzf-preview.sh", executable=True)
    file_exists("/home/debian/.config/zed/settings.json")
    file_exists("/home/debian/.ssh/config")
    record(Path("/home/debian/.ssh/cm_socket").is_dir(), "SSH multiplexing directory exists")
    command_succeeds(["bash", "-n", "/home/debian/.bashrc"], "Bash configuration is valid")
    command_succeeds(["bash", "-n", "/home/debian/.bash_aliases"], "Bash aliases are valid")

    gsettings = run(
        [
            "runuser",
            "-u",
            "debian",
            "--",
            "env",
            "HOME=/home/debian",
            "dbus-run-session",
            "--",
            "gsettings",
            "get",
            "org.gnome.desktop.interface",
            "color-scheme",
        ]
    )
    record(
        gsettings.returncode == 0 and gsettings.stdout.strip() == "'prefer-dark'",
        "GNOME prefers the dark color scheme",
        (gsettings.stderr or gsettings.stdout).strip(),
    )


def verify_tooling() -> None:
    commands = [
        "7z",
        "binwalk",
        "batcat",
        "burpsuite",
        "cargo",
        "certipy",
        "checksec",
        "chromium",
        "curl",
        "exiftool",
        "fdfind",
        "ffuf",
        "fzf",
        "gdb",
        "gdb-multiarch",
        "ghidra",
        "gimp",
        "gobuster",
        "go",
        "hashcat",
        "htop",
        "hydra",
        "impacket-GetUserSPNs",
        "impacket-ntlmrelayx",
        "impacket-psexec",
        "impacket-secretsdump",
        "impacket-wmiexec",
        "john",
        "jwt_tool",
        "ltrace",
        "ldapsearch",
        "meld",
        "netexec",
        "nikto",
        "nmap",
        "nxc",
        "nxcdb",
        "openvpn",
        "pwn",
        "pycdas",
        "pycdc",
        "r2",
        "responder",
        "rg",
        "rustc",
        "rust-analyzer",
        "sqlite3",
        "smbclient",
        "sqlmap",
        "strace",
        "tcpdump",
        "testssl.sh",
        "tshark",
        "uv",
        "vol",
        "volatility2",
        "wg",
        "wl-copy",
        "yara",
        "ysoserial",
        "zed",
    ]
    for command in commands:
        command_exists(command)

    file_exists("/usr/local/bin/binaryninja", executable=True)
    file_exists("/opt/peda/peda.py")
    file_exists("/etc/gdb/gdbinit.d/peda.gdb")
    file_exists("/opt/burpsuite-community/burpsuite-community.jar")
    file_exists("/opt/jwt_tool/jwt_tool.py")
    file_exists("/opt/nikto/program/nikto.pl")
    file_exists("/opt/sqlmap/sqlmap.py")
    file_exists("/opt/testssl.sh/testssl.sh")
    file_exists("/opt/ysoserial/ysoserial-all.jar")
    file_exists("/usr/share/seclists/README.md")
    record(not Path("/srv/deburner/mirror").exists(), "offline mirror was not synchronized")


def verify_test_profile() -> None:
    try:
        profile = json.loads(Path("/opt/deburner/.test-profile.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        record(False, "integration-test profile is readable", str(error))
        return

    bloodhound_enabled = profile.get("bloodhound_enabled") is True
    marker = Path("/var/lib/deburner/bloodhound-ce-installed")
    if not bloodhound_enabled:
        record(not marker.exists(), "disabled BloodHound profile was not staged")
        return

    command_exists("bloodhound-cli")
    file_exists(str(marker))
    credentials = Path("/root/.config/bloodhound/deburner-initial-install.txt")
    file_exists(str(credentials))
    if credentials.exists():
        record(
            credentials.stat().st_mode & 0o777 == 0o600,
            "BloodHound initial credentials are mode 0600",
        )

    images = run(["docker", "image", "ls", "--format", "{{.Repository}}"])
    repositories = images.stdout.splitlines()
    record(
        images.returncode == 0 and any("bloodhound" in item.lower() for item in repositories),
        "BloodHound container image is staged",
        (images.stderr or images.stdout).strip(),
    )
    running = run(["docker", "ps", "--format", "{{.Image}}"])
    running_images = running.stdout.splitlines()
    record(
        running.returncode == 0
        and not any("bloodhound" in item.lower() for item in running_images),
        "BloodHound application container is stopped",
        (running.stderr or running.stdout).strip(),
    )


def main() -> int:
    if os.geteuid() != 0:
        print("FAIL: guest verification must run as root", file=sys.stderr)
        return 1
    verify_platform()
    verify_hardening()
    verify_docker()
    verify_customization()
    verify_tooling()
    verify_test_profile()
    print(f"\nVerification summary: {len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        for failure in FAILURES:
            print(f" - {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
