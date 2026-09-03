from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blendersessiond import setup_owner

ATTEMPT_ID = "bbsa_" + "A" * 43
LAUNCH_ID = "bbsl_" + "B" * 43
SCRIPT = b"Write-Output 'owned bytes'\n"
SCRIPT_SHA256 = hashlib.sha256(SCRIPT).hexdigest()


def raw_launch(now: datetime) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "attempt_id": ATTEMPT_ID,
            "launch_id": LAUNCH_ID,
            "deadline_utc": (now + timedelta(minutes=4)).isoformat().replace("+00:00", "Z"),
            "operation_revision": "windows-setup-owner-v1",
            "script": {
                "artifact_id": f"{ATTEMPT_ID}.ps1",
                "size": len(SCRIPT),
                "sha256": SCRIPT_SHA256,
            },
        },
        separators=(",", ":"),
    ).encode()


def stage_script(root: Path) -> Path:
    path = setup_owner.script_path_for_attempt(root, ATTEMPT_ID)
    path.parent.mkdir(parents=True)
    path.write_bytes(SCRIPT)
    return path


def test_request_records_exact_raw_hash_and_both_identities(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    raw = raw_launch(now)
    request = setup_owner.accept_launch_request(raw, state_root=tmp_path, now=now)

    assert request.attempt_id == ATTEMPT_ID
    assert request.launch_id == LAUNCH_ID
    assert request.request_sha256 == hashlib.sha256(raw).hexdigest()
    assert request.script.artifact_id == f"{ATTEMPT_ID}.ps1"
    persisted = json.loads(
        (tmp_path / "setup-attempts" / ATTEMPT_ID / "request.json").read_text()
    )
    assert persisted["request_sha256"] == hashlib.sha256(raw).hexdigest()


def test_replay_requires_byte_identical_raw_json(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    raw = raw_launch(now)
    first = setup_owner.accept_launch_request(raw, state_root=tmp_path, now=now)
    assert first == setup_owner.accept_launch_request(raw, state_root=tmp_path, now=now)
    with pytest.raises(setup_owner.AttemptConflictError, match="different request"):
        setup_owner.accept_launch_request(raw + b"\n", state_root=tmp_path, now=now)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value | {"schema_version": 2}, "schema_version"),
        (lambda value: value | {"attempt_id": "../escape"}, "Attempt ID"),
        (lambda value: value | {"launch_id": "bbsl_short"}, "Launch ID"),
        (lambda value: value | {"operation_revision": "generic-command-v1"}, "operation_revision"),
        (lambda value: value | {"unknown": True}, "unknown"),
        (lambda value: value | {"deadline_utc": "2026-09-03T12:05:00.001Z"}, "five minutes"),
        (
            lambda value: value | {"script": value["script"] | {"artifact_id": "chosen.ps1"}},
            "artifact_id",
        ),
    ],
)
def test_request_is_strictly_bounded(tmp_path: Path, mutate, message: str) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    payload = mutate(json.loads(raw_launch(now)))
    with pytest.raises(setup_owner.SetupOwnerError, match=message):
        setup_owner.accept_launch_request(
            json.dumps(payload, separators=(",", ":")).encode(),
            state_root=tmp_path,
            now=now,
        )


def test_stop_before_dispatch_requires_full_fence_and_is_immutable(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    request = setup_owner.accept_launch_request(raw_launch(now), state_root=tmp_path, now=now)
    with pytest.raises(setup_owner.SetupOwnerError, match="request SHA-256"):
        setup_owner.stop_setup(
            ATTEMPT_ID,
            expected_request_sha256="0" * 64,
            expected_launch_id=LAUNCH_ID,
            state_root=tmp_path,
            now=now,
            platform_name="Windows",
        )
    first = setup_owner.stop_setup(
        ATTEMPT_ID,
        expected_request_sha256=request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        now=now,
        platform_name="Windows",
    )
    second = setup_owner.stop_setup(
        ATTEMPT_ID,
        expected_request_sha256=request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        now=now + timedelta(seconds=1),
        platform_name="Windows",
    )
    assert first == second
    assert first.terminal.outcome == "stopped_before_ownership"
    assert first.terminal.cleanup == "tree_gone"


def test_inflight_pre_receipt_stop_never_claims_tree_gone(tmp_path: Path, monkeypatch) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    stage_script(tmp_path)
    view = setup_owner.launch_setup(
        raw_launch(now),
        state_root=tmp_path,
        now=now,
        platform_name="Windows",
        dispatch_keeper=lambda _directory: None,
    )
    monkeypatch.setattr(setup_owner, "_STOP_WAIT_SECONDS", 0)
    stopped = setup_owner.stop_setup(
        ATTEMPT_ID,
        expected_request_sha256=view.request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        now=now,
        platform_name="Windows",
    )
    assert stopped.terminal.outcome == "launch_failed"
    assert stopped.terminal.cleanup == "cleanup_unverified"


def test_existing_terminal_prevents_late_dispatch(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    request = setup_owner.accept_launch_request(raw_launch(now), state_root=tmp_path, now=now)
    terminal = setup_owner.stop_setup(
        ATTEMPT_ID,
        expected_request_sha256=request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        now=now,
        platform_name="Windows",
    )
    replay = setup_owner.launch_setup(
        raw_launch(now),
        state_root=tmp_path,
        now=now,
        platform_name="Windows",
        dispatch_keeper=lambda _directory: (_ for _ in ()).throw(AssertionError("dispatch")),
    )
    assert replay == terminal


def test_only_three_json_records_define_attempt_state(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    stage_script(tmp_path)
    setup_owner.launch_setup(
        raw_launch(now), state_root=tmp_path, now=now, platform_name="Windows",
        dispatch_keeper=lambda _directory: None,
    )
    names = {path.name for path in (tmp_path / "setup-attempts" / ATTEMPT_ID).glob("*.json")}
    assert names <= {"request.json", "launch-receipt.json", "terminal.json"}


def test_non_windows_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(setup_owner.SetupOwnerError, match="Windows"):
        setup_owner.status_setup(ATTEMPT_ID, state_root=tmp_path, platform_name="Darwin")
