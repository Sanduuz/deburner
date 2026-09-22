"""Stream an operator command to the terminal and a private timestamped log."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if arguments.command[:1] == ["--"]:
        arguments.command = arguments.command[1:]
    if not arguments.command:
        parser.error("a command is required after --")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", arguments.label):
        parser.error("--label must contain lowercase letters, numbers, and hyphens only")
    return arguments


def main() -> int:
    arguments = parse_arguments()
    arguments.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(arguments.log_dir, 0o700)

    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    log_path = arguments.log_dir / f"{timestamp}-{arguments.label}.log"
    print(f"Logging live output to {log_path}", flush=True)

    with log_path.open("xb", buffering=0) as log_file:
        os.chmod(log_path, 0o600)
        try:
            process = subprocess.Popen(
                arguments.command,
                stdin=None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except OSError as error:
            message = f"Unable to start {arguments.command[0]!r}: {error}\n"
            encoded_message = message.encode()
            log_file.write(encoded_message)
            sys.stderr.write(message)
            print(f"Log retained at {log_path}", flush=True)
            return 127
        if process.stdout is None:
            raise RuntimeError("failed to capture command output")
        try:
            while chunk := process.stdout.read1(64 * 1024):
                log_file.write(chunk)
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
            return process.wait()
        except KeyboardInterrupt:
            while process.poll() is None:
                try:
                    process.wait()
                except KeyboardInterrupt:
                    continue
            return process.returncode if process.returncode is not None else 130
        finally:
            print(f"Log retained at {log_path}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
