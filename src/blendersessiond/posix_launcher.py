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
    if os.name == "nt" or len(sys.argv) != 6:
        return 2
    gate = Path(sys.argv[1])
    executable = sys.argv[2]
    bootstrap = Path(sys.argv[3])
    addon_bootstrap = Path(sys.argv[4])
    scene_path = sys.argv[5]
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
                # Managed Sessions isolate user preferences even when opening a scene.
                blender_arguments = [executable, "--factory-startup"]
                if scene_path:
                    blender_arguments.append(scene_path)
                blender_arguments.extend(["--python", str(addon_bootstrap)])
                process = subprocess.Popen(
                    blender_arguments,
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
