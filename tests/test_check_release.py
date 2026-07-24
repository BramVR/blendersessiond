from pathlib import Path

import pytest
from check_release import ReleaseError, build_release_notes


def _release_repo(tmp_path: Path, *, version: str = "1.2.3") -> Path:
    (tmp_path / "src/blendersessiond").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "blendersessiond"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (tmp_path / "src/blendersessiond/__init__.py").write_text(
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        "\n".join(
            [
                "version = 1",
                "[[package]]",
                'name = "blendersessiond"',
                f'version = "{version}"',
                'source = { editable = "." }',
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "\n".join(
            [
                "# Changelog",
                "",
                "## [Unreleased]",
                "",
                f"## [{version}] - 2026-07-24",
                "",
                "### Added",
                "",
                "- A releasable package.",
                "",
                "## [1.0.0] - 2026-01-01",
                "",
                "- Earlier work.",
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_build_release_notes_validates_versions_and_extracts_section(
    tmp_path: Path,
) -> None:
    repo = _release_repo(tmp_path)

    notes = build_release_notes(repo, "v1.2.3")

    assert notes == (
        "# blendersessiond 1.2.3\n\n"
        "### Added\n\n"
        "- A releasable package.\n"
    )
    assert "Earlier work." not in notes


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.3rc1", "release-1.2.3"])
def test_build_release_notes_rejects_non_stable_semver_tags(
    tmp_path: Path, tag: str
) -> None:
    repo = _release_repo(tmp_path)

    with pytest.raises(ReleaseError, match="stable SemVer"):
        build_release_notes(repo, tag)


def test_build_release_notes_rejects_mismatched_versions(tmp_path: Path) -> None:
    repo = _release_repo(tmp_path, version="1.2.4")

    with pytest.raises(ReleaseError, match="versions do not match"):
        build_release_notes(repo, "v1.2.3")


def test_build_release_notes_requires_dated_changelog_section(tmp_path: Path) -> None:
    repo = _release_repo(tmp_path)
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseError, match="dated"):
        build_release_notes(repo, "v1.2.3")
