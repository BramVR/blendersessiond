from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blendersessiond import setup_owner, windows_setup_process


class FakeWin32:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def create_job(self, name: str):
        self.calls.append(("create_job", name))
        return "job"

    def set_kill_on_close(self, job) -> None:
        self.calls.append(("set_kill_on_close", job))

    def create_standard_pipes(self):
        self.calls.append("create_standard_pipes")
        return ("child-in", "child-out", "child-err"), (
            "parent-in",
            "parent-out",
            "parent-err",
        )

    def create_process(self, **kwargs):
        self.calls.append(("create_process", kwargs))
        return "process", "thread", 123

    def close_handles(self, handles) -> None:
        self.calls.append(("close_handles", tuple(handles)))

    def process_creation_filetime(self, process) -> int:
        self.calls.append(("process_creation_filetime", process))
        return 456

    def is_process_in_job(self, process, job) -> bool:
        self.calls.append(("is_process_in_job", process, job))
        return True

    def terminate_process(self, process) -> None:
        self.calls.append(("terminate_process", process))

    def wait_process(self, process, milliseconds: int) -> int:
        self.calls.append(("wait_process", process, milliseconds))
        return windows_setup_process._WAIT_OBJECT_0


def test_process_is_suspended_and_atomically_assigned_before_receipt(
    tmp_path: Path,
) -> None:
    api = FakeWin32()
    owned = windows_setup_process.WindowsSetupProcess.create_suspended(
        api=api,
        powershell_path=tmp_path / "powershell.exe",
        job_name="Local\\BlenderSessiond.Setup.bbsl_test",
    )
    assert [call if isinstance(call, str) else call[0] for call in api.calls] == [
        "create_job",
        "set_kill_on_close",
        "create_standard_pipes",
        "create_process",
        "close_handles",
        "process_creation_filetime",
        "is_process_in_job",
    ]
    create = api.calls[3][1]
    assert create["creation_flags"] & windows_setup_process.CREATE_SUSPENDED
    assert create["creation_flags"] & windows_setup_process.EXTENDED_STARTUPINFO_PRESENT
    assert create["job_list"] == ("job",)
    assert create["handle_list"] == ("child-in", "child-out", "child-err")
    assert create["inherit_handles"] is True
    assert "job" not in create["handle_list"]
    assert owned.root_pid == 123
    assert owned.root_creation_filetime == 456


def test_process_creation_rejects_a_child_outside_the_job(tmp_path: Path) -> None:
    api = FakeWin32()
    api.is_process_in_job = lambda _process, _job: False
    with pytest.raises(RuntimeError, match="not inside"):
        windows_setup_process.WindowsSetupProcess.create_suspended(
            api=api,
            powershell_path=tmp_path / "powershell.exe",
            job_name="Local\\BlenderSessiond.Setup.bbsl_test",
        )
    closed = [
        call[1]
        for call in api.calls
        if isinstance(call, tuple) and call[0] == "close_handles"
    ]
    assert ("thread", "process", "parent-in", "parent-out", "parent-err") in closed
    assert ("job",) in closed
    assert ("terminate_process", "process") in api.calls
    assert ("wait_process", "process", 5000) in api.calls


def test_receipt_flush_precedes_resume() -> None:
    events: list[str] = []
    owned = object.__new__(windows_setup_process.WindowsSetupProcess)
    owned._api = type(
        "API",
        (),
        {
            "resume_thread": lambda _self, _thread: events.append("resume"),
            "close_handles": lambda _self, _handles: None,
        },
    )()
    owned.thread_handle = "thread"
    owned.publish_then_resume(lambda: events.append("receipt_flushed"))
    assert events == ["receipt_flushed", "resume"]


def test_bootstrap_reads_exact_bytes_and_decodes_strict_utf8() -> None:
    bootstrap = windows_setup_process.powershell_bootstrap(27, "a" * 64)
    assert "OpenStandardInput" in bootstrap
    assert "UTF8Encoding]::new($false, $true)" in bootstrap
    assert "SHA256" in bootstrap
    assert "ReadByte() -ne -1" in bootstrap
    assert "27" in bootstrap
    assert "a" * 64 in bootstrap


def test_stdin_writer_keeps_the_owned_handle_when_the_field_is_cleared(
    monkeypatch,
) -> None:
    writes: list[tuple[object, bytes]] = []

    class API:
        def write(self, handle: object, content: bytes) -> None:
            writes.append((handle, content))

        def close_handles(self, _handles: object) -> None:
            return None

        def read(self, _handle: object) -> None:
            return None

    owned = windows_setup_process.WindowsSetupProcess(
        _api=API(),
        job_handle="job",
        process_handle="process",
        thread_handle="thread",
        root_pid=123,
        root_creation_filetime=456,
        parent_stdin="parent-in",
        parent_stdout="parent-out",
        parent_stderr="parent-err",
    )

    class ImmediateThread:
        def __init__(self, *, target, args=(), daemon=False) -> None:
            self.target = target
            self.args = args

        def start(self) -> None:
            owned.parent_stdin = 0
            self.target(*self.args)

        def join(self, timeout=None) -> None:
            return None

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(windows_setup_process.threading, "Thread", ImmediateThread)
    output: dict[str, tuple[bytes, bool]] = {}
    threads: list[threading.Thread] = []
    owned.start_io(b"script", output, threads)
    assert writes == [("parent-in", b"script")]


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Job Objects")
def test_concrete_windows_job_list_runtime_self_test() -> None:
    windows_setup_process.runtime_self_test()


def _attempt(tmp_path: Path, deadline: datetime) -> Path:
    attempt_id = "bbsa_" + "A" * 43
    launch_id = "bbsl_" + "B" * 43
    script = b"Write-Output 'setup'\n"
    raw = json.dumps(
        {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "launch_id": launch_id,
            "deadline_utc": deadline.isoformat().replace("+00:00", "Z"),
            "operation_revision": "windows-setup-owner-v1",
            "script": {
                "artifact_id": f"{attempt_id}.ps1",
                "size": len(script),
                "sha256": hashlib.sha256(script).hexdigest(),
            },
        },
        separators=(",", ":"),
    ).encode()
    setup_owner.accept_launch_request(
        raw,
        state_root=tmp_path,
        now=deadline - timedelta(minutes=1),
    )
    path = setup_owner.script_path_for_attempt(tmp_path, attempt_id)
    path.write_bytes(script)
    return path.parent


class FakeOwnedProcess:
    root_pid = 123
    root_creation_filetime = 456

    def __init__(self, *, drain_error: bool = False) -> None:
        self.drain_error = drain_error
        self.resumed = False
        self.terminated = False
        self.closed = False
        self.io_started = False

    def start_io(self, _script, _output, _threads) -> None:
        self.io_started = True

    def resume(self) -> None:
        self.resumed = True

    def terminate(self) -> None:
        self.terminated = True

    def wait_empty(self) -> None:
        if self.drain_error:
            raise RuntimeError("did not drain")

    def close(self) -> None:
        self.closed = True

    def finish_io(self, _threads) -> None:
        return None


def test_keeper_does_not_resume_after_the_absolute_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    deadline = datetime(2026, 9, 3, 12, tzinfo=UTC)
    directory = _attempt(tmp_path, deadline)
    owned = FakeOwnedProcess()

    class Clock:
        @classmethod
        def now(cls, _tz=None) -> datetime:
            return deadline + timedelta(seconds=1)

    monkeypatch.setattr(windows_setup_process, "datetime", Clock)
    monkeypatch.setattr(
        windows_setup_process.WindowsSetupProcess,
        "create_suspended",
        lambda **_values: owned,
    )
    monkeypatch.setattr(
        windows_setup_process, "process_start_time", lambda _pid: "windows:keeper"
    )
    assert windows_setup_process.run_keeper(directory) == 1
    terminal = setup_owner._read_optional_record(directory / "terminal.json")
    assert terminal["outcome"] == "timed_out"
    assert terminal["cleanup"] == "tree_gone"
    assert owned.terminated
    assert owned.io_started
    assert not owned.resumed


def test_keeper_persists_unverified_cleanup_when_job_drain_times_out(
    tmp_path: Path, monkeypatch
) -> None:
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    directory = _attempt(tmp_path, deadline)
    owned = FakeOwnedProcess(drain_error=True)
    monkeypatch.setattr(
        windows_setup_process.WindowsSetupProcess,
        "create_suspended",
        lambda **_values: owned,
    )
    monkeypatch.setattr(
        windows_setup_process, "process_start_time", lambda _pid: "windows:keeper"
    )
    monkeypatch.setattr(
        windows_setup_process,
        "_wait_for_process",
        lambda *_args: (_ for _ in ()).throw(OSError("wait failed")),
    )
    assert windows_setup_process.run_keeper(directory) == 1
    terminal = setup_owner._read_optional_record(directory / "terminal.json")
    assert terminal["outcome"] == "process_failed"
    assert terminal["cleanup"] == "cleanup_unverified"
    assert owned.closed


def test_keeper_resumes_while_the_attempt_lock_is_held(
    tmp_path: Path, monkeypatch
) -> None:
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    directory = _attempt(tmp_path, deadline)
    attempt_lock = threading.Lock()
    lock_state_at_resume: list[bool] = []
    owned = FakeOwnedProcess()

    @contextmanager
    def tracked_lock(_path):
        with attempt_lock:
            yield

    def resume() -> None:
        lock_state_at_resume.append(attempt_lock.locked())
        raise OSError("stop after observation")

    owned.resume = resume
    monkeypatch.setattr(windows_setup_process, "file_lock", tracked_lock)
    monkeypatch.setattr(
        windows_setup_process.WindowsSetupProcess,
        "create_suspended",
        lambda **_values: owned,
    )
    monkeypatch.setattr(
        windows_setup_process, "process_start_time", lambda _pid: "windows:keeper"
    )
    assert windows_setup_process.run_keeper(directory) == 1
    assert lock_state_at_resume == [True]


def test_active_descendants_must_finish_before_success(
    tmp_path: Path, monkeypatch
) -> None:
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    states = iter([False, True])
    terminated: list[bool] = []
    owned = type(
        "Owned",
        (),
        {
            "empty": lambda _self: next(states),
            "terminate": lambda _self: terminated.append(True),
        },
    )()
    monkeypatch.setattr(windows_setup_process.time, "sleep", lambda _seconds: None)
    outcome = windows_setup_process._wait_for_owned_job(
        owned,
        deadline,
        tmp_path / ".stop",
    )
    assert outcome == "exited"
    assert terminated == []


def test_active_descendants_are_not_success_after_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    deadline = datetime(2026, 9, 3, 12, tzinfo=UTC)
    terminated: list[bool] = []
    owned = type(
        "Owned",
        (),
        {
            "empty": lambda _self: False,
            "terminate": lambda _self: terminated.append(True),
        },
    )()

    class Clock:
        @classmethod
        def now(cls, _tz=None) -> datetime:
            return deadline + timedelta(seconds=1)

    monkeypatch.setattr(windows_setup_process, "datetime", Clock)
    outcome = windows_setup_process._wait_for_owned_job(
        owned,
        deadline,
        tmp_path / ".stop",
    )
    assert outcome == "timed_out"
    assert terminated == [True]


def test_empty_job_after_deadline_is_still_timed_out(
    tmp_path: Path, monkeypatch
) -> None:
    deadline = datetime(2026, 9, 3, 12, tzinfo=UTC)
    terminated: list[bool] = []
    owned = type(
        "Owned",
        (),
        {
            "empty": lambda _self: True,
            "terminate": lambda _self: terminated.append(True),
        },
    )()

    class Clock:
        @classmethod
        def now(cls, _tz=None) -> datetime:
            return deadline + timedelta(microseconds=1)

    monkeypatch.setattr(windows_setup_process, "datetime", Clock)
    outcome = windows_setup_process._wait_for_owned_job(
        owned,
        deadline,
        tmp_path / ".stop",
    )
    assert outcome == "timed_out"
    assert terminated == []
