from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from blendersessiond.wire import (
    AddonError,
    WireConnectionError,
    WireProtocolError,
    WireTimeoutError,
    call_addon,
    probe_addon,
)


@contextmanager
def scripted_addon(
    response: bytes,
    *,
    chunks: tuple[int, ...] = (),
    delay: float = 0,
) -> Iterator[tuple[int, list[dict[str, Any]]]]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    requests: list[dict[str, Any]] = []
    finished = threading.Event()

    def serve() -> None:
        try:
            connection, _address = listener.accept()
            with connection:
                request = connection.recv(8192)
                requests.append(json.loads(request.decode("utf-8")))
                if delay:
                    time.sleep(delay)
                offset = 0
                for size in chunks:
                    connection.sendall(response[offset : offset + size])
                    offset += size
                connection.sendall(response[offset:])
        finally:
            listener.close()
            finished.set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port, requests
    finally:
        listener.close()
        finished.wait(timeout=2)
        thread.join(timeout=2)


def _response(result: Any) -> bytes:
    return json.dumps({"status": "success", "result": result}).encode("utf-8")


def test_health_ping_answers() -> None:
    with scripted_addon(_response({"enabled": False})) as (port, requests):
        probe = probe_addon(port, attempts=1)

    assert probe.healthy is True
    assert probe.reason == "answered"
    assert requests == [{"type": "get_polyhaven_status", "params": {}}]


def test_connection_refused() -> None:
    temporary = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    temporary.bind(("127.0.0.1", 0))
    port = temporary.getsockname()[1]
    temporary.close()

    with pytest.raises(WireConnectionError, match="Could not connect"):
        call_addon(port, "get_scene_info", connect_timeout=0.1)


def test_response_timeout() -> None:
    with scripted_addon(_response({}), delay=0.2) as (port, _requests):
        with pytest.raises(WireTimeoutError, match="Timed out waiting"):
            call_addon(port, "get_scene_info", read_timeout=0.05)


def test_partial_response() -> None:
    with scripted_addon(b'{"status":"success"') as (port, _requests):
        with pytest.raises(WireProtocolError, match="partial JSON"):
            call_addon(port, "get_scene_info")


def test_oversized_response_is_received_without_truncation() -> None:
    expected = {"payload": "x" * 200_000}
    response = _response(expected)
    with scripted_addon(response, chunks=(17, 8192, 32768)) as (port, _requests):
        result = call_addon(port, "get_scene_info")

    assert result == expected


def test_addon_error_payload() -> None:
    response = json.dumps(
        {"status": "error", "message": "Object not found: Missing"}
    ).encode("utf-8")
    with scripted_addon(response) as (port, _requests):
        with pytest.raises(AddonError, match="Object not found: Missing"):
            call_addon(port, "get_object_info", {"name": "Missing"})
