from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from blendersessiond import setup_owner

ATTEMPT_ID = "a" * 32
SCRIPT = b'Write-Output \'{"status":"configured"}\'\n'
SCRIPT_SHA256 = hashlib.sha256(SCRIPT).hexdigest()


def stage_script(root: Path, content: bytes = SCRIPT) -> Path:
    path = root / "setup-attempts" / ATTEMPT_ID / "setup.ps1"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    return path


def test_launch_publishes_request_before_spawning_keeper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage_script(tmp_path)
    observed: list[dict[str, object]] = []

    def fake_spawn(attempt_dir: Path) -> tuple[int, str]:
        observed.append(json.loads((attempt_dir / "request.json").read_text()))
        setup_owner._write_create_once(
            attempt_dir / "launch-receipt.json",
            {
                "schema_version": 1,
                "attempt_id": ATTEMPT_ID,
                "request_sha256": observed[0]["request_sha256"],
                "launch_id": "launch_test",
                "keeper_pid": 10,
                "keeper_creation_time": "windows:keeper",
                "root_pid": 11,
                "root_creation_time": "windows:root",
                "owned_at": "2026-09-03T00:00:00Z",
            },
        )
        return 10, "windows:keeper"

    monkeypatch.setattr(setup_owner, "_spawn_keeper", fake_spawn)

    result = setup_owner.launch_setup(
        state_root=tmp_path,
        attempt_id=ATTEMPT_ID,
        script_sha256=SCRIPT_SHA256,
        deadline_unix_ms=2_000_000_000_000,
        platform_name="Windows",
    )

    assert result["status"] == "owned"
    assert observed[0]["script_size"] == len(SCRIPT)
    assert observed[0]["script_sha256"] == SCRIPT_SHA256


def test_launch_is_byte_identical_replay(tmp_path: Path, monkeypatch) -> None:
    stage_script(tmp_path)
    calls = 0

    def fake_spawn(attempt_dir: Path) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        request = json.loads((attempt_dir / "request.json").read_text())
        setup_owner._write_create_once(
            attempt_dir / "launch-receipt.json",
            {
                "schema_version": 1,
                "attempt_id": ATTEMPT_ID,
                "request_sha256": request["request_sha256"],
                "launch_id": "launch_test",
                "keeper_pid": 10,
                "keeper_creation_time": "windows:keeper",
                "root_pid": 11,
                "root_creation_time": "windows:root",
                "owned_at": "2026-09-03T00:00:00Z",
            },
        )
        return 10, "windows:keeper"

    monkeypatch.setattr(setup_owner, "_spawn_keeper", fake_spawn)
    arguments = {
        "state_root": tmp_path,
        "attempt_id": ATTEMPT_ID,
        "script_sha256": SCRIPT_SHA256,
        "deadline_unix_ms": 2_000_000_000_000,
        "platform_name": "Windows",
    }

    first = setup_owner.launch_setup(**arguments)
    second = setup_owner.launch_setup(**arguments)

    assert first == second
    assert calls == 1


def test_lost_launch_response_reconciles_the_published_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage_script(tmp_path)
    calls = 0

    def ambiguous_spawn(attempt_dir: Path) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        request = json.loads((attempt_dir / "request.json").read_text())
        setup_owner._write_create_once(
            attempt_dir / "launch-receipt.json",
            {
                "schema_version": 1,
                "attempt_id": ATTEMPT_ID,
                "request_sha256": request["request_sha256"],
                "launch_id": "launch_test",
                "keeper_pid": 10,
                "keeper_creation_time": "windows:keeper",
                "root_pid": 11,
                "root_creation_time": "windows:root",
                "job_name": f"Local\\blendersessiond-setup-{ATTEMPT_ID}",
                "owned_at": "2026-09-03T00:00:00Z",
            },
        )
        raise OSError("launch response was lost")

    monkeypatch.setattr(setup_owner, "_spawn_keeper", ambiguous_spawn)
    arguments = {
        "state_root": tmp_path,
        "attempt_id": ATTEMPT_ID,
        "script_sha256": SCRIPT_SHA256,
        "deadline_unix_ms": 2_000_000_000_000,
        "platform_name": "Windows",
    }

    with pytest.raises(OSError, match="response was lost"):
        setup_owner.launch_setup(**arguments)
    reconciled = setup_owner.launch_setup(**arguments)

    assert reconciled["status"] == "owned"
    assert reconciled["receipt"]["launch_id"] == "launch_test"
    assert calls == 1


def test_abandoned_pre_receipt_claim_becomes_an_immutable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage_script(tmp_path)
    attempt_dir = tmp_path / "setup-attempts" / ATTEMPT_ID
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
            "launch_id": "launch_abandoned",
            "claimed_at": "2026-09-03T00:00:00Z",
        },
    )
    monkeypatch.setattr(setup_owner, "_LAUNCH_WAIT_SECONDS", 0)
    monkeypatch.setattr(
        setup_owner,
        "_spawn_keeper",
        lambda _attempt_dir: (_ for _ in ()).throw(AssertionError("replacement")),
    )

    first = setup_owner.launch_setup(
        state_root=tmp_path,
        attempt_id=ATTEMPT_ID,
        script_sha256=SCRIPT_SHA256,
        deadline_unix_ms=2_000_000_000_000,
        platform_name="Windows",
    )
    second = setup_owner.status_setup(
        state_root=tmp_path,
        attempt_id=ATTEMPT_ID,
        platform_name="Windows",
    )

    assert first == second
    assert first["status"] == "launch_failed"
    assert first["cleanup"] == "cleanup_unverified"
    assert json.loads((attempt_dir / "terminal.json").read_text()) == first


def test_launch_rejects_request_replacement(tmp_path: Path, monkeypatch) -> None:
    stage_script(tmp_path)
    monkeypatch.setattr(
        setup_owner,
        "_spawn_keeper",
        lambda _attempt_dir: (_ for _ in ()).throw(RuntimeError("not reached")),
    )
    attempt_dir = tmp_path / "setup-attempts" / ATTEMPT_ID
    setup_owner._write_create_once(
        attempt_dir / "request.json",
        {
            "schema_version": 1,
            "attempt_id": ATTEMPT_ID,
            "request_sha256": "b" * 64,
            "script_sha256": "c" * 64,
            "script_size": 1,
            "deadline_unix_ms": 2_000_000_000_000,
            "operation": "blender-box-windows-setup-v1",
        },
    )

    with pytest.raises(setup_owner.SetupOwnerError, match="different request"):
        setup_owner.launch_setup(
            state_root=tmp_path,
            attempt_id=ATTEMPT_ID,
            script_sha256=SCRIPT_SHA256,
            deadline_unix_ms=2_000_000_000_000,
            platform_name="Windows",
        )


def test_stop_before_ownership_is_immutable_and_prevents_launch(
    tmp_path: Path, monkeypatch
) -> None:
    stage_script(tmp_path)
    attempt_dir = tmp_path / "setup-attempts" / ATTEMPT_ID
    request = setup_owner._request_payload(
        ATTEMPT_ID, SCRIPT_SHA256, len(SCRIPT), 2_000_000_000_000
    )
    setup_owner._write_create_once(attempt_dir / "request.json", request)
    monkeypatch.setattr(
        setup_owner,
        "_spawn_keeper",
        lambda _attempt_dir: (_ for _ in ()).throw(RuntimeError("not reached")),
    )

    first = setup_owner.stop_setup(
        state_root=tmp_path,
        attempt_id=ATTEMPT_ID,
        platform_name="Windows",
    )
    second = setup_owner.stop_setup(
        state_root=tmp_path,
        attempt_id=ATTEMPT_ID,
        platform_name="Windows",
    )
    replay = setup_owner.launch_setup(
        state_root=tmp_path,
        attempt_id=ATTEMPT_ID,
        script_sha256=SCRIPT_SHA256,
        deadline_unix_ms=2_000_000_000_000,
        platform_name="Windows",
    )

    assert first == second == replay
    assert first["status"] == "stopped_before_ownership"
    assert first["cleanup"] == "tree_gone"


def test_status_does_not_dispatch_owner(tmp_path: Path, monkeypatch) -> None:
    stage_script(tmp_path)
    monkeypatch.setattr(
        setup_owner,
        "_spawn_keeper",
        lambda _attempt_dir: (_ for _ in ()).throw(RuntimeError("not reached")),
    )

    result = setup_owner.status_setup(
        state_root=tmp_path,
        attempt_id=ATTEMPT_ID,
        platform_name="Windows",
    )

    assert result["status"] == "not_found"


def test_owner_loss_publishes_one_stable_tree_gone_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage_script(tmp_path)
    attempt_dir = tmp_path / "setup-attempts" / ATTEMPT_ID
    request = setup_owner._request_payload(
        ATTEMPT_ID, SCRIPT_SHA256, len(SCRIPT), 2_000_000_000_000
    )
    setup_owner._write_create_once(attempt_dir / "request.json", request)
    setup_owner._write_create_once(
        attempt_dir / "launch-receipt.json",
        {
            "schema_version": 1,
            "attempt_id": ATTEMPT_ID,
            "request_sha256": request["request_sha256"],
            "launch_id": "launch_lost",
            "keeper_pid": 10,
            "keeper_creation_time": "windows:keeper",
            "root_pid": 11,
            "root_creation_time": "windows:root",
            "job_name": f"Local\\blendersessiond-setup-{ATTEMPT_ID}",
            "owned_at": "2026-09-03T00:00:00Z",
        },
    )
    monkeypatch.setattr(setup_owner, "process_matches", lambda *_args: False)
    monkeypatch.setattr(setup_owner, "_job_exists", lambda _name: False)

    first = setup_owner.status_setup(
        state_root=tmp_path,
        attempt_id=ATTEMPT_ID,
        platform_name="Windows",
    )
    second = setup_owner.stop_setup(
        state_root=tmp_path,
        attempt_id=ATTEMPT_ID,
        platform_name="Windows",
    )

    assert first == second
    assert first["status"] == "owner_lost"
    assert first["cleanup"] == "tree_gone"
    assert json.loads((attempt_dir / "terminal.json").read_text()) == first


def test_pid_reuse_is_never_terminated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage_script(tmp_path)
    attempt_dir = tmp_path / "setup-attempts" / ATTEMPT_ID
    request = setup_owner._request_payload(
        ATTEMPT_ID, SCRIPT_SHA256, len(SCRIPT), 2_000_000_000_000
    )
    setup_owner._write_create_once(attempt_dir / "request.json", request)
    setup_owner._write_create_once(
        attempt_dir / "launch-receipt.json",
        {
            "schema_version": 1,
            "attempt_id": ATTEMPT_ID,
            "request_sha256": request["request_sha256"],
            "launch_id": "launch_stale_pid",
            "keeper_pid": 10,
            "keeper_creation_time": "windows:old-keeper",
            "root_pid": 11,
            "root_creation_time": "windows:old-root",
            "job_name": f"Local\\blendersessiond-setup-{ATTEMPT_ID}",
            "owned_at": "2026-09-03T00:00:00Z",
        },
    )
    monkeypatch.setattr(setup_owner, "process_matches", lambda *_args: False)
    monkeypatch.setattr(setup_owner, "_job_exists", lambda _name: True)
    monkeypatch.setattr(setup_owner, "_STOP_WAIT_SECONDS", 0)
    terminated: list[tuple[int, str]] = []
    monkeypatch.setattr(
        setup_owner,
        "_terminate_exact_keeper",
        lambda pid, identity: terminated.append((pid, identity)),
    )

    first = setup_owner.stop_setup(
        state_root=tmp_path,
        attempt_id=ATTEMPT_ID,
        platform_name="Windows",
    )
    second = setup_owner.stop_setup(
        state_root=tmp_path,
        attempt_id=ATTEMPT_ID,
        platform_name="Windows",
    )

    assert first == second
    assert first["status"] == "cleanup_unverified"
    assert first["cleanup"] == "cleanup_unverified"
    assert terminated == []


@pytest.mark.parametrize("attempt_id", ["", "A" * 32, "../escape", "a" * 31])
def test_attempt_identity_is_strict(attempt_id: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Attempt ID"):
        setup_owner.status_setup(
            state_root=tmp_path,
            attempt_id=attempt_id,
            platform_name="Windows",
        )


def test_non_windows_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(setup_owner.SetupOwnerError, match="Windows"):
        setup_owner.status_setup(
            state_root=tmp_path,
            attempt_id=ATTEMPT_ID,
            platform_name="Darwin",
        )
