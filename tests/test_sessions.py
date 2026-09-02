from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from blendersessiond.processes import process_start_time
from blendersessiond.sessions import (
    BASE_MCP_PORT,
    BASE_MCP_PORT_ENV_VAR,
    SessionError,
    SessionRecord,
    _base_mcp_port,
    call_session,
    inspect_all_sessions,
    inspect_session,
    session_directory,
    start_session,
    stop_session,
    validate_session_name,
)

SESSION_ID = "bss_" + "a" * 32


@pytest.mark.parametrize("name", ["default", "shot-01", "shot.one", "shot_one", "9"])
def test_valid_session_names(name: str) -> None:
    assert validate_session_name(name) == name


def test_base_mcp_port_env_override() -> None:
    assert _base_mcp_port({}) == BASE_MCP_PORT
    assert _base_mcp_port({BASE_MCP_PORT_ENV_VAR: "23456"}) == 23456


@pytest.mark.parametrize("raw", ["not-a-port", "0", "65536", "-1", ""])
def test_invalid_base_mcp_port_override_is_rejected(raw: str) -> None:
    with pytest.raises(SessionError, match="not a port number"):
        _base_mcp_port({BASE_MCP_PORT_ENV_VAR: raw})


@pytest.mark.parametrize("name", ["", "../escape", ".hidden", "has space", "a" * 65])
def test_invalid_session_names(name: str) -> None:
    with pytest.raises(ValueError, match="Session Name"):
        validate_session_name(name)


def test_state_components_are_portable_and_case_distinct(tmp_path: Path) -> None:
    upper = session_directory(tmp_path, "CON")
    lower = session_directory(tmp_path, "con")

    assert upper != lower
    assert upper.name == "CON".encode("ascii").hex()
    assert lower.name == "con".encode("ascii").hex()
    assert upper.name.isalnum()


def test_listing_drops_session_removed_before_locked_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = session_directory(tmp_path, "stopped")
    directory.mkdir(parents=True)
    record_path = directory / "session.json"
    record_path.write_text("{}", encoding="utf-8")
    original_inspect = inspect_session

    def remove_then_inspect(**kwargs) -> object:
        record_path.unlink()
        return original_inspect(**kwargs)

    monkeypatch.setattr(
        "blendersessiond.sessions.inspect_session",
        remove_then_inspect,
    )

    assert inspect_all_sessions(state_root=tmp_path) == []


def test_start_time_mismatch_is_stale_even_for_a_live_pid(tmp_path: Path) -> None:
    directory = session_directory(tmp_path, "reused")
    directory.mkdir(parents=True)
    token = "test-token"
    record = SessionRecord(
        schema_version=1,
        session_id=SESSION_ID,
        name="reused",
        pid=os.getpid(),
        process_start_time="definitely-not-this-process",
        mcp_port=9876,
        blender_path="/fake/blender",
        blender_version="4.3.2",
        stdout_log=str(directory / "stdout.log"),
        stderr_log=str(directory / "stderr.log"),
        state_dir=str(directory),
        started_at="2026-07-23T00:00:00+00:00",
        owner_token=token,
        controller_pid=os.getpid(),
        controller_start_time="definitely-not-this-controller",
    )
    (directory / "session.json").write_text(
        json.dumps(record.__dict__),
        encoding="utf-8",
    )
    (directory / "ownership.json").write_text(
        json.dumps({"token": token}),
        encoding="utf-8",
    )

    result = inspect_session(name="reused", state_root=tmp_path)

    assert process_start_time(os.getpid()) is not None
    assert result.status == "stale"
    assert result.healthy is False
    assert "process start time does not match" in result.message


def test_missing_ownership_marker_is_stale(tmp_path: Path) -> None:
    directory = session_directory(tmp_path, "unowned")
    directory.mkdir(parents=True)
    record = SessionRecord(
        schema_version=1,
        session_id=SESSION_ID,
        name="unowned",
        pid=os.getpid(),
        process_start_time=process_start_time(os.getpid()) or "missing",
        mcp_port=9876,
        blender_path="/fake/blender",
        blender_version="4.3.2",
        stdout_log=str(directory / "stdout.log"),
        stderr_log=str(directory / "stderr.log"),
        state_dir=str(directory),
        started_at="2026-07-23T00:00:00+00:00",
        owner_token="missing",
        controller_pid=os.getpid(),
        controller_start_time=process_start_time(os.getpid()) or "missing",
    )
    (directory / "session.json").write_text(
        json.dumps(record.__dict__),
        encoding="utf-8",
    )

    result = inspect_session(name="unowned", state_root=tmp_path)

    assert result.status == "stale"
    assert "Ownership marker" in result.message


def test_legacy_record_gets_stable_opaque_session_identity(tmp_path: Path) -> None:
    directory = session_directory(tmp_path, "legacy")
    directory.mkdir(parents=True)
    token = "legacy-owner-token"
    record = SessionRecord(
        schema_version=1,
        session_id=SESSION_ID,
        name="legacy",
        pid=os.getpid(),
        process_start_time="not-this-process",
        mcp_port=9876,
        blender_path="/fake/blender",
        blender_version="4.3.2",
        stdout_log=str(directory / "stdout.log"),
        stderr_log=str(directory / "stderr.log"),
        state_dir=str(directory),
        started_at="2026-07-23T00:00:00+00:00",
        owner_token=token,
        controller_pid=os.getpid(),
        controller_start_time="not-this-controller",
    )
    legacy_payload = dict(record.__dict__)
    del legacy_payload["session_id"]
    (directory / "session.json").write_text(
        json.dumps(legacy_payload),
        encoding="utf-8",
    )
    (directory / "ownership.json").write_text(
        json.dumps({"token": token}),
        encoding="utf-8",
    )

    first = inspect_session(name="legacy", state_root=tmp_path)
    second = inspect_session(name="legacy", state_root=tmp_path)

    assert first.record is not None
    assert second.record is not None
    assert first.record.session_id.startswith("bss_legacy_")
    assert second.record.session_id == first.record.session_id
    assert token not in first.record.session_id


@pytest.mark.parametrize("operation", ["call", "stop"])
def test_wrong_session_identity_is_rejected_before_session_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    directory = session_directory(tmp_path, "replacement")
    directory.mkdir(parents=True)
    record = SessionRecord(
        schema_version=1,
        session_id=SESSION_ID,
        name="replacement",
        pid=os.getpid(),
        process_start_time=process_start_time(os.getpid()) or "missing",
        mcp_port=9876,
        blender_path="/fake/blender",
        blender_version="4.3.2",
        stdout_log=str(directory / "stdout.log"),
        stderr_log=str(directory / "stderr.log"),
        state_dir=str(directory),
        started_at="2026-07-23T00:00:00+00:00",
        owner_token="replacement-owner",
        controller_pid=os.getpid(),
        controller_start_time=process_start_time(os.getpid()) or "missing",
    )
    (directory / "session.json").write_text(
        json.dumps(record.__dict__),
        encoding="utf-8",
    )

    def unexpected_inspection(*_args, **_kwargs):
        pytest.fail("replacement Session was inspected before identity fencing")

    monkeypatch.setattr(
        "blendersessiond.sessions._inspect_unlocked",
        unexpected_inspection,
    )
    replacement_id = "bss_" + "z" * 32

    with pytest.raises(SessionError, match="identity does not match"):
        if operation == "call":
            call_session(
                "get_scene_info",
                name="replacement",
                expected_session_id=replacement_id,
                state_root=tmp_path,
            )
        else:
            stop_session(
                name="replacement",
                expected_session_id=replacement_id,
                state_root=tmp_path,
            )


def test_live_unverifiable_record_is_preserved_and_cannot_be_reclaimed(
    tmp_path: Path,
) -> None:
    directory = session_directory(tmp_path, "unverifiable")
    directory.mkdir(parents=True)
    record = SessionRecord(
        schema_version=1,
        session_id=SESSION_ID,
        name="unverifiable",
        pid=os.getpid(),
        process_start_time=process_start_time(os.getpid()) or "missing",
        mcp_port=9876,
        blender_path="/fake/blender",
        blender_version="4.3.2",
        stdout_log=str(directory / "stdout.log"),
        stderr_log=str(directory / "stderr.log"),
        state_dir=str(directory),
        started_at="2026-07-23T00:00:00+00:00",
        owner_token="missing-marker",
        controller_pid=os.getpid(),
        controller_start_time=process_start_time(os.getpid()) or "missing",
    )
    record_path = directory / "session.json"
    record_path.write_text(json.dumps(record.__dict__), encoding="utf-8")

    with pytest.raises(SessionError, match="cannot be reclaimed"):
        start_session(name="unverifiable", state_root=tmp_path)
    stopped = stop_session(
        name="unverifiable",
        expected_session_id=SESSION_ID,
        state_root=tmp_path,
    )

    assert stopped.stopped is False
    assert "Preserved the Session record" in stopped.message
    assert record_path.exists()
    assert process_start_time(os.getpid()) == record.process_start_time


def test_non_positive_record_pid_is_unreadable_and_never_signaled(
    tmp_path: Path,
) -> None:
    directory = session_directory(tmp_path, "invalid-pid")
    directory.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "name": "invalid-pid",
        "pid": 0,
        "process_start_time": "invalid",
        "mcp_port": 9876,
        "blender_path": "/fake/blender",
        "blender_version": "4.3.2",
        "stdout_log": str(directory / "stdout.log"),
        "stderr_log": str(directory / "stderr.log"),
        "state_dir": str(directory),
        "started_at": "2026-07-23T00:00:00+00:00",
        "owner_token": "token",
        "controller_pid": 0,
        "controller_start_time": "invalid",
    }
    (directory / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    (directory / "ownership.json").write_text(
        json.dumps({"token": "token"}),
        encoding="utf-8",
    )

    inspection = inspect_session(name="invalid-pid", state_root=tmp_path)
    stopped = stop_session(
        name="invalid-pid",
        expected_session_id=SESSION_ID,
        state_root=tmp_path,
    )

    assert inspection.status == "unreadable"
    assert "pid fields must be positive" in inspection.message
    assert stopped.stopped is False
