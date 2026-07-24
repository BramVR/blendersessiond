"""Validate a release tag and render its changelog section."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

STABLE_TAG = re.compile(r"v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))")
INIT_VERSION = re.compile(r'^__version__ = "(?P<version>[^"]+)"$', re.MULTILINE)


class ReleaseError(RuntimeError):
    """Release inputs are incomplete or inconsistent."""


def build_release_notes(repo: Path, tag: str) -> str:
    """Validate release metadata and return the matching changelog section."""
    tag_match = STABLE_TAG.fullmatch(tag)
    if tag_match is None:
        raise ReleaseError(
            f"tag must be a stable SemVer tag such as v1.2.3, got {tag!r}"
        )

    tagged_version = tag_match.group("version")
    project = _load_toml(repo / "pyproject.toml")
    project_version = project["project"]["version"]
    init_version = _read_init_version(repo / "src/blendersessiond/__init__.py")
    lock_version = _read_lock_version(repo / "uv.lock")

    versions = {
        "tag": tagged_version,
        "pyproject.toml": project_version,
        "src/blendersessiond/__init__.py": init_version,
        "uv.lock": lock_version,
    }
    mismatches = [
        f"{source} has {version!r}"
        for source, version in versions.items()
        if version != tagged_version
    ]
    if mismatches:
        details = "; ".join(mismatches)
        raise ReleaseError(
            f"release versions do not match {tagged_version!r}: {details}"
        )

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.compile(
        rf"^## \[{re.escape(tagged_version)}\] - \d{{4}}-\d{{2}}-\d{{2}}\s*$",
        re.MULTILINE,
    )
    heading_match = heading.search(changelog)
    if heading_match is None:
        raise ReleaseError(
            f"CHANGELOG.md needs a dated '## [{tagged_version}] - YYYY-MM-DD' section"
        )

    body_start = heading_match.end()
    next_section = re.search(r"^## \[", changelog[body_start:], re.MULTILINE)
    body_end = (
        body_start + next_section.start()
        if next_section is not None
        else len(changelog)
    )
    body = changelog[body_start:body_end].strip()
    if not body:
        raise ReleaseError(f"CHANGELOG.md section for {tagged_version} is empty")

    return f"# blendersessiond {tagged_version}\n\n{body}\n"


def _load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _read_init_version(path: Path) -> str:
    match = INIT_VERSION.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ReleaseError(f"{path} does not define __version__")
    return match.group("version")


def _read_lock_version(path: Path) -> str:
    lock = _load_toml(path)
    for package in lock["package"]:
        if package["name"] == "blendersessiond" and package.get("source") == {
            "editable": "."
        }:
            return package["version"]
    raise ReleaseError(f"{path} does not contain the editable blendersessiond package")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag", help="Release tag, for example v1.2.3")
    parser.add_argument(
        "--output",
        type=Path,
        help="Write release notes here instead of standard output.",
    )
    args = parser.parse_args()

    try:
        notes = build_release_notes(Path.cwd(), args.tag)
    except (KeyError, OSError, ReleaseError, tomllib.TOMLDecodeError) as error:
        parser.error(str(error))

    if args.output is None:
        print(notes, end="")
    else:
        args.output.write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
