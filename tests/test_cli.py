from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from blendersessiond import cli
from blendersessiond.state import STATE_DIR_ENV_VAR
from blendersessiond.wire import AddonError


@pytest.mark.parametrize("verb", ["doctor", "start", "status", "stop"])
def test_relative_state_override_is_machine_readable_error(verb: str) -> None:
    environment = dict(os.environ)
    environment[STATE_DIR_ENV_VAR] = "relative/state"

    completed = subprocess.run(
        [sys.executable, "-m", "blendersessiond", verb, "--json"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["status"] == "error"
    assert payload["command"] == verb
    assert STATE_DIR_ENV_VAR in payload["message"]


def test_call_addon_error_uses_stderr_and_exit_one(monkeypatch, capsys) -> None:
    def fail_call(*_args, **_kwargs):
        raise AddonError("Object not found: Missing")

    monkeypatch.setattr(cli, "call_session", fail_call)

    exit_code = cli.main(["call", "get_object_info", "--params", '{"name":"Missing"}'])
    output = capsys.readouterr()

    assert exit_code == 1
    assert output.out == ""
    assert output.err == "ERROR: Object not found: Missing\n"


def test_call_rejects_non_object_params() -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["call", "get_scene_info", "--params", "[]"])

    assert error.value.code == 2


@pytest.mark.parametrize(
    ("scene_name", "create_file", "message"),
    [
        ("missing.blend", False, "Scene file does not exist"),
        ("not-a-blend.txt", True, "Scene path must end in .blend"),
    ],
)
def test_start_rejects_invalid_scene_as_usage_error_without_state(
    tmp_path: Path,
    fake_blender: Path,
    scene_name: str,
    create_file: bool,
    message: str,
) -> None:
    state_root = tmp_path / "state"
    scene = tmp_path / scene_name
    if create_file:
        scene.write_bytes(b"not a blend")
    environment = dict(os.environ)
    environment[STATE_DIR_ENV_VAR] = str(state_root)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "blendersessiond",
            "start",
            "--blender",
            str(fake_blender),
            "--scene",
            str(scene),
            "--json",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert message in completed.stderr
    assert not state_root.exists()
