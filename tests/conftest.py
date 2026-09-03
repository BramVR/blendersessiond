from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from blendersessiond import setup_owner
from blendersessiond.processes import (
    owned_tree_exists,
    terminate_owned_tree,
)
from blendersessiond.sessions import BASE_MCP_PORT_ENV_VAR
from blendersessiond.state import STATE_DIR_ENV_VAR


@dataclass(frozen=True)
class CliResult:
    completed: subprocess.CompletedProcess[str]
    payload: dict


@pytest.fixture
def bypass_setup_path_authority(monkeypatch):
    guarded = setup_owner._guard_setup_path

    @contextmanager
    def unguarded(*_args, **_kwargs):
        yield

    monkeypatch.setattr(setup_owner, "_guard_setup_path", unguarded)
    return guarded


def _free_mcp_port_base(span: int = 8) -> int:
    # Sessions get base, base+1, ... so a fixed base (9876) collides with any
    # real Session running on the developer machine; probe an ephemeral base
    # whose next few ports are also free.
    for _attempt in range(64):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            base = probe.getsockname()[1]
        if base + span > 65535:
            continue
        try:
            for offset in range(span):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as scan:
                    scan.bind(("127.0.0.1", base + offset))
        except OSError:
            continue
        return base
    raise RuntimeError("could not find a free MCP port range for tests")


@pytest.fixture
def isolated_cli(tmp_path: Path) -> Iterator[tuple[dict[str, str], object]]:
    state_root = tmp_path / "state"
    environment = dict(os.environ)
    environment[STATE_DIR_ENV_VAR] = str(state_root)
    environment[BASE_MCP_PORT_ENV_VAR] = str(_free_mcp_port_base())

    def run(*arguments: str, timeout: float = 20) -> CliResult:
        command_arguments = list(arguments)
        if (
            command_arguments
            and command_arguments[0] in {"call", "stop"}
            and "--expect-session-id" not in command_arguments
        ):
            name = "default"
            if "--name" in command_arguments:
                name = command_arguments[command_arguments.index("--name") + 1]
            record_path = (
                state_root / "sessions" / name.encode("ascii").hex() / "session.json"
            )
            record = json.loads(record_path.read_text(encoding="utf-8"))
            command_arguments.extend(
                ["--expect-session-id", record["session_id"]]
            )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "blendersessiond",
                *command_arguments,
                "--json",
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        payload = json.loads(completed.stdout)
        return CliResult(completed, payload)

    yield environment, run

    sessions_root = state_root / "sessions"
    if sessions_root.is_dir():
        record_paths = (
            *sessions_root.glob("*/session.json"),
            *sessions_root.glob("*/launching.json"),
        )
        for record_path in record_paths:
            try:
                payload = json.loads(record_path.read_text(encoding="utf-8"))
                pid = payload["pid"]
                start_time = payload["process_start_time"]
                controller_pid = payload["controller_pid"]
                controller_start_time = payload["controller_start_time"]
                if owned_tree_exists(
                    pid,
                    start_time,
                    controller_pid=controller_pid,
                    controller_start_time=controller_start_time,
                ):
                    terminate_owned_tree(
                        pid,
                        start_time,
                        controller_pid=controller_pid,
                        controller_start_time=controller_start_time,
                    )
            except (KeyError, OSError, ValueError, json.JSONDecodeError):
                pass


@pytest.fixture
def fake_blender(tmp_path: Path) -> Path:
    implementation = Path(__file__).parent / "fixtures" / "fake_blender.py"
    if os.name == "nt":
        wrapper = tmp_path / "blender.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{implementation}" %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = tmp_path / "blender"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{implementation}" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return wrapper
