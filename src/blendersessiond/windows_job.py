"""Persistent Windows Job Object keeper for one Blender Session."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_time", ctypes.c_longlong),
        ("per_job_time", ctypes.c_longlong),
        ("flags", ctypes.c_uint32),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_uint32),
        ("scheduling_class", ctypes.c_uint32),
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
        ("page_faults", ctypes.c_uint32),
        ("total_processes", ctypes.c_uint32),
        ("active_processes", ctypes.c_uint32),
        ("terminated_processes", ctypes.c_uint32),
    ]


def main() -> int:
    if len(sys.argv) == 9 and sys.argv[1] == "--child":
        return _launch_child(*sys.argv[2:])
    if len(sys.argv) >= 4 and sys.argv[1] == "--stdio-child":
        return _launch_stdio_child(sys.argv[2], sys.argv[3:])
    if os.name != "nt" or len(sys.argv) != 7:
        return 2
    (
        executable,
        stdout_name,
        stderr_name,
        bootstrap_name,
        addon_bootstrap_name,
        scene_name,
    ) = sys.argv[1:]
    bootstrap = Path(bootstrap_name)
    gate = bootstrap.with_suffix(".gate")
    ready = bootstrap.with_suffix(".ready")
    job = None
    launcher = None
    try:
        gate.unlink(missing_ok=True)
        job = create_kill_on_close_job()
        launcher = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "blendersessiond.windows_job",
                "--child",
                executable,
                stdout_name,
                stderr_name,
                bootstrap_name,
                str(gate),
                addon_bootstrap_name,
                scene_name,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        assign_process(job, launcher)
        ready.touch()
        while _active_processes(job) > 0:
            time.sleep(0.1)
        return 0
    except BaseException as error:
        if launcher is not None and launcher.poll() is None:
            launcher.kill()
        try:
            bootstrap.write_text(
                json.dumps({"error": f"{type(error).__name__}: {error}"}),
                encoding="utf-8",
            )
        except OSError:
            pass
        return 1
    finally:
        gate.unlink(missing_ok=True)
        ready.unlink(missing_ok=True)
        if job is not None:
            close_job(job)


def _launch_child(
    executable: str,
    stdout_name: str,
    stderr_name: str,
    bootstrap_name: str,
    gate_name: str,
    addon_bootstrap_name: str,
    scene_name: str,
) -> int:
    bootstrap = Path(bootstrap_name)
    try:
        gate = Path(gate_name)
        _wait_for_gate(gate)
        with Path(stdout_name).open("ab", buffering=0) as stdout, Path(
            stderr_name
        ).open("ab", buffering=0) as stderr:
            # Managed Sessions isolate user preferences even when opening a scene.
            blender_arguments = [executable, "--factory-startup", "--no-splash"]
            if scene_name:
                blender_arguments.append(scene_name)
            blender_arguments.extend(["--python", addon_bootstrap_name])
            process = subprocess.Popen(
                blender_arguments,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                close_fds=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        bootstrap.write_text(json.dumps({"pid": process.pid}), encoding="utf-8")
        return 0
    except BaseException as error:
        bootstrap.write_text(
            json.dumps({"error": f"{type(error).__name__}: {error}"}),
            encoding="utf-8",
        )
        return 1


def _launch_stdio_child(gate_name: str, command: list[str]) -> int:
    _wait_for_gate(Path(gate_name))
    return subprocess.run(command, check=False).returncode


def _wait_for_gate(gate: Path) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not gate.exists():
        time.sleep(0.01)
    if not gate.exists():
        raise RuntimeError("Job Object assignment gate timed out")


def create_kill_on_close_job():
    """Create a Job Object that terminates its processes when closed."""

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError()
    information = _ExtendedLimitInformation()
    information.basic.flags = 0x00002000
    if not kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(information), ctypes.sizeof(information)
    ):
        kernel32.CloseHandle(job)
        raise ctypes.WinError()
    return job


def assign_process(job, process: subprocess.Popen[bytes]) -> None:
    """Assign a spawned process and its future descendants to a Job Object."""

    if not ctypes.windll.kernel32.AssignProcessToJobObject(job, process._handle):
        raise ctypes.WinError()


def close_job(job) -> None:
    """Close a Job Object handle."""

    ctypes.windll.kernel32.CloseHandle(job)


def _active_processes(job) -> int:
    information = _BasicAccountingInformation()
    if not ctypes.windll.kernel32.QueryInformationJobObject(
        job,
        1,
        ctypes.byref(information),
        ctypes.sizeof(information),
        None,
    ):
        raise ctypes.WinError()
    return information.active_processes


if __name__ == "__main__":
    raise SystemExit(main())
