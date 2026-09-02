from __future__ import annotations

import real_blender_smoke

SESSION_ID = "bss_exact-session-identity-value-123456"


def test_fenced_cli_passes_exact_session_identity(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_cli(*arguments, **kwargs):
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(real_blender_smoke, "_run_cli", fake_run_cli)

    result = real_blender_smoke._run_fenced_cli(
        "call",
        "get_scene_info",
        session_id=SESSION_ID,
        environment={"PATH": "test"},
        expected_codes={0},
        versioned=False,
    )

    assert result == {"ok": True}
    assert captured["arguments"] == (
        "call",
        "get_scene_info",
        "--expect-session-id",
        SESSION_ID,
    )
    assert captured["kwargs"] == {
        "environment": {"PATH": "test"},
        "expected_codes": {0},
        "versioned": False,
    }


def test_recover_started_session_identity_from_status(monkeypatch) -> None:
    def fake_run_cli(*arguments, **_kwargs):
        assert arguments == (
            "status",
            "--name",
            "ci-real-blender",
            "--json",
        )
        return {"status": "starting", "session": {"session_id": SESSION_ID}}

    monkeypatch.setattr(real_blender_smoke, "_run_cli", fake_run_cli)

    recovered = real_blender_smoke._recover_started_session_id(
        "ci-real-blender",
        environment={},
    )

    assert recovered == SESSION_ID


def test_wait_for_health_waits_for_unsaved_changes(monkeypatch) -> None:
    payloads = iter(
        [
            _healthy_payload(unsaved_changes="unknown"),
            _healthy_payload(unsaved_changes=False),
        ]
    )
    calls = 0

    def fake_run_cli(*_arguments, **_kwargs):
        nonlocal calls
        calls += 1
        return next(payloads)

    monkeypatch.setattr(real_blender_smoke, "_run_cli", fake_run_cli)
    monkeypatch.setattr(real_blender_smoke.time, "sleep", lambda _seconds: None)

    result = real_blender_smoke._wait_for_health(
        "ci-real-blender",
        expected_session_id=SESSION_ID,
        timeout=1,
        environment={},
    )

    assert result["session"]["unsaved_changes"] is False
    assert calls == 2


def _healthy_payload(*, unsaved_changes: bool | str) -> dict:
    return {
        "status": "healthy",
        "session": {
            "session_id": SESSION_ID,
            "status": "healthy",
            "unsaved_changes": unsaved_changes,
            "health": {
                "status": "healthy",
                "process": {
                    "status": "healthy",
                    "alive": True,
                },
                "socket": {
                    "status": "healthy",
                    "answered": True,
                },
            },
        },
    }
