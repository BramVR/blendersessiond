"""Detached process spawning, identity, and tree termination."""

from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import IO

_IDENTITY_WAIT_SECONDS = 2.0
_STOP_WAIT_SECONDS = 5.0


def spawn_detached(
    executable: str,
    *,
    stdout: IO[bytes],
    stderr: IO[bytes],
    environ: dict[str, str],
    gate_path: Path,
    bootstrap_path: Path,
) -> subprocess.Popen[bytes]:
    """Launch a detached process-tree root waiting behind a pre-exec gate."""

    arguments: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": stdout,
        "stderr": stderr,
        "env": environ,
        "close_fds": True,
    }
    arguments["start_new_session"] = True
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "blendersessiond.posix_launcher",
            str(gate_path),
            executable,
            str(bootstrap_path),
        ],
        **arguments,
    )


def spawn_windows_job_keeper(
    executable: str,
    *,
    stdout_path: Path,
    stderr_path: Path,
    environ: dict[str, str],
    bootstrap_path: Path,
) -> int:
    """Launch a persistent Job Object keeper blocked before Blender spawn."""

    bootstrap_path.unlink(missing_ok=True)
    bootstrap_path.with_suffix(".gate").unlink(missing_ok=True)
    bootstrap_path.with_suffix(".ready").unlink(missing_ok=True)
    keeper = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "blendersessiond.windows_job",
            executable,
            str(stdout_path),
            str(stderr_path),
            str(bootstrap_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environ,
        close_fds=True,
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        ),
    )
    return keeper.pid


def finish_windows_job_start(
    bootstrap_path: Path,
    *,
    keeper_pid: int,
) -> int:
    """Open the assigned-launcher gate and wait for Blender's PID."""

    ready_path = bootstrap_path.with_suffix(".ready")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not ready_path.exists():
        if process_start_time(keeper_pid) is None:
            raise RuntimeError("Windows Job Object keeper exited before readiness.")
        time.sleep(0.02)
    if not ready_path.exists():
        raise RuntimeError("Windows Job Object keeper readiness timed out.")
    bootstrap_path.with_suffix(".gate").touch()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            payload = json.loads(bootstrap_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            if process_start_time(keeper_pid) is None:
                break
            time.sleep(0.02)
            continue
        if "error" in payload:
            raise RuntimeError(f"Windows Job Object launch failed: {payload['error']}")
        pid = payload.get("pid")
        if isinstance(pid, int) and pid > 0:
            return pid
        break
    _terminate_windows_pid(keeper_pid)
    raise RuntimeError("Windows Job Object keeper did not report Blender startup.")


def finish_posix_start(
    bootstrap_path: Path,
    *,
    gate_path: Path,
    supervisor_pid: int,
) -> int:
    """Open the assigned process-group gate and wait for Blender's PID."""

    gate_path.touch()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            payload = json.loads(bootstrap_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            if process_start_time(supervisor_pid) is None:
                break
            time.sleep(0.02)
            continue
        if "error" in payload:
            raise RuntimeError(f"POSIX Blender launch failed: {payload['error']}")
        pid = payload.get("pid")
        if isinstance(pid, int) and pid > 0:
            return pid
        if payload.get("status") == "launching":
            time.sleep(0.02)
            continue
        break
    raise RuntimeError("POSIX supervisor did not report Blender startup.")


def wait_for_process_start_time(pid: int) -> str | None:
    """Wait briefly for the OS to expose a new process identity."""

    deadline = time.monotonic() + _IDENTITY_WAIT_SECONDS
    while time.monotonic() < deadline:
        identity = process_start_time(pid)
        if identity is not None:
            return identity
        time.sleep(0.02)
    return None


def process_start_time(pid: int) -> str | None:
    """Return a stable OS process creation identity, or None when absent."""

    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_process_start_time(pid)
    if sys.platform.startswith("linux"):
        return _linux_process_start_time(pid)
    if sys.platform == "darwin":
        return _darwin_process_start_time(pid)
    return _ps_process_start_time(pid)


def process_matches(pid: int, expected_start_time: str) -> bool:
    """Check that a PID still names the recorded process."""

    return process_start_time(pid) == expected_start_time


def terminate_process_tree(pid: int, expected_start_time: str) -> None:
    """Terminate only the tree whose root still has the recorded identity."""

    if not process_matches(pid, expected_start_time):
        return
    if os.name == "nt":
        _terminate_windows_tree(pid)
    else:
        _terminate_posix_group(pid)


def owned_tree_exists(
    pid: int,
    expected_start_time: str,
    *,
    controller_pid: int,
    controller_start_time: str,
) -> bool:
    """Return whether an independently tracked owned tree still exists."""

    if os.name == "nt":
        return process_matches(controller_pid, controller_start_time)
    if process_matches(controller_pid, controller_start_time):
        return True
    return _process_in_group(pid, expected_start_time, controller_pid)


def terminate_owned_tree(
    pid: int,
    expected_start_time: str,
    *,
    controller_pid: int,
    controller_start_time: str,
) -> None:
    """Terminate a verified process group or persistent Windows Job Object."""

    if os.name != "nt":
        controller_matches = process_matches(
            controller_pid, controller_start_time
        )
        if not controller_matches and not _process_in_group(
            pid, expected_start_time, controller_pid
        ):
            return
        _terminate_posix_group(controller_pid)
        return
    if not process_matches(controller_pid, controller_start_time):
        return
    _terminate_windows_pid(controller_pid)
    deadline = time.monotonic() + _STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not process_matches(controller_pid, controller_start_time) and not (
            process_matches(pid, expected_start_time)
        ):
            return
        time.sleep(0.05)
    raise RuntimeError("Windows Job Object did not terminate the Session tree.")


def _process_in_group(
    pid: int,
    expected_start_time: str,
    process_group: int,
) -> bool:
    if not process_matches(pid, expected_start_time):
        return False
    try:
        return os.getpgid(pid) == process_group
    except ProcessLookupError:
        return False


def _linux_process_start_time(pid: int) -> str | None:
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
        content = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    if not boot_id:
        return None
    try:
        fields_after_name = content.rsplit(")", 1)[1].split()
        if fields_after_name[0] == "Z":
            return None
        start_ticks = fields_after_name[19]
    except (IndexError, ValueError):
        return None
    return f"linux:{boot_id}:{start_ticks}"


def _ps_process_start_time(pid: int) -> str | None:
    try:
        completed = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = " ".join(completed.stdout.split())
    if completed.returncode != 0 or not value:
        return None
    return f"ps:{value}"


def _darwin_process_start_time(pid: int) -> str | None:
    class ProcBsdInfo(ctypes.Structure):
        _fields_ = [
            ("pbi_flags", ctypes.c_uint32),
            ("pbi_status", ctypes.c_uint32),
            ("pbi_xstatus", ctypes.c_uint32),
            ("pbi_pid", ctypes.c_uint32),
            ("pbi_ppid", ctypes.c_uint32),
            ("pbi_uid", ctypes.c_uint32),
            ("pbi_gid", ctypes.c_uint32),
            ("pbi_ruid", ctypes.c_uint32),
            ("pbi_rgid", ctypes.c_uint32),
            ("pbi_svuid", ctypes.c_uint32),
            ("pbi_svgid", ctypes.c_uint32),
            ("rfu_1", ctypes.c_uint32),
            ("pbi_comm", ctypes.c_char * 16),
            ("pbi_name", ctypes.c_char * 32),
            ("pbi_nfiles", ctypes.c_uint32),
            ("pbi_pgid", ctypes.c_uint32),
            ("pbi_pjobc", ctypes.c_uint32),
            ("e_tdev", ctypes.c_uint32),
            ("e_tpgid", ctypes.c_uint32),
            ("pbi_nice", ctypes.c_int32),
            ("pbi_start_tvsec", ctypes.c_uint64),
            ("pbi_start_tvusec", ctypes.c_uint64),
        ]

    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    except OSError:
        return None
    proc_pidinfo = libproc.proc_pidinfo
    proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    proc_pidinfo.restype = ctypes.c_int
    info = ProcBsdInfo()
    proc_pid_tbsdinfo = 3
    size = proc_pidinfo(
        pid,
        proc_pid_tbsdinfo,
        0,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if size != ctypes.sizeof(info) or info.pbi_pid != pid:
        return None
    if info.pbi_status == 5:
        return None
    return f"darwin:{info.pbi_start_tvsec}:{info.pbi_start_tvusec}"


def _windows_process_start_time(pid: int) -> str | None:
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        exit_value = (exit_time.dwHighDateTime << 32) | exit_time.dwLowDateTime
        if exit_value:
            return None
        value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return f"windows:{value}"
    finally:
        kernel32.CloseHandle(handle)


def _terminate_posix_group(pid: int) -> None:
    if pid <= 0:
        raise RuntimeError("Refusing to signal a non-positive process group ID.")
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + _STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not _process_group_exists(pid):
            return
        time.sleep(0.05)

    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + _STOP_WAIT_SECONDS
    while time.monotonic() < deadline and _process_group_exists(pid):
        time.sleep(0.05)
    if _process_group_exists(pid):
        raise RuntimeError("The Session process group survived SIGKILL.")


def _process_group_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "darwin":
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if sys.platform.startswith("linux"):
        return _linux_process_group_has_live_members(pid)
    return True


def _linux_process_group_has_live_members(process_group: int) -> bool:
    try:
        entries = Path("/proc").iterdir()
    except OSError:
        return True
    unknown = False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            content = (entry / "stat").read_text(encoding="utf-8")
            fields_after_name = content.rsplit(")", 1)[1].split()
            state = fields_after_name[0]
            group = int(fields_after_name[2])
        except FileNotFoundError:
            continue
        except (IndexError, PermissionError, ValueError):
            unknown = True
            continue
        except OSError:
            continue
        if group == process_group and state != "Z":
            return True
    return unknown


def _terminate_windows_tree(pid: int) -> None:
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "Windows timed out stopping the Session process tree."
        ) from error
    if completed.returncode not in (0, 128):
        details = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"Windows could not stop the Session process tree: {details}"
        )


def _terminate_windows_pid(pid: int) -> None:
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "Windows timed out stopping the Job Object keeper."
        ) from error
    if completed.returncode not in (0, 128):
        details = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Windows could not stop the Job Object keeper: {details}")
