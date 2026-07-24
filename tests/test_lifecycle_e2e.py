from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from stat import S_IMODE

import pytest

from blendersessiond.processes import (
    process_start_time,
    terminate_owned_tree,
)
from blendersessiond.sessions import session_directory
from blendersessiond.state import STATE_DIR_ENV_VAR

pytestmark = pytest.mark.e2e


def _wait_until_gone(pid: int, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process_start_time(pid) is None:
            return
        time.sleep(0.05)
    pytest.fail(f"process {pid} did not exit")


def _wait_until_reaped(pid: int, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail(f"process {pid} was not reaped")


def _wait_for_file(path: Path, timeout: float = 5) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        time.sleep(0.05)
    pytest.fail(f"{path} was not written")


def test_start_status_stop_kills_tree_and_keeps_logs(
    isolated_cli,
    fake_blender: Path,
    tmp_path: Path,
) -> None:
    environment, run = isolated_cli
    child_pid_file = tmp_path / "child.pid"
    environment["FAKE_BLENDER_CHILD_PID_FILE"] = str(child_pid_file)

    started = run("start", "--blender", str(fake_blender))
    assert started.completed.returncode == 0, started.completed.stderr
    session = started.payload["session"]
    assert session["name"] == "default"
    assert session["mcp_port"] == 9876
    assert session["scene_path"] is None
    assert session["blender"]["version"] == "4.3.2"
    assert Path(session["state_dir"]).name == "default".encode("ascii").hex()

    status = run("status", "--name", "default")
    assert status.completed.returncode == 0
    assert status.payload["session"]["unsaved_changes"] is False
    assert status.payload["session"]["health"] == {
        "status": "healthy",
        "process": {"status": "healthy", "alive": True},
        "socket": {
            "status": "healthy",
            "answered": True,
            "reason": "answered",
            "message": (
                "Addon socket at 127.0.0.1:9876 answered the Health ping."
            ),
        },
    }

    doctor = run("doctor", "--blender", str(fake_blender))
    assert doctor.completed.returncode == 0
    session_check = next(
        check
        for check in doctor.payload["checks"]
        if check["name"] == "session:default"
    )
    assert session_check["status"] == "pass"
    assert "record is readable" in session_check["message"]
    doctor_session = doctor.payload["sessions"][0]
    assert doctor_session["health"]["process"]["alive"] is True
    assert doctor_session["health"]["socket"]["answered"] is True

    child_pid = int(_wait_for_file(child_pid_file))
    stdout_log = Path(session["logs"]["stdout"])
    stderr_log = Path(session["logs"]["stderr"])
    assert "fake Blender stdout" in _wait_for_file(stdout_log)
    assert "fake Blender stderr" in _wait_for_file(stderr_log)
    if os.name != "nt":
        session_directory = Path(session["state_dir"])
        assert S_IMODE(session_directory.stat().st_mode) == 0o700
        for private_file in (
            session_directory / ".lock",
            session_directory / ".wire.lock",
            session_directory / "session.json",
            session_directory / "ownership.json",
            stdout_log,
            stderr_log,
        ):
            assert S_IMODE(private_file.stat().st_mode) == 0o600

    stopped = run("stop")
    assert stopped.completed.returncode == 0
    assert stopped.payload["status"] == "stopped"
    assert stopped.payload["session"]["health"]["process"] == {
        "status": "stopped",
        "alive": False,
    }
    _wait_until_gone(session["pid"])
    _wait_until_gone(child_pid)
    assert stdout_log.exists()
    assert stderr_log.exists()

    gone = run("status", "--name", "default")
    assert gone.completed.returncode == 1
    assert gone.payload["status"] == "not-found"


def test_two_named_sessions_have_distinct_ports_and_live_start_is_rejected(
    isolated_cli,
    fake_blender: Path,
) -> None:
    _environment, run = isolated_cli

    first = run("start", "--name", "a", "--blender", str(fake_blender))
    second = run("start", "--name", "61", "--blender", str(fake_blender))
    duplicate = run("start", "--name", "a", "--blender", str(fake_blender))

    assert first.completed.returncode == 0
    assert second.completed.returncode == 0
    assert first.payload["session"]["mcp_port"] == 9876
    assert second.payload["session"]["mcp_port"] == 9877
    assert (
        first.payload["session"]["state_dir"]
        != second.payload["session"]["state_dir"]
    )
    assert duplicate.completed.returncode == 1
    assert "already running; reuse it" in duplicate.payload["message"]

    listed = run("status")
    assert listed.completed.returncode == 0
    assert [session["name"] for session in listed.payload["sessions"]] == [
        "61",
        "a",
    ]
    assert all(
        session["health"]["process"]["alive"]
        for session in listed.payload["sessions"]
    )

    assert run("stop", "--name", "a").completed.returncode == 0
    assert run("stop", "--name", "61").completed.returncode == 0


def test_call_round_trip_and_parameterized_command(
    isolated_cli,
    fake_blender: Path,
) -> None:
    _environment, run = isolated_cli
    started = run("start", "--blender", str(fake_blender))
    assert started.completed.returncode == 0

    scene = run("call", "get_scene_info")
    object_info = run(
        "call",
        "get_object_info",
        "--params",
        '{"name":"Cube"}',
    )

    assert scene.completed.returncode == 0
    assert scene.payload["name"] == "Scene"
    assert scene.payload["object_count"] == 3
    assert object_info.completed.returncode == 0
    assert object_info.payload == {"name": "Cube", "type": "MESH"}
    assert run("stop").completed.returncode == 0


def test_live_process_with_dead_socket_is_unhealthy_with_reason(
    isolated_cli,
    fake_blender: Path,
) -> None:
    environment, run = isolated_cli
    environment["FAKE_BLENDER_DISABLE_MCP_SOCKET"] = "1"
    started = run("start", "--blender", str(fake_blender))
    assert started.completed.returncode == 0

    status = run("status", "--name", "default")

    assert status.completed.returncode == 1
    assert status.payload["status"] == "unhealthy"
    assert status.payload["session"]["health"]["process"] == {
        "status": "healthy",
        "alive": True,
    }
    socket_health = status.payload["session"]["health"]["socket"]
    assert socket_health["status"] == "unhealthy"
    assert socket_health["answered"] is False
    # Windows runners drop loopback SYNs to closed ports instead of RST,
    # so a dead socket legitimately reports a timeout reason there.
    expected_reasons = (
        {"connection-failed", "timeout"} if os.name == "nt" else {"connection-failed"}
    )
    assert socket_health["reason"] in expected_reasons
    assert status.payload["session"]["unsaved_changes"] == "unknown"
    assert "process is alive, but its MCP addon socket is unhealthy" in (
        status.payload["session"]["message"]
    )
    assert run("stop").completed.returncode == 0


def test_scene_path_is_recorded_and_passed_to_fake_blender(
    isolated_cli,
    fake_blender: Path,
    tmp_path: Path,
) -> None:
    environment, run = isolated_cli
    scene = tmp_path / "input scene.blend"
    scene.write_bytes(b"fake blend fixture")
    argv_file = tmp_path / "blender-argv.json"
    environment["FAKE_BLENDER_ARGV_FILE"] = str(argv_file)

    started = run(
        "start",
        "--blender",
        str(fake_blender),
        "--scene",
        str(scene),
    )

    assert started.completed.returncode == 0, started.completed.stderr
    expected_scene = str(scene.absolute())
    assert started.payload["session"]["scene_path"] == expected_scene
    record = json.loads(
        (Path(started.payload["session"]["state_dir"]) / "session.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["scene_path"] == expected_scene
    blender_argv = json.loads(_wait_for_file(argv_file))
    assert blender_argv == [
        "--factory-startup",
        expected_scene,
        "--python",
        str(
            (
                Path(__file__).parents[1]
                / "src"
                / "blendersessiond"
                / "blender_bootstrap.py"
            ).resolve()
        ),
    ]

    status = run("status", "--name", "default")
    assert status.payload["session"]["scene_path"] == expected_scene
    human_status = subprocess.run(
        [
            sys.executable,
            "-m",
            "blendersessiond",
            "status",
            "--name",
            "default",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert human_status.returncode == 0
    assert f"scene: {expected_scene}" in human_status.stdout
    assert "unsaved changes: no" in human_status.stdout
    assert run("stop").completed.returncode == 0


def test_concurrent_start_calls_have_one_owner(
    isolated_cli,
    fake_blender: Path,
) -> None:
    environment, run = isolated_cli
    command = [
        sys.executable,
        "-m",
        "blendersessiond",
        "start",
        "--name",
        "race",
        "--blender",
        str(fake_blender),
        "--json",
    ]
    first = subprocess.Popen(
        command,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        command,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    first_stdout, first_stderr = first.communicate(timeout=20)
    second_stdout, second_stderr = second.communicate(timeout=20)
    results = [
        (first.returncode, json.loads(first_stdout), first_stderr),
        (second.returncode, json.loads(second_stdout), second_stderr),
    ]

    assert sorted(result[0] for result in results) == [0, 1]
    failure = next(result for result in results if result[0] == 1)
    assert "already running; reuse it" in failure[1]["message"]
    assert failure[2] == ""
    assert run("status", "--name", "race").completed.returncode == 0
    assert run("stop", "--name", "race").completed.returncode == 0


def test_stop_terminates_tree_after_blender_root_exits(
    isolated_cli,
    fake_blender: Path,
    tmp_path: Path,
) -> None:
    environment, run = isolated_cli
    child_pid_file = tmp_path / "orphan-child.pid"
    environment["FAKE_BLENDER_CHILD_PID_FILE"] = str(child_pid_file)
    environment["FAKE_BLENDER_EXIT_AFTER_CHILD"] = "1"

    started = run("start", "--name", "orphan", "--blender", str(fake_blender))
    assert started.completed.returncode == 0
    child_pid = int(_wait_for_file(child_pid_file))

    deadline = time.monotonic() + 5
    status = None
    while time.monotonic() < deadline:
        status = run("status", "--name", "orphan")
        if status.payload["status"] == "stale":
            break
        time.sleep(0.05)
    assert status is not None
    assert status.payload["status"] == "stale"
    assert status.payload["session"]["reclaimable"] is False
    if os.name != "nt":
        _wait_until_reaped(started.payload["session"]["pid"])

    stopped = run("stop", "--name", "orphan")
    assert stopped.completed.returncode == 0
    _wait_until_gone(child_pid)


def test_dead_record_is_stale_then_start_reclaims_name_and_port(
    isolated_cli,
    fake_blender: Path,
) -> None:
    environment, run = isolated_cli

    original = run("start", "--name", "stale", "--blender", str(fake_blender))
    record = original.payload["session"]
    record_path = (
        Path(record["state_dir"]) / "session.json"
    )
    durable_record = json.loads(record_path.read_text(encoding="utf-8"))
    terminate_owned_tree(
        record["pid"],
        record["process_start_time"],
        controller_pid=durable_record["controller_pid"],
        controller_start_time=durable_record["controller_start_time"],
    )
    _wait_until_gone(record["pid"])

    stale = run("status", "--name", "stale")
    assert stale.completed.returncode == 1
    assert stale.payload["status"] == "stale"

    doctor = run("doctor", "--blender", str(fake_blender))
    assert doctor.completed.returncode == 1
    stale_check = next(
        check
        for check in doctor.payload["checks"]
        if check["name"] == "session:stale"
    )
    assert stale_check["status"] == "fail"

    reclaimed = run("start", "--name", "stale", "--blender", str(fake_blender))
    assert reclaimed.completed.returncode == 0
    assert reclaimed.payload["reclaimed_stale"] is True
    assert "record is stale" in reclaimed.payload["message"]
    assert reclaimed.payload["session"]["mcp_port"] == 9876
    assert run("stop", "--name", "stale").completed.returncode == 0

    state_root = Path(environment[STATE_DIR_ENV_VAR])
    assert not (state_root / "sessions" / "stale" / "session.json").exists()


def test_reused_pid_record_is_stale_and_reclaimed_without_touching_process(
    isolated_cli,
    fake_blender: Path,
) -> None:
    environment, run = isolated_cli
    state_root = Path(environment[STATE_DIR_ENV_VAR])
    directory = session_directory(state_root, "reused")
    directory.mkdir(parents=True)
    token = "synthetic-stale-record"
    record = {
        "schema_version": 1,
        "name": "reused",
        "pid": os.getpid(),
        "process_start_time": "wrong-start-time",
        "mcp_port": 9876,
        "blender_path": str(fake_blender),
        "blender_version": "4.3.2",
        "stdout_log": str(directory / "stdout.log"),
        "stderr_log": str(directory / "stderr.log"),
        "state_dir": str(directory),
        "started_at": "2026-07-23T00:00:00+00:00",
        "owner_token": token,
        "controller_pid": os.getpid(),
        "controller_start_time": "wrong-controller-start-time",
    }
    (directory / "session.json").write_text(json.dumps(record), encoding="utf-8")
    (directory / "ownership.json").write_text(
        json.dumps({"token": token}),
        encoding="utf-8",
    )

    stale = run("status", "--name", "reused")
    assert stale.completed.returncode == 1
    assert stale.payload["status"] == "stale"

    reclaimed = run("start", "--name", "reused", "--blender", str(fake_blender))
    assert reclaimed.completed.returncode == 0
    assert reclaimed.payload["reclaimed_stale"] is True
    assert process_start_time(os.getpid()) is not None
    assert run("stop", "--name", "reused").completed.returncode == 0
