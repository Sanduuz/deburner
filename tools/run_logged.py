"""Run a command while writing combined output to a file for QGA streaming."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: run_logged.py LOG COMMAND [ARG ...]", file=sys.stderr)
        return 2

    log_path = Path(sys.argv[1])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as output:
        process = subprocess.run(sys.argv[2:], stdout=output, stderr=subprocess.STDOUT, check=False)
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
