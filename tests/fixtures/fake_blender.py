"""Long-running fake Blender used by CLI subprocess e2e tests."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    if "--version" in sys.argv:
        print("Blender 4.3.2")
        return 0
    if "--fake-child" in sys.argv:
        while True:
            time.sleep(1)

    child = subprocess.Popen(
        [sys.executable, __file__, "--fake-child"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid_file = os.environ.get("FAKE_BLENDER_CHILD_PID_FILE")
    if child_pid_file:
        Path(child_pid_file).write_text(str(child.pid), encoding="utf-8")

    print("fake Blender stdout", flush=True)
    print("fake Blender stderr", file=sys.stderr, flush=True)
    if os.environ.get("FAKE_BLENDER_EXIT_AFTER_CHILD") == "1":
        time.sleep(0.2)
        return 0
    while True:
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
