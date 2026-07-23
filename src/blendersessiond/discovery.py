"""Resolve and inspect the Blender executable."""

from __future__ import annotations

import glob
import ntpath
import os
import posixpath
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

BLENDER_ENV_VAR = "BLENDERSESSIOND_BLENDER"
_VERSION_PATTERN = re.compile(
    r"^Blender\s+(\d+(?:\.\d+){1,2})\b",
    flags=re.IGNORECASE | re.MULTILINE,
)


class CompletedProcessLike(Protocol):
    """Subset of subprocess output needed for version probing."""

    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., CompletedProcessLike]


@dataclass(frozen=True)
class BlenderDiscovery:
    """Result of resolving and probing Blender."""

    path: str | None
    version: str | None
    source: str | None
    message: str

    @property
    def passed(self) -> bool:
        return self.path is not None and self.version is not None


@dataclass(frozen=True)
class _ProbedCandidate:
    path: str
    version: str
    version_key: tuple[int, int, int]


def discover_blender(
    *,
    explicit_path: str | None = None,
    environ: Mapping[str, str] | None = None,
    system: str,
    which: Callable[[str], str | None] | None = None,
    globber: Callable[[str], list[str]] | None = None,
    is_file: Callable[[str], bool] | None = None,
    can_execute: Callable[[str], bool] | None = None,
    runner: Runner | None = None,
) -> BlenderDiscovery:
    """Resolve Blender according to the configured source priority."""

    environment = os.environ if environ is None else environ
    expand_glob = glob.glob if globber is None else globber
    path_is_file = os.path.isfile if is_file is None else is_file
    executable_check = (
        (lambda path: os.access(path, os.X_OK)) if can_execute is None else can_execute
    )
    run = subprocess.run if runner is None else runner

    if explicit_path:
        return _select_candidates(
            [_absolute_path(explicit_path, system)],
            source="--blender",
            source_description="the --blender flag",
            is_file=path_is_file,
            runner=run,
        )

    environment_path = environment.get(BLENDER_ENV_VAR)
    if environment_path:
        return _select_candidates(
            [_absolute_path(environment_path, system)],
            source=BLENDER_ENV_VAR,
            source_description=f"the {BLENDER_ENV_VAR} environment variable",
            is_file=path_is_file,
            runner=run,
        )

    executable_name = "blender.exe" if system == "Windows" else "blender"
    path_hit = _find_on_explicit_path(
        executable_name,
        path=environment.get("PATH", ""),
        system=system,
        which=which,
        is_file=path_is_file,
        can_execute=executable_check,
    )
    if path_hit:
        return _select_candidates(
            [_absolute_path(path_hit, system)],
            source="PATH",
            source_description="PATH",
            is_file=path_is_file,
            runner=run,
        )

    standard = standard_candidates(
        system=system,
        environ=environment,
        globber=expand_glob,
    )
    if standard:
        return _select_candidates(
            standard,
            source="standard_location",
            source_description=f"standard {platform_label(system)} install locations",
            is_file=path_is_file,
            runner=run,
        )

    return BlenderDiscovery(
        path=None,
        version=None,
        source=None,
        message=_missing_blender_message(),
    )


def standard_candidates(
    *,
    system: str,
    environ: Mapping[str, str],
    globber: Callable[[str], list[str]] = glob.glob,
) -> list[str]:
    """Return possible Blender binaries from platform install locations."""

    if system == "Darwin":
        return _deduplicate(
            [
                "/Applications/Blender.app/Contents/MacOS/Blender",
                *globber(
                    "/Applications/Blender *.app/Contents/MacOS/Blender"
                ),
            ]
        )

    if system == "Windows":
        program_files = environ.get("ProgramFiles")
        if not program_files:
            return []
        pattern = ntpath.join(
            program_files,
            "Blender Foundation",
            "Blender *",
            "blender.exe",
        )
        return _deduplicate(globber(pattern))

    if system == "Linux":
        return _deduplicate(
            [
                "/usr/bin/blender",
                "/snap/bin/blender",
                *globber("/opt/blender*/blender"),
            ]
        )

    return []


def platform_label(system: str) -> str:
    """Return the user-facing name for a platform.system() value."""

    return {
        "Darwin": "macOS",
        "Windows": "Windows",
        "Linux": "Linux",
    }.get(system, system or "unknown")


def _select_candidates(
    candidates: Sequence[str],
    *,
    source: str,
    source_description: str,
    is_file: Callable[[str], bool],
    runner: Runner,
) -> BlenderDiscovery:
    existing = [path for path in _deduplicate(candidates) if is_file(path)]
    if not existing:
        configured_path = candidates[0] if len(candidates) == 1 else None
        if configured_path:
            message = (
                f"Blender from {source_description} is not a file: "
                f"{configured_path}. {_remedy()}"
            )
        else:
            message = _missing_blender_message()
        return BlenderDiscovery(None, None, None, message)

    probed: list[_ProbedCandidate] = []
    failures: list[str] = []
    for path in existing:
        try:
            version, version_key = _probe_version(path, runner)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            failures.append(f"{path}: {error}")
            continue
        probed.append(_ProbedCandidate(path, version, version_key))

    if not probed:
        details = "; ".join(failures)
        return BlenderDiscovery(
            None,
            None,
            None,
            (
                f"Found Blender via {source_description}, but could not read its "
                f"version ({details}). {_remedy()}"
            ),
        )

    selected = max(probed, key=lambda candidate: candidate.version_key)
    newest_note = ""
    if len(probed) > 1:
        newest_note = f"; newest of {len(probed)} valid candidates"
    return BlenderDiscovery(
        path=selected.path,
        version=selected.version,
        source=source,
        message=(
            f"Blender {selected.version} at {selected.path} selected from "
            f"{source_description}{newest_note}."
        ),
    )


def _probe_version(path: str, runner: Runner) -> tuple[str, tuple[int, int, int]]:
    completed = runner(
        [path, "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    if completed.returncode != 0:
        raise RuntimeError(f"`--version` exited with code {completed.returncode}")

    match = _VERSION_PATTERN.search(output)
    if not match:
        raise RuntimeError("output did not contain a Blender version")

    version = match.group(1)
    parts = tuple(int(part) for part in version.split("."))
    version_key = (parts + (0, 0, 0))[:3]
    return version, version_key


def _absolute_path(path: str, system: str) -> str:
    expanded = os.path.expanduser(path)
    path_module = ntpath if system == "Windows" else posixpath
    return path_module.abspath(expanded)


def _find_on_explicit_path(
    executable_name: str,
    *,
    path: str,
    system: str,
    which: Callable[[str], str | None] | None,
    is_file: Callable[[str], bool],
    can_execute: Callable[[str], bool],
) -> str | None:
    path_module = ntpath if system == "Windows" else posixpath
    # Separator follows the simulated system, never the host (os.pathsep).
    separator = ";" if system == "Windows" else ":"
    # Empty and relative entries implicitly trust the current directory.
    entries = [
        path_module.normpath(entry)
        for entry in path.split(separator)
        if entry and path_module.isabs(entry)
    ]
    if not entries:
        return None

    if which is None:
        for entry in entries:
            candidate = path_module.join(entry, executable_name)
            # Windows has no execute bit; POSIX must skip non-executable
            # files so a shadowing plain file cannot mask a later real hit.
            if is_file(candidate) and (system == "Windows" or can_execute(candidate)):
                return candidate
        return None

    path_hit = which(executable_name)
    if not path_hit:
        return None

    allowed_parents = {
        path_module.normcase(path_module.abspath(entry)) for entry in entries
    }
    hit_parent = path_module.normcase(
        path_module.dirname(path_module.abspath(path_hit))
    )
    return path_hit if hit_parent in allowed_parents else None


def _deduplicate(paths: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(paths))


def _missing_blender_message() -> str:
    return f"Blender was not found. {_remedy()}"


def _remedy() -> str:
    return (
        "Install Blender, pass --blender PATH, set "
        f"{BLENDER_ENV_VAR}, or add Blender to PATH."
    )
