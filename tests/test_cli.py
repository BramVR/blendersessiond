from __future__ import annotations

import json
import io
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from blendersessiond import cli
from blendersessiond.state import STATE_DIR_ENV_VAR
from blendersessiond.wire import AddonError, WireTimeoutError

SESSION_ID = "bss_" + "a" * 32


def test_capabilities_reports_required_blender_box_contract(capsys) -> None:
    exit_code = cli.main(["capabilities", "--require", "blender-box-v1"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "status": "compatible",
        "contract": "blender-box-v1",
        "capabilities": [
            "opaque-session-identity",
            "expect-session-id-call",
            "expect-session-id-stop",
            "bounded-call-read-timeout",
            "typed-call-error-reason",
        ],
    }


def test_capabilities_accepts_required_typed_call_error_reason(capsys) -> None:
    exit_code = cli.main(
        [
            "capabilities",
            "--require",
            "blender-box-v1",
            "--require-capability",
            "typed-call-error-reason",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "compatible"


def test_setup_owner_launch_reads_exact_raw_json_from_stdin(monkeypatch, capsys) -> None:
    raw = b'{"schema_version":1,"attempt_id":"exact bytes"}\n'

    def fake_launch(raw_request: bytes):
        assert raw_request == raw
        return SimpleNamespace(to_dict=lambda: {"schema_version": 1, "status": "accepted"})

    monkeypatch.setattr(cli, "launch_setup", fake_launch)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))

    assert cli.main(["setup-owner", "launch", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "accepted"


def test_setup_owner_stop_routes_complete_fence(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_stop(attempt_id: str, **kwargs):
        captured["attempt_id"] = attempt_id
        captured.update(kwargs)
        return SimpleNamespace(to_dict=lambda: {"schema_version": 1, "status": "terminal"})

    monkeypatch.setattr(cli, "stop_setup", fake_stop)
    attempt_id = "bbsa_" + "A" * 43
    launch_id = "bbsl_" + "B" * 43

    assert cli.main([
        "setup-owner", "stop", "--attempt-id", attempt_id,
        "--expect-request-sha256", "c" * 64,
        "--expect-launch-id", launch_id, "--json",
    ]) == 0
    assert captured == {
        "attempt_id": attempt_id,
        "expected_request_sha256": "c" * 64,
        "expected_launch_id": launch_id,
    }
    assert json.loads(capsys.readouterr().out)["status"] == "terminal"


def test_setup_owner_parser_rejects_generic_process_inputs() -> None:
    parser = cli.build_parser()
    for option in ("--state-root", "--executable", "--args", "--env", "--cwd"):
        with pytest.raises(SystemExit) as error:
            parser.parse_args(["setup-owner", "launch", option, "value", "--json"])
        assert error.value.code == 2


def test_windows_setup_owner_capability_fails_closed_off_windows(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_platform_system", lambda: "Linux")
    exit_code = cli.main([
        "capabilities", "--require", "blender-box-v1",
        "--require-capability", "windows-setup-owner-v1",
    ])
    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "incompatible"


def test_windows_setup_owner_capability_runs_runtime_self_test(monkeypatch, capsys) -> None:
    calls = 0

    def pass_self_test() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(cli, "_platform_system", lambda: "Windows")
    monkeypatch.setattr(cli, "windows_setup_owner_self_test", pass_self_test)
    exit_code = cli.main([
        "capabilities", "--require", "blender-box-v1",
        "--require-capability", "windows-setup-owner-v1",
    ])
    assert exit_code == 0
    assert calls == 1
    assert "windows-setup-owner-v1" in json.loads(capsys.readouterr().out)["capabilities"]


def test_capabilities_rejects_unknown_contract() -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["capabilities", "--require", "unknown-v1"])

    assert error.value.code == 2


@pytest.mark.parametrize("verb", ["doctor", "start", "status", "stop"])
def test_relative_state_override_is_machine_readable_error(verb: str) -> None:
    environment = dict(os.environ)
    environment[STATE_DIR_ENV_VAR] = "relative/state"

    arguments = [sys.executable, "-m", "blendersessiond", verb]
    if verb == "stop":
        arguments.extend(["--expect-session-id", SESSION_ID])
    completed = subprocess.run(
        [*arguments, "--json"],
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

    exit_code = cli.main(
        [
            "call",
            "get_object_info",
            "--params",
            '{"name":"Missing"}',
            "--expect-session-id",
            SESSION_ID,
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 1
    assert output.out == ""
    assert output.err == "ERROR: Object not found: Missing\n"


def test_call_json_timeout_reports_typed_reason(monkeypatch, capsys) -> None:
    def fail_call(*_args, **_kwargs):
        raise WireTimeoutError("Timed out waiting for Blender")

    monkeypatch.setattr(cli, "call_session", fail_call)

    exit_code = cli.main(
        [
            "call",
            "get_scene_info",
            "--expect-session-id",
            SESSION_ID,
            "--json",
        ]
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "status": "error",
        "command": "call",
        "reason": "timeout",
        "message": "Timed out waiting for Blender",
    }


def test_call_passes_bounded_read_timeout(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_call(*_args, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(cli, "call_session", fake_call)

    exit_code = cli.main(
        [
            "call",
            "get_scene_info",
            "--expect-session-id",
            SESSION_ID,
            "--read-timeout",
            "600",
        ]
    )

    assert exit_code == 0
    assert captured["read_timeout"] == 600.0
    assert json.loads(capsys.readouterr().out) == {"ok": True}


@pytest.mark.parametrize("value", ["0", "3600.1", "not-a-number"])
def test_call_rejects_invalid_read_timeout(value: str) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "call",
                "get_scene_info",
                "--expect-session-id",
                SESSION_ID,
                "--read-timeout",
                value,
            ]
        )

    assert error.value.code == 2


def test_call_rejects_non_object_params() -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "call",
                "get_scene_info",
                "--params",
                "[]",
                "--expect-session-id",
                SESSION_ID,
            ]
        )

    assert error.value.code == 2


@pytest.mark.parametrize(
    "arguments",
    [
        ["call", "get_scene_info"],
        ["stop"],
    ],
)
def test_stale_sensitive_commands_require_session_identity(
    arguments: list[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)

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
