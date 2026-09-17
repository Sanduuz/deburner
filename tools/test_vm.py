#!/usr/bin/env python3
"""Manage the local libvirt domain used for deburner integration tests."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

VM_NAME_PATTERN = re.compile(r"^deburner-test(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?$")
MARKER_NAME = ".deburner-test-vm.json"
CACHE_MARKER_NAME = ".deburner-image-cache.json"
CACHE_METADATA_NAME = "current.json"
IMAGE_FILENAME = "debian-13-genericcloud-amd64.qcow2"
REQUIRED_COMMANDS = (
    "git",
    "passt",
    "qemu-img",
    "virsh",
    "virt-install",
    "virt-xml-validate",
    "xorriso",
)
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
MAX_CHECKSUM_MANIFEST_SIZE = 1024 * 1024


class VmError(RuntimeError):
    """A safe, user-facing VM lifecycle error."""


@dataclass(frozen=True)
class Config:
    name: str
    uri: str
    state_root: Path
    cache_root: Path
    results_root: Path
    image_url: str
    checksums_url: str
    memory_mib: int
    vcpus: int
    disk_gib: int

    @property
    def state_dir(self) -> Path:
        return self.state_root / self.name

    @property
    def disk_path(self) -> Path:
        return self.state_dir / "disk.qcow2"

    @property
    def xml_path(self) -> Path:
        return self.state_dir / "domain.xml"

    @property
    def marker_path(self) -> Path:
        return self.state_dir / MARKER_NAME

    @property
    def seed_path(self) -> Path:
        return self.state_dir / "seed.iso"

    @property
    def user_data_path(self) -> Path:
        return self.state_dir / "user-data"

    @property
    def metadata_path(self) -> Path:
        return self.state_dir / "meta-data"

    @property
    def source_tree_path(self) -> Path:
        return self.state_dir / "source-tree"

    @property
    def source_iso_path(self) -> Path:
        return self.state_dir / "source.iso"

    @property
    def results_dir(self) -> Path:
        return self.results_root / self.name

    @property
    def cache_marker_path(self) -> Path:
        return self.cache_root / CACHE_MARKER_NAME

    @property
    def cache_metadata_path(self) -> Path:
        return self.cache_root / CACHE_METADATA_NAME


def run(
    arguments: list[str],
    *,
    check: bool = True,
    capture_output: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command and turn failures into concise lifecycle errors."""
    try:
        process = subprocess.run(
            arguments,
            check=False,
            capture_output=capture_output,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise VmError(f"{' '.join(arguments)} timed out after {timeout} seconds") from error
    except OSError as error:
        raise VmError(f"Unable to run {arguments[0]}: {error}") from error
    if check and process.returncode != 0:
        details = (process.stderr or process.stdout or "command failed").strip()
        raise VmError(f"{' '.join(arguments)} failed: {details}")
    return process


def virsh(config: Config, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run virsh against the configured connection."""
    return run(["virsh", "--connect", config.uri, *arguments], check=check)


def validate_config(config: Config) -> None:
    """Reject unsafe names, locations, and resource values."""
    if not VM_NAME_PATTERN.fullmatch(config.name):
        raise VmError("VM names must be 'deburner-test' or start with 'deburner-test-'.")
    if config.memory_mib < 2048:
        raise VmError("VM memory must be at least 2048 MiB.")
    if not 1 <= config.vcpus <= 64:
        raise VmError("VM CPU count must be between 1 and 64.")
    if config.disk_gib < 16:
        raise VmError("VM disk size must be at least 16 GiB.")
    repository = Path(__file__).resolve().parent.parent
    for label, directory in (
        ("VM state", config.state_root),
        ("image cache", config.cache_root),
        ("test results", config.results_root),
    ):
        resolved = directory.resolve()
        try:
            resolved.relative_to(repository)
        except ValueError as error:
            raise VmError(f"The {label} directory must remain inside the repository.") from error
        if resolved == repository:
            raise VmError(f"The repository root cannot be used as the {label} directory.")

    image_url = urlparse(config.image_url)
    checksums_url = urlparse(config.checksums_url)
    if image_url.scheme != "https" or not image_url.netloc:
        raise VmError("The Debian image URL must use HTTPS.")
    if checksums_url.scheme != "https" or not checksums_url.netloc:
        raise VmError("The Debian checksum URL must use HTTPS.")
    if Path(image_url.path).name != IMAGE_FILENAME:
        raise VmError(f"The test image must be named {IMAGE_FILENAME}.")
    if Path(checksums_url.path).name != "SHA512SUMS":
        raise VmError("The checksum URL must point to SHA512SUMS.")


def check_commands() -> None:
    """Ensure all host-side virtualization commands are installed."""
    missing = [command for command in REQUIRED_COMMANDS if shutil.which(command) is None]
    if missing:
        raise VmError(f"Missing host commands: {', '.join(missing)}")


def check_connection(config: Config) -> None:
    """Ensure libvirt is reachable and KVM is available to this user."""
    check_commands()
    virsh(config, "uri")
    os_variants = run(["virt-install", "--osinfo", "list"]).stdout.splitlines()
    if not any(line.split(",", maxsplit=1)[0].strip() == "debian13" for line in os_variants):
        raise VmError("virt-install does not provide the required debian13 OS definition.")
    kvm = Path("/dev/kvm")
    if not kvm.exists():
        raise VmError("/dev/kvm is unavailable; hardware virtualization is required.")
    if not os.access(kvm, os.R_OK | os.W_OK):
        raise VmError("The current user cannot access /dev/kvm; check membership in the kvm group.")


def domain_names(config: Config) -> set[str]:
    """Return all domains visible through the configured libvirt connection."""
    result = virsh(config, "list", "--all", "--name")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def domain_exists(config: Config) -> bool:
    return config.name in domain_names(config)


def domain_state(config: Config) -> str:
    result = virsh(config, "domstate", config.name)
    return result.stdout.strip().lower()


def is_running(config: Config) -> bool:
    return domain_state(config) not in {"shut off", "shutoff", "crashed"}


def write_marker(config: Config) -> None:
    marker = {
        "disk": str(config.disk_path),
        "name": config.name,
        "uri": config.uri,
    }
    config.marker_path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n")


def validate_marker(config: Config) -> None:
    if not config.marker_path.is_file():
        raise VmError(f"Refusing cleanup: safety marker is missing from {config.state_dir}.")
    try:
        marker = json.loads(config.marker_path.read_text())
    except (json.JSONDecodeError, OSError) as error:
        raise VmError("Refusing cleanup: the VM safety marker is invalid.") from error
    expected = {
        "disk": str(config.disk_path),
        "name": config.name,
        "uri": config.uri,
    }
    if marker != expected:
        raise VmError("Refusing cleanup: the VM safety marker does not match this configuration.")


def write_cache_marker(config: Config) -> None:
    marker = {"kind": "deburner-test-image-cache", "version": 1}
    config.cache_marker_path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n")


def validate_cache_marker(config: Config) -> None:
    if not config.cache_marker_path.is_file():
        raise VmError(f"Refusing cache removal: safety marker is missing from {config.cache_root}.")
    try:
        marker = json.loads(config.cache_marker_path.read_text())
    except (json.JSONDecodeError, OSError) as error:
        raise VmError(
            "Refusing cache removal: the image-cache safety marker is invalid."
        ) from error
    if marker != {"kind": "deburner-test-image-cache", "version": 1}:
        raise VmError("Refusing cache removal: the image-cache safety marker is unexpected.")


def request(url: str) -> Request:
    return Request(url, headers={"User-Agent": "deburner-test-vm/1"})


def read_checksum_manifest(config: Config) -> str:
    try:
        with urlopen(request(config.checksums_url), timeout=30) as response:
            content = response.read(MAX_CHECKSUM_MANIFEST_SIZE + 1)
    except (HTTPError, URLError, TimeoutError) as error:
        raise VmError(f"Unable to download {config.checksums_url}: {error}") from error
    if len(content) > MAX_CHECKSUM_MANIFEST_SIZE:
        raise VmError("The Debian checksum manifest exceeds the 1 MiB safety limit.")
    try:
        return content.decode("ascii")
    except UnicodeDecodeError as error:
        raise VmError("The Debian checksum manifest is not ASCII text.") from error


def image_checksum(manifest: str) -> str:
    matches = []
    for line in manifest.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].removeprefix("*") == IMAGE_FILENAME:
            matches.append(parts[0])
    if len(matches) != 1 or not re.fullmatch(r"[0-9a-f]{128}", matches[0]):
        raise VmError(f"SHA512SUMS does not contain exactly one valid entry for {IMAGE_FILENAME}.")
    return matches[0]


def hash_file(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as source:
        while chunk := source.read(DOWNLOAD_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def cached_image_path(config: Config, checksum: str) -> Path:
    return config.cache_root / f"debian-13-genericcloud-amd64-{checksum}.qcow2"


def download_image(config: Config, destination: Path, checksum: str) -> None:
    temporary = destination.with_name(f".{destination.name}.part-{os.getpid()}")
    digest = hashlib.sha512()
    downloaded = 0
    try:
        with (
            urlopen(request(config.image_url), timeout=60) as response,
            temporary.open("xb") as output,
        ):
            while chunk := response.read(DOWNLOAD_CHUNK_SIZE):
                output.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if downloaded % (64 * DOWNLOAD_CHUNK_SIZE) < DOWNLOAD_CHUNK_SIZE:
                    print(f"Downloaded {downloaded // DOWNLOAD_CHUNK_SIZE} MiB...", flush=True)
    except FileExistsError as error:
        raise VmError(f"Temporary download already exists: {temporary}") from error
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        temporary.unlink(missing_ok=True)
        raise VmError(f"Unable to download {config.image_url}: {error}") from error

    if digest.hexdigest() != checksum:
        temporary.unlink(missing_ok=True)
        raise VmError("The downloaded Debian image does not match its published SHA-512 checksum.")
    os.replace(temporary, destination)


def write_cache_metadata(config: Config, image: Path, checksum: str) -> None:
    metadata = {
        "checksum": checksum,
        "checksum_algorithm": "sha512",
        "checksums_url": config.checksums_url,
        "image": str(image),
        "image_url": config.image_url,
    }
    temporary = config.cache_metadata_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, config.cache_metadata_path)


def prepare_image(config: Config) -> Path:
    validate_config(config)
    manifest = read_checksum_manifest(config)
    checksum = image_checksum(manifest)
    image = cached_image_path(config, checksum)
    config.cache_root.mkdir(parents=True, exist_ok=True)
    write_cache_marker(config)

    if image.exists():
        print(f"Verifying cached image {image.name}...")
        if hash_file(image) != checksum:
            image.unlink()
            print("The cached image was corrupt and has been removed.")
            print(f"Downloading {IMAGE_FILENAME} from Debian...")
            download_image(config, image, checksum)
    else:
        print(f"Downloading {IMAGE_FILENAME} from Debian...")
        download_image(config, image, checksum)

    write_cache_metadata(config, image, checksum)
    print(f"Verified Debian 13 image: {image}")
    return image


def read_cache_metadata(config: Config) -> dict[str, object]:
    if not config.cache_metadata_path.is_file():
        raise VmError("No cached Debian image metadata exists; run make test-image first.")
    try:
        metadata = json.loads(config.cache_metadata_path.read_text())
    except (json.JSONDecodeError, OSError) as error:
        raise VmError("The cached Debian image metadata is invalid.") from error
    if not isinstance(metadata, dict):
        raise VmError("The cached Debian image metadata is invalid.")
    return metadata


def image_prepare(config: Config) -> None:
    prepare_image(config)


def image_status(config: Config) -> None:
    validate_config(config)
    metadata = read_cache_metadata(config)
    checksum = metadata.get("checksum")
    image_value = metadata.get("image")
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{128}", checksum):
        raise VmError("The cached Debian image checksum is invalid.")
    if not isinstance(image_value, str):
        raise VmError("The cached Debian image path is invalid.")
    image = Path(image_value)
    if image != cached_image_path(config, checksum) or not image.is_file():
        raise VmError("The cached Debian image is missing or stored at an unexpected path.")
    if hash_file(image) != checksum:
        raise VmError("The cached Debian image does not match its recorded SHA-512 checksum.")
    print(json.dumps(metadata, indent=2, sort_keys=True))
    print("Cached image checksum verified.")


def image_purge(config: Config) -> None:
    validate_config(config)
    check_commands()
    virsh(config, "uri")
    if not config.cache_root.exists():
        print("No Debian image cache exists.")
        return
    validate_cache_marker(config)
    test_domains = sorted(name for name in domain_names(config) if name.startswith("deburner-test"))
    if test_domains:
        raise VmError("Refusing cache removal while test domains exist: " + ", ".join(test_domains))
    shutil.rmtree(config.cache_root)
    print(f"Removed Debian image cache {config.cache_root}.")


def build_domain_xml(config: Config) -> str:
    """Ask virt-install to construct host-compatible domain XML."""
    command = [
        "virt-install",
        "--connect",
        config.uri,
        "--name",
        config.name,
        "--metadata",
        "description=Disposable deburner integration-test VM",
        "--memory",
        str(config.memory_mib),
        "--vcpus",
        str(config.vcpus),
        "--cpu",
        "host-passthrough",
        "--arch",
        "x86_64",
        "--virt-type",
        "kvm",
        "--machine",
        "q35",
        "--import",
        "--disk",
        f"path={config.disk_path},format=qcow2,bus=virtio,cache=none",
        "--disk",
        f"path={config.seed_path},device=cdrom,readonly=on",
        "--disk",
        f"path={config.source_iso_path},device=cdrom,readonly=on",
        "--network",
        "passt,model=virtio",
        "--graphics",
        "spice,listen=none",
        "--video",
        "virtio",
        "--channel",
        "unix,target_type=virtio,name=org.qemu.guest_agent.0",
        "--console",
        "pty,target_type=serial",
        "--rng",
        "/dev/urandom",
        "--boot",
        "hd",
        "--osinfo",
        "debian13",
        "--noautoconsole",
        "--print-xml",
    ]
    return run(command).stdout


def create_source_iso(config: Config) -> None:
    """Package the public working tree and an isolated test configuration."""
    repository = Path(__file__).resolve().parent.parent
    relative_paths = repository_source_paths(repository)

    config.source_tree_path.mkdir()
    for relative_path in relative_paths:
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise VmError(f"Git reported an unsafe source path: {relative_path}")
        source = repository / relative_path
        if source.is_symlink():
            raise VmError(f"Refusing a symbolic link in the test payload: {relative_path}")
        if not source.is_file():
            raise VmError(f"Test payload source is not a regular file: {relative_path}")
        destination = config.source_tree_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    (config.source_tree_path / "local.yml").write_text(
        "---\n"
        "# Generated only for the disposable integration-test guest.\n"
        "customization_user: debian\n"
        "offline_mirror_enabled: false\n"
    )
    run(
        [
            "xorriso",
            "-as",
            "mkisofs",
            "-output",
            str(config.source_iso_path),
            "-volid",
            "DEBURNER_SRC",
            "-joliet",
            "-rock",
            "-graft-points",
            f"/={config.source_tree_path}",
        ]
    )
    shutil.rmtree(config.source_tree_path)


def repository_source_paths(repository: Path) -> list[Path]:
    listing = run(
        [
            "git",
            "-C",
            str(repository),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ]
    ).stdout
    relative_paths = [Path(value) for value in listing.split("\0") if value]
    if not relative_paths:
        raise VmError("Git did not report any source files for the test payload.")
    return relative_paths


def create_cloud_init_seed(config: Config) -> None:
    """Create a NoCloud seed that enables the private guest-agent channel."""
    config.user_data_path.write_text(
        "#cloud-config\n"
        f"hostname: {config.name}\n"
        "manage_etc_hosts: true\n"
        "package_update: true\n"
        "packages:\n"
        "  - ansible\n"
        "  - qemu-guest-agent\n"
        "  - task-gnome-desktop\n"
        "growpart:\n"
        "  mode: auto\n"
        "  devices: ['/']\n"
        "resize_rootfs: true\n"
        "runcmd:\n"
        "  - [mkdir, -p, /mnt/deburner-source, /opt/deburner]\n"
        "  - [mount, -o, ro, -L, DEBURNER_SRC, /mnt/deburner-source]\n"
        "  - [cp, -a, /mnt/deburner-source/., /opt/deburner/]\n"
        "  - [systemctl, enable, --now, qemu-guest-agent.service]\n"
    )
    config.metadata_path.write_text(f"instance-id: {config.name}\nlocal-hostname: {config.name}\n")
    run(
        [
            "xorriso",
            "-as",
            "mkisofs",
            "-output",
            str(config.seed_path),
            "-volid",
            "cidata",
            "-joliet",
            "-rock",
            str(config.user_data_path),
            str(config.metadata_path),
        ]
    )


def decode_agent_output(value: object, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise VmError(f"The guest agent returned invalid {label} data.")
    try:
        return base64.b64decode(value, validate=True).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError) as error:
        raise VmError(f"The guest agent returned invalid base64 {label} data.") from error


def agent_command(config: Config, payload: dict[str, object]) -> object:
    result = virsh(
        config,
        "qemu-agent-command",
        config.name,
        "--timeout",
        "10",
        json.dumps(payload, separators=(",", ":")),
    )
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise VmError("The QEMU guest agent returned invalid JSON.") from error
    if not isinstance(response, dict) or "return" not in response:
        raise VmError("The QEMU guest agent returned an unexpected response.")
    return response["return"]


def guest_write_file(config: Config, destination: str, content: bytes) -> None:
    response = agent_command(
        config,
        {
            "execute": "guest-file-open",
            "arguments": {"path": destination, "mode": "w"},
        },
    )
    if not isinstance(response, int):
        raise VmError("The QEMU guest agent did not return a file handle.")
    handle = response
    try:
        for offset in range(0, len(content), 32 * 1024):
            chunk = content[offset : offset + 32 * 1024]
            result = agent_command(
                config,
                {
                    "execute": "guest-file-write",
                    "arguments": {
                        "handle": handle,
                        "buf-b64": base64.b64encode(chunk).decode("ascii"),
                    },
                },
            )
            if not isinstance(result, dict) or result.get("count") != len(chunk):
                raise VmError(f"The guest agent did not fully write {destination}.")
        agent_command(
            config,
            {"execute": "guest-file-flush", "arguments": {"handle": handle}},
        )
    finally:
        agent_command(
            config,
            {"execute": "guest-file-close", "arguments": {"handle": handle}},
        )


def refresh_source(config: Config) -> None:
    """Refresh a preserved VM from the public working tree through QGA."""
    validate_config(config)
    check_connection(config)
    if not domain_exists(config) or not is_running(config):
        raise VmError(f"Domain {config.name!r} is not running.")
    validate_marker(config)

    repository = Path(__file__).resolve().parent.parent
    relative_paths = repository_source_paths(repository)
    directories = sorted({f"/opt/deburner/{path.parent}" for path in relative_paths})
    require_guest_command(config, "/usr/bin/mkdir", ["-p", *directories])
    for relative_path in relative_paths:
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise VmError(f"Git reported an unsafe source path: {relative_path}")
        source = repository / relative_path
        if source.is_symlink() or not source.is_file():
            raise VmError(f"Test payload source is not a regular file: {relative_path}")
        destination = f"/opt/deburner/{relative_path}"
        guest_write_file(config, destination, source.read_bytes())
        mode = format(source.stat().st_mode & 0o777, "04o")
        require_guest_command(config, "/usr/bin/chmod", [mode, destination])
    print(f"Refreshed {len(relative_paths)} source files in the preserved guest.")


def guest_exec(
    config: Config,
    path: str,
    arguments: list[str] | None = None,
    *,
    environment: dict[str, str] | None = None,
    timeout: int = 300,
) -> tuple[int, str, str]:
    request_arguments: dict[str, object] = {"path": path, "capture-output": True}
    if arguments:
        request_arguments["arg"] = arguments
    if environment:
        request_arguments["env"] = [f"{key}={value}" for key, value in sorted(environment.items())]
    response = agent_command(
        config,
        {"execute": "guest-exec", "arguments": request_arguments},
    )
    if not isinstance(response, dict) or not isinstance(response.get("pid"), int):
        raise VmError("The QEMU guest agent did not return a process ID.")
    pid = response["pid"]

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = agent_command(
            config,
            {"execute": "guest-exec-status", "arguments": {"pid": pid}},
        )
        if not isinstance(status, dict):
            raise VmError("The QEMU guest agent returned an invalid process status.")
        if status.get("exited"):
            if status.get("out-truncated") or status.get("err-truncated"):
                raise VmError("The QEMU guest agent truncated command output.")
            exit_code = status.get("exitcode")
            if not isinstance(exit_code, int):
                signal = status.get("signal", "unknown")
                raise VmError(f"Guest command terminated by signal {signal}.")
            return (
                exit_code,
                decode_agent_output(status.get("out-data"), "stdout"),
                decode_agent_output(status.get("err-data"), "stderr"),
            )
        time.sleep(0.5)
    raise VmError(f"Guest command {path} timed out after {timeout} seconds.")


def require_guest_command(
    config: Config,
    path: str,
    arguments: list[str] | None = None,
    *,
    environment: dict[str, str] | None = None,
    timeout: int = 300,
) -> str:
    exit_code, stdout, stderr = guest_exec(
        config,
        path,
        arguments,
        environment=environment,
        timeout=timeout,
    )
    if exit_code != 0:
        details = (stderr or stdout or "guest command failed").strip()
        raise VmError(f"Guest command {path} exited with {exit_code}: {details}")
    return stdout.strip()


def write_result(config: Config, name: str, stdout: str, stderr: str, exit_code: int) -> Path:
    config.results_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.results_dir / f"{name}.log"
    metadata_path = config.results_dir / f"{name}.json"
    log = stdout
    if stderr:
        log += "\n--- stderr ---\n" + stderr
    log_path.write_text(log.rstrip() + "\n")
    metadata_path.write_text(json.dumps({"exit_code": exit_code}, indent=2, sort_keys=True) + "\n")
    return log_path


def provision_guest(config: Config, *, idempotence: bool = False) -> None:
    validate_config(config)
    check_connection(config)
    if not domain_exists(config) or not is_running(config):
        raise VmError(f"Domain {config.name!r} is not running.")
    validate_marker(config)
    label = "idempotence" if idempotence else "provision"
    print(f"Running deburner.yml inside the guest ({label})...", flush=True)
    exit_code, stdout, stderr = guest_exec(
        config,
        "/usr/bin/ansible-playbook",
        [
            "--inventory",
            "/opt/deburner/inventory.ini",
            "/opt/deburner/deburner.yml",
        ],
        environment={
            "ANSIBLE_CONFIG": "/opt/deburner/ansible.cfg",
            "ANSIBLE_FORCE_COLOR": "0",
            "DEBIAN_FRONTEND": "noninteractive",
            "HOME": "/root",
            "LANG": "C.UTF-8",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        },
        timeout=14_400,
    )
    log_path = write_result(config, label, stdout, stderr, exit_code)
    if exit_code != 0:
        details = (stderr or stdout or "Ansible failed").strip().splitlines()[-1]
        raise VmError(f"Guest provisioning failed; see {log_path}: {details}")
    recap = next(
        (line.strip() for line in reversed(stdout.splitlines()) if "failed=" in line),
        "",
    )
    if not recap or "failed=0" not in recap or "unreachable=0" not in recap:
        raise VmError(f"Ansible output did not contain a successful recap; see {log_path}.")
    if idempotence and not re.search(r"\bchanged=0\b", recap):
        raise VmError(f"The second provisioning run was not idempotent ({recap}); see {log_path}.")
    print(f"Provisioning completed: {recap}")
    print(f"Full log: {log_path}")


def provision(config: Config) -> None:
    provision_guest(config)


def idempotence(config: Config) -> None:
    provision_guest(config, idempotence=True)


def verify(config: Config, *, label: str = "verify") -> None:
    validate_config(config)
    check_connection(config)
    if not domain_exists(config) or not is_running(config):
        raise VmError(f"Domain {config.name!r} is not running.")
    validate_marker(config)
    print("Running the read-only verification playbook inside the guest...", flush=True)
    playbook_exit, playbook_stdout, playbook_stderr = guest_exec(
        config,
        "/usr/bin/ansible-playbook",
        [
            "--inventory",
            "/opt/deburner/inventory.ini",
            "/opt/deburner/verify.yml",
        ],
        environment={
            "ANSIBLE_CONFIG": "/opt/deburner/ansible.cfg",
            "ANSIBLE_FORCE_COLOR": "0",
            "HOME": "/root",
            "LANG": "C.UTF-8",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        },
        timeout=900,
    )
    playbook_log = write_result(
        config, f"{label}-playbook", playbook_stdout, playbook_stderr, playbook_exit
    )
    if playbook_exit != 0:
        raise VmError(f"Verification playbook failed; see {playbook_log}.")
    playbook_recap = next(
        (line.strip() for line in reversed(playbook_stdout.splitlines()) if "failed=" in line),
        "",
    )
    if (
        not playbook_recap
        or "failed=0" not in playbook_recap
        or "unreachable=0" not in playbook_recap
        or not re.search(r"\bchanged=0\b", playbook_recap)
    ):
        raise VmError(
            f"Verification playbook was unsuccessful or changed the guest; see {playbook_log}."
        )
    print(f"Verification playbook completed without changes: {playbook_recap}")

    print("Running post-provision checks inside the guest...", flush=True)
    exit_code, stdout, stderr = guest_exec(
        config,
        "/usr/bin/python3",
        ["/opt/deburner/tools/verify_guest.py"],
        timeout=900,
    )
    log_path = write_result(config, label, stdout, stderr, exit_code)
    if exit_code != 0:
        raise VmError(f"Guest verification failed; see {log_path}.")
    summary = next(
        (
            line.strip()
            for line in reversed(stdout.splitlines())
            if line.startswith("Verification summary:")
        ),
        "verification passed",
    )
    print(f"{summary}. Full log: {log_path}")


def verify_guest(config: Config) -> None:
    verify(config)


def prerequisites(config: Config) -> None:
    validate_config(config)
    check_connection(config)
    print(f"Host prerequisites are available for {config.uri}.")


def create(config: Config) -> None:
    validate_config(config)
    check_connection(config)
    if domain_exists(config):
        raise VmError(f"Domain {config.name!r} already exists; refusing to replace it.")
    if config.state_dir.exists():
        raise VmError(f"State directory {config.state_dir} already exists; refusing to replace it.")

    base_image = prepare_image(config)
    config.state_dir.mkdir(parents=True)
    try:
        create_source_iso(config)
        create_cloud_init_seed(config)
        run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(base_image.resolve()),
                str(config.disk_path),
                f"{config.disk_gib}G",
            ]
        )
        write_marker(config)
        config.xml_path.write_text(build_domain_xml(config))
        run(["virt-xml-validate", str(config.xml_path)])
        virsh(config, "define", "--validate", str(config.xml_path))
    except Exception:
        if not domain_exists(config):
            shutil.rmtree(config.state_dir)
        raise

    print(f"Defined {config.name!r} with state in {config.state_dir}.")
    print(f"The sparse guest disk uses verified base image {base_image.name}.")
    print("The QEMU guest agent will become available through its private virtio channel.")


def start(config: Config) -> None:
    validate_config(config)
    check_connection(config)
    if not domain_exists(config):
        raise VmError(f"Domain {config.name!r} is not defined; run make test-create first.")
    if is_running(config):
        print(f"Domain {config.name!r} is already {domain_state(config)}.")
        return
    virsh(config, "start", config.name)
    print(f"Started {config.name!r}.")


def stop(config: Config) -> None:
    validate_config(config)
    check_connection(config)
    if not domain_exists(config):
        print(f"Domain {config.name!r} is not defined.")
        return
    if not is_running(config):
        print(f"Domain {config.name!r} is already shut off.")
        return

    virsh(config, "shutdown", config.name)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not is_running(config):
            print(f"Stopped {config.name!r}.")
            return
        time.sleep(1)
    raise VmError("The guest did not shut down within 60 seconds; use make test-destroy if needed.")


def destroy(config: Config) -> None:
    validate_config(config)
    check_connection(config)
    if not domain_exists(config):
        print(f"Domain {config.name!r} is not defined.")
        return
    if not is_running(config):
        print(f"Domain {config.name!r} is already shut off.")
        return
    virsh(config, "destroy", config.name)
    print(f"Forcibly stopped {config.name!r}.")


def status(config: Config) -> None:
    validate_config(config)
    check_commands()
    virsh(config, "uri")
    if not domain_exists(config):
        print(f"Domain {config.name!r} is not defined.")
        return
    result = virsh(config, "dominfo", config.name)
    print(result.stdout.rstrip())
    print(f"State directory: {config.state_dir}")


def console(config: Config) -> NoReturn:
    validate_config(config)
    check_connection(config)
    if not domain_exists(config):
        raise VmError(f"Domain {config.name!r} is not defined.")
    if not is_running(config):
        raise VmError(f"Domain {config.name!r} is not running.")
    os.execvp("virsh", ["virsh", "--connect", config.uri, "console", config.name, "--safe"])


def wait_agent(config: Config) -> None:
    validate_config(config)
    check_connection(config)
    if not domain_exists(config) or not is_running(config):
        raise VmError(f"Domain {config.name!r} is not running.")
    validate_marker(config)

    print(
        "Waiting for cloud-init to install the GNOME baseline and QEMU guest agent...", flush=True
    )
    deadline = time.monotonic() + 1800
    last_error = "guest agent did not respond"
    while time.monotonic() < deadline:
        result = virsh(
            config,
            "qemu-agent-command",
            config.name,
            "--timeout",
            "5",
            '{"execute":"guest-ping"}',
            check=False,
        )
        if result.returncode == 0:
            try:
                response = json.loads(result.stdout)
            except json.JSONDecodeError:
                response = None
            if response == {"return": {}}:
                break
        last_error = (result.stderr or result.stdout or last_error).strip().splitlines()[-1]
        time.sleep(2)
    else:
        raise VmError(
            f"The QEMU guest agent did not become ready within 1800 seconds: {last_error}"
        )

    cloud_init = require_guest_command(
        config,
        "/usr/bin/cloud-init",
        ["status", "--wait"],
        timeout=3600,
    )
    python_version = require_guest_command(config, "/usr/bin/python3", ["--version"])
    user_id = require_guest_command(config, "/usr/bin/id", ["-u"])
    root_size = require_guest_command(config, "/usr/bin/findmnt", ["-n", "-b", "-o", "SIZE", "/"])
    dns_result = require_guest_command(
        config,
        "/usr/bin/getent",
        ["ahostsv4", "deb.debian.org"],
    )
    if user_id != "0":
        raise VmError(f"Guest-agent commands unexpectedly run as user ID {user_id}.")
    try:
        root_size_bytes = int(root_size)
    except ValueError as error:
        raise VmError(f"Guest root filesystem returned an invalid size: {root_size!r}") from error
    if root_size_bytes < 400_000_000_000:
        raise VmError(
            f"Guest root filesystem did not expand beyond 400 GB: {root_size_bytes} bytes"
        )
    if not dns_result:
        raise VmError("Guest DNS lookup for deb.debian.org returned no addresses.")
    print(f"QEMU guest agent ready; {cloud_init}; {python_version}.")
    print(f"Guest commands run as root; filesystem size is {root_size_bytes} bytes; DNS works.")


def reboot(config: Config) -> None:
    validate_config(config)
    check_connection(config)
    if not domain_exists(config) or not is_running(config):
        raise VmError(f"Domain {config.name!r} is not running.")
    validate_marker(config)
    previous_boot = require_guest_command(
        config, "/usr/bin/cat", ["/proc/sys/kernel/random/boot_id"]
    )
    virsh(config, "reboot", config.name, "--mode", "agent")

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        result = virsh(
            config,
            "qemu-agent-command",
            config.name,
            "--timeout",
            "3",
            '{"execute":"guest-ping"}',
            check=False,
        )
        if result.returncode != 0:
            break
        time.sleep(1)
    else:
        raise VmError("The guest agent never disconnected during reboot.")

    wait_agent(config)
    current_boot = require_guest_command(
        config, "/usr/bin/cat", ["/proc/sys/kernel/random/boot_id"]
    )
    if current_boot == previous_boot:
        raise VmError("The guest boot ID did not change after reboot.")
    print(f"Rebooted {config.name!r} and re-established the guest-agent channel.")


def run_workflow(config: Config) -> None:
    """Run the complete integration workflow and clean up only after success."""
    try:
        create(config)
        start(config)
        wait_agent(config)
        provision(config)
        verify(config)
        reboot(config)
        verify(config, label="verify-after-reboot")
        idempotence(config)
        stop(config)
        clean(config)
    except Exception:
        print(
            "The failed test VM and its overlay were preserved for inspection. "
            "Use make test-status or make test-console, then stop it before make test-clean.",
            file=sys.stderr,
        )
        raise
    print(f"Complete integration test passed. Results: {config.results_dir}")


def clean(config: Config) -> None:
    validate_config(config)
    check_commands()
    virsh(config, "uri")
    exists = domain_exists(config)
    if not exists and not config.state_dir.exists():
        try:
            config.state_root.rmdir()
        except OSError:
            pass
        print(f"No state exists for {config.name!r}.")
        return

    validate_marker(config)
    if exists and is_running(config):
        raise VmError("Refusing cleanup while the VM is running; stop or destroy it first.")
    if exists:
        virsh(config, "undefine", config.name)
    shutil.rmtree(config.state_dir)
    try:
        config.state_root.rmdir()
    except OSError:
        pass
    print(f"Removed domain and local state for {config.name!r}.")


COMMANDS = {
    "clean": clean,
    "console": console,
    "create": create,
    "destroy": destroy,
    "image-prepare": image_prepare,
    "image-purge": image_purge,
    "image-status": image_status,
    "idempotence": idempotence,
    "prerequisites": prerequisites,
    "provision": provision,
    "reboot": reboot,
    "refresh-source": refresh_source,
    "run-workflow": run_workflow,
    "start": start,
    "status": status,
    "stop": stop,
    "wait-agent": wait_agent,
    "verify": verify_guest,
}


def positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="deburner-test")
    parser.add_argument("--uri", default="qemu:///session")
    parser.add_argument("--state-root", type=Path, default=Path(".test-vm"))
    parser.add_argument("--cache-root", type=Path, default=Path(".cache/test-vm"))
    parser.add_argument("--results-root", type=Path, default=Path(".test-results"))
    parser.add_argument(
        "--image-url",
        default=(
            "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
        ),
    )
    parser.add_argument(
        "--checksums-url",
        default="https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS",
    )
    parser.add_argument("--memory-mib", type=positive_integer, default=8192)
    parser.add_argument("--vcpus", type=positive_integer, default=4)
    parser.add_argument("--disk-gib", type=positive_integer, default=450)
    parser.add_argument("command", choices=sorted(COMMANDS))
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    config = Config(
        name=arguments.name,
        uri=arguments.uri,
        state_root=arguments.state_root,
        cache_root=arguments.cache_root,
        results_root=arguments.results_root,
        image_url=arguments.image_url,
        checksums_url=arguments.checksums_url,
        memory_mib=arguments.memory_mib,
        vcpus=arguments.vcpus,
        disk_gib=arguments.disk_gib,
    )
    try:
        COMMANDS[arguments.command](config)
    except VmError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
