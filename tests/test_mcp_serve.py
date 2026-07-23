from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from blendersessiond.processes import process_start_time, terminate_owned_tree

pytestmark = pytest.mark.e2e


def _install_fake_uvx(tmp_path: Path, environment: dict[str, str]) -> Path:
    executable = tmp_path / ("uvx.cmd" if os.name == "nt" else "uvx")
    implementation = """\
import json
import os
import sys
from pathlib import Path

Path(os.environ["FAKE_UVX_RECORD"]).write_text(
    json.dumps(
        {
            "argv": sys.argv[1:],
            "host": os.environ.get("BLENDER_HOST"),
            "port": os.environ.get("BLENDER_PORT"),
        }
    ),
    encoding="utf-8",
)
sys.stdout.write(sys.stdin.read())
"""
    if os.name == "nt":
        script = tmp_path / "fake_uvx.py"
        script.write_text(implementation, encoding="utf-8")
        executable.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
            encoding="utf-8",
        )
    else:
        executable.write_text(
            f"#!{sys.executable}\n{implementation}",
            encoding="utf-8",
        )
        executable.chmod(0o755)
    environment["PATH"] = str(tmp_path) + os.pathsep + environment.get("PATH", "")
    return executable


def _run_mcp_serve(
    environment: dict[str, str],
    *,
    name: str = "default",
    input_text: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "blendersessiond",
            "mcp-serve",
            "--name",
            name,
        ],
        env=environment,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def _wait_until_gone(pid: int, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process_start_time(pid) is None:
            return
        time.sleep(0.05)
    pytest.fail(f"process {pid} did not exit")


def test_missing_session_fails_before_uvx(
    isolated_cli,
    tmp_path: Path,
) -> None:
    environment, _run = isolated_cli
    _install_fake_uvx(tmp_path, environment)
    marker = tmp_path / "uvx.json"
    environment["FAKE_UVX_RECORD"] = str(marker)

    completed = _run_mcp_serve(environment, name="missing")

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == (
        "ERROR: Session 'missing' is not-found; start it with "
        "`blendersessiond start --name missing`.\n"
    )
    assert not marker.exists()


def test_stale_session_fails_before_uvx(
    isolated_cli,
    fake_blender: Path,
    tmp_path: Path,
) -> None:
    environment, run = isolated_cli
    started = run("start", "--name", "stale", "--blender", str(fake_blender))
    session = started.payload["session"]
    durable_record = json.loads(
        (Path(session["state_dir"]) / "session.json").read_text(encoding="utf-8")
    )
    terminate_owned_tree(
        session["pid"],
        session["process_start_time"],
        controller_pid=durable_record["controller_pid"],
        controller_start_time=durable_record["controller_start_time"],
    )
    _wait_until_gone(session["pid"])
    _install_fake_uvx(tmp_path, environment)
    marker = tmp_path / "uvx.json"
    environment["FAKE_UVX_RECORD"] = str(marker)

    completed = _run_mcp_serve(environment, name="stale")

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == (
        "ERROR: Session 'stale' is stale; start it with "
        "`blendersessiond start --name stale`.\n"
    )
    assert not marker.exists()


def test_unhealthy_session_fails_before_uvx(
    isolated_cli,
    fake_blender: Path,
    tmp_path: Path,
) -> None:
    environment, run = isolated_cli
    environment["FAKE_BLENDER_DISABLE_MCP_SOCKET"] = "1"
    started = run("start", "--name", "unhealthy", "--blender", str(fake_blender))
    assert started.completed.returncode == 0
    _install_fake_uvx(tmp_path, environment)
    marker = tmp_path / "uvx.json"
    environment["FAKE_UVX_RECORD"] = str(marker)

    completed = _run_mcp_serve(environment, name="unhealthy")

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == (
        "ERROR: Session 'unhealthy' is unhealthy; check "
        "`blendersessiond doctor`, then restart it with "
        "`blendersessiond start --name unhealthy`.\n"
    )
    assert not marker.exists()
    assert run("stop", "--name", "unhealthy").completed.returncode == 0


def test_uvx_missing_names_prerequisite(
    isolated_cli,
    fake_blender: Path,
    tmp_path: Path,
) -> None:
    environment, run = isolated_cli
    started = run("start", "--blender", str(fake_blender))
    assert started.completed.returncode == 0
    environment["PATH"] = str(tmp_path / "empty-path")

    completed = _run_mcp_serve(environment)

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == (
        "ERROR: uvx is required to run blender-mcp; install the uv "
        "prerequisite and ensure uvx is on PATH.\n"
    )
    assert run("stop").completed.returncode == 0


def test_named_sessions_exec_uvx_with_their_own_ports_and_stdio(
    isolated_cli,
    fake_blender: Path,
    tmp_path: Path,
) -> None:
    environment, run = isolated_cli
    first = run("start", "--name", "first", "--blender", str(fake_blender))
    second = run("start", "--name", "second", "--blender", str(fake_blender))
    assert first.completed.returncode == 0
    assert second.completed.returncode == 0
    _install_fake_uvx(tmp_path, environment)

    records = {}
    for name in ("first", "second"):
        marker = tmp_path / f"{name}.json"
        environment["FAKE_UVX_RECORD"] = str(marker)
        completed = _run_mcp_serve(
            environment,
            name=name,
            input_text=f"stdio:{name}\n",
        )
        assert completed.returncode == 0
        assert completed.stdout == f"stdio:{name}\n"
        assert completed.stderr == ""
        records[name] = json.loads(marker.read_text(encoding="utf-8"))

    assert records == {
        "first": {
            "argv": ["blender-mcp"],
            "host": "127.0.0.1",
            "port": str(first.payload["session"]["mcp_port"]),
        },
        "second": {
            "argv": ["blender-mcp"],
            "host": "127.0.0.1",
            "port": str(second.payload["session"]["mcp_port"]),
        },
    }
    assert run("stop", "--name", "first").completed.returncode == 0
    assert run("stop", "--name", "second").completed.returncode == 0
