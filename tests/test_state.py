from pathlib import Path

import pytest

from blendersessiond.state import check_state_directory, resolve_state_directory


@pytest.mark.parametrize(
    ("system", "environment", "expected_suffix"),
    [
        ("Darwin", {}, Path("Library/Application Support/blendersessiond")),
        (
            "Windows",
            {},
            Path("AppData/Local/blendersessiond"),
        ),
        ("Linux", {}, Path(".local/state/blendersessiond")),
    ],
)
def test_default_state_directory_per_platform(
    system: str,
    environment: dict[str, str],
    expected_suffix: Path,
    tmp_path: Path,
) -> None:
    result = resolve_state_directory(
        system=system,
        environ=environment,
        home=tmp_path,
    )

    assert result == tmp_path / expected_suffix


def test_environment_state_directories_are_honored(tmp_path: Path) -> None:
    linux_base = tmp_path / "xdg"
    windows_base = tmp_path / "local"

    assert resolve_state_directory(
        system="Linux",
        environ={"XDG_STATE_HOME": str(linux_base)},
        home=tmp_path,
    ) == linux_base / "blendersessiond"
    assert resolve_state_directory(
        system="Windows",
        environ={"LOCALAPPDATA": str(windows_base)},
        home=tmp_path,
    ) == windows_base / "blendersessiond"


def test_state_directory_writability_probe(tmp_path: Path) -> None:
    state_path = tmp_path / "state"

    result = check_state_directory(state_path)

    assert result.passed is True
    assert state_path.is_dir()
    assert list(state_path.iterdir()) == []
