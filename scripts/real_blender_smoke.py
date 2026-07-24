"""Drive a real Blender Session through the installed CLI."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SESSION_NAME = "ci-real-blender"
CLI_TIMEOUT_SECONDS = 45
MCP_TIMEOUT_SECONDS = 45
PROCESS_EXIT_TIMEOUT_SECONDS = 10


class SmokeFailure(RuntimeError):
    """A failed real-Blender smoke assertion."""


def main() -> int:
    args = _parse_args()
    blender = args.blender.resolve()
    state_dir = args.state_dir.resolve()
    environment = dict(os.environ)
    environment["BLENDERSESSIOND_STATE_DIR"] = str(state_dir)
    cli = shutil.which("blendersessiond", path=environment.get("PATH"))
    if cli is None:
        print("FAIL: installed blendersessiond CLI is not on PATH.", file=sys.stderr)
        return 1
    if not blender.is_file():
        print(f"FAIL: Blender executable is not a file: {blender}", file=sys.stderr)
        return 1

    print(f"Installed CLI: {cli}")
    print(f"Blender: {blender}")
    print(f"Session state: {state_dir}")
    baseline_pids: set[int] = set()
    start_attempted = False
    stop_succeeded = False
    session_logs: set[Path] = set()
    try:
        scene_fixture = _generate_scene_fixture(blender, state_dir)
        fixture_hash = _file_hash(scene_fixture)
        baseline_pids = _blender_process_ids(blender)
        start_attempted = True
        started = _run_cli(
            "start",
            "--name",
            args.name,
            "--blender",
            str(blender),
            "--scene",
            str(scene_fixture),
            "--json",
            environment=environment,
            expected_codes={0},
        )
        _require(started.get("status") == "started", "start did not report started")
        session = _dict_field(started, "session")
        _require(session.get("name") == args.name, "start returned wrong Session Name")
        _require(
            _same_path(_dict_field(session, "blender").get("path"), blender),
            "start returned a different Blender executable",
        )
        _require(
            _dict_field(session, "blender").get("version")
            == args.expected_version,
            "start returned an unexpected Blender version",
        )
        blender_pid = _positive_int(session.get("pid"), "Session pid")
        session_logs.update(_log_paths(session))

        healthy = _wait_for_health(
            args.name,
            timeout=args.health_timeout,
            environment=environment,
        )
        healthy_session = _dict_field(healthy, "session")
        session_logs.update(_log_paths(healthy_session))
        _require(
            _same_path(healthy_session.get("scene_path"), scene_fixture),
            "status did not report the fixture scene path",
        )
        _require(
            healthy_session.get("unsaved_changes") is False,
            "fresh fixture Session did not report unsaved_changes=false",
        )
        splash = _run_cli(
            "call",
            "execute_code",
            "--name",
            args.name,
            "--params",
            json.dumps(
                {
                    "code": (
                        'print("true" if '
                        "bpy.context.preferences.view.show_splash else "
                        '"false")'
                    )
                }
            ),
            environment=environment,
            expected_codes={0},
            versioned=False,
        )
        _require(
            splash.get("executed") is True
            and splash.get("result", "").strip() == "false",
            "managed Session did not disable Blender's startup splash preference",
        )
        active_blender_pids = _blender_process_ids(blender)
        _require(
            blender_pid in active_blender_pids - baseline_pids,
            "healthy Session pid is not the selected Blender executable",
        )

        scene = _run_cli(
            "call",
            "get_scene_info",
            "--name",
            args.name,
            environment=environment,
            expected_codes={0},
            versioned=False,
        )
        _require(
            {"name", "object_count", "objects", "materials_count"} <= scene.keys(),
            "get_scene_info did not return the expected real scene keys",
        )
        _require(
            scene.get("name") == "Slice6FixtureScene",
            "get_scene_info did not report the fixture scene",
        )
        _require(
            _scene_object_names(scene)
            == {"Slice6FixtureAnchor", "Slice6FixtureCube"},
            "get_scene_info did not report the fixture object set",
        )

        mcp_scene = run_mcp_stdio_round_trip(
            ["blendersessiond", "mcp-serve", "--name", args.name],
            environment=environment,
        )
        _require(
            {"name", "object_count", "objects", "materials_count"}
            <= mcp_scene.keys(),
            "MCP get_scene_info did not return the expected real scene keys",
        )
        _require(
            _scene_object_names(mcp_scene)
            == {"Slice6FixtureAnchor", "Slice6FixtureCube"},
            "MCP get_scene_info did not report the fixture object set",
        )

        # A low-level data-API poke never marks the file dirty (Blender's own
        # asterisk behaves the same); push an undo step like an interactive
        # edit so bpy.data.is_dirty flips.
        mutation = (
            "obj = bpy.data.objects.new('Slice6UnsavedObject', None)\n"
            "bpy.context.scene.collection.objects.link(obj)\n"
            "bpy.ops.ed.undo_push(message='add Slice6UnsavedObject')"
        )
        _run_cli(
            "call",
            "execute_code",
            "--name",
            args.name,
            "--params",
            json.dumps({"code": mutation}),
            environment=environment,
            expected_codes={0},
            versioned=False,
        )
        dirty = _run_cli(
            "status",
            "--name",
            args.name,
            "--json",
            environment=environment,
            expected_codes={0},
        )
        _require(
            _dict_field(dirty, "session").get("unsaved_changes") is True,
            "mutated Session did not report unsaved_changes=true",
        )

        stopped = _run_cli(
            "stop",
            "--name",
            args.name,
            "--json",
            environment=environment,
            expected_codes={0},
        )
        _require(stopped.get("status") == "stopped", "stop did not report stopped")
        stop_succeeded = True
        _require(
            _file_hash(scene_fixture) == fixture_hash,
            "stop saved or otherwise changed the fixture .blend bytes",
        )

        gone = _run_cli(
            "status",
            "--name",
            args.name,
            "--json",
            environment=environment,
            expected_codes={1},
        )
        _require(gone.get("status") == "not-found", "Session still has a record")
        _require(
            _dict_field(gone, "session").get("status") == "not-found",
            "Session status is not not-found",
        )
        _wait_for_owned_blender_exit(
            blender,
            blender_pid,
            baseline_pids=baseline_pids,
        )
    except (OSError, SmokeFailure, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        if start_attempted and not stop_succeeded:
            _best_effort_stop(args.name, environment)
        _print_log_tails(session_logs, state_dir)
        return 1

    print(
        "PASS: real Blender fixture opened without its startup splash, direct "
        "and MCP scene info verified, unsaved changes surfaced, stop preserved "
        "fixture bytes, and Ownership verified."
    )
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", required=True, type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--name", default=SESSION_NAME)
    parser.add_argument("--health-timeout", default=60.0, type=float)
    return parser.parse_args()


def _run_cli(
    *arguments: str,
    environment: dict[str, str],
    expected_codes: set[int],
    versioned: bool = True,
) -> dict[str, Any]:
    command = ["blendersessiond", *arguments]
    print(f"$ {shlex.join(command)}")
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT_SECONDS,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise SmokeFailure(
            f"CLI timed out after {CLI_TIMEOUT_SECONDS}s: {shlex.join(command)}"
        ) from error
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SmokeFailure(f"CLI stdout was not JSON: {completed.stdout!r}") from error
    if not isinstance(payload, dict):
        raise SmokeFailure("CLI JSON payload is not an object")
    if versioned and payload.get("schema_version") != 1:
        raise SmokeFailure("CLI JSON schema_version is not 1")
    if completed.returncode not in expected_codes:
        raise SmokeFailure(
            f"CLI exited {completed.returncode}; expected {sorted(expected_codes)}"
        )
    return payload


def _generate_scene_fixture(blender: Path, state_dir: Path) -> Path:
    fixture = state_dir.parent / f"{state_dir.name}-slice6-scene.blend"
    generator = Path(__file__).with_name("generate_scene_fixture.py")
    command = [
        str(blender),
        "--background",
        "--factory-startup",
        "--python",
        str(generator),
        "--",
        str(fixture),
    ]
    print(f"$ {shlex.join(command)}")
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr)
    if completed.returncode != 0 or not fixture.is_file():
        raise SmokeFailure(
            f"fixture generation failed with exit code {completed.returncode}"
        )
    return fixture.resolve()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scene_object_names(scene: dict[str, Any]) -> set[str]:
    objects = scene.get("objects")
    if not isinstance(objects, list):
        raise SmokeFailure("scene objects field is not a list")
    return {
        item["name"]
        for item in objects
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def _wait_for_health(
    name: str,
    *,
    timeout: float,
    environment: dict[str, str],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last_payload = _run_cli(
            "status",
            "--name",
            name,
            "--json",
            environment=environment,
            expected_codes={0, 1},
        )
        session = _dict_field(last_payload, "session")
        health = _dict_field(session, "health")
        process = _dict_field(health, "process")
        socket_health = _dict_field(health, "socket")
        if (
            last_payload.get("status") == "healthy"
            and session.get("status") == "healthy"
            and health.get("status") == "healthy"
            and process.get("status") == "healthy"
            and process.get("alive") is True
            and socket_health.get("status") == "healthy"
            and socket_health.get("answered") is True
        ):
            return last_payload
        time.sleep(1)
    raise SmokeFailure(
        f"Session Health did not become healthy within {timeout:.0f}s; "
        f"last status: {last_payload}"
    )


def run_mcp_stdio_round_trip(
    command: Sequence[str],
    *,
    environment: dict[str, str],
) -> dict[str, Any]:
    """Run initialize, tools/list, and get_scene_info over MCP stdio."""

    print(f"$ {shlex.join(command)}  # MCP stdio JSON-RPC")
    client = _McpStdioClient(command, environment=environment)
    failure: BaseException | None = None
    try:
        initialized = client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "blendersessiond-real-smoke",
                    "version": "1",
                },
            },
        )
        _require(
            isinstance(initialized.get("protocolVersion"), str),
            "MCP initialize response has no protocolVersion",
        )
        client.notify("notifications/initialized")

        listed = client.request("tools/list", {})
        tools = listed.get("tools")
        _require(isinstance(tools, list), "MCP tools/list result has no tools list")
        _require(
            any(
                isinstance(tool, dict) and tool.get("name") == "get_scene_info"
                for tool in tools
            ),
            "MCP tools/list did not advertise get_scene_info",
        )

        called = client.request(
            "tools/call",
            {
                "name": "get_scene_info",
                "arguments": {"user_prompt": "blendersessiond real smoke"},
            },
        )
        _require(called.get("isError") is not True, "MCP tool call reported an error")
        content = called.get("content")
        _require(
            isinstance(content, list),
            "MCP get_scene_info result has no content list",
        )
        text = next(
            (
                item.get("text")
                for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ),
            None,
        )
        _require(text is not None, "MCP get_scene_info result has no text content")
        try:
            scene = json.loads(text)
        except json.JSONDecodeError as error:
            raise SmokeFailure(
                f"MCP get_scene_info text is not scene JSON: {text!r}"
            ) from error
        _require(
            isinstance(scene, dict),
            "MCP get_scene_info scene JSON is not an object",
        )
        return scene
    except BaseException as error:
        failure = error
        raise
    finally:
        client.close(suppress_errors=failure is not None)


class _McpStdioClient:
    def __init__(
        self,
        command: Sequence[str],
        *,
        environment: dict[str, str],
    ) -> None:
        self._process = subprocess.Popen(
            list(command),
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stdout: queue.Queue[str | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=200)
        self._next_id = 1
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        deadline = time.monotonic() + MCP_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise self._failure(f"MCP {method} timed out")
            try:
                line = self._stdout.get(timeout=remaining)
            except queue.Empty as error:
                raise self._failure(f"MCP {method} timed out") from error
            if line is None:
                raise self._failure(
                    f"MCP server exited before answering {method}"
                )
            try:
                message = json.loads(line)
            except json.JSONDecodeError as error:
                raise self._failure(
                    f"MCP stdout was not JSON: {line!r}"
                ) from error
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            error_payload = message.get("error")
            if error_payload is not None:
                raise self._failure(
                    f"MCP {method} returned an error: {error_payload}"
                )
            result = message.get("result")
            if not isinstance(result, dict):
                raise self._failure(
                    f"MCP {method} result is not an object: {result!r}"
                )
            return result

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def close(self, *, suppress_errors: bool) -> None:
        if self._process.stdin is not None and not self._process.stdin.closed:
            self._process.stdin.close()
        try:
            return_code = self._process.wait(timeout=10)
        except subprocess.TimeoutExpired as error:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            if not suppress_errors:
                raise self._failure(
                    "MCP server did not exit after stdin closed"
                ) from error
            return
        if return_code != 0 and not suppress_errors:
            raise self._failure(f"MCP server exited {return_code}")

    def _send(self, message: dict[str, Any]) -> None:
        if self._process.stdin is None:
            raise self._failure("MCP stdin is unavailable")
        try:
            self._process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise self._failure("MCP stdin closed unexpectedly") from error

    def _read_stdout(self) -> None:
        if self._process.stdout is None:
            self._stdout.put(None)
            return
        for line in self._process.stdout:
            self._stdout.put(line)
        self._stdout.put(None)

    def _read_stderr(self) -> None:
        if self._process.stderr is None:
            return
        for line in self._process.stderr:
            self._stderr.append(line.rstrip())
            print(line, end="", file=sys.stderr)

    def _failure(self, message: str) -> SmokeFailure:
        details = "\n".join(self._stderr)
        if details:
            return SmokeFailure(f"{message}; MCP stderr tail:\n{details}")
        return SmokeFailure(message)


def _wait_for_owned_blender_exit(
    blender: Path,
    blender_pid: int,
    *,
    baseline_pids: set[int],
) -> None:
    deadline = time.monotonic() + PROCESS_EXIT_TIMEOUT_SECONDS
    remaining: set[int] = set()
    while time.monotonic() < deadline:
        remaining = _blender_process_ids(blender) - baseline_pids
        if not _process_exists(blender_pid) and not remaining:
            return
        time.sleep(0.25)
    raise SmokeFailure(
        "Ownership check failed: "
        f"recorded pid alive={_process_exists(blender_pid)}, "
        f"new Blender pids still alive={sorted(remaining)}"
    )


def _blender_process_ids(blender: Path) -> set[int]:
    if sys.platform.startswith("linux"):
        return _linux_executable_pids(blender)
    if sys.platform == "darwin":
        return _macos_executable_pids(blender)
    if os.name == "nt":
        return _windows_executable_pids(blender)
    raise SmokeFailure(f"unsupported smoke platform: {sys.platform}")


def _linux_executable_pids(blender: Path) -> set[int]:
    expected = os.path.realpath(blender)
    matches: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            executable = os.path.realpath(entry / "exe")
        except OSError:
            continue
        if executable == expected:
            matches.add(int(entry.name))
    return matches


def _macos_executable_pids(blender: Path) -> set[int]:
    completed = subprocess.run(
        ["ps", "-axo", "pid=,comm="],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    expected = os.path.realpath(blender)
    matches: set[int] = set()
    for line in completed.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) == 2 and os.path.realpath(fields[1]) == expected:
            matches.add(int(fields[0]))
    return matches


def _windows_executable_pids(blender: Path) -> set[int]:
    environment = dict(os.environ)
    environment["BLENDER_SMOKE_EXECUTABLE"] = str(blender)
    script = """
$target = [System.IO.Path]::GetFullPath($env:BLENDER_SMOKE_EXECUTABLE)
Get-CimInstance Win32_Process |
  Where-Object {
    $_.ExecutablePath -and
    [StringComparer]::OrdinalIgnoreCase.Equals($_.ExecutablePath, $target)
  } |
  ForEach-Object { $_.ProcessId }
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
        env=environment,
        check=True,
    )
    return {
        int(line.strip())
        for line in completed.stdout.splitlines()
        if line.strip().isdigit()
    }


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        process = kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259
        finally:
            kernel32.CloseHandle(process)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _best_effort_stop(name: str, environment: dict[str, str]) -> None:
    print("Attempting best-effort Session cleanup.", file=sys.stderr)
    try:
        _run_cli(
            "stop",
            "--name",
            name,
            "--json",
            environment=environment,
            expected_codes={0, 1},
        )
    except (OSError, SmokeFailure, subprocess.SubprocessError) as error:
        print(f"Cleanup failed: {error}", file=sys.stderr)


def _log_paths(session: dict[str, Any]) -> set[Path]:
    logs = session.get("logs")
    if not isinstance(logs, dict):
        return set()
    return {
        Path(value)
        for value in logs.values()
        if isinstance(value, str) and value
    }


def _print_log_tails(known_logs: set[Path], state_dir: Path) -> None:
    candidates = set(known_logs)
    if state_dir.exists():
        candidates.update(state_dir.rglob("*.log"))
    for path in sorted(candidates):
        print(f"--- tail: {path} ---", file=sys.stderr)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as error:
            print(f"could not read log: {error}", file=sys.stderr)
            continue
        print("\n".join(lines[-200:]), file=sys.stderr)


def _dict_field(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise SmokeFailure(f"CLI JSON field {key!r} is not an object")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SmokeFailure(f"{label} is not a positive integer")
    return value


def _same_path(value: object, expected: Path) -> bool:
    if not isinstance(value, str):
        return False
    return os.path.normcase(os.path.realpath(value)) == os.path.normcase(
        os.path.realpath(expected)
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


if __name__ == "__main__":
    raise SystemExit(main())
