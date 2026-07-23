"""Wait for durable Session state, then replace this process with Blender."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    if os.name == "nt" or len(sys.argv) != 5:
        return 2
    gate = Path(sys.argv[1])
    executable = sys.argv[2]
    bootstrap = Path(sys.argv[3])
    addon_bootstrap = Path(sys.argv[4])
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if gate.exists():
            gate.unlink(missing_ok=True)
            try:
                bootstrap.write_text(
                    json.dumps({"status": "launching"}),
                    encoding="utf-8",
                )
            except OSError:
                return 1
            try:
                process = subprocess.Popen(
                    [
                        executable,
                        "--factory-startup",
                        "--python",
                        str(addon_bootstrap),
                    ],
                    stdin=subprocess.DEVNULL,
                    close_fds=True,
                )
            except OSError as error:
                bootstrap.write_text(
                    json.dumps({"error": str(error)}),
                    encoding="utf-8",
                )
                return 1
            try:
                bootstrap.write_text(
                    json.dumps({"pid": process.pid}),
                    encoding="utf-8",
                )
            except OSError:
                os.killpg(os.getpgrp(), signal.SIGKILL)
                return 1
            process.wait()
            while True:
                time.sleep(1)
        time.sleep(0.01)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
