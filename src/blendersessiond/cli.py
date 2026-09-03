"""Command-line interface for blendersessiond."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from blendersessiond.doctor import DoctorReport, build_doctor_report
from blendersessiond.mcp_serve import McpServeError, serve_mcp
from blendersessiond.sessions import (
    DEFAULT_SESSION_NAME,
    AlreadyRunningError,
    SessionError,
    call_session,
    inspect_all_sessions,
    inspect_session,
    resolve_scene_path,
    start_session,
    stop_session,
    validate_session_id,
    validate_session_name,
)
from blendersessiond.setup_owner import (
    MAX_REQUEST_BYTES,
    SetupOwnerError,
    launch_setup,
    status_setup,
    stop_setup,
)
from blendersessiond.windows_setup_process import (
    runtime_self_test as windows_setup_owner_self_test,
)
from blendersessiond.wire import (
    DEFAULT_READ_TIMEOUT_SECONDS,
    MAX_READ_TIMEOUT_SECONDS,
    WireError,
)

BLENDER_BOX_CONTRACT = "blender-box-v1"
BLENDER_BOX_CAPABILITIES = [
    "opaque-session-identity",
    "expect-session-id-call",
    "expect-session-id-stop",
    "bounded-call-read-timeout",
    "typed-call-error-reason",
]
WINDOWS_SETUP_OWNER_CAPABILITY = "windows-setup-owner-v1"
_platform_system = platform.system


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blendersessiond",
        description="Own Blender Sessions for agent workflows.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    capabilities = commands.add_parser(
        "capabilities",
        help="Report a required machine-readable integration contract.",
    )
    capabilities.add_argument(
        "--require",
        required=True,
        choices=[BLENDER_BOX_CONTRACT],
        metavar="CONTRACT",
        help="Require one exact supported integration contract.",
    )
    capabilities.add_argument(
        "--require-capability",
        action="append",
        choices=[*BLENDER_BOX_CAPABILITIES, WINDOWS_SETUP_OWNER_CAPABILITY],
        metavar="CAPABILITY",
        help="Require a named capability within the integration contract.",
    )

    doctor = commands.add_parser(
        "doctor",
        help="Check whether this machine can host a Session.",
    )
    doctor.add_argument(
        "--blender",
        metavar="PATH",
        help="Use this Blender executable.",
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Print the versioned machine-readable report.",
    )
    start = commands.add_parser(
        "start",
        help="Start and own a Blender Session.",
    )
    _add_name_argument(start)
    start.add_argument(
        "--blender",
        metavar="PATH",
        help="Use this Blender executable.",
    )
    start.add_argument(
        "--scene",
        metavar="PATH",
        type=_scene_path,
        help="Open an existing .blend file instead of a factory-empty file.",
    )
    _add_json_argument(start)

    status = commands.add_parser(
        "status",
        help="Report Session Health.",
    )
    status.add_argument(
        "--name",
        type=_session_name,
        help="Report one Session Name; omit to list all Sessions.",
    )
    _add_json_argument(status)

    stop = commands.add_parser(
        "stop",
        help="Stop an owned Session without saving.",
    )
    _add_name_argument(stop)
    _add_expected_session_id_argument(stop)
    _add_json_argument(stop)

    call = commands.add_parser(
        "call",
        help="Call one raw command on a Session's MCP addon.",
    )
    call.add_argument("addon_command", metavar="COMMAND")
    _add_name_argument(call)
    _add_expected_session_id_argument(call)
    call.add_argument(
        "--params",
        default={},
        type=_json_object,
        metavar="JSON",
        help="Command parameters as a JSON object (default: {}).",
    )
    call.add_argument(
        "--read-timeout",
        default=DEFAULT_READ_TIMEOUT_SECONDS,
        type=_read_timeout,
        metavar="SECONDS",
        help="Wait up to SECONDS for a response (default: 180; maximum: 3600).",
    )
    _add_json_argument(call)

    mcp_serve = commands.add_parser(
        "mcp-serve",
        help="Serve MCP stdio for one healthy Session.",
    )
    _add_name_argument(mcp_serve)

    setup_owner = commands.add_parser(
        "setup-owner",
        help="Own one fixed-purpose Blender Box Windows setup attempt.",
    )
    setup_commands = setup_owner.add_subparsers(
        dest="setup_owner_command", required=True
    )
    setup_launch = setup_commands.add_parser(
        "launch", help="Launch the exact setup request read from standard input."
    )
    _add_json_argument(setup_launch)
    for command_name in ("status", "stop"):
        setup_command = setup_commands.add_parser(command_name)
        setup_command.add_argument("--attempt-id", required=True)
        setup_command.add_argument("--expect-request-sha256", required=True)
        setup_command.add_argument("--expect-launch-id", required=True)
        _add_json_argument(setup_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "capabilities":
        capabilities = list(BLENDER_BOX_CAPABILITIES)
        if WINDOWS_SETUP_OWNER_CAPABILITY in (args.require_capability or []):
            try:
                if _platform_system() != "Windows":
                    raise RuntimeError(
                        "Windows setup ownership is unavailable on this platform."
                    )
                windows_setup_owner_self_test()
            except (OSError, RuntimeError, ValueError) as error:
                print(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "status": "incompatible",
                            "contract": args.require,
                            "capability": WINDOWS_SETUP_OWNER_CAPABILITY,
                            "message": str(error),
                        },
                        indent=2,
                    )
                )
                return 1
            capabilities.append(WINDOWS_SETUP_OWNER_CAPABILITY)
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "compatible",
                    "contract": args.require,
                    "capabilities": capabilities,
                },
                indent=2,
            )
        )
        return 0

    if args.command == "doctor":
        try:
            report = build_doctor_report(explicit_blender=args.blender)
        except (OSError, RuntimeError, ValueError) as error:
            _print_failure(
                command="doctor",
                message=str(error),
                as_json=args.json,
            )
            return 1
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            _print_human_report(report)
        return 0 if report.status == "pass" else 1

    if args.command == "start":
        try:
            result = start_session(
                name=args.name,
                explicit_blender=args.blender,
                scene_path=args.scene,
            )
        except (
            AlreadyRunningError,
            SessionError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            _print_failure(
                command="start",
                message=str(error),
                as_json=args.json,
            )
            return 1
        payload = {
            "schema_version": 1,
            "status": "started",
            "message": result.message,
            "reclaimed_stale": result.reclaimed_stale,
            "session": result.record.public_dict(),
        }
        _print_result(payload, as_json=args.json)
        return 0

    if args.command == "status":
        try:
            if args.name is not None:
                inspection = inspect_session(name=args.name)
                payload = {
                    "schema_version": 1,
                    "status": inspection.status,
                    "session": inspection.to_dict(),
                }
                _print_result(payload, as_json=args.json)
                return 0 if inspection.healthy else 1

            inspections = inspect_all_sessions()
        except (OSError, RuntimeError, ValueError) as error:
            _print_failure(
                command="status",
                message=str(error),
                as_json=args.json,
            )
            return 1
        healthy = bool(inspections) and all(
            inspection.healthy for inspection in inspections
        )
        payload = {
            "schema_version": 1,
            "status": "healthy" if healthy else "unhealthy",
            "message": (
                f"Found {len(inspections)} recorded Session(s)."
                if inspections
                else "No Sessions found."
            ),
            "sessions": [inspection.to_dict() for inspection in inspections],
        }
        _print_result(payload, as_json=args.json)
        return 0 if healthy else 1

    if args.command == "stop":
        try:
            result = stop_session(
                name=args.name,
                expected_session_id=args.expect_session_id,
            )
        except (OSError, RuntimeError, ValueError) as error:
            _print_failure(
                command="stop",
                message=str(error),
                as_json=args.json,
            )
            return 1
        payload = {
            "schema_version": 1,
            "status": "stopped" if result.stopped else result.inspection.status,
            "message": result.message,
            "session": result.inspection.to_dict(),
        }
        _print_result(payload, as_json=args.json)
        return 0 if result.stopped else 1

    if args.command == "call":
        try:
            result = call_session(
                args.addon_command,
                params=args.params,
                name=args.name,
                expected_session_id=args.expect_session_id,
                read_timeout=args.read_timeout,
            )
        except (SessionError, WireError, OSError, RuntimeError, ValueError) as error:
            if args.json:
                _print_failure(
                    command="call",
                    message=str(error),
                    as_json=True,
                    reason=error.reason if isinstance(error, WireError) else None,
                )
            else:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "mcp-serve":
        try:
            return serve_mcp(name=args.name)
        except (McpServeError, OSError, RuntimeError, ValueError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1

    if args.command == "setup-owner":
        try:
            if args.setup_owner_command == "launch":
                result = launch_setup(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
            elif args.setup_owner_command == "status":
                result = status_setup(
                    args.attempt_id,
                    expected_request_sha256=args.expect_request_sha256,
                    expected_launch_id=args.expect_launch_id,
                )
            elif args.setup_owner_command == "stop":
                result = stop_setup(
                    args.attempt_id,
                    expected_request_sha256=args.expect_request_sha256,
                    expected_launch_id=args.expect_launch_id,
                )
            else:
                raise AssertionError(args.setup_owner_command)
        except (SetupOwnerError, OSError, RuntimeError, ValueError) as error:
            _print_failure(
                command=f"setup-owner {args.setup_owner_command}",
                message=str(error),
                as_json=args.json,
            )
            return 1
        _print_result(result.to_dict(), as_json=args.json)
        return 0

    raise AssertionError(f"unhandled command: {args.command}")


def _print_human_report(report: DoctorReport) -> None:
    for check in report.checks:
        print(f"[{check.status.upper()}] {check.name}: {check.message}")

    if report.status == "pass":
        print("PASS: This machine can host a Session.")
    else:
        print("FAIL: This machine cannot host a Session until the checks pass.")


def _add_name_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--name",
        default=DEFAULT_SESSION_NAME,
        type=_session_name,
        help=f"Session Name (default: {DEFAULT_SESSION_NAME}).",
    )


def _add_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the versioned machine-readable result.",
    )


def _add_expected_session_id_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--expect-session-id",
        required=True,
        type=_session_id,
        metavar="ID",
        help="Require the exact opaque Session ID returned by start or status.",
    )


def _session_name(value: str) -> str:
    try:
        return validate_session_name(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _session_id(value: str) -> str:
    try:
        return validate_session_id(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _read_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "read timeout must be a number of seconds"
        ) from error
    if not 0 < timeout <= MAX_READ_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            "read timeout must be greater than 0 and no more than "
            f"{MAX_READ_TIMEOUT_SECONDS:g} seconds"
        )
    return timeout


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(f"invalid JSON: {error.msg}") from error
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("--params must be a JSON object")
    return parsed


def _scene_path(value: str) -> Path:
    try:
        return resolve_scene_path(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _print_result(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return

    message = payload.get("message")
    if isinstance(message, str):
        print(message)
    session = payload.get("session")
    if isinstance(session, dict):
        _print_session(session)
    sessions = payload.get("sessions")
    if isinstance(sessions, list):
        if not sessions:
            return
        for item in sessions:
            if isinstance(item, dict):
                _print_session(item)


def _print_session(session: dict[str, Any]) -> None:
    print(
        f"Session {session['name']}: {session.get('status', 'recorded')} "
        f"(pid {session.get('pid', 'unknown')}, "
        f"MCP port {session.get('mcp_port', 'unknown')})"
    )
    session_id = session.get("session_id")
    if isinstance(session_id, str):
        print(f"  Session ID: {session_id}")
    logs = session.get("logs")
    if isinstance(logs, dict):
        print(f"  stdout log: {logs.get('stdout')}")
        print(f"  stderr log: {logs.get('stderr')}")
    if "scene_path" in session:
        scene_path = session.get("scene_path")
        print(
            f"  scene: {scene_path}"
            if isinstance(scene_path, str)
            else "  scene: factory-empty"
        )
    unsaved_changes = session.get("unsaved_changes")
    if unsaved_changes is True:
        print("  WARNING: Session has unsaved changes.")
    elif unsaved_changes is False:
        print("  unsaved changes: no")
    elif unsaved_changes == "unknown":
        print("  unsaved changes: unknown")
    health = session.get("health")
    if isinstance(health, dict):
        process = health.get("process")
        socket_health = health.get("socket")
        if isinstance(process, dict) and isinstance(socket_health, dict):
            print(
                f"  Health: process={process.get('status')} "
                f"socket={socket_health.get('status')}"
            )
            socket_message = socket_health.get("message")
            if socket_health.get("status") != "healthy" and isinstance(
                socket_message, str
            ):
                print(f"  socket: {socket_message}")


def _print_failure(
    *, command: str, message: str, as_json: bool, reason: str | None = None
) -> None:
    payload = {
        "schema_version": 1,
        "status": "error",
        "command": command,
        "message": message,
    }
    if reason is not None:
        payload["reason"] = reason
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"ERROR: {message}")
