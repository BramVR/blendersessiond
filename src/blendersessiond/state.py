"""Resolve and validate the state directory."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StateDirectoryCheck:
    """State-directory path and writability result."""

    path: str
    passed: bool
    message: str


def resolve_state_directory(
    *,
    system: str,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the per-user state directory for the current platform."""

    environment = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home

    if system == "Darwin":
        base = user_home / "Library" / "Application Support"
    elif system == "Windows":
        local_app_data = environment.get("LOCALAPPDATA")
        base = (
            Path(local_app_data)
            if local_app_data
            else user_home / "AppData" / "Local"
        )
    else:
        xdg_state_home = environment.get("XDG_STATE_HOME")
        base = (
            Path(xdg_state_home)
            if xdg_state_home
            else user_home / ".local" / "state"
        )

    return base / "blendersessiond"


def check_state_directory(path: Path) -> StateDirectoryCheck:
    """Create the state directory and verify a file can be written there."""

    descriptor: int | None = None
    probe_path: str | None = None
    try:
        path.mkdir(parents=True, exist_ok=True)
        descriptor, probe_path = tempfile.mkstemp(prefix=".doctor-", dir=path)
        os.close(descriptor)
        descriptor = None
        os.unlink(probe_path)
        probe_path = None
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        if probe_path is not None:
            try:
                os.unlink(probe_path)
            except OSError:
                pass
        return StateDirectoryCheck(
            path=str(path),
            passed=False,
            message=f"State directory {path} is not writable: {error}.",
        )

    return StateDirectoryCheck(
        path=str(path),
        passed=True,
        message=f"State directory {path} is writable.",
    )
