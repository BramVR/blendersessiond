from __future__ import annotations

from pathlib import Path

from blendersessiond import windows_setup_process


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
        return ("child-in", "child-out", "child-err"), ("parent-in", "parent-out", "parent-err")

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


def test_process_is_suspended_and_atomically_assigned_before_receipt(tmp_path: Path) -> None:
    api = FakeWin32()
    owned = windows_setup_process.WindowsSetupProcess.create_suspended(
        api=api,
        powershell_path=tmp_path / "powershell.exe",
        job_name="Local\\BlenderSessiond.Setup.bbsl_test",
    )
    assert [call if isinstance(call, str) else call[0] for call in api.calls] == [
        "create_job", "set_kill_on_close", "create_standard_pipes", "create_process",
        "close_handles", "process_creation_filetime", "is_process_in_job",
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


def test_receipt_flush_precedes_resume() -> None:
    events: list[str] = []
    owned = object.__new__(windows_setup_process.WindowsSetupProcess)
    owned._api = type("API", (), {"resume_thread": lambda _self, _thread: events.append("resume")})()
    owned.thread_handle = "thread"
    owned.publish_then_resume(lambda: events.append("receipt_flushed"))
    assert events == ["receipt_flushed", "resume"]


def test_bootstrap_reads_exact_bytes_and_decodes_strict_utf8() -> None:
    bootstrap = windows_setup_process.powershell_bootstrap(27, "a" * 64)
    assert "OpenStandardInput" in bootstrap
    assert "UTF8Encoding($false, $true)" in bootstrap
    assert "SHA256" in bootstrap
    assert "ReadByte() -ne -1" in bootstrap
    assert "27" in bootstrap
    assert "a" * 64 in bootstrap
