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
    if len(sys.argv) == 8 and sys.argv[1] == "--child":
        return _launch_child(*sys.argv[2:])
    if os.name != "nt" or len(sys.argv) != 6:
        return 2
    (
        executable,
        stdout_name,
        stderr_name,
        bootstrap_name,
        addon_bootstrap_name,
    ) = sys.argv[1:]
    bootstrap = Path(bootstrap_name)
    gate = bootstrap.with_suffix(".gate")
    ready = bootstrap.with_suffix(".ready")
    job = None
    launcher = None
    try:
        gate.unlink(missing_ok=True)
        job = _create_job()
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
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        if not ctypes.windll.kernel32.AssignProcessToJobObject(
            job, launcher._handle
        ):
            raise ctypes.WinError()
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
            ctypes.windll.kernel32.CloseHandle(job)


def _launch_child(
    executable: str,
    stdout_name: str,
    stderr_name: str,
    bootstrap_name: str,
    gate_name: str,
    addon_bootstrap_name: str,
) -> int:
    bootstrap = Path(bootstrap_name)
    try:
        gate = Path(gate_name)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not gate.exists():
            time.sleep(0.01)
        if not gate.exists():
            raise RuntimeError("Job Object assignment gate timed out")
        with Path(stdout_name).open("ab", buffering=0) as stdout, Path(
            stderr_name
        ).open("ab", buffering=0) as stderr:
            process = subprocess.Popen(
                [
                    executable,
                    "--factory-startup",
                    "--python",
                    addon_bootstrap_name,
                ],
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


def _create_job():
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
