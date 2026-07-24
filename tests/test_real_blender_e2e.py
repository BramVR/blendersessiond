from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import pytest
from real_blender_smoke import run_mcp_stdio_round_trip

from blendersessiond.discovery import discover_blender

pytestmark = [pytest.mark.e2e, pytest.mark.real_blender]


@pytest.fixture(scope="session")
def real_blender_path() -> Path:
    if os.environ.get("BLENDERSESSIOND_REAL_E2E") != "1":
        pytest.skip("set BLENDERSESSIOND_REAL_E2E=1 to launch real Blender")
    discovery = discover_blender(system=platform.system())
    if not discovery.passed or discovery.path is None:
        pytest.skip("Blender is not resolvable")
    return Path(discovery.path).resolve()


@pytest.fixture(scope="session")
def real_scene_fixture(
    tmp_path_factory: pytest.TempPathFactory,
    real_blender_path: Path,
) -> Path:
    output = tmp_path_factory.mktemp("real-scene") / "slice6_scene.blend"
    generator = (
        Path(__file__).parents[1] / "scripts" / "generate_scene_fixture.py"
    )
    completed = subprocess.run(
        [
            str(real_blender_path),
            "--background",
            "--factory-startup",
            "--python",
            str(generator),
            "--",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, (
        f"fixture generation failed\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    assert output.is_file()
    return output


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
        assert status.payload["session"]["health"]["socket"] == {
            "status": "healthy",
            "answered": True,
            "reason": "answered",
            "message": (
                "Addon socket at 127.0.0.1:9876 answered the Health ping."
            ),
        }

        scene = run("call", "get_scene_info", timeout=30)
        assert scene.completed.returncode == 0, scene.completed.stderr
        assert {
            "name",
            "object_count",
            "objects",
            "materials_count",
        } <= scene.payload.keys()

        mcp_scene = run_mcp_stdio_round_trip(
            [
                sys.executable,
                "-m",
                "blendersessiond",
                "mcp-serve",
            ],
            environment=environment,
        )
        assert {
            "name",
            "object_count",
            "objects",
            "materials_count",
        } <= mcp_scene.keys()

        cube = run(
            "call",
            "get_object_info",
            "--params",
            '{"name":"Cube"}',
            timeout=30,
        )
        assert cube.completed.returncode == 0, cube.completed.stderr
        assert cube.payload["name"] == "Cube"
    finally:
        if started:
            stopped = run("stop", timeout=30)
            assert stopped.completed.returncode == 0, stopped.completed.stderr

    gone = run("status", "--name", "default")
    assert gone.completed.returncode == 1
    assert gone.payload["status"] == "not-found"


def test_two_real_sessions_answer_independently(
    isolated_cli,
    real_blender_path: Path,
) -> None:
    environment, run = isolated_cli
    environment["PATH"] = (
        str(real_blender_path.parent)
        + os.pathsep
        + environment.get("PATH", "")
    )
    names = ("real-a", "real-b")
    started: list[str] = []
    try:
        first = run("start", "--name", names[0], timeout=45)
        assert first.completed.returncode == 0, first.completed.stderr
        started.append(names[0])
        second = run("start", "--name", names[1], timeout=45)
        assert second.completed.returncode == 0, second.completed.stderr
        started.append(names[1])
        assert (
            first.payload["session"]["mcp_port"]
            != second.payload["session"]["mcp_port"]
        )

        for name in names:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                status = run("status", "--name", name)
                if status.completed.returncode == 0:
                    break
                time.sleep(0.25)
            assert status.completed.returncode == 0, status.completed.stderr
            renamed = run(
                "call",
                "execute_code",
                "--name",
                name,
                "--params",
                json.dumps({"code": f"bpy.context.scene.name = {name!r}"}),
                timeout=30,
            )
            assert renamed.completed.returncode == 0, renamed.completed.stderr

        scenes = {
            name: run("call", "get_scene_info", "--name", name, timeout=30)
            for name in names
        }
        assert {name: result.payload["name"] for name, result in scenes.items()} == {
            name: name for name in names
        }
    finally:
        for name in reversed(started):
            stopped = run("stop", "--name", name, timeout=30)
            assert stopped.completed.returncode == 0, stopped.completed.stderr


def test_scene_dirty_status_and_stop_never_saves(
    isolated_cli,
    real_blender_path: Path,
    real_scene_fixture: Path,
) -> None:
    environment, run = isolated_cli
    environment["PATH"] = (
        str(real_blender_path.parent)
        + os.pathsep
        + environment.get("PATH", "")
    )
    before = hashlib.sha256(real_scene_fixture.read_bytes()).hexdigest()
    started = False
    stopped = False
    try:
        result = run(
            "start",
            "--scene",
            str(real_scene_fixture),
            timeout=45,
        )
        assert result.completed.returncode == 0, result.completed.stderr
        started = True
        assert result.payload["session"]["scene_path"] == str(real_scene_fixture)

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = run("status", "--name", "default")
            if status.completed.returncode == 0:
                break
            time.sleep(0.25)
        assert status.completed.returncode == 0, status.completed.stderr
        session = status.payload["session"]
        assert session["scene_path"] == str(real_scene_fixture)
        assert session["unsaved_changes"] is False

        scene = run("call", "get_scene_info", timeout=30)
        assert scene.completed.returncode == 0, scene.completed.stderr
        assert scene.payload["name"] == "Slice6FixtureScene"
        assert {item["name"] for item in scene.payload["objects"]} == {
            "Slice6FixtureAnchor",
            "Slice6FixtureCube",
        }

        # Mutate the way an interactive edit does: a low-level data-API poke
        # never marks the file dirty (Blender's own asterisk works the same),
        # so add an object and push an undo step, which flips bpy.data.is_dirty.
        mutation = (
            "obj = bpy.data.objects.new('Slice6UnsavedObject', None)\n"
            "bpy.context.scene.collection.objects.link(obj)\n"
            "bpy.ops.ed.undo_push(message='add Slice6UnsavedObject')"
        )
        mutated = run(
            "call",
            "execute_code",
            "--params",
            json.dumps({"code": mutation}),
            timeout=30,
        )
        assert mutated.completed.returncode == 0, mutated.completed.stderr

        dirty = run("status", "--name", "default")
        assert dirty.completed.returncode == 0, dirty.completed.stderr
        assert dirty.payload["session"]["unsaved_changes"] is True

        stop_result = run("stop", timeout=30)
        assert stop_result.completed.returncode == 0, stop_result.completed.stderr
        stopped = True
    finally:
        if started and not stopped:
            cleanup = run("stop", timeout=30)
            assert cleanup.completed.returncode == 0, cleanup.completed.stderr

    after = hashlib.sha256(real_scene_fixture.read_bytes()).hexdigest()
    assert after == before
