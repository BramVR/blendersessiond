from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from blendersessiond import setup_owner, windows_setup_process

ATTEMPT_ID = "a" * 32
SCRIPT = b"Write-Output 'owned bytes'\n"
SCRIPT_SHA256 = hashlib.sha256(SCRIPT).hexdigest()


def test_keeper_uses_validated_bytes_and_publishes_receipt_before_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_dir = tmp_path / "setup-attempts" / ATTEMPT_ID
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "setup.ps1").write_bytes(SCRIPT)
    request = setup_owner._request_payload(
        ATTEMPT_ID, SCRIPT_SHA256, len(SCRIPT), 2_000_000_000_000
    )
    setup_owner._write_create_once(attempt_dir / "request.json", request)
    setup_owner._write_create_once(
        attempt_dir / "launch-claim.json",
        {
            "schema_version": 1,
            "attempt_id": ATTEMPT_ID,
            "request_sha256": request["request_sha256"],
            "launch_id": "launch_test",
            "claimed_at": "2026-09-03T00:00:00Z",
        },
    )
    child = windows_setup_process._OwnedProcess(
        process_handle=101,
        thread_handle=102,
        process_id=103,
        stdin_write=104,
        stdout_read=105,
        stderr_read=106,
        child_handles=(),
        attribute_list=None,
        attribute_buffer=None,
    )
    created: list[tuple[int, str]] = []
    started: list[bytes] = []

    def create(script_size: int, script_sha256: str, job: int, *, cwd: Path):
        created.append((script_size, script_sha256))
        assert job == 100
        assert cwd == attempt_dir
        return child

    def start_io(owned, content: bytes, output, threads) -> None:
        assert owned is child
        assert json.loads((attempt_dir / "launch-receipt.json").read_text())[
            "root_pid"
        ] == 103
        started.append(content)

    monkeypatch.setattr(windows_setup_process, "_create_kill_on_close_job", lambda _: 100)
    monkeypatch.setattr(windows_setup_process, "_create_suspended_setup_process", create)
    monkeypatch.setattr(windows_setup_process, "_creation_time_from_handle", lambda _: "windows:root")
    monkeypatch.setattr(windows_setup_process, "process_start_time", lambda _: "windows:keeper")
    monkeypatch.setattr(windows_setup_process, "_is_process_in_job", lambda *_: True)
    monkeypatch.setattr(windows_setup_process, "_start_process_io", start_io)
    monkeypatch.setattr(windows_setup_process, "_resume_process", lambda _: None)
    monkeypatch.setattr(windows_setup_process, "_wait_for_process", lambda *_: "exited")
    monkeypatch.setattr(windows_setup_process, "_process_exit_code", lambda _: 0)
    monkeypatch.setattr(windows_setup_process, "_active_processes", lambda _: 0)
    monkeypatch.setattr(windows_setup_process, "_wait_for_empty_job", lambda _: None)
    monkeypatch.setattr(windows_setup_process, "_finish_process_io", lambda *_: None)
    monkeypatch.setattr(windows_setup_process, "_close_owned_process", lambda _: None)
    monkeypatch.setattr(windows_setup_process, "_close_handle", lambda _: None)

    assert windows_setup_process.run_keeper(attempt_dir) == 0

    terminal = json.loads((attempt_dir / "terminal.json").read_text())
    assert created == [(len(SCRIPT), SCRIPT_SHA256)]
    assert started == [SCRIPT]
    assert terminal["status"] == "completed"
    assert terminal["process"] == "exited"
    assert terminal["cleanup"] == "tree_gone"


def test_keeper_rechecks_pre_ownership_stop_before_process_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_dir = tmp_path / "setup-attempts" / ATTEMPT_ID
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "setup.ps1").write_bytes(SCRIPT)
    request = setup_owner._request_payload(
        ATTEMPT_ID, SCRIPT_SHA256, len(SCRIPT), 2_000_000_000_000
    )
    setup_owner._write_create_once(attempt_dir / "request.json", request)
    setup_owner._write_create_once(
        attempt_dir / "launch-claim.json",
        {
            "schema_version": 1,
            "attempt_id": ATTEMPT_ID,
            "request_sha256": request["request_sha256"],
            "launch_id": "launch_test",
            "claimed_at": "2026-09-03T00:00:00Z",
        },
    )
    setup_owner._write_create_once(
        attempt_dir / "terminal.json",
        {
            "schema_version": 1,
            "attempt_id": ATTEMPT_ID,
            "request_sha256": request["request_sha256"],
            "status": "stopped_before_ownership",
            "process": "not_started",
            "cleanup": "tree_gone",
            "finished_at": "2026-09-03T00:00:01Z",
        },
    )
    monkeypatch.setattr(
        windows_setup_process,
        "_create_kill_on_close_job",
        lambda _: (_ for _ in ()).throw(AssertionError("created Job after stop")),
    )

    assert windows_setup_process.run_keeper(attempt_dir) == 0


def test_powershell_bootstrap_reads_exact_bytes_and_rejects_trailing_input() -> None:
    encoded = windows_setup_process._powershell_bootstrap(
        len(SCRIPT), SCRIPT_SHA256
    )

    assert "OpenStandardInput" in encoded
    assert "UTF8Encoding($false, $true)" in encoded
    assert "SHA256" in encoded
    assert "ReadByte() -ne -1" in encoded
    assert str(len(SCRIPT)) in encoded
    assert SCRIPT_SHA256 in encoded
