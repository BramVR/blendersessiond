"""Launch the stock BlenderMCP stdio server for one healthy Session."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from blendersessiond import windows_job
from blendersessiond.sessions import (
    DEFAULT_SESSION_NAME,
    inspect_session,
)
from blendersessiond.wire import LOOPBACK_HOST

BLENDER_HOST_ENV_VAR = "BLENDER_HOST"
BLENDER_PORT_ENV_VAR = "BLENDER_PORT"
TELEMETRY_DISABLE_ENV_VAR = "DISABLE_TELEMETRY"
VALIDATED_SERVER_REQUIREMENT = "blender-mcp==1.6.4"


class McpServeError(RuntimeError):
    """A failure that must be reported before starting the stdio server."""


def serve_mcp(
    *,
    name: str = DEFAULT_SESSION_NAME,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Validate a named Session, then hand stdio to the validated server."""

    environment = dict(os.environ if environ is None else environ)
    inspection = inspect_session(name=name, environ=environment)
    if not inspection.healthy or inspection.record is None:
        raise McpServeError(_session_remedy(name, inspection.status))

    uvx = shutil.which("uvx", path=environment.get("PATH"))
    if uvx is None:
        raise McpServeError(
            "uvx is required to run blender-mcp; install the uv prerequisite "
            "and ensure uvx is on PATH."
        )

    environment[BLENDER_HOST_ENV_VAR] = LOOPBACK_HOST
    environment[BLENDER_PORT_ENV_VAR] = str(inspection.record.mcp_port)
    # The stock server ships default-on telemetry; managed Sessions never send it.
    environment[TELEMETRY_DISABLE_ENV_VAR] = "true"
    command = [uvx, VALIDATED_SERVER_REQUIREMENT]
    if os.name == "nt":
        return _run_in_windows_job(command, environment)

    os.execve(uvx, command, environment)
    raise AssertionError("os.execve returned unexpectedly")


def _run_in_windows_job(command: list[str], environment: dict[str, str]) -> int:
    job = windows_job.create_kill_on_close_job()
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="blendersessiond-mcp-") as temp_dir:
            gate = Path(temp_dir) / "assigned"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "blendersessiond.windows_job",
                    "--stdio-child",
                    str(gate),
                    *command,
                ],
                env=environment,
            )
            windows_job.assign_process(job, process)
            # uvx starts only after its launcher belongs to the kill-on-close job.
            gate.touch()
            return process.wait()
    except BaseException:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise
    finally:
        windows_job.close_job(job)


def _session_remedy(name: str, status: str) -> str:
    if status in {"not-found", "stale"}:
        return (
            f"Session '{name}' is {status}; start it with "
            f"`blendersessiond start --name {name}`."
        )
    return (
        f"Session '{name}' is {status}; check `blendersessiond doctor`, then "
        f"restart it with `blendersessiond start --name {name}`."
    )
