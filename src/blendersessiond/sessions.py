"""Session records and lifecycle operations."""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from blendersessiond.discovery import BlenderDiscovery, discover_blender
from blendersessiond.locking import file_lock
from blendersessiond.processes import (
    finish_posix_start,
    finish_windows_job_start,
    owned_tree_exists,
    process_matches,
    spawn_detached,
    spawn_windows_job_keeper,
    terminate_owned_tree,
    wait_for_process_start_time,
)
from blendersessiond.state import resolve_state_directory

DEFAULT_SESSION_NAME = "default"
BASE_MCP_PORT = 9876
OWNER_ENV_VAR = "BLENDERSESSIOND_OWNER_TOKEN"
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_RECORD_FILE = "session.json"
_OWNERSHIP_FILE = "ownership.json"
_LAUNCHING_FILE = "launching.json"


class SessionError(RuntimeError):
    """Expected lifecycle failure suitable for user-facing output."""


class AlreadyRunningError(SessionError):
    """A live Session already owns a Session Name."""


@dataclass(frozen=True)
class SessionRecord:
    """Durable facts needed to identify and manage one Session."""

    schema_version: int
    name: str
    pid: int
    process_start_time: str
    mcp_port: int
    blender_path: str
    blender_version: str
    stdout_log: str
    stderr_log: str
    state_dir: str
    started_at: str
    owner_token: str
    controller_pid: int
    controller_start_time: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pid": self.pid,
            "process_start_time": self.process_start_time,
            "mcp_port": self.mcp_port,
            "blender": {
                "path": self.blender_path,
                "version": self.blender_version,
            },
            "logs": {
                "stdout": self.stdout_log,
                "stderr": self.stderr_log,
            },
            "state_dir": self.state_dir,
            "started_at": self.started_at,
        }


@dataclass(frozen=True)
class SessionInspection:
    """Current interpretation of a Session record."""

    name: str
    status: str
    message: str
    record: SessionRecord | None = None
    reclaimable: bool = False
    stoppable: bool = False

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"

    def to_dict(self) -> dict[str, Any]:
        result = (
            self.record.public_dict()
            if self.record is not None
            else {"name": self.name}
        )
        result["status"] = self.status
        result["message"] = self.message
        result["reclaimable"] = self.reclaimable
        result["health"] = {
            "status": "healthy" if self.healthy else "unhealthy",
            "process": {
                "status": "healthy" if self.healthy else self.status,
                "alive": self.healthy,
            },
            "socket": {
                "status": "not-configured",
                "message": "The Session socket is not configured in this slice.",
            },
        }
        return result


@dataclass(frozen=True)
class StartResult:
    record: SessionRecord
    reclaimed_stale: bool
    message: str


@dataclass(frozen=True)
class StopResult:
    inspection: SessionInspection
    stopped: bool
    message: str


def validate_session_name(name: str) -> str:
    """Validate a Session Name before it becomes a directory name."""

    if not _NAME_PATTERN.fullmatch(name) or name in {".", ".."}:
        raise ValueError(
            "Session Name must be 1-64 characters using letters, numbers, "
            "periods, underscores, or hyphens, and must start with a letter "
            "or number."
        )
    return name


def session_directory(state_root: Path, name: str) -> Path:
    validated = validate_session_name(name)
    return state_root / "sessions" / validated.encode("ascii").hex()


def start_session(
    *,
    name: str = DEFAULT_SESSION_NAME,
    explicit_blender: str | None = None,
    state_root: Path | None = None,
    environ: dict[str, str] | None = None,
) -> StartResult:
    """Start and record an owned Blender Session."""

    validate_session_name(name)
    environment = dict(os.environ if environ is None else environ)
    root = (
        resolve_state_directory(environ=environment)
        if state_root is None
        else state_root
    )
    _ensure_private_directory(root, protect_existing=False)
    _ensure_private_directory(root / "sessions")
    directory = session_directory(root, name)
    _ensure_private_directory(directory)
    with file_lock(directory / ".lock"):
        previous = _inspect_unlocked(root, name)
        if previous.healthy:
            raise AlreadyRunningError(
                f"Session '{name}' is already running; reuse it."
            )

        if previous.status != "not-found" and not previous.reclaimable:
            raise SessionError(
                f"{previous.message} The Session Name cannot be reclaimed "
                "because Ownership of a live process cannot be verified."
            )
        blender = discover_blender(
            explicit_path=explicit_blender,
            environ=environment,
            system=_platform_system(),
        )
        if not blender.passed:
            raise SessionError(blender.message)

        reclaimed = previous.reclaimable
        with file_lock(root / ".ports.lock"):
            port = _allocate_port(root, excluding_name=name)
            if reclaimed:
                _remove_record_files(directory)
            record = _launch_and_record(
                directory=directory,
                name=name,
                blender=blender,
                port=port,
                environment=environment,
            )

    if reclaimed:
        message = (
            f"{previous.message} Reclaimed Session Name '{name}' and started "
            f"Session on MCP port {record.mcp_port}."
        )
    else:
        message = (
            f"Started Session '{name}' on MCP port {record.mcp_port} "
            f"(pid {record.pid})."
        )
    return StartResult(record=record, reclaimed_stale=reclaimed, message=message)


def inspect_session(
    *,
    name: str = DEFAULT_SESSION_NAME,
    state_root: Path | None = None,
    environ: dict[str, str] | None = None,
) -> SessionInspection:
    """Inspect one Session under its Session-Name lock."""

    validate_session_name(name)
    root = _resolve_root(state_root, environ)
    directory = session_directory(root, name)
    if not directory.exists():
        return _not_found(name)
    with file_lock(directory / ".lock"):
        return _inspect_unlocked(root, name)


def inspect_all_sessions(
    *,
    state_root: Path | None = None,
    environ: dict[str, str] | None = None,
) -> list[SessionInspection]:
    """Inspect every recorded Session in Session-Name order."""

    root = _resolve_root(state_root, environ)
    sessions_root = root / "sessions"
    if not sessions_root.is_dir():
        return []
    results: list[SessionInspection] = []
    for directory in sorted(sessions_root.iterdir()):
        if not directory.is_dir() or not (
            (directory / _RECORD_FILE).exists()
            or (directory / _LAUNCHING_FILE).exists()
        ):
            continue
        try:
            name = bytes.fromhex(directory.name).decode("ascii")
            validate_session_name(name)
        except (UnicodeDecodeError, ValueError):
            results.append(
                SessionInspection(
                    directory.name,
                    "unreadable",
                    (
                        f"Session state directory '{directory.name}' has an "
                        "invalid Session Name."
                    ),
                )
            )
            continue
        inspection = inspect_session(name=name, state_root=root)
        if inspection.status != "not-found":
            results.append(inspection)
    return results


def stop_session(
    *,
    name: str = DEFAULT_SESSION_NAME,
    state_root: Path | None = None,
    environ: dict[str, str] | None = None,
) -> StopResult:
    """Stop an owned Session tree and remove its record."""

    validate_session_name(name)
    root = _resolve_root(state_root, environ)
    directory = session_directory(root, name)
    if not directory.exists():
        inspection = _not_found(name)
        return StopResult(inspection, False, inspection.message)

    with file_lock(directory / ".lock"):
        inspection = _inspect_unlocked(root, name)
        if inspection.record is None:
            return StopResult(inspection, False, inspection.message)
        if inspection.stoppable:
            with file_lock(root / ".ports.lock"):
                terminate_owned_tree(
                    inspection.record.pid,
                    inspection.record.process_start_time,
                    controller_pid=inspection.record.controller_pid,
                    controller_start_time=inspection.record.controller_start_time,
                )
                _remove_record_files(directory)
            stopped_inspection = SessionInspection(
                name,
                "stopped",
                f"Session '{name}' process tree is gone.",
                inspection.record,
            )
            return StopResult(
                stopped_inspection,
                True,
                (
                    f"Stopped Session '{name}' without saving; "
                    "log files remain on disk."
                ),
            )
        if not inspection.healthy:
            if not inspection.reclaimable:
                return StopResult(
                    inspection,
                    False,
                    (
                        f"{inspection.message} Preserved the Session record "
                        "because Ownership of a live process cannot be verified."
                    ),
                )
            with file_lock(root / ".ports.lock"):
                _remove_record_files(directory)
            return StopResult(
                inspection,
                False,
                f"{inspection.message} Removed the stale Session record.",
            )

        raise AssertionError("healthy Sessions must be stoppable")


def _resolve_root(
    state_root: Path | None,
    environ: dict[str, str] | None,
) -> Path:
    if state_root is not None:
        return state_root
    return resolve_state_directory(environ=environ)


def _platform_system() -> str:
    import platform

    return platform.system()


def _launch_and_record(
    *,
    directory: Path,
    name: str,
    blender: BlenderDiscovery,
    port: int,
    environment: dict[str, str],
) -> SessionRecord:
    if blender.path is None or blender.version is None:
        raise AssertionError("Blender must be resolved before launch")

    stdout_path = (directory / "stdout.log").resolve()
    stderr_path = (directory / "stderr.log").resolve()
    owner_token = secrets.token_hex(32)
    process_environment = dict(environment)
    process_environment[OWNER_ENV_VAR] = owner_token

    controller_pid: int | None = None
    controller_start_time: str | None = None
    bootstrap_path = directory / ".launch-bootstrap.json"
    gate_path = (
        bootstrap_path.with_suffix(".gate")
        if os.name == "nt"
        else directory / ".posix-launch.gate"
    )
    started_at = datetime.now(UTC).isoformat()
    try:
        if os.name == "nt":
            controller_pid = spawn_windows_job_keeper(
                blender.path,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                environ=process_environment,
                bootstrap_path=bootstrap_path,
            )
        else:
            gate_path.unlink(missing_ok=True)
            bootstrap_path.unlink(missing_ok=True)
            with _open_private_append(stdout_path) as stdout, _open_private_append(
                stderr_path
            ) as stderr:
                process = spawn_detached(
                    blender.path,
                    stdout=stdout,
                    stderr=stderr,
                    environ=process_environment,
                    gate_path=gate_path,
                    bootstrap_path=bootstrap_path,
                )
            controller_pid = process.pid

        controller_start_time = wait_for_process_start_time(controller_pid)
        if controller_start_time is None:
            raise SessionError("Session process-tree controller exited during startup.")
        provisional = SessionRecord(
            schema_version=1,
            name=name,
            pid=controller_pid,
            process_start_time=controller_start_time,
            mcp_port=port,
            blender_path=blender.path,
            blender_version=blender.version,
            stdout_log=str(stdout_path),
            stderr_log=str(stderr_path),
            state_dir=str(directory.resolve()),
            started_at=started_at,
            owner_token=owner_token,
            controller_pid=controller_pid,
            controller_start_time=controller_start_time,
        )
        _atomic_write_json(directory / _OWNERSHIP_FILE, {"token": owner_token})
        _atomic_write_json(directory / _LAUNCHING_FILE, asdict(provisional))
        if os.name == "nt":
            process_pid = finish_windows_job_start(
                bootstrap_path,
                keeper_pid=controller_pid,
            )
        else:
            process_pid = finish_posix_start(
                bootstrap_path,
                gate_path=gate_path,
                supervisor_pid=controller_pid,
            )

        start_time = wait_for_process_start_time(process_pid)
        if start_time is None or controller_start_time is None:
            raise SessionError(
                f"Blender exited before Session '{name}' could be recorded."
            )

        record = SessionRecord(
            schema_version=1,
            name=name,
            pid=process_pid,
            process_start_time=start_time,
            mcp_port=port,
            blender_path=blender.path,
            blender_version=blender.version,
            stdout_log=str(stdout_path),
            stderr_log=str(stderr_path),
            state_dir=str(directory.resolve()),
            started_at=started_at,
            owner_token=owner_token,
            controller_pid=controller_pid,
            controller_start_time=controller_start_time,
        )
        _atomic_write_json(directory / _OWNERSHIP_FILE, {"token": owner_token})
        _atomic_write_json(directory / _RECORD_FILE, asdict(record))
        (directory / _LAUNCHING_FILE).unlink(missing_ok=True)
        return record
    except BaseException:
        if controller_pid is not None and controller_start_time is not None:
            process_time = (
                wait_for_process_start_time(process_pid)
                if "process_pid" in locals()
                else None
            )
            terminate_owned_tree(
                process_pid if "process_pid" in locals() else -1,
                process_time or "",
                controller_pid=controller_pid,
                controller_start_time=controller_start_time,
            )
        _remove_record_files(directory)
        raise
    finally:
        bootstrap_path.unlink(missing_ok=True)
        gate_path.unlink(missing_ok=True)


def _inspect_unlocked(state_root: Path, name: str) -> SessionInspection:
    directory = session_directory(state_root, name)
    record_path = directory / _RECORD_FILE
    if not record_path.exists():
        record_path = directory / _LAUNCHING_FILE
        if not record_path.exists():
            return _not_found(name)
    launching = record_path.name == _LAUNCHING_FILE
    try:
        record = _read_record(record_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return SessionInspection(
            name,
            "unreadable",
            f"Session '{name}' record is unreadable: {error}.",
        )
    try:
        ownership = _read_json(directory / _OWNERSHIP_FILE)
    except (OSError, ValueError, json.JSONDecodeError):
        ownership = {}

    root_matches = process_matches(record.pid, record.process_start_time)
    tree_exists = owned_tree_exists(
        record.pid,
        record.process_start_time,
        controller_pid=record.controller_pid,
        controller_start_time=record.controller_start_time,
    )
    if record.name != name:
        if not tree_exists:
            return SessionInspection(
                name,
                "stale",
                (
                    f"Session '{name}' record is stale: pid {record.pid} is "
                    "absent or its process start time does not match."
                ),
                record,
                True,
            )
        return SessionInspection(
            name,
            "stale",
            f"Session '{name}' record has a mismatched Session Name.",
            record,
        )
    if ownership.get("token") != record.owner_token:
        if not tree_exists:
            return SessionInspection(
                name,
                "stale",
                (
                    f"Session '{name}' record is stale: pid {record.pid} is "
                    "absent or its process start time does not match."
                ),
                record,
                True,
            )
        return SessionInspection(
            name,
            "stale",
            f"Session '{name}' record has no matching Ownership marker.",
            record,
        )
    if launching and tree_exists:
        return SessionInspection(
            name,
            "starting",
            f"Session '{name}' launch is still in progress.",
            record,
            False,
            True,
        )
    if not root_matches:
        if tree_exists:
            return SessionInspection(
                name,
                "stale",
                (
                    f"Session '{name}' Blender process is gone, but its owned "
                    "process tree still exists."
                ),
                record,
                False,
                True,
            )
        return SessionInspection(
            name,
            "stale",
            (
                f"Session '{name}' record is stale: pid {record.pid} is absent "
                "or its process start time does not match."
            ),
            record,
            True,
        )
    return SessionInspection(
        name,
        "healthy",
        f"Session '{name}' process is alive.",
        record,
        False,
        True,
    )


def _allocate_port(state_root: Path, *, excluding_name: str) -> int:
    occupied: set[int] = set()
    sessions_root = state_root / "sessions"
    excluded_component = session_directory(state_root, excluding_name).name
    if sessions_root.is_dir():
        for directory in sessions_root.iterdir():
            if not directory.is_dir() or directory.name == excluded_component:
                continue
            try:
                name = bytes.fromhex(directory.name).decode("ascii")
                validate_session_name(name)
                inspection = _inspect_unlocked(state_root, name)
            except (UnicodeDecodeError, ValueError) as error:
                raise SessionError(
                    "Cannot allocate an MCP port: a Session state directory "
                    f"is invalid ({error})."
                ) from error
            if inspection.record is None and inspection.status == "unreadable":
                raise SessionError(
                    f"Cannot allocate an MCP port: {inspection.message}"
                )
            if (
                inspection.record is not None
                and (inspection.healthy or not inspection.reclaimable)
            ):
                occupied.add(inspection.record.mcp_port)

    port = BASE_MCP_PORT
    while port in occupied:
        port += 1
    return port


def _read_record(path: Path) -> SessionRecord:
    payload = _read_json(path)
    required = {
        "schema_version": int,
        "name": str,
        "pid": int,
        "process_start_time": str,
        "mcp_port": int,
        "blender_path": str,
        "blender_version": str,
        "stdout_log": str,
        "stderr_log": str,
        "state_dir": str,
        "started_at": str,
        "owner_token": str,
        "controller_pid": int,
        "controller_start_time": str,
    }
    for key, expected_type in required.items():
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, expected_type):
            raise ValueError(f"field {key!r} is missing or invalid")
    if payload["schema_version"] != 1:
        raise ValueError("schema_version is unsupported")
    if payload["pid"] <= 0 or payload["controller_pid"] <= 0:
        raise ValueError("pid fields must be positive")
    if not 1 <= payload["mcp_port"] <= 65535:
        raise ValueError("mcp_port is outside the valid port range")
    return SessionRecord(**{key: payload[key] for key in required})


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + f".{secrets.token_hex(8)}.tmp")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_record_files(directory: Path) -> None:
    (directory / _RECORD_FILE).unlink(missing_ok=True)
    (directory / _LAUNCHING_FILE).unlink(missing_ok=True)
    (directory / _OWNERSHIP_FILE).unlink(missing_ok=True)
    (directory / ".launch-bootstrap.json").unlink(missing_ok=True)
    (directory / ".launch-bootstrap.gate").unlink(missing_ok=True)
    (directory / ".launch-bootstrap.ready").unlink(missing_ok=True)
    (directory / ".posix-launch.gate").unlink(missing_ok=True)


def _not_found(name: str) -> SessionInspection:
    return SessionInspection(
        name,
        "not-found",
        f"Session '{name}' was not found.",
    )


def _ensure_private_directory(
    path: Path,
    *,
    protect_existing: bool = True,
) -> None:
    existed = path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt" and (protect_existing or not existed):
        os.chmod(path, 0o700)


def _open_private_append(path: Path):
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    if os.name != "nt":
        os.chmod(path, 0o600)
    return os.fdopen(descriptor, "ab", buffering=0)
