from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from blendersessiond.locking import file_lock
from blendersessiond.processes import process_matches, wait_for_process_start_time
from blendersessiond.state import resolve_state_directory

SCHEMA_VERSION = 1
OPERATION_REVISION = "windows-setup-owner-v1"
MAX_REQUEST_BYTES = 16 * 1024
MAX_SCRIPT_BYTES = 128 * 1024
MAX_RECORD_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 24 * 1024
MAX_DEADLINE = timedelta(minutes=5)
_LAUNCH_WAIT_SECONDS = 15.0
_STOP_WAIT_SECONDS = 10.0
_OWNER_LOSS_WAIT_SECONDS = 2.0
_ATTEMPT_ID = re.compile(r"^bbsa_[A-Za-z0-9_-]{43}$")
_LAUNCH_ID = re.compile(r"^bbsl_[A-Za-z0-9_-]{43}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
JOB_SCOPE = "unnamed-kill-on-close"
_TERMINAL_OUTCOMES = {
    "cancelled",
    "cleanup_unverified",
    "launch_failed",
    "owner_lost",
    "process_failed",
    "process_succeeded",
    "stopped",
    "stopped_before_ownership",
    "timed_out",
}
_TERMINAL_PROCESSES = {
    "cancelled",
    "cancelled_before_resume",
    "exited",
    "failed",
    "not_resumed",
    "not_started",
    "owner_lost",
    "timed_out",
}
_TERMINAL_CLEANUP = {"cleanup_unverified", "tree_gone"}


class SetupOwnerError(RuntimeError):
    """A Setup Attempt cannot proceed without weakening ownership."""


class AttemptConflictError(SetupOwnerError):
    """An immutable Setup Attempt identity was reused for different bytes."""


@dataclass(frozen=True)
class ScriptArtifact:
    artifact_id: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class SetupRequest:
    attempt_id: str
    launch_id: str
    deadline_utc: datetime
    operation_revision: str
    script: ScriptArtifact
    request_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": self.attempt_id,
            "launch_id": self.launch_id,
            "deadline_utc": _format_time(self.deadline_utc),
            "operation_revision": self.operation_revision,
            "script": self.script.to_dict(),
            "request_sha256": self.request_sha256,
        }


@dataclass(frozen=True)
class LaunchReceipt:
    attempt_id: str
    launch_id: str
    request_sha256: str
    keeper_pid: int
    keeper_creation_time: str
    root_pid: int
    root_creation_time: str
    job_scope: str | None
    owned_at: datetime
    job_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": self.attempt_id,
            "launch_id": self.launch_id,
            "request_sha256": self.request_sha256,
            "keeper_pid": self.keeper_pid,
            "keeper_creation_time": self.keeper_creation_time,
            "root_pid": self.root_pid,
            "root_creation_time": self.root_creation_time,
            "owned_at": _format_time(self.owned_at),
        }
        if self.job_name is not None:
            payload["job_name"] = self.job_name
        else:
            payload["job_scope"] = self.job_scope
        return payload


@dataclass(frozen=True)
class SetupTerminal:
    attempt_id: str
    launch_id: str
    request_sha256: str
    outcome: str
    process: str
    cleanup: str
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    finished_at: datetime
    message: str | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.outcome == "process_succeeded"
            and self.process == "exited"
            and self.cleanup == "tree_gone"
            and self.exit_code == 0
            and not self.stdout_truncated
            and not self.stderr_truncated
        )

    @classmethod
    def owned(
        cls,
        *,
        request: SetupRequest,
        outcome: str,
        process: str,
        cleanup: str,
        stdout: bytes,
        stderr: bytes,
        finished_at: datetime,
        exit_code: int | None = None,
        stdout_truncated: bool = False,
        stderr_truncated: bool = False,
        message: str | None = None,
    ) -> SetupTerminal:
        return cls(
            attempt_id=request.attempt_id,
            launch_id=request.launch_id,
            request_sha256=request.request_sha256,
            outcome=outcome,
            process=process,
            cleanup=cleanup,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            finished_at=finished_at,
            message=message,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": self.attempt_id,
            "launch_id": self.launch_id,
            "request_sha256": self.request_sha256,
            "status": "terminal",
            "outcome": self.outcome,
            "process": self.process,
            "cleanup": self.cleanup,
            "exit_code": self.exit_code,
            "stdout": self.stdout.decode("utf-8", errors="strict"),
            "stderr": self.stderr.decode("utf-8", errors="strict"),
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "finished_at": _format_time(self.finished_at),
        }
        if self.message is not None:
            payload["message"] = self.message
        return payload


@dataclass(frozen=True)
class SetupView:
    request: SetupRequest
    receipt: LaunchReceipt | None = None
    terminal: SetupTerminal | None = None
    ownership_unverified: bool = False

    def to_dict(self) -> dict[str, Any]:
        if self.terminal is not None:
            return self.terminal.to_dict()
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": self.request.attempt_id,
            "launch_id": self.request.launch_id,
            "request_sha256": self.request.request_sha256,
            "status": (
                "ownership_unverified"
                if self.ownership_unverified
                else "owned"
                if self.receipt is not None
                else "accepted"
            ),
        }
        if self.receipt is not None:
            payload["receipt"] = self.receipt.to_dict()
        return payload


def accept_launch_request(
    raw_request: bytes,
    *,
    state_root: Path,
    now: datetime | None = None,
) -> SetupRequest:
    current = datetime.now(UTC) if now is None else now
    request = _parse_request(raw_request)
    directory = _attempt_directory(state_root, request.attempt_id)
    with _guard_setup_path(directory, allow_missing=True):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with _setup_lock(directory):
        existing = _read_optional_record(directory / "request.json")
        if existing is None:
            if (
                request.deadline_utc <= current
                or request.deadline_utc - current > MAX_DEADLINE
            ):
                raise SetupOwnerError(
                    "Setup launch deadline must be within five minutes."
                )
            _write_create_once(directory / "request.json", request.to_dict())
        elif existing != request.to_dict():
            raise AttemptConflictError(
                "Setup Attempt already contains a different request."
            )
    return request


def launch_setup(
    raw_request: bytes,
    *,
    state_root: Path | None = None,
    now: datetime | None = None,
    platform_name: str | None = None,
    dispatch_keeper: Callable[[Path], None] | None = None,
) -> SetupView:
    _require_windows(platform_name)
    root = resolve_state_directory() if state_root is None else Path(state_root)
    request = accept_launch_request(raw_request, state_root=root, now=now)
    directory = _attempt_directory(root, request.attempt_id)
    should_dispatch = False
    with _setup_lock(directory):
        view = _read_view(directory, request)
        if view.terminal is not None or view.receipt is not None:
            return view
        should_dispatch = _touch_create_once(directory / ".dispatch")
    if should_dispatch:
        try:
            if dispatch_keeper is not None:
                dispatch_keeper(directory)
                return SetupView(request=request)
            _spawn_keeper(directory)
        except (OSError, RuntimeError, ValueError) as error:
            with _setup_lock(directory):
                view = _read_view(directory, request)
                if view.receipt is not None or view.terminal is not None:
                    return view
                _touch_create_once(directory / ".stop")
                terminal = SetupTerminal.owned(
                    request=request,
                    outcome="launch_failed",
                    process="not_started",
                    cleanup="cleanup_unverified",
                    stdout=b"",
                    stderr=b"",
                    finished_at=datetime.now(UTC),
                    message=f"{type(error).__name__}: {error}"[:4096],
                )
                _write_create_once(
                    directory / "terminal.json", terminal.to_dict()
                )
                return SetupView(request=request, terminal=terminal)
    deadline = time.monotonic() + _LAUNCH_WAIT_SECONDS
    while time.monotonic() < deadline:
        view = _read_view(directory, request)
        if view.receipt is not None or view.terminal is not None:
            return view
        time.sleep(0.02)
    with _setup_lock(directory):
        view = _read_view(directory, request)
        if view.receipt is not None or view.terminal is not None:
            return view
        _touch_create_once(directory / ".stop")
        terminal = SetupTerminal.owned(
            request=request,
            outcome="launch_failed",
            process="not_started",
            cleanup="cleanup_unverified",
            stdout=b"",
            stderr=b"",
            finished_at=datetime.now(UTC),
            message="The keeper did not acknowledge ownership before the launch bound.",
        )
        _write_create_once(directory / "terminal.json", terminal.to_dict())
        return SetupView(request=request, terminal=terminal)


def status_setup(
    attempt_id: str,
    *,
    expected_request_sha256: str,
    expected_launch_id: str,
    state_root: Path | None = None,
    platform_name: str | None = None,
) -> SetupView:
    _require_windows(platform_name)
    root = resolve_state_directory() if state_root is None else Path(state_root)
    directory = _attempt_directory(root, attempt_id)
    request = _load_request(directory)
    _check_fence(request, expected_request_sha256, expected_launch_id)
    view = _read_view(directory, request)
    if view.terminal is not None:
        return view
    if view.receipt is None:
        if (
            not _marker_exists(directory / ".dispatch")
            or request.deadline_utc > datetime.now(UTC)
        ):
            return view
        with _setup_lock(directory):
            view = _read_view(directory, request)
            if view.terminal is not None or view.receipt is not None:
                return view
            _touch_create_once(directory / ".stop")
            terminal = SetupTerminal.owned(
                request=request,
                outcome="launch_failed",
                process="not_started",
                cleanup="cleanup_unverified",
                stdout=b"",
                stderr=b"",
                finished_at=datetime.now(UTC),
                message="The dispatched keeper did not acknowledge ownership.",
            )
            _write_create_once(directory / "terminal.json", terminal.to_dict())
            return SetupView(request=request, terminal=terminal)
    receipt = view.receipt
    keeper_state = _process_state(
        receipt.keeper_pid, receipt.keeper_creation_time
    )
    if keeper_state == "matches":
        return view
    if keeper_state == "unknown":
        return _unverified_view(request, receipt)
    return _reconcile_owner_loss(directory, request, receipt)


def stop_setup(
    attempt_id: str,
    *,
    expected_request_sha256: str,
    expected_launch_id: str,
    state_root: Path | None = None,
    now: datetime | None = None,
    platform_name: str | None = None,
) -> SetupView:
    _require_windows(platform_name)
    root = resolve_state_directory() if state_root is None else Path(state_root)
    directory = _attempt_directory(root, attempt_id)
    request = _load_request(directory)
    _check_fence(request, expected_request_sha256, expected_launch_id)
    with _setup_lock(directory):
        view = _read_view(directory, request)
        if view.terminal is not None:
            return view
        if view.receipt is None and not _marker_exists(directory / ".dispatch"):
            terminal = SetupTerminal.owned(
                request=request,
                outcome="stopped_before_ownership",
                process="not_started",
                cleanup="tree_gone",
                stdout=b"",
                stderr=b"",
                finished_at=datetime.now(UTC) if now is None else now,
            )
            _write_create_once(directory / "terminal.json", terminal.to_dict())
            return SetupView(request=request, terminal=terminal)
        _touch_create_once(directory / ".stop")
    deadline = time.monotonic() + _STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        view = _read_view(directory, request)
        if view.terminal is not None:
            return view
        if view.receipt is not None:
            break
        time.sleep(0.02)
    view = _read_view(directory, request)
    if view.terminal is not None:
        return view
    if view.receipt is None:
        return _publish_terminal(
            directory,
            request,
            SetupTerminal.owned(
                request=request,
                outcome="launch_failed",
                process="not_started",
                cleanup="cleanup_unverified",
                stdout=b"",
                stderr=b"",
                finished_at=datetime.now(UTC),
                message="Stop could not obtain a keeper ownership acknowledgement.",
            ),
        )
    receipt = view.receipt
    deadline = time.monotonic() + _STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        current = _read_view(directory, request)
        if current.terminal is not None:
            return current
        if _process_state(
            receipt.keeper_pid, receipt.keeper_creation_time
        ) != "matches":
            break
        time.sleep(0.05)
    keeper_state = _process_state(
        receipt.keeper_pid, receipt.keeper_creation_time
    )
    if keeper_state in {"matches", "unknown"}:
        from blendersessiond.windows_setup_process import terminate_exact_process

        try:
            terminate_exact_process(receipt.keeper_pid, receipt.keeper_creation_time)
        except (OSError, RuntimeError):
            return _unverified_view(request, receipt)
    return _reconcile_after_stop(directory, request, receipt)


def script_path_for_attempt(state_root: Path, attempt_id: str) -> Path:
    return _attempt_directory(state_root, attempt_id) / f"{attempt_id}.ps1"


def read_staged_script(state_root: Path, request: SetupRequest) -> bytes:
    path = script_path_for_attempt(state_root, request.attempt_id)
    try:
        with _guard_setup_path(path):
            path_metadata = path.lstat()
            if stat.S_ISLNK(path_metadata.st_mode) or not stat.S_ISREG(
                path_metadata.st_mode
            ):
                raise SetupOwnerError(
                    "The staged setup script must be a regular file."
                )
            with path.open("rb") as stream:
                opened_metadata = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(opened_metadata.st_mode)
                    or (path_metadata.st_dev, path_metadata.st_ino)
                    != (opened_metadata.st_dev, opened_metadata.st_ino)
                ):
                    raise SetupOwnerError(
                        "The staged setup script changed while it was opened."
                    )
                content = stream.read(request.script.size + 1)
    except SetupOwnerError as error:
        if not isinstance(error.__cause__, FileNotFoundError):
            raise
        raise SetupOwnerError("The staged setup script is missing.") from error
    except FileNotFoundError as error:
        raise SetupOwnerError("The staged setup script is missing.") from error
    if (
        opened_metadata.st_size != request.script.size
        or len(content) != request.script.size
    ):
        raise SetupOwnerError("The staged setup script size does not match.")
    try:
        content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise SetupOwnerError("The staged setup script is not strict UTF-8.") from error
    if hashlib.sha256(content).hexdigest() != request.script.sha256:
        raise SetupOwnerError("The staged setup script SHA-256 does not match.")
    return content


def _parse_request(raw: bytes) -> SetupRequest:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_REQUEST_BYTES:
        raise SetupOwnerError("Setup launch request must be 1-16 KiB.")
    try:
        payload = _strict_json(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise SetupOwnerError(
            "Setup launch request is not valid UTF-8 JSON."
        ) from error
    if not isinstance(payload, dict):
        raise SetupOwnerError("Setup launch request must be a JSON object.")
    expected = {
        "schema_version",
        "attempt_id",
        "launch_id",
        "deadline_utc",
        "operation_revision",
        "script",
    }
    unknown = set(payload) - expected
    missing = expected - set(payload)
    if unknown:
        raise SetupOwnerError(
            f"Setup launch request contains unknown keys: {sorted(unknown)}."
        )
    if missing:
        raise SetupOwnerError(
            f"Setup launch request is missing keys: {sorted(missing)}."
        )
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        raise SetupOwnerError("Setup launch request schema_version must be 1.")
    attempt_id = _validate_attempt_id(payload["attempt_id"])
    launch_id = _validate_launch_id(payload["launch_id"])
    if payload["operation_revision"] != OPERATION_REVISION:
        raise SetupOwnerError(
            f"Setup launch operation_revision must be {OPERATION_REVISION}."
        )
    deadline = _parse_time(payload["deadline_utc"], "deadline_utc")
    script = payload["script"]
    if not isinstance(script, dict) or set(script) != {
        "artifact_id",
        "size",
        "sha256",
    }:
        raise SetupOwnerError("Setup launch script must contain exact artifact fields.")
    if script["artifact_id"] != f"{attempt_id}.ps1":
        raise SetupOwnerError(
            "Setup script artifact_id must derive from the Attempt ID."
        )
    size = script["size"]
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or not 0 < size <= MAX_SCRIPT_BYTES
    ):
        raise SetupOwnerError("Setup script size must be 1-128 KiB.")
    return SetupRequest(
        attempt_id=attempt_id,
        launch_id=launch_id,
        deadline_utc=deadline,
        operation_revision=OPERATION_REVISION,
        script=ScriptArtifact(
            artifact_id=script["artifact_id"],
            size=size,
            sha256=_validate_sha256(script["sha256"]),
        ),
        request_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _spawn_keeper(directory: Path) -> None:
    flags = 0
    if os.name == "nt":
        flags = _windows_keeper_creation_flags()
    with _guard_setup_path(directory):
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "blendersessiond.windows_setup_process",
                "--keeper",
                str(directory),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
    if wait_for_process_start_time(process.pid) is None:
        raise SetupOwnerError(
            "The setup keeper exited before ownership acknowledgement."
        )


def _windows_keeper_creation_flags() -> int:
    try:
        return (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        )
    except AttributeError as error:
        raise SetupOwnerError(
            "Windows setup keeper requires breakaway process creation support."
        ) from error


def _read_view(directory: Path, request: SetupRequest) -> SetupView:
    terminal = _read_optional_record(directory / "terminal.json")
    if terminal is not None:
        return SetupView(
            request=request,
            terminal=_terminal_from_record(terminal, request),
        )
    receipt = _read_optional_record(directory / "launch-receipt.json")
    if receipt is not None:
        return SetupView(
            request=request,
            receipt=_receipt_from_record(receipt, request),
        )
    return SetupView(request=request)


def _load_request(directory: Path) -> SetupRequest:
    payload = _read_optional_record(directory / "request.json")
    if payload is None:
        raise SetupOwnerError("The Setup Attempt was not found.")
    return _request_from_record(payload)


def _check_fence(
    request: SetupRequest,
    request_sha256: str,
    launch_id: str,
) -> None:
    request_sha256 = _validate_sha256(request_sha256)
    launch_id = _validate_launch_id(launch_id)
    if request_sha256 != request.request_sha256:
        raise SetupOwnerError("The expected request SHA-256 does not match.")
    if launch_id != request.launch_id:
        raise SetupOwnerError("The expected Launch ID does not match.")


def _reconcile_after_stop(
    directory: Path,
    request: SetupRequest,
    receipt: LaunchReceipt,
) -> SetupView:
    deadline = time.monotonic() + _STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        current = _read_view(directory, request)
        if current.terminal is not None:
            return current
        time.sleep(0.05)
    keeper = _process_state(receipt.keeper_pid, receipt.keeper_creation_time)
    root = _process_state(receipt.root_pid, receipt.root_creation_time)
    if keeper in {"matches", "unknown"} or root == "unknown":
        return _unverified_view(request, receipt)
    return _publish_terminal(
        directory,
        request,
        SetupTerminal.owned(
            request=request,
            outcome="cleanup_unverified",
            process="cancelled",
            cleanup="cleanup_unverified",
            stdout=b"",
            stderr=b"",
            finished_at=datetime.now(UTC),
        ),
    )


def _reconcile_owner_loss(
    directory: Path,
    request: SetupRequest,
    receipt: LaunchReceipt,
) -> SetupView:
    deadline = time.monotonic() + _OWNER_LOSS_WAIT_SECONDS
    while time.monotonic() < deadline:
        keeper = _process_state(receipt.keeper_pid, receipt.keeper_creation_time)
        if keeper == "matches":
            return SetupView(request=request, receipt=receipt)
        if _process_is_gone(
            _process_state(receipt.root_pid, receipt.root_creation_time)
        ):
            break
        time.sleep(0.02)
    keeper = _process_state(receipt.keeper_pid, receipt.keeper_creation_time)
    if keeper == "matches":
        return SetupView(request=request, receipt=receipt)
    if keeper == "unknown":
        return _unverified_view(request, receipt)
    root = _process_state(receipt.root_pid, receipt.root_creation_time)
    if root == "unknown":
        return _unverified_view(request, receipt)
    return _publish_terminal(
        directory,
        request,
        SetupTerminal.owned(
            request=request,
            outcome="cleanup_unverified",
            process="owner_lost",
            cleanup="cleanup_unverified",
            stdout=b"",
            stderr=b"",
            finished_at=datetime.now(UTC),
        ),
    )


def _publish_terminal(
    directory: Path,
    request: SetupRequest,
    terminal: SetupTerminal,
) -> SetupView:
    with _setup_lock(directory):
        existing = _read_optional_record(directory / "terminal.json")
        if existing is not None:
            return SetupView(
                request=request,
                terminal=_terminal_from_record(existing, request),
            )
        _write_create_once(directory / "terminal.json", terminal.to_dict())
    return SetupView(request=request, terminal=terminal)


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    with _guard_setup_path(path.parent):
        encoded = _encode_record(payload)
        temporary = path.parent / f".{path.name}.{secrets.token_hex(16)}.tmp"
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        try:
            _publish_no_replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _publish_no_replace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        import ctypes

        move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move_file.restype = ctypes.c_int
        if not move_file(str(source), str(destination), 0x00000008):
            error = ctypes.get_last_error()
            if error in {80, 183}:
                raise FileExistsError(destination)
            raise ctypes.WinError(error)
        return
    os.link(source, destination)


def _touch_create_once(path: Path) -> bool:
    with _guard_setup_path(path.parent):
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        os.close(descriptor)
        return True


def _read_optional_record(path: Path) -> dict[str, Any] | None:
    with _guard_setup_path(path, allow_missing=True):
        try:
            with path.open("rb") as stream:
                encoded = stream.read(MAX_RECORD_BYTES + 1)
        except FileNotFoundError:
            return None
    if len(encoded) > MAX_RECORD_BYTES:
        raise SetupOwnerError(f"Setup record is too large: {path.name}.")
    try:
        payload = _strict_json(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise SetupOwnerError(f"Setup record is invalid: {path.name}.") from error
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
    ):
        raise SetupOwnerError(f"Setup record is incompatible: {path.name}.")
    return payload


def _encode_record(payload: dict[str, Any]) -> bytes:
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")
    if len(encoded) > MAX_RECORD_BYTES:
        raise SetupOwnerError("Setup record exceeds its size limit.")
    return encoded


def _request_from_record(payload: dict[str, Any]) -> SetupRequest:
    try:
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise SetupOwnerError("Setup request record schema is invalid.")
        attempt_id = _validate_attempt_id(payload["attempt_id"])
        script_payload = payload["script"]
        if not isinstance(script_payload, dict) or set(script_payload) != {
            "artifact_id",
            "size",
            "sha256",
        }:
            raise SetupOwnerError("Setup request record script is invalid.")
        size = script_payload["size"]
        if type(size) is not int or not 0 < size <= MAX_SCRIPT_BYTES:
            raise SetupOwnerError("Setup request record script size is invalid.")
        if script_payload["artifact_id"] != f"{attempt_id}.ps1":
            raise SetupOwnerError("Setup request record artifact is invalid.")
        if payload["operation_revision"] != OPERATION_REVISION:
            raise SetupOwnerError("Setup request record revision is invalid.")
        request = SetupRequest(
            attempt_id=attempt_id,
            launch_id=_validate_launch_id(payload["launch_id"]),
            deadline_utc=_parse_time(payload["deadline_utc"], "deadline_utc"),
            operation_revision=OPERATION_REVISION,
            script=ScriptArtifact(
                artifact_id=script_payload["artifact_id"],
                size=size,
                sha256=_validate_sha256(script_payload["sha256"]),
            ),
            request_sha256=_validate_sha256(payload["request_sha256"]),
        )
    except (KeyError, SetupOwnerError, TypeError) as error:
        raise SetupOwnerError("Setup request record is invalid.") from error
    if payload != request.to_dict():
        raise SetupOwnerError("Setup request record contains unexpected fields.")
    return request


def _receipt_from_record(
    payload: dict[str, Any], request: SetupRequest
) -> LaunchReceipt:
    try:
        receipt = LaunchReceipt(
            attempt_id=payload["attempt_id"],
            launch_id=payload["launch_id"],
            request_sha256=payload["request_sha256"],
            keeper_pid=payload["keeper_pid"],
            keeper_creation_time=payload["keeper_creation_time"],
            root_pid=payload["root_pid"],
            root_creation_time=payload["root_creation_time"],
            job_scope=payload.get("job_scope"),
            owned_at=_parse_time(payload["owned_at"], "owned_at"),
            job_name=payload.get("job_name"),
        )
    except (KeyError, TypeError) as error:
        raise SetupOwnerError("Setup launch receipt is invalid.") from error
    valid = (
        payload == receipt.to_dict()
        and receipt.attempt_id == request.attempt_id
        and receipt.launch_id == request.launch_id
        and receipt.request_sha256 == request.request_sha256
        and isinstance(receipt.keeper_pid, int)
        and not isinstance(receipt.keeper_pid, bool)
        and receipt.keeper_pid > 0
        and isinstance(receipt.keeper_creation_time, str)
        and isinstance(receipt.root_pid, int)
        and not isinstance(receipt.root_pid, bool)
        and receipt.root_pid > 0
        and isinstance(receipt.root_creation_time, str)
        and (
            (receipt.job_scope == JOB_SCOPE and receipt.job_name is None)
            or (
                receipt.job_scope is None
                and receipt.job_name == _legacy_job_name(request.launch_id)
            )
        )
    )
    if not valid:
        raise SetupOwnerError("Setup launch receipt is invalid or stale.")
    return receipt


def _terminal_from_record(
    payload: dict[str, Any], request: SetupRequest
) -> SetupTerminal:
    try:
        terminal = SetupTerminal(
            attempt_id=payload["attempt_id"],
            launch_id=payload["launch_id"],
            request_sha256=payload["request_sha256"],
            outcome=payload["outcome"],
            process=payload["process"],
            cleanup=payload["cleanup"],
            exit_code=payload["exit_code"],
            stdout=payload["stdout"].encode("utf-8"),
            stderr=payload["stderr"].encode("utf-8"),
            stdout_truncated=payload["stdout_truncated"],
            stderr_truncated=payload["stderr_truncated"],
            finished_at=_parse_time(payload["finished_at"], "finished_at"),
            message=payload.get("message"),
        )
    except (AttributeError, KeyError, TypeError) as error:
        raise SetupOwnerError("Setup terminal record is invalid.") from error
    valid = (
        payload == terminal.to_dict()
        and (
            terminal.attempt_id,
            terminal.launch_id,
            terminal.request_sha256,
        )
        == (request.attempt_id, request.launch_id, request.request_sha256)
        and terminal.outcome in _TERMINAL_OUTCOMES
        and terminal.process in _TERMINAL_PROCESSES
        and terminal.cleanup in _TERMINAL_CLEANUP
        and (
            terminal.exit_code is None
            or (
                isinstance(terminal.exit_code, int)
                and not isinstance(terminal.exit_code, bool)
                and 0 <= terminal.exit_code <= 0xFFFFFFFF
            )
        )
        and isinstance(terminal.stdout_truncated, bool)
        and isinstance(terminal.stderr_truncated, bool)
        and len(terminal.stdout) <= MAX_OUTPUT_BYTES
        and len(terminal.stderr) <= MAX_OUTPUT_BYTES
        and (
            terminal.message is None
            or (isinstance(terminal.message, str) and len(terminal.message) <= 4096)
        )
        and _terminal_semantics_are_valid(terminal)
    )
    if not valid:
        raise SetupOwnerError("Setup terminal record is invalid or stale.")
    return terminal


def _terminal_semantics_are_valid(terminal: SetupTerminal) -> bool:
    if terminal.outcome == "process_succeeded":
        return terminal.succeeded
    if terminal.outcome == "process_failed":
        if terminal.process == "failed":
            return terminal.exit_code is None
        return terminal.process == "exited" and not (
            terminal.exit_code == 0
            and not terminal.stdout_truncated
            and not terminal.stderr_truncated
        )
    if terminal.outcome == "cancelled":
        return terminal.process == "cancelled" and terminal.cleanup == "tree_gone"
    if terminal.outcome == "timed_out":
        return (
            terminal.process in {"timed_out", "not_resumed"}
            and terminal.cleanup == "tree_gone"
        )
    if terminal.outcome == "stopped":
        return (
            terminal.process in {"cancelled", "cancelled_before_resume"}
            and terminal.cleanup == "tree_gone"
            and terminal.exit_code is None
        )
    if terminal.outcome == "stopped_before_ownership":
        return (
            terminal.process == "not_started"
            and terminal.cleanup == "tree_gone"
            and terminal.exit_code is None
        )
    if terminal.outcome == "launch_failed":
        return (
            terminal.process in {"not_started", "not_resumed"}
            and terminal.exit_code is None
        )
    if terminal.outcome == "owner_lost":
        return (
            terminal.process == "owner_lost"
            and terminal.cleanup == "tree_gone"
            and terminal.exit_code is None
        )
    return (
        terminal.outcome == "cleanup_unverified"
        and terminal.process in {"cancelled", "owner_lost"}
        and terminal.cleanup == "cleanup_unverified"
        and terminal.exit_code is None
    )


def _validate_attempt_id(value: Any) -> str:
    if not isinstance(value, str) or not _ATTEMPT_ID.fullmatch(value):
        raise SetupOwnerError("Attempt ID is invalid.")
    return value


def _validate_launch_id(value: Any) -> str:
    if not isinstance(value, str) or not _LAUNCH_ID.fullmatch(value):
        raise SetupOwnerError("Launch ID is invalid.")
    return value


def _validate_sha256(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise SetupOwnerError("SHA-256 is invalid.")
    return value


def _attempt_directory(state_root: Path, attempt_id: str) -> Path:
    return Path(state_root) / "setup-attempts" / _validate_attempt_id(attempt_id)


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SetupOwnerError(f"Setup {field} must be a UTC timestamp.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise SetupOwnerError(f"Setup {field} is invalid.") from error
    return parsed.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _strict_json(encoded: bytes) -> Any:
    text = encoded.decode("utf-8", errors="strict")
    if text.startswith("\ufeff"):
        raise ValueError("UTF-8 BOM is not allowed.")

    def object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"Duplicate JSON key: {key}.")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(f"Non-finite JSON value: {value}.")

    return json.loads(
        text,
        object_pairs_hook=object_from_pairs,
        parse_constant=reject_constant,
    )


def _process_state(pid: int, creation_time: str) -> str:
    if os.name == "nt":
        from blendersessiond.windows_setup_process import exact_process_state

        return exact_process_state(pid, creation_time)
    return "matches" if process_matches(pid, creation_time) else "absent"


def _process_is_gone(state: str) -> bool:
    return state in {"absent", "different"}


def _unverified_view(
    request: SetupRequest, receipt: LaunchReceipt
) -> SetupView:
    return SetupView(
        request=request,
        receipt=receipt,
        ownership_unverified=True,
    )


def _legacy_job_name(launch_id: str) -> str:
    return f"Local\\BlenderSessiond.Setup.{launch_id}"


@contextmanager
def _guard_setup_path(
    path: Path, *, allow_missing: bool = False
) -> Iterator[None]:
    if os.name != "nt":
        yield
        return
    from blendersessiond.windows_path_authority import (
        PathAuthorityError,
        guard_path,
    )

    parts = path.parts
    setup_indexes = [
        index for index, part in enumerate(parts) if part == "setup-attempts"
    ]
    authority_root = (
        path.__class__(*parts[: setup_indexes[-1]]) if setup_indexes else path
    )
    try:
        with guard_path(
            path,
            authority_root=authority_root,
            allow_missing=allow_missing,
        ):
            yield
    except (OSError, PathAuthorityError) as error:
        raise SetupOwnerError(str(error)) from error


@contextmanager
def _setup_lock(directory: Path) -> Iterator[None]:
    with _guard_setup_path(directory):
        with file_lock(directory / ".lock"):
            yield


def _marker_exists(path: Path) -> bool:
    with _guard_setup_path(path, allow_missing=True):
        return path.exists()


def _require_windows(value: str | None) -> None:
    if (platform.system() if value is None else value) != "Windows":
        raise SetupOwnerError(
            "Windows setup ownership is unavailable on this platform."
        )
