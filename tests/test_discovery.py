from __future__ import annotations

from dataclasses import dataclass

import pytest

from blendersessiond.discovery import BLENDER_ENV_VAR, discover_blender


@dataclass
class Completed:
    stdout: str
    returncode: int = 0
    stderr: str = ""


def runner_for(versions: dict[str, str]):
    def run(command, **_kwargs):
        path = command[0]
        return Completed(stdout=f"Blender {versions[path]}\n")

    return run


def test_flag_wins_over_environment_and_path() -> None:
    flag = "/custom/blender"
    result = discover_blender(
        explicit_path=flag,
        environ={BLENDER_ENV_VAR: "/env/blender"},
        system="Linux",
        which=lambda _name: "/path/blender",
        is_file=lambda path: path == flag,
        runner=runner_for({flag: "4.3.2"}),
    )

    assert result.path == flag
    assert result.version == "4.3.2"
    assert result.source == "--blender"


def test_environment_wins_over_path() -> None:
    environment_path = "/env/blender"
    result = discover_blender(
        environ={BLENDER_ENV_VAR: environment_path},
        system="Linux",
        which=lambda _name: "/path/blender",
        is_file=lambda path: path == environment_path,
        runner=runner_for({environment_path: "4.2.1"}),
    )

    assert result.path == environment_path
    assert result.source == BLENDER_ENV_VAR


def test_path_hit_is_used_before_standard_locations() -> None:
    path_hit = "/tools/blender"
    result = discover_blender(
        environ={"PATH": "/tools"},
        system="Linux",
        which=lambda _name: path_hit,
        globber=lambda _pattern: ["/opt/blender-9.0/blender"],
        is_file=lambda path: path == path_hit,
        runner=runner_for({path_hit: "3.6.0"}),
    )

    assert result.path == path_hit
    assert result.source == "PATH"


def test_path_probe_uses_absolute_path_hit_when_cwd_also_has_blender() -> None:
    cwd_hit = r"C:\untrusted\blender.exe"
    path_hit = r"C:\trusted\blender.exe"
    existing_files = {cwd_hit, path_hit}

    result = discover_blender(
        environ={"PATH": r"C:\trusted"},
        system="Windows",
        globber=lambda _pattern: [],
        is_file=lambda path: path in existing_files,
        runner=runner_for({path_hit: "4.3.0"}),
    )

    assert result.path == path_hit
    assert result.source == "PATH"


def test_path_probe_rejects_current_directory_hit_not_on_path() -> None:
    cwd_hit = r"C:\untrusted\blender.exe"
    probed: list[str] = []

    def run(command, **_kwargs):
        probed.append(command[0])
        return Completed(stdout="Blender 4.3.0\n")

    result = discover_blender(
        environ={"PATH": r"C:\trusted"},
        system="Windows",
        which=lambda _name: cwd_hit,
        globber=lambda _pattern: [],
        is_file=lambda path: path == cwd_hit,
        runner=run,
    )

    assert result.path is None
    assert result.source is None
    assert probed == []


@pytest.mark.parametrize(
    ("system", "environment", "candidate"),
    [
        (
            "Darwin",
            {},
            "/Applications/Blender 4.3.app/Contents/MacOS/Blender",
        ),
        (
            "Windows",
            {"ProgramFiles": r"C:\Program Files"},
            (
                r"C:\Program Files\Blender Foundation"
                r"\Blender 4.3\blender.exe"
            ),
        ),
        ("Linux", {}, "/opt/blender-4.3/blender"),
    ],
)
def test_standard_location_hit_per_platform(
    system: str,
    environment: dict[str, str],
    candidate: str,
) -> None:
    result = discover_blender(
        environ=environment,
        system=system,
        which=lambda _name: None,
        globber=lambda _pattern: [candidate],
        is_file=lambda path: path == candidate,
        runner=runner_for({candidate: "4.3.0"}),
    )

    assert result.path == candidate
    assert result.source == "standard_location"


def test_newest_version_wins_when_multiple_standard_candidates_exist() -> None:
    candidates = [
        "/opt/blender-3.6/blender",
        "/opt/blender-4.3/blender",
        "/opt/blender-4.2/blender",
    ]
    result = discover_blender(
        environ={},
        system="Linux",
        which=lambda _name: None,
        globber=lambda _pattern: candidates,
        is_file=lambda path: path in candidates,
        runner=runner_for(
            {
                candidates[0]: "3.6.9",
                candidates[1]: "4.3.1",
                candidates[2]: "4.2.10",
            }
        ),
    )

    assert result.path == candidates[1]
    assert result.version == "4.3.1"
    assert "newest of 3" in result.message


def test_none_found_returns_clear_remedy() -> None:
    result = discover_blender(
        environ={},
        system="Linux",
        which=lambda _name: None,
        globber=lambda _pattern: [],
        is_file=lambda _path: False,
    )

    assert result.passed is False
    assert result.path is None
    assert result.version is None
    assert "Blender was not found" in result.message
    assert "--blender" in result.message
    assert BLENDER_ENV_VAR in result.message


def test_invalid_flag_does_not_fall_through() -> None:
    result = discover_blender(
        explicit_path="/missing/blender",
        environ={},
        system="Linux",
        which=lambda _name: "/path/blender",
        is_file=lambda _path: False,
    )

    assert result.passed is False
    assert "/missing/blender" in result.message
