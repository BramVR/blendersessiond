from __future__ import annotations

import os
import platform
import time
from pathlib import Path

import pytest

from blendersessiond.discovery import discover_blender

pytestmark = [pytest.mark.e2e, pytest.mark.real_blender]


@pytest.fixture
def real_blender_path() -> Path:
    if os.environ.get("BLENDERSESSIOND_REAL_E2E") != "1":
        pytest.skip("set BLENDERSESSIOND_REAL_E2E=1 to launch real Blender")
    discovery = discover_blender(system=platform.system())
    if not discovery.passed or discovery.path is None:
        pytest.skip("Blender is not resolvable")
    return Path(discovery.path).resolve()


def test_doctor_resolves_path_blender(
    isolated_cli,
    real_blender_path: Path,
) -> None:
    environment, run = isolated_cli
    environment["PATH"] = (
        str(real_blender_path.parent)
        + os.pathsep
        + environment.get("PATH", "")
    )

    result = run("doctor", timeout=30)

    assert result.completed.returncode == 0, result.completed.stderr
    assert os.path.samefile(result.payload["blender"]["path"], real_blender_path)
    assert result.payload["blender"]["source"] == "PATH"


def test_real_blender_start_status_stop_round_trip(
    isolated_cli,
    real_blender_path: Path,
) -> None:
    environment, run = isolated_cli
    environment["PATH"] = (
        str(real_blender_path.parent)
        + os.pathsep
        + environment.get("PATH", "")
    )
    started = False
    try:
        result = run("start", timeout=45)
        assert result.completed.returncode == 0, result.completed.stderr
        started = True
        assert os.path.samefile(
            result.payload["session"]["blender"]["path"],
            real_blender_path,
        )

        deadline = time.monotonic() + 30
        status = None
        while time.monotonic() < deadline:
            status = run("status", "--name", "default")
            if status.completed.returncode == 0:
                break
            time.sleep(0.25)
        assert status is not None
        assert status.completed.returncode == 0, status.completed.stderr
        assert status.payload["session"]["health"]["process"] == {
            "status": "healthy",
            "alive": True,
        }
    finally:
        if started:
            stopped = run("stop", timeout=30)
            assert stopped.completed.returncode == 0, stopped.completed.stderr

    gone = run("status", "--name", "default")
    assert gone.completed.returncode == 1
    assert gone.payload["status"] == "not-found"
