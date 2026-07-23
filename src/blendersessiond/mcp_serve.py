"""Launch the stock BlenderMCP stdio server for one healthy Session."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping

from blendersessiond.sessions import (
    DEFAULT_SESSION_NAME,
    inspect_session,
)
from blendersessiond.wire import LOOPBACK_HOST

BLENDER_HOST_ENV_VAR = "BLENDER_HOST"
BLENDER_PORT_ENV_VAR = "BLENDER_PORT"


class McpServeError(RuntimeError):
    """A failure that must be reported before starting the stdio server."""


def serve_mcp(
    *,
    name: str = DEFAULT_SESSION_NAME,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Validate a named Session, then hand stdio to ``uvx blender-mcp``."""

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
    command = [uvx, "blender-mcp"]
    if os.name == "nt":
        return subprocess.run(command, env=environment, check=False).returncode

    os.execve(uvx, command, environment)
    raise AssertionError("os.execve returned unexpectedly")


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
