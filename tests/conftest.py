from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from blendersessiond.processes import (
    owned_tree_exists,
    terminate_owned_tree,
)
from blendersessiond.state import STATE_DIR_ENV_VAR


@dataclass(frozen=True)
class CliResult:
    completed: subprocess.CompletedProcess[str]
    payload: dict


@pytest.fixture
def isolated_cli(tmp_path: Path) -> Iterator[tuple[dict[str, str], object]]:
    state_root = tmp_path / "state"
    environment = dict(os.environ)
    environment[STATE_DIR_ENV_VAR] = str(state_root)

    def run(*arguments: str, timeout: float = 20) -> CliResult:
        completed = subprocess.run(
            [sys.executable, "-m", "blendersessiond", *arguments, "--json"],
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
        for record_path in sessions_root.glob("*/session.json"):
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
