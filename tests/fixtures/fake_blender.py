"""Long-running fake Blender used by CLI subprocess e2e tests."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


def _serve_fake_addon(port: int) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(1)
    while True:
        client, _address = server.accept()
        with client:
            buffer = b""
            while True:
                chunk = client.recv(8192)
                if not chunk:
                    break
                buffer += chunk
                try:
                    command = json.loads(buffer.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                command_type = command.get("type")
                params = command.get("params", {})
                if command_type == "get_polyhaven_status":
                    result = {"enabled": False}
                elif command_type == "get_scene_info":
                    result = {
                        "name": "Scene",
                        "object_count": 3,
                        "objects": [],
                        "materials_count": 0,
                    }
                elif command_type == "get_object_info":
                    result = {
                        "name": params.get("name"),
                        "type": "MESH",
                    }
                elif command_type == "execute_code":
                    result = {"executed": True, "result": "false\n"}
                else:
                    client.sendall(
                        json.dumps(
                            {
                                "status": "error",
                                "message": f"Unknown command type: {command_type}",
                            }
                        ).encode("utf-8")
                    )
                    break
                client.sendall(
                    json.dumps(
                        {"status": "success", "result": result}
                    ).encode("utf-8")
                )
                break


def main() -> int:
    if "--version" in sys.argv:
        print("Blender 4.3.2")
        return 0
    if "--fake-child" in sys.argv:
        while True:
            time.sleep(1)

    argv_file = os.environ.get("FAKE_BLENDER_ARGV_FILE")
    if argv_file:
        Path(argv_file).write_text(
            json.dumps(sys.argv[1:]),
            encoding="utf-8",
        )

    if os.environ.get("FAKE_BLENDER_DISABLE_MCP_SOCKET") != "1":
        port = int(os.environ["BLENDERSESSIOND_MCP_PORT"])
        addon_thread = threading.Thread(
            target=_serve_fake_addon,
            args=(port,),
            daemon=True,
        )
        addon_thread.start()

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
