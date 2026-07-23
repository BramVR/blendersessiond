"""Resolve and validate the state directory."""

from __future__ import annotations

import os
import platform
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

STATE_DIR_ENV_VAR = "BLENDERSESSIOND_STATE_DIR"


@dataclass(frozen=True)
class StateDirectoryCheck:
    """State-directory path and writability result."""

    path: str
    passed: bool
    message: str


def resolve_state_directory(
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the per-user state directory for the current platform."""

    environment = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    current_system = platform.system() if system is None else system

    override = environment.get(STATE_DIR_ENV_VAR)
    if override:
        override_path = Path(override).expanduser()
        if not override_path.is_absolute():
            raise ValueError(f"{STATE_DIR_ENV_VAR} must be an absolute path.")
        return override_path

    if current_system == "Darwin":
        base = user_home / "Library" / "Application Support"
    elif current_system == "Windows":
        local_app_data = environment.get("LOCALAPPDATA")
        base = (
            Path(local_app_data)
            if local_app_data
            else user_home / "AppData" / "Local"
        )
    else:
        xdg_state_home = environment.get("XDG_STATE_HOME")
        xdg_state_path = Path(xdg_state_home) if xdg_state_home else None
        base = (
            xdg_state_path
            if xdg_state_path is not None and xdg_state_path.is_absolute()
            else user_home / ".local" / "state"
        )

    return base / "blendersessiond"


def check_state_directory(path: Path) -> StateDirectoryCheck:
    """Create the state directory and verify a file can be written there."""

    descriptor: int | None = None
    probe_path: str | None = None
    try:
        created = not path.exists()
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if created and os.name != "nt":
            os.chmod(path, 0o700)
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
