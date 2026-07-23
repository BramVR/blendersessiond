"""Build the machine-readiness doctor report."""

from __future__ import annotations

import os
import platform
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from blendersessiond.discovery import (
    BlenderDiscovery,
    discover_blender,
    platform_label,
)
from blendersessiond.sessions import SessionInspection, inspect_all_sessions
from blendersessiond.state import (
    StateDirectoryCheck,
    check_state_directory,
    resolve_state_directory,
)

SUPPORTED_SYSTEMS = frozenset({"Darwin", "Windows", "Linux"})


@dataclass(frozen=True)
class Check:
    """A named doctor check."""

    name: str
    status: str
    message: str

    @classmethod
    def from_result(cls, name: str, passed: bool, message: str) -> Check:
        return cls(name=name, status="pass" if passed else "fail", message=message)


@dataclass(frozen=True)
class DoctorReport:
    """Stable versioned report returned by the doctor verb."""

    platform_system: str
    platform_name: str
    checks: tuple[Check, ...]
    blender: BlenderDiscovery
    state_directory: StateDirectoryCheck
    sessions: tuple[SessionInspection, ...] = ()

    @property
    def status(self) -> str:
        return (
            "pass"
            if all(check.status == "pass" for check in self.checks)
            else "fail"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "platform": {
                "system": self.platform_system,
                "name": self.platform_name,
            },
            "checks": [asdict(check) for check in self.checks],
            "blender": {
                "path": self.blender.path,
                "version": self.blender.version,
                "source": self.blender.source,
            },
            "state_dir": self.state_directory.path,
            "sessions": [session.to_dict() for session in self.sessions],
        }


def build_doctor_report(
    *,
    explicit_blender: str | None = None,
    environ: Mapping[str, str] | None = None,
    system: str | None = None,
    home: Path | None = None,
) -> DoctorReport:
    """Run all checks needed to decide whether this machine can host a Session."""

    environment = os.environ if environ is None else environ
    current_system = platform.system() if system is None else system
    label = platform_label(current_system)
    platform_passed = current_system in SUPPORTED_SYSTEMS
    platform_message = (
        f"{label} is supported."
        if platform_passed
        else f"{label} is unsupported; use macOS, Windows, or Linux."
    )

    blender = discover_blender(
        explicit_path=explicit_blender,
        environ=environment,
        system=current_system,
    )
    state_path = resolve_state_directory(
        system=current_system,
        environ=environment,
        home=home,
    )
    state_directory = check_state_directory(state_path)

    sessions = (
        tuple(inspect_all_sessions(state_root=state_path))
        if state_directory.passed
        else ()
    )
    checks = (
        Check.from_result("platform", platform_passed, platform_message),
        Check.from_result("blender", blender.passed, blender.message),
        Check.from_result(
            "state_directory",
            state_directory.passed,
            state_directory.message,
        ),
        *(
            Check.from_result(
                f"session:{session.name}",
                session.healthy,
                (
                    f"Session '{session.name}' record is readable; "
                    f"{session.message}"
                    if session.healthy
                    else session.message
                ),
            )
            for session in sessions
        ),
    )
    return DoctorReport(
        platform_system=current_system,
        platform_name=label,
        checks=checks,
        blender=blender,
        state_directory=state_directory,
        sessions=sessions,
    )
