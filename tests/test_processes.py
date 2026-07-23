from __future__ import annotations

import os
from pathlib import Path

import pytest

from blendersessiond.processes import (
    _linux_process_start_time,
    _windows_taskkill_command,
    finish_posix_start,
    spawn_detached,
)


def test_linux_process_identity_includes_boot_id(monkeypatch) -> None:
    def read_text(path: Path, **_kwargs) -> str:
        # Match by name: str(path) uses backslashes on a Windows host.
        if path.name == "boot_id":
            return "boot-identity\n"
        return "123 (fake process) S " + " ".join(
            str(value) for value in range(1, 25)
        )

    monkeypatch.setattr(Path, "read_text", read_text)

    assert _linux_process_start_time(123) == "linux:boot-identity:19"


def test_windows_taskkill_commands_use_system32(monkeypatch, tmp_path: Path) -> None:
    system_root = tmp_path / "Windows"
    monkeypatch.setenv("SystemRoot", str(system_root))

    assert _windows_taskkill_command(123, tree=True) == [
        str(system_root / "System32" / "taskkill.exe"),
        "/PID",
        "123",
        "/T",
        "/F",
    ]
    assert _windows_taskkill_command(456, tree=False) == [
        str(system_root / "System32" / "taskkill.exe"),
        "/PID",
        "456",
        "/F",
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX exec handshake")
def test_posix_launcher_reports_exec_failure(tmp_path: Path) -> None:
    gate = tmp_path / "gate"
    bootstrap = tmp_path / "bootstrap.json"
    with (tmp_path / "stdout.log").open("wb") as stdout, (
        tmp_path / "stderr.log"
    ).open("wb") as stderr:
        process = spawn_detached(
            str(tmp_path / "missing-blender"),
            stdout=stdout,
            stderr=stderr,
            environ=dict(os.environ),
            gate_path=gate,
            bootstrap_path=bootstrap,
            addon_bootstrap_path=tmp_path / "addon-bootstrap.py",
        )

    with pytest.raises(RuntimeError, match="POSIX Blender launch failed"):
        finish_posix_start(
            bootstrap,
            gate_path=gate,
            supervisor_pid=process.pid,
        )
    assert process.wait(timeout=5) == 1
