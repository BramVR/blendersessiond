from __future__ import annotations

import hashlib
import json
import threading
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
            "deadline_utc": (now + timedelta(minutes=4))
            .isoformat()
            .replace("+00:00", "Z"),
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


def test_byte_identical_replay_remains_valid_after_deadline(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    raw = raw_launch(now)
    first = setup_owner.accept_launch_request(raw, state_root=tmp_path, now=now)
    replay = setup_owner.accept_launch_request(
        raw,
        state_root=tmp_path,
        now=now + timedelta(hours=1),
    )
    assert replay == first


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value | {"schema_version": 2}, "schema_version"),
        (lambda value: value | {"schema_version": True}, "schema_version"),
        (lambda value: value | {"attempt_id": "../escape"}, "Attempt ID"),
        (lambda value: value | {"launch_id": "bbsl_short"}, "Launch ID"),
        (
            lambda value: value | {"operation_revision": "generic-command-v1"},
            "operation_revision",
        ),
        (lambda value: value | {"unknown": True}, "unknown"),
        (
            lambda value: value | {"deadline_utc": "2026-09-03T12:05:00.001Z"},
            "five minutes",
        ),
        (
            lambda value: (
                value | {"script": value["script"] | {"artifact_id": "chosen.ps1"}}
            ),
            "artifact_id",
        ),
        (lambda value: value | {"deadline_utc": float("nan")}, "UTF-8 JSON"),
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


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_version":1,"schema_version":1}'.encode(),
        raw_launch(datetime(2026, 9, 3, 12, tzinfo=UTC)).decode().encode("utf-16"),
        b"\xef\xbb\xbf{}",
    ],
)
def test_request_rejects_duplicate_or_non_utf8_json(
    tmp_path: Path, raw: bytes
) -> None:
    with pytest.raises(setup_owner.SetupOwnerError, match="UTF-8 JSON"):
        setup_owner.accept_launch_request(raw, state_root=tmp_path)


def test_stop_before_dispatch_requires_full_fence_and_is_immutable(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    request = setup_owner.accept_launch_request(
        raw_launch(now), state_root=tmp_path, now=now
    )
    with pytest.raises(setup_owner.SetupOwnerError, match="request SHA-256"):
        setup_owner.stop_setup(
            ATTEMPT_ID,
            expected_request_sha256="0" * 64,
            expected_launch_id=LAUNCH_ID,
            state_root=tmp_path,
            now=now,
            platform_name="Windows",
        )
    with pytest.raises(setup_owner.SetupOwnerError, match="Launch ID"):
        setup_owner.stop_setup(
            ATTEMPT_ID,
            expected_request_sha256=request.request_sha256,
            expected_launch_id="bbsl_" + "C" * 43,
            state_root=tmp_path,
            now=now,
            platform_name="Windows",
        )
    assert not (
        tmp_path / "setup-attempts" / ATTEMPT_ID / "terminal.json"
    ).exists()
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


def test_inflight_pre_receipt_stop_never_claims_tree_gone(
    tmp_path: Path, monkeypatch
) -> None:
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
    request = setup_owner.accept_launch_request(
        raw_launch(now), state_root=tmp_path, now=now
    )
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
        dispatch_keeper=lambda _directory: (_ for _ in ()).throw(
            AssertionError("dispatch")
        ),
    )
    assert replay == terminal


def test_stop_wins_when_dispatch_reaches_the_keeper_late(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    stage_script(tmp_path)
    dispatch_started = threading.Event()
    release_dispatch = threading.Event()
    launch_result: list[setup_owner.SetupView] = []
    from blendersessiond import windows_setup_process

    monkeypatch.setattr(setup_owner, "_STOP_WAIT_SECONDS", 0)
    monkeypatch.setattr(
        windows_setup_process.WindowsSetupProcess,
        "create_suspended",
        lambda **_values: (_ for _ in ()).throw(AssertionError("process started")),
    )

    def dispatch(directory: Path) -> None:
        dispatch_started.set()
        assert release_dispatch.wait(timeout=2)
        windows_setup_process.run_keeper(directory)

    launch = threading.Thread(
        target=lambda: launch_result.append(
            setup_owner.launch_setup(
                raw_launch(now),
                state_root=tmp_path,
                now=now,
                platform_name="Windows",
                dispatch_keeper=dispatch,
            )
        )
    )
    launch.start()
    assert dispatch_started.wait(timeout=2)
    request = setup_owner._load_request(
        tmp_path / "setup-attempts" / ATTEMPT_ID
    )
    stopped = setup_owner.stop_setup(
        ATTEMPT_ID,
        expected_request_sha256=request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        platform_name="Windows",
    )
    release_dispatch.set()
    launch.join(timeout=2)
    assert not launch.is_alive()
    assert len(launch_result) == 1
    assert stopped.terminal.cleanup == "cleanup_unverified"
    assert not (
        tmp_path / "setup-attempts" / ATTEMPT_ID / "launch-receipt.json"
    ).exists()


def test_active_launch_replay_does_not_dispatch_twice(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    raw = raw_launch(now)
    dispatches = 0

    def dispatch(directory: Path) -> None:
        nonlocal dispatches
        dispatches += 1
        request = setup_owner._load_request(directory)
        receipt = setup_owner.LaunchReceipt(
            attempt_id=request.attempt_id,
            launch_id=request.launch_id,
            request_sha256=request.request_sha256,
            keeper_pid=10,
            keeper_creation_time="windows:10",
            root_pid=11,
            root_creation_time="windows:11",
            job_name=setup_owner._job_name(request.launch_id),
            owned_at=now,
        )
        setup_owner._write_create_once(
            directory / "launch-receipt.json", receipt.to_dict()
        )

    stage_script(tmp_path)
    setup_owner.launch_setup(
        raw,
        state_root=tmp_path,
        now=now,
        platform_name="Windows",
        dispatch_keeper=dispatch,
    )
    replay = setup_owner.launch_setup(
        raw,
        state_root=tmp_path,
        now=now + timedelta(hours=1),
        platform_name="Windows",
        dispatch_keeper=dispatch,
    )
    assert replay.receipt is not None
    assert dispatches == 1


def test_launch_timeout_rechecks_a_concurrent_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    stage_script(tmp_path)

    def dispatch(directory: Path) -> None:
        request = setup_owner._load_request(directory)
        receipt = setup_owner.LaunchReceipt(
            attempt_id=request.attempt_id,
            launch_id=request.launch_id,
            request_sha256=request.request_sha256,
            keeper_pid=10,
            keeper_creation_time="windows:10",
            root_pid=11,
            root_creation_time="windows:11",
            job_name=setup_owner._job_name(request.launch_id),
            owned_at=now,
        )
        setup_owner._write_create_once(
            directory / "launch-receipt.json", receipt.to_dict()
        )

    monkeypatch.setattr(setup_owner, "_spawn_keeper", dispatch)
    monkeypatch.setattr(setup_owner, "_LAUNCH_WAIT_SECONDS", 0)
    result = setup_owner.launch_setup(
        raw_launch(now),
        state_root=tmp_path,
        now=now,
        platform_name="Windows",
    )
    assert result.receipt is not None
    assert result.terminal is None


def test_spawn_failure_is_an_immutable_terminal(tmp_path: Path, monkeypatch) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    stage_script(tmp_path)
    monkeypatch.setattr(
        setup_owner,
        "_spawn_keeper",
        lambda _directory: (_ for _ in ()).throw(OSError("spawn failed")),
    )
    first = setup_owner.launch_setup(
        raw_launch(now),
        state_root=tmp_path,
        now=now,
        platform_name="Windows",
    )
    second = setup_owner.launch_setup(
        raw_launch(now),
        state_root=tmp_path,
        now=now,
        platform_name="Windows",
    )
    assert first == second
    assert first.terminal.outcome == "launch_failed"
    assert first.terminal.cleanup == "cleanup_unverified"


def test_status_requires_well_formed_complete_fences(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    setup_owner.accept_launch_request(raw_launch(now), state_root=tmp_path, now=now)
    with pytest.raises(setup_owner.SetupOwnerError, match="SHA-256"):
        setup_owner.status_setup(
            ATTEMPT_ID,
            expected_request_sha256="short",
            expected_launch_id=LAUNCH_ID,
            state_root=tmp_path,
            platform_name="Windows",
        )
    with pytest.raises(setup_owner.SetupOwnerError, match="Launch ID"):
        setup_owner.status_setup(
            ATTEMPT_ID,
            expected_request_sha256="0" * 64,
            expected_launch_id="short",
            state_root=tmp_path,
            platform_name="Windows",
        )


def test_only_three_json_records_define_attempt_state(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    stage_script(tmp_path)
    setup_owner.launch_setup(
        raw_launch(now),
        state_root=tmp_path,
        now=now,
        platform_name="Windows",
        dispatch_keeper=lambda _directory: None,
    )
    names = {
        path.name for path in (tmp_path / "setup-attempts" / ATTEMPT_ID).glob("*.json")
    }
    assert names <= {"request.json", "launch-receipt.json", "terminal.json"}


def test_owner_loss_is_persisted_only_after_tree_and_job_are_absent(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    request = setup_owner.accept_launch_request(
        raw_launch(now), state_root=tmp_path, now=now
    )
    directory = tmp_path / "setup-attempts" / ATTEMPT_ID
    receipt = setup_owner.LaunchReceipt(
        attempt_id=ATTEMPT_ID,
        launch_id=LAUNCH_ID,
        request_sha256=request.request_sha256,
        keeper_pid=10,
        keeper_creation_time="windows:10",
        root_pid=11,
        root_creation_time="windows:11",
        job_name=setup_owner._job_name(LAUNCH_ID),
        owned_at=now,
    )
    setup_owner._write_create_once(directory / "launch-receipt.json", receipt.to_dict())
    monkeypatch.setattr(setup_owner, "process_matches", lambda *_args: False)
    monkeypatch.setattr(setup_owner, "_job_exists", lambda _name: False)

    first = setup_owner.status_setup(
        ATTEMPT_ID,
        expected_request_sha256=request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        platform_name="Windows",
    )
    second = setup_owner.status_setup(
        ATTEMPT_ID,
        expected_request_sha256=request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        platform_name="Windows",
    )
    assert first == second
    assert first.terminal.outcome == "owner_lost"
    assert first.terminal.cleanup == "tree_gone"


def test_pid_reuse_is_safe(tmp_path: Path, monkeypatch) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    request = setup_owner.accept_launch_request(
        raw_launch(now), state_root=tmp_path, now=now
    )
    directory = tmp_path / "setup-attempts" / ATTEMPT_ID
    receipt = setup_owner.LaunchReceipt(
        attempt_id=ATTEMPT_ID,
        launch_id=LAUNCH_ID,
        request_sha256=request.request_sha256,
        keeper_pid=10,
        keeper_creation_time="windows:old",
        root_pid=11,
        root_creation_time="windows:old-root",
        job_name=setup_owner._job_name(LAUNCH_ID),
        owned_at=now,
    )
    setup_owner._write_create_once(directory / "launch-receipt.json", receipt.to_dict())
    monkeypatch.setattr(setup_owner, "process_matches", lambda *_args: False)
    monkeypatch.setattr(setup_owner, "_job_exists", lambda _name: True)
    monkeypatch.setattr(setup_owner, "_STOP_WAIT_SECONDS", 0)
    from blendersessiond import windows_setup_process

    terminated: list[tuple[int, str]] = []
    monkeypatch.setattr(
        windows_setup_process,
        "terminate_exact_process",
        lambda pid, identity: terminated.append((pid, identity)),
    )
    result = setup_owner.stop_setup(
        ATTEMPT_ID,
        expected_request_sha256=request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        platform_name="Windows",
    )
    assert terminated == []
    assert result.terminal.cleanup == "cleanup_unverified"


def test_owner_loss_job_query_error_is_cleanup_unverified(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    request = setup_owner.accept_launch_request(
        raw_launch(now), state_root=tmp_path, now=now
    )
    directory = tmp_path / "setup-attempts" / ATTEMPT_ID
    receipt = setup_owner.LaunchReceipt(
        attempt_id=ATTEMPT_ID,
        launch_id=LAUNCH_ID,
        request_sha256=request.request_sha256,
        keeper_pid=10,
        keeper_creation_time="windows:10",
        root_pid=11,
        root_creation_time="windows:11",
        job_name=setup_owner._job_name(LAUNCH_ID),
        owned_at=now,
    )
    setup_owner._write_create_once(directory / "launch-receipt.json", receipt.to_dict())
    monkeypatch.setattr(setup_owner, "process_matches", lambda *_args: False)
    monkeypatch.setattr(setup_owner, "_OWNER_LOSS_WAIT_SECONDS", 0)
    monkeypatch.setattr(
        setup_owner,
        "_job_exists",
        lambda _name: (_ for _ in ()).throw(OSError("query failed")),
    )
    result = setup_owner.status_setup(
        ATTEMPT_ID,
        expected_request_sha256=request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        platform_name="Windows",
    )
    assert result.terminal.outcome == "cleanup_unverified"
    assert result.terminal.cleanup == "cleanup_unverified"


def test_keeper_loss_reconciles_while_the_root_still_exists(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    request = setup_owner.accept_launch_request(
        raw_launch(now), state_root=tmp_path, now=now
    )
    directory = tmp_path / "setup-attempts" / ATTEMPT_ID
    receipt = setup_owner.LaunchReceipt(
        attempt_id=ATTEMPT_ID,
        launch_id=LAUNCH_ID,
        request_sha256=request.request_sha256,
        keeper_pid=10,
        keeper_creation_time="windows:10",
        root_pid=11,
        root_creation_time="windows:11",
        job_name=setup_owner._job_name(LAUNCH_ID),
        owned_at=now,
    )
    setup_owner._write_create_once(directory / "launch-receipt.json", receipt.to_dict())
    monkeypatch.setattr(
        setup_owner,
        "process_matches",
        lambda pid, _identity: pid == receipt.root_pid,
    )
    monkeypatch.setattr(setup_owner, "_OWNER_LOSS_WAIT_SECONDS", 0)
    monkeypatch.setattr(setup_owner, "_job_exists", lambda _name: True)
    result = setup_owner.status_setup(
        ATTEMPT_ID,
        expected_request_sha256=request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        platform_name="Windows",
    )
    assert result.terminal.outcome == "cleanup_unverified"
    assert result.terminal.cleanup == "cleanup_unverified"


def test_keeper_termination_error_is_cleanup_unverified(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    request = setup_owner.accept_launch_request(
        raw_launch(now), state_root=tmp_path, now=now
    )
    directory = tmp_path / "setup-attempts" / ATTEMPT_ID
    receipt = setup_owner.LaunchReceipt(
        attempt_id=ATTEMPT_ID,
        launch_id=LAUNCH_ID,
        request_sha256=request.request_sha256,
        keeper_pid=10,
        keeper_creation_time="windows:10",
        root_pid=11,
        root_creation_time="windows:11",
        job_name=setup_owner._job_name(LAUNCH_ID),
        owned_at=now,
    )
    setup_owner._write_create_once(directory / "launch-receipt.json", receipt.to_dict())
    monkeypatch.setattr(setup_owner, "process_matches", lambda *_args: True)
    monkeypatch.setattr(setup_owner, "_STOP_WAIT_SECONDS", 0)
    from blendersessiond import windows_setup_process

    monkeypatch.setattr(
        windows_setup_process,
        "terminate_exact_process",
        lambda *_args: (_ for _ in ()).throw(OSError("terminate failed")),
    )
    result = setup_owner.stop_setup(
        ATTEMPT_ID,
        expected_request_sha256=request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        platform_name="Windows",
    )
    assert result.terminal.outcome == "cleanup_unverified"
    assert result.terminal.cleanup == "cleanup_unverified"


def test_staged_script_read_is_bounded_to_the_declared_size(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    path = stage_script(tmp_path)
    request = setup_owner.accept_launch_request(
        raw_launch(now), state_root=tmp_path, now=now
    )
    real_open = Path.open
    read_sizes: list[int] = []

    class Stream:
        def __init__(self, wrapped) -> None:
            self.wrapped = wrapped

        def __enter__(self):
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args) -> None:
            self.wrapped.__exit__(*args)

        def fileno(self) -> int:
            return self.wrapped.fileno()

        def read(self, size: int) -> bytes:
            read_sizes.append(size)
            return self.wrapped.read(size)

    def open_spy(target: Path, *args, **kwargs):
        opened = real_open(target, *args, **kwargs)
        return Stream(opened) if target == path else opened

    monkeypatch.setattr(Path, "open", open_spy)
    assert setup_owner.read_staged_script(tmp_path, request) == SCRIPT
    assert read_sizes == [len(SCRIPT) + 1]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["script"].__setitem__(
            "size", setup_owner.MAX_SCRIPT_BYTES + 1
        ),
        lambda payload: payload["script"].__setitem__("size", True),
        lambda payload: payload.__setitem__(
            "operation_revision", "generic-command-v1"
        ),
        lambda payload: payload["script"].__setitem__(
            "artifact_id", "replacement.ps1"
        ),
    ],
)
def test_recovery_revalidates_request_invariants(
    tmp_path: Path, mutate
) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    setup_owner.accept_launch_request(raw_launch(now), state_root=tmp_path, now=now)
    path = tmp_path / "setup-attempts" / ATTEMPT_ID / "request.json"
    payload = json.loads(path.read_text())
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(setup_owner.SetupOwnerError, match="request record"):
        setup_owner.status_setup(
            ATTEMPT_ID,
            expected_request_sha256=payload["request_sha256"],
            expected_launch_id=LAUNCH_ID,
            state_root=tmp_path,
            platform_name="Windows",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("exit_code", False),
        ("stdout_truncated", 0),
        ("stderr_truncated", 0),
        ("outcome", "looks_successful"),
        ("process", "unknown"),
        ("cleanup", "probably_gone"),
    ],
)
def test_recovery_rejects_malformed_terminal_fields(
    tmp_path: Path, field: str, value
) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    request = setup_owner.accept_launch_request(
        raw_launch(now), state_root=tmp_path, now=now
    )
    result = setup_owner.stop_setup(
        ATTEMPT_ID,
        expected_request_sha256=request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        now=now,
        platform_name="Windows",
    )
    path = tmp_path / "setup-attempts" / ATTEMPT_ID / "terminal.json"
    payload = result.terminal.to_dict()
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(setup_owner.SetupOwnerError, match="record"):
        setup_owner.status_setup(
            ATTEMPT_ID,
            expected_request_sha256=request.request_sha256,
            expected_launch_id=LAUNCH_ID,
            state_root=tmp_path,
            platform_name="Windows",
        )


def test_cancelled_terminal_round_trips_through_recovery(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    request = setup_owner.accept_launch_request(
        raw_launch(now), state_root=tmp_path, now=now
    )
    terminal = setup_owner.SetupTerminal.owned(
        request=request,
        outcome="cancelled",
        process="cancelled",
        cleanup="tree_gone",
        stdout=b"",
        stderr=b"",
        finished_at=now,
    )
    recovered = setup_owner._terminal_from_record(terminal.to_dict(), request)
    assert recovered == terminal


def test_recovery_rejects_inconsistent_success_state(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    request = setup_owner.accept_launch_request(
        raw_launch(now), state_root=tmp_path, now=now
    )
    terminal = setup_owner.SetupTerminal.owned(
        request=request,
        outcome="process_succeeded",
        process="cancelled",
        cleanup="tree_gone",
        exit_code=0,
        stdout=b"",
        stderr=b"",
        finished_at=now,
    )
    with pytest.raises(setup_owner.SetupOwnerError, match="terminal record"):
        setup_owner._terminal_from_record(terminal.to_dict(), request)


def test_status_terminalizes_an_expired_dispatch_without_a_receipt(
    tmp_path: Path,
) -> None:
    accepted_at = datetime.now(UTC) - timedelta(minutes=10)
    raw = raw_launch(accepted_at)
    request = setup_owner.accept_launch_request(
        raw,
        state_root=tmp_path,
        now=accepted_at,
    )
    directory = tmp_path / "setup-attempts" / ATTEMPT_ID
    (directory / ".dispatch").touch()
    result = setup_owner.status_setup(
        ATTEMPT_ID,
        expected_request_sha256=request.request_sha256,
        expected_launch_id=LAUNCH_ID,
        state_root=tmp_path,
        platform_name="Windows",
    )
    assert result.terminal.outcome == "launch_failed"
    assert result.terminal.cleanup == "cleanup_unverified"


def test_non_windows_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(setup_owner.SetupOwnerError, match="Windows"):
        setup_owner.status_setup(
            ATTEMPT_ID, state_root=tmp_path, platform_name="Darwin"
        )
