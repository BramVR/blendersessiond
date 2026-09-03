from __future__ import annotations

import base64
import ctypes
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from blendersessiond.locking import file_lock
from blendersessiond.processes import process_start_time

CREATE_SUSPENDED = 0x00000004
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_NO_WINDOW = 0x08000000
_STARTF_USESTDHANDLES = 0x00000100
_HANDLE_FLAG_INHERIT = 0x00000001
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_ERROR_BROKEN_PIPE = 109
_ERROR_ALREADY_EXISTS = 183
_ERROR_FILE_NOT_FOUND = 2
_STILL_ACTIVE = 259
_MAX_STREAM_BYTES = 24 * 1024


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("security_descriptor", wintypes.LPVOID),
        ("inherit_handle", wintypes.BOOL),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR),
        ("title", wintypes.LPWSTR),
        ("x", wintypes.DWORD),
        ("y", wintypes.DWORD),
        ("x_size", wintypes.DWORD),
        ("y_size", wintypes.DWORD),
        ("x_count_chars", wintypes.DWORD),
        ("y_count_chars", wintypes.DWORD),
        ("fill_attribute", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("show_window", wintypes.WORD),
        ("reserved_size", wintypes.WORD),
        ("reserved_bytes", ctypes.POINTER(ctypes.c_ubyte)),
        ("stdin", wintypes.HANDLE),
        ("stdout", wintypes.HANDLE),
        ("stderr", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [
        ("startup_info", _StartupInfo),
        ("attribute_list", wintypes.LPVOID),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("process", wintypes.HANDLE),
        ("thread", wintypes.HANDLE),
        ("process_id", wintypes.DWORD),
        ("thread_id", wintypes.DWORD),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_time", ctypes.c_longlong),
        ("per_job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "read_operations",
            "write_operations",
            "other_operations",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimitInformation),
        ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _BasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("total_user_time", ctypes.c_longlong),
        ("total_kernel_time", ctypes.c_longlong),
        ("period_user_time", ctypes.c_longlong),
        ("period_kernel_time", ctypes.c_longlong),
        ("page_faults", wintypes.DWORD),
        ("total_processes", wintypes.DWORD),
        ("active_processes", wintypes.DWORD),
        ("terminated_processes", wintypes.DWORD),
    ]


class _NativeWin32:
    def __init__(self) -> None:
        self.kernel = _kernel32()

    def create_job(self, name: str | None) -> int:
        job = self.kernel.CreateJobObjectW(None, name)
        error = ctypes.get_last_error()
        if not job:
            raise ctypes.WinError(error)
        if name is not None and error == _ERROR_ALREADY_EXISTS:
            self.close_handles((job,))
            raise RuntimeError("Setup Job Object name is already in use.")
        return job

    def set_kill_on_close(self, job: int) -> None:
        information = _ExtendedLimitInformation()
        information.basic.flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def create_standard_pipes(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        security = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), None, True)
        values: list[tuple[int, int]] = []
        try:
            for _ in range(3):
                read = wintypes.HANDLE()
                write = wintypes.HANDLE()
                if not self.kernel.CreatePipe(
                    ctypes.byref(read),
                    ctypes.byref(write),
                    ctypes.byref(security),
                    0,
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                values.append((read.value, write.value))
            child = (values[0][0], values[1][1], values[2][1])
            parent = (values[0][1], values[1][0], values[2][0])
            for handle in parent:
                if not self.kernel.SetHandleInformation(
                    handle, _HANDLE_FLAG_INHERIT, 0
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
            return child, parent
        except BaseException:
            self.close_handles(handle for pair in values for handle in pair)
            raise

    def create_process(self, **values: Any) -> tuple[int, int, int]:
        child_handles = values["handle_list"]
        jobs = values["job_list"]
        attribute_size = ctypes.c_size_t()
        self.kernel.InitializeProcThreadAttributeList(
            None, 2, 0, ctypes.byref(attribute_size)
        )
        buffer = ctypes.create_string_buffer(attribute_size.value)
        attributes = ctypes.cast(buffer, wintypes.LPVOID)
        if not self.kernel.InitializeProcThreadAttributeList(
            attributes, 2, 0, ctypes.byref(attribute_size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        inherited = (wintypes.HANDLE * len(child_handles))(*child_handles)
        job_list = (wintypes.HANDLE * len(jobs))(*jobs)
        try:
            if not self.kernel.UpdateProcThreadAttribute(
                attributes,
                0,
                _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                ctypes.byref(inherited),
                ctypes.sizeof(inherited),
                None,
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.UpdateProcThreadAttribute(
                attributes,
                0,
                _PROC_THREAD_ATTRIBUTE_JOB_LIST,
                ctypes.byref(job_list),
                ctypes.sizeof(job_list),
                None,
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            startup = _StartupInfoEx()
            startup.startup_info.cb = ctypes.sizeof(startup)
            startup.startup_info.flags = _STARTF_USESTDHANDLES
            startup.startup_info.stdin = values["standard_handles"][0]
            startup.startup_info.stdout = values["standard_handles"][1]
            startup.startup_info.stderr = values["standard_handles"][2]
            startup.attribute_list = attributes
            information = _ProcessInformation()
            command = ctypes.create_unicode_buffer(
                subprocess.list2cmdline(
                    [values["application_name"], *values["command_line"]]
                )
            )
            if not self.kernel.CreateProcessW(
                values["application_name"],
                command,
                None,
                None,
                values["inherit_handles"],
                values["creation_flags"] | _CREATE_NO_WINDOW,
                None,
                values["cwd"],
                ctypes.byref(startup),
                ctypes.byref(information),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return information.process, information.thread, information.process_id
        finally:
            self.kernel.DeleteProcThreadAttributeList(attributes)

    def close_handles(self, handles: Any) -> None:
        for handle in handles:
            if handle:
                self.kernel.CloseHandle(handle)

    def process_creation_filetime(self, process: int) -> int:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not self.kernel.GetProcessTimes(
            process,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime

    def is_process_in_job(self, process: int, job: int) -> bool:
        result = wintypes.BOOL()
        if not self.kernel.IsProcessInJob(process, job, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(result.value)

    def resume_thread(self, thread: int) -> None:
        if self.kernel.ResumeThread(thread) == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate_job(self, job: int) -> None:
        if not self.kernel.TerminateJobObject(job, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate_process(self, process: int) -> None:
        if not self.kernel.TerminateProcess(process, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def active_processes(self, job: int) -> int:
        information = _BasicAccountingInformation()
        if not self.kernel.QueryInformationJobObject(
            job,
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return information.active_processes

    def wait_process(self, process: int, milliseconds: int) -> int:
        return self.kernel.WaitForSingleObject(process, milliseconds)

    def exit_code(self, process: int) -> int:
        value = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(process, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.value

    def write(self, handle: int, content: bytes) -> None:
        offset = 0
        while offset < len(content):
            chunk = content[offset : offset + 16 * 1024]
            buffer = ctypes.create_string_buffer(chunk)
            written = wintypes.DWORD()
            if not self.kernel.WriteFile(
                handle, buffer, len(chunk), ctypes.byref(written), None
            ):
                if ctypes.get_last_error() == _ERROR_BROKEN_PIPE:
                    return
                raise ctypes.WinError(ctypes.get_last_error())
            if written.value == 0:
                raise RuntimeError("Setup stdin stopped accepting bytes.")
            offset += written.value

    def read(self, handle: int) -> bytes | None:
        buffer = ctypes.create_string_buffer(16 * 1024)
        read = wintypes.DWORD()
        if not self.kernel.ReadFile(
            handle, buffer, len(buffer), ctypes.byref(read), None
        ):
            if ctypes.get_last_error() == _ERROR_BROKEN_PIPE:
                return None
            raise ctypes.WinError(ctypes.get_last_error())
        return bytes(buffer.raw[: read.value]) if read.value else None


@dataclass
class WindowsSetupProcess:
    _api: Any
    job_handle: Any
    process_handle: Any
    thread_handle: Any
    root_pid: int
    root_creation_filetime: Any
    parent_stdin: Any
    parent_stdout: Any
    parent_stderr: Any

    @classmethod
    def create_suspended(
        cls,
        *,
        api: Any | None = None,
        powershell_path: Path | None = None,
        job_name: str | None,
        script_size: int = 1,
        script_sha256: str = "0" * 64,
        cwd: Path | None = None,
    ) -> WindowsSetupProcess:
        owner = _NativeWin32() if api is None else api
        powershell = (
            _system_powershell() if powershell_path is None else str(powershell_path)
        )
        job = owner.create_job(job_name)
        try:
            owner.set_kill_on_close(job)
            child, parent = owner.create_standard_pipes()
            try:
                process, thread, pid = owner.create_process(
                    application_name=powershell,
                    command_line=_powershell_arguments(script_size, script_sha256),
                    creation_flags=CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT,
                    job_list=(job,),
                    handle_list=tuple(child),
                    standard_handles=tuple(child),
                    inherit_handles=True,
                    cwd=str(Path.cwd() if cwd is None else cwd),
                )
            except BaseException:
                owner.close_handles((*child, *parent))
                raise
            owner.close_handles(child)
            try:
                creation = owner.process_creation_filetime(process)
                if not owner.is_process_in_job(process, job):
                    raise RuntimeError("Setup process is not inside its Job Object.")
            except BaseException:
                try:
                    owner.terminate_process(process)
                    owner.wait_process(process, 5000)
                finally:
                    owner.close_handles((thread, process, *parent))
                raise
            return cls(
                _api=owner,
                job_handle=job,
                process_handle=process,
                thread_handle=thread,
                root_pid=pid,
                root_creation_filetime=creation,
                parent_stdin=parent[0],
                parent_stdout=parent[1],
                parent_stderr=parent[2],
            )
        except BaseException:
            owner.close_handles((job,))
            raise

    def publish_then_resume(self, publish: Callable[[], None]) -> None:
        publish()
        self.resume()

    def resume(self) -> None:
        self._api.resume_thread(self.thread_handle)
        self._api.close_handles((self.thread_handle,))
        self.thread_handle = 0

    def start_io(
        self,
        script: bytes,
        output: dict[str, tuple[bytes, bool]],
        threads: list[threading.Thread],
    ) -> None:
        stdin_handle = self.parent_stdin

        def write() -> None:
            try:
                self._api.write(stdin_handle, script)
            finally:
                self._api.close_handles((stdin_handle,))

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        threads.append(writer)
        self.parent_stdin = 0
        for name, handle in (
            ("stdout", self.parent_stdout),
            ("stderr", self.parent_stderr),
        ):
            thread = threading.Thread(
                target=self._drain,
                args=(handle, output, name),
                daemon=True,
            )
            thread.start()
            threads.append(thread)

    def _drain(
        self,
        handle: Any,
        output: dict[str, tuple[bytes, bool]],
        name: str,
    ) -> None:
        collected = bytearray()
        truncated = False
        try:
            while True:
                chunk = self._api.read(handle)
                if chunk is None:
                    break
                remaining = _MAX_STREAM_BYTES - len(collected)
                if remaining > 0:
                    collected.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated = True
        except OSError:
            truncated = True
        output[name] = (bytes(collected), truncated)

    def finish_io(self, threads: list[threading.Thread]) -> None:
        for thread in threads:
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("Setup process stream did not close.")
        self._api.close_handles((self.parent_stdout, self.parent_stderr))
        self.parent_stdout = 0
        self.parent_stderr = 0

    def terminate(self) -> None:
        self._api.terminate_job(self.job_handle)

    def wait_empty(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self._api.active_processes(self.job_handle) == 0:
                return
            time.sleep(0.02)
        raise RuntimeError("Setup Job Object did not become empty.")

    def empty(self) -> bool:
        return self._api.active_processes(self.job_handle) == 0

    def active_count(self) -> int:
        return self._api.active_processes(self.job_handle)

    def close(self) -> None:
        self._api.close_handles(
            (
                self.thread_handle,
                self.process_handle,
                self.parent_stdin,
                self.parent_stdout,
                self.parent_stderr,
                self.job_handle,
            )
        )
        self.thread_handle = 0
        self.process_handle = 0
        self.job_handle = 0


def powershell_bootstrap(script_size: int, script_sha256: str) -> str:
    return f"""$ErrorActionPreference = 'Stop'
$stream = [Console]::OpenStandardInput()
$bytes = [byte[]]::new({script_size})
$offset = 0
while ($offset -lt $bytes.Length) {{
    $read = $stream.Read($bytes, $offset, $bytes.Length - $offset)
    if ($read -eq 0) {{ throw 'Setup input ended before the declared byte count.' }}
    $offset += $read
}}
if ($stream.ReadByte() -ne -1) {{
    throw 'Setup input exceeds the declared byte count.'
}}
$sha = [Security.Cryptography.SHA256]::Create()
try {{ $digest = $sha.ComputeHash($bytes) }} finally {{ $sha.Dispose() }}
$actual = ([BitConverter]::ToString($digest)).Replace('-', '').ToLowerInvariant()
if ($actual -cne '{script_sha256}') {{
    throw 'Setup input SHA256 does not match the accepted request.'
}}
$text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$block = [ScriptBlock]::Create($text)
& $block
"""


def runtime_self_test() -> None:
    owned = WindowsSetupProcess.create_suspended(
        job_name=None,
        script_size=1,
        script_sha256="0" * 64,
    )
    try:
        if owned.active_count() != 1:
            raise RuntimeError("Setup owner self-test found unexpected Job members.")
        owned.terminate()
        owned.wait_empty()
        if not owned.empty():
            raise RuntimeError("Setup owner self-test did not empty the Job Object.")
    finally:
        owned.close()


def terminate_exact_process(pid: int, expected_creation_time: str) -> None:
    api = _NativeWin32()
    process = api.kernel.OpenProcess(0x00100000 | 0x1000 | 0x0001, False, pid)
    if not process:
        return
    try:
        actual = f"windows:{api.process_creation_filetime(process)}"
        if actual != expected_creation_time:
            return
        if not api.kernel.TerminateProcess(process, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        api.kernel.WaitForSingleObject(process, 5000)
    finally:
        api.close_handles((process,))


def job_exists(name: str) -> bool:
    kernel = _kernel32()
    job = kernel.OpenJobObjectW(0x0004, False, name)
    if not job:
        error = ctypes.get_last_error()
        if error == _ERROR_FILE_NOT_FOUND:
            return False
        raise ctypes.WinError(error)
    kernel.CloseHandle(job)
    return True


def run_keeper(attempt_dir: Path) -> int:
    from blendersessiond import setup_owner

    try:
        request = setup_owner._load_request(attempt_dir)
    except BaseException:
        return 2
    try:
        script = setup_owner.read_staged_script(attempt_dir.parent.parent, request)
    except BaseException as error:
        terminal = setup_owner.SetupTerminal.owned(
            request=request,
            outcome="launch_failed",
            process="not_started",
            cleanup="tree_gone",
            stdout=b"",
            stderr=b"",
            finished_at=datetime.now(UTC),
            message=f"{type(error).__name__}: {error}"[:4096],
        )
        setup_owner._publish_terminal(attempt_dir, request, terminal)
        return 1
    owned: WindowsSetupProcess | None = None
    output: dict[str, tuple[bytes, bool]] = {}
    threads: list[threading.Thread] = []
    resumed = False
    try:
        with file_lock(attempt_dir / ".lock"):
            if (attempt_dir / "terminal.json").exists():
                return 0
            if (attempt_dir / ".stop").exists():
                terminal = setup_owner.SetupTerminal.owned(
                    request=request,
                    outcome="stopped_before_ownership",
                    process="not_started",
                    cleanup="tree_gone",
                    stdout=b"",
                    stderr=b"",
                    finished_at=datetime.now(UTC),
                )
                setup_owner._write_create_once(
                    attempt_dir / "terminal.json", terminal.to_dict()
                )
                return 0
        owned = WindowsSetupProcess.create_suspended(
            job_name=setup_owner._job_name(request.launch_id),
            script_size=request.script.size,
            script_sha256=request.script.sha256,
            cwd=attempt_dir,
        )
        keeper_time = process_start_time(os.getpid())
        if keeper_time is None:
            raise RuntimeError("Setup keeper identity is unavailable.")
        receipt = setup_owner.LaunchReceipt(
            attempt_id=request.attempt_id,
            launch_id=request.launch_id,
            request_sha256=request.request_sha256,
            keeper_pid=os.getpid(),
            keeper_creation_time=keeper_time,
            root_pid=owned.root_pid,
            root_creation_time=f"windows:{owned.root_creation_filetime}",
            job_name=setup_owner._job_name(request.launch_id),
            owned_at=datetime.now(UTC),
        )
        with file_lock(attempt_dir / ".lock"):
            if (attempt_dir / "terminal.json").exists():
                owned.terminate()
                owned.wait_empty()
                return 0
            setup_owner._write_create_once(
                attempt_dir / "launch-receipt.json", receipt.to_dict()
            )
            if (attempt_dir / ".stop").exists():
                owned.terminate()
                owned.wait_empty()
                terminal = setup_owner.SetupTerminal.owned(
                    request=request,
                    outcome="stopped",
                    process="cancelled_before_resume",
                    cleanup="tree_gone",
                    stdout=b"",
                    stderr=b"",
                    finished_at=datetime.now(UTC),
                )
                setup_owner._write_create_once(
                    attempt_dir / "terminal.json", terminal.to_dict()
                )
                return 0
            owned.start_io(script, output, threads)
            if request.deadline_utc <= datetime.now(UTC):
                owned.terminate()
                owned.wait_empty()
                owned.finish_io(threads)
                terminal = setup_owner.SetupTerminal.owned(
                    request=request,
                    outcome="timed_out",
                    process="not_resumed",
                    cleanup="tree_gone",
                    stdout=b"",
                    stderr=b"",
                    finished_at=datetime.now(UTC),
                )
                setup_owner._write_create_once(
                    attempt_dir / "terminal.json", terminal.to_dict()
                )
                return 1
            owned.resume()
            resumed = True
        process = _wait_for_process(owned, request.deadline_utc, attempt_dir / ".stop")
        exit_code = owned._api.exit_code(owned.process_handle)
        if not owned.empty():
            owned.terminate()
        owned.wait_empty()
        owned.finish_io(threads)
        stdout, stdout_truncated = output.get("stdout", (b"", False))
        stderr, stderr_truncated = output.get("stderr", (b"", False))
        stdout.decode("utf-8", errors="strict")
        stderr.decode("utf-8", errors="strict")
        exit_value = None if exit_code == _STILL_ACTIVE else exit_code
        outcome = process
        if process == "exited":
            outcome = (
                "process_succeeded"
                if exit_value == 0 and not stdout_truncated and not stderr_truncated
                else "process_failed"
            )
        terminal = setup_owner.SetupTerminal.owned(
            request=request,
            outcome=outcome,
            process=process,
            cleanup="tree_gone",
            exit_code=exit_value,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            finished_at=datetime.now(UTC),
        )
        setup_owner._publish_terminal(attempt_dir, request, terminal)
        return 0
    except BaseException as error:
        cleanup = "cleanup_unverified"
        if owned is not None:
            try:
                owned.terminate()
                owned.wait_empty()
                cleanup = "tree_gone"
            except (OSError, RuntimeError):
                pass
        terminal = setup_owner.SetupTerminal.owned(
            request=request,
            outcome="launch_failed" if not resumed else "process_failed",
            process="not_resumed" if not resumed else "failed",
            cleanup=cleanup,
            stdout=b"",
            stderr=b"",
            finished_at=datetime.now(UTC),
            message=f"{type(error).__name__}: {error}"[:4096],
        )
        try:
            setup_owner._publish_terminal(attempt_dir, request, terminal)
        except OSError:
            pass
        return 1
    finally:
        if owned is not None:
            owned.close()


def _wait_for_process(
    owned: WindowsSetupProcess,
    deadline_utc: datetime,
    stop_path: Path,
) -> str:
    remaining = max(0.0, (deadline_utc - datetime.now(UTC)).total_seconds())
    deadline = time.monotonic() + min(remaining, 5 * 60)
    while True:
        if stop_path.exists():
            owned.terminate()
            return "cancelled"
        wait_seconds = deadline - time.monotonic()
        if wait_seconds <= 0:
            owned.terminate()
            return "timed_out"
        result = owned._api.wait_process(
            owned.process_handle,
            min(100, max(1, int(wait_seconds * 1000))),
        )
        if result == _WAIT_OBJECT_0:
            return _wait_for_owned_job(owned, deadline_utc, stop_path)
        if result != _WAIT_TIMEOUT:
            raise ctypes.WinError(ctypes.get_last_error())


def _wait_for_owned_job(
    owned: WindowsSetupProcess,
    deadline_utc: datetime,
    stop_path: Path,
) -> str:
    while True:
        if stop_path.exists():
            owned.terminate()
            return "cancelled"
        if datetime.now(UTC) >= deadline_utc:
            if not owned.empty():
                owned.terminate()
            return "timed_out"
        if owned.empty():
            return "exited"
        time.sleep(0.02)


def _powershell_arguments(script_size: int, script_sha256: str) -> list[str]:
    encoded = base64.b64encode(
        powershell_bootstrap(script_size, script_sha256).encode("utf-16-le")
    ).decode("ascii")
    return [
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]


def _system_powershell(kernel: Any | None = None) -> str:
    native = _kernel32() if kernel is None else kernel
    size = 260
    while size <= 32768:
        buffer = ctypes.create_unicode_buffer(size)
        length = native.GetSystemDirectoryW(buffer, size)
        if length == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        if length < size:
            root = buffer.value
            break
        size = length + 1
    else:
        raise RuntimeError("Windows system directory exceeds its supported bound.")
    return str(Path(root) / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel.GetSystemDirectoryW.restype = wintypes.UINT
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.InitializeProcThreadAttributeList.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel.UpdateProcThreadAttribute.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_size_t,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.LPVOID,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
    kernel.DeleteProcThreadAttributeList.restype = None
    kernel.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(_SecurityAttributes),
        wintypes.DWORD,
    ]
    kernel.CreatePipe.restype = wintypes.BOOL
    kernel.SetHandleInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel.SetHandleInformation.restype = wintypes.BOOL
    kernel.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        wintypes.LPVOID,
        ctypes.POINTER(_ProcessInformation),
    ]
    kernel.CreateProcessW.restype = wintypes.BOOL
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.WriteFile.argtypes = kernel.ReadFile.argtypes
    kernel.WriteFile.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenJobObjectW.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def main() -> int:
    if os.name != "nt" or len(sys.argv) != 3 or sys.argv[1] != "--keeper":
        return 2
    return run_keeper(Path(sys.argv[2]))


if __name__ == "__main__":
    raise SystemExit(main())
