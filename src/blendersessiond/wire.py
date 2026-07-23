"""TCP JSON client for the vendored BlenderMCP addon protocol."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any

LOOPBACK_HOST = "127.0.0.1"
HEALTH_COMMAND = "get_polyhaven_status"
MAX_READ_TIMEOUT_SECONDS = 180.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 3.0
DEFAULT_READ_TIMEOUT_SECONDS = MAX_READ_TIMEOUT_SECONDS
HEALTH_CONNECT_TIMEOUT_SECONDS = 0.5
HEALTH_READ_TIMEOUT_SECONDS = 2.0
_RECEIVE_CHUNK_BYTES = 8192


class WireError(RuntimeError):
    """A user-facing addon communication failure."""

    reason = "wire-error"


class WireConnectionError(WireError):
    """The addon socket could not be reached."""

    reason = "connection-failed"


class WireTimeoutError(WireError):
    """The addon did not answer before the configured timeout."""

    reason = "timeout"


class WireProtocolError(WireError):
    """The addon returned invalid or incomplete protocol data."""

    reason = "protocol-error"


class AddonError(WireError):
    """The addon reported a command failure."""

    reason = "addon-error"


@dataclass(frozen=True)
class HealthProbe:
    """Socket component result for Session Health."""

    healthy: bool
    reason: str
    message: str


def call_addon(
    port: int,
    command: str,
    params: dict[str, Any] | None = None,
    *,
    host: str = LOOPBACK_HOST,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
) -> Any:
    """Send one command on one short-lived addon connection."""

    _validate_timeout("connect", connect_timeout)
    _validate_timeout("read", read_timeout)
    request = json.dumps(
        {"type": command, "params": params or {}},
        separators=(",", ":"),
    ).encode("utf-8")

    try:
        connection = socket.create_connection(
            (host, port),
            timeout=connect_timeout,
        )
    except TimeoutError as error:
        raise WireTimeoutError(
            f"Timed out connecting to the addon at {host}:{port}."
        ) from error
    except OSError as error:
        raise WireConnectionError(
            f"Could not connect to the addon at {host}:{port}: {error}."
        ) from error

    with connection:
        try:
            connection.settimeout(read_timeout)
            connection.sendall(request)
            response = _receive_json(connection)
        except TimeoutError as error:
            raise WireTimeoutError(
                f"Timed out waiting for the addon at {host}:{port}."
            ) from error
        except (BrokenPipeError, ConnectionError, OSError) as error:
            raise WireConnectionError(
                f"Connection to the addon at {host}:{port} failed: {error}."
            ) from error

    if not isinstance(response, dict):
        raise WireProtocolError("Addon response must be a JSON object.")
    status = response.get("status")
    if status == "error":
        message = response.get("message")
        raise AddonError(
            message if isinstance(message, str) and message else "Unknown addon error."
        )
    if status != "success":
        raise WireProtocolError(
            "Addon response has no recognized success or error status."
        )
    result = response.get("result")
    # Many upstream handlers report failures as {"error": ...} results that
    # the addon dispatcher still wraps in a success envelope.
    if isinstance(result, dict):
        error_message = result.get("error")
        if isinstance(error_message, str) and error_message:
            raise AddonError(error_message)
    return result


def probe_addon(port: int, *, attempts: int = 2) -> HealthProbe:
    """Ping the addon, allowing one retry for first-command flakiness."""

    if attempts < 1:
        raise ValueError("Health probe attempts must be positive.")
    last_error: WireError | None = None
    for _attempt in range(attempts):
        try:
            call_addon(
                port,
                HEALTH_COMMAND,
                connect_timeout=HEALTH_CONNECT_TIMEOUT_SECONDS,
                read_timeout=HEALTH_READ_TIMEOUT_SECONDS,
            )
        except WireError as error:
            last_error = error
            continue
        return HealthProbe(
            healthy=True,
            reason="answered",
            message=f"Addon socket at {LOOPBACK_HOST}:{port} answered the Health ping.",
        )

    if last_error is None:
        raise AssertionError("positive probe attempts must produce a result")
    return HealthProbe(
        healthy=False,
        reason=last_error.reason,
        message=str(last_error),
    )


def _receive_json(connection: socket.socket) -> Any:
    chunks: list[bytes] = []
    while True:
        chunk = connection.recv(_RECEIVE_CHUNK_BYTES)
        if not chunk:
            if not chunks:
                raise WireProtocolError(
                    "Addon closed the connection before sending a response."
                )
            raise WireProtocolError("Addon closed the connection with partial JSON.")
        chunks.append(chunk)
        payload = b"".join(chunks)
        try:
            decoded = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            if error.reason == "unexpected end of data":
                continue
            raise WireProtocolError("Addon response is not valid UTF-8.") from error
        try:
            return json.loads(decoded)
        except json.JSONDecodeError:
            continue


def _validate_timeout(label: str, timeout: float) -> None:
    if not 0 < timeout <= MAX_READ_TIMEOUT_SECONDS:
        raise ValueError(
            f"{label.capitalize()} timeout must be greater than 0 and no more "
            f"than {MAX_READ_TIMEOUT_SECONDS:g} seconds."
        )
